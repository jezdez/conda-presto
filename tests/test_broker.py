"""Tests for the optional conda-broker provider."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from tomllib import loads
from types import SimpleNamespace

import psutil
import pytest
from conda_broker import Broker
from conda_broker.broker import BrokerServer
from conda_broker.paths import ServicePaths
from conda_broker.registry import ServiceRegistry

import conda_presto.broker as broker_module

ROOT = Path(__file__).resolve().parents[1]


def test_broker_service_exposes_root_and_health_check():
    service = next(broker_module.conda_broker_services())

    assert service.name == "conda-presto.server"
    assert service.start_policy == "manual"
    assert service.restart_policy == "on-failure"
    assert service.endpoints[0].path == "/"
    assert service.endpoints[0].port_env == "CONDA_PRESTO_PORT"
    assert service.endpoints[0].url_env == "CONDA_PRESTO_URL"
    assert service.health_check.type == "exec"
    assert service.health_check.command[-1] == "conda_presto.broker"
    assert service.health_check.start_period_s == 120
    assert service.process is not None
    assert service.process.argv[-1] == "--serve"
    assert service.process.env == {
        "CONDA_PRESTO_HOST": "127.0.0.1",
        "CONDA_PRESTO_CONCURRENCY": "1",
        "CONDA_PRESTO_SOLVER_ENDPOINT": "1",
        "CONDA_PRESTO_RATE_LIMIT": "0",
        "CONDA_PRESTO_PERSISTENT_WORKER": "1",
    }


def test_broker_health_check_uses_service_root(monkeypatch):
    checked = []

    @contextmanager
    def response():
        yield SimpleNamespace(status=200)

    monkeypatch.setenv("CONDA_PRESTO_URL", "http://127.0.0.1:8765/")
    monkeypatch.setattr(
        broker_module,
        "urlopen",
        lambda url, timeout: checked.append((url, timeout)) or response(),
    )

    broker_module.main()

    assert checked == [("http://127.0.0.1:8765/health", 2)]


def test_broker_health_check_fails_when_unavailable(monkeypatch):
    monkeypatch.setenv("CONDA_PRESTO_URL", "http://127.0.0.1:8765")

    def unavailable(*_, **__):
        raise OSError

    monkeypatch.setattr(broker_module, "urlopen", unavailable)

    with pytest.raises(SystemExit, match="1"):
        broker_module.main()


def test_broker_health_check_rejects_unsuccessful_response(monkeypatch):
    @contextmanager
    def response():
        yield SimpleNamespace(status=500)

    monkeypatch.setenv("CONDA_PRESTO_URL", "http://127.0.0.1:8765")
    monkeypatch.setattr(broker_module, "urlopen", lambda *_, **__: response())

    with pytest.raises(SystemExit, match="1"):
        broker_module.main()


def test_broker_entry_point_is_discoverable():
    registry = ServiceRegistry.discover()

    assert registry.names() == ["conda-presto.server"]
    assert registry.provider_errors == []


def test_broker_is_a_required_dependency():
    config = loads((ROOT / "pyproject.toml").read_text())

    assert config["project"]["entry-points"]["conda_broker"] == {
        "conda-presto": "conda_presto.broker"
    }
    assert "conda-broker>=0.1.1" in config["project"]["dependencies"]


def test_broker_replaces_service_after_nested_worker_exit(monkeypatch, tmp_path):
    if os.environ.get("CONDA_PRESTO_BROKER_LIFECYCLE_TEST") != "1":
        pytest.skip("broker lifecycle integration runs in its dedicated CI job")

    service = next(broker_module.conda_broker_services())
    paths = ServicePaths(tmp_path / "runtime", tmp_path / "logs")
    registry = ServiceRegistry([service])
    monkeypatch.setattr(
        ServiceRegistry,
        "discover",
        classmethod(lambda _: registry),
    )
    server = BrokerServer(paths)
    broker = Broker.current(paths)
    managed_pids = set()
    server_errors = []

    def diagnostics(status):
        return {
            "status": status.to_dict(),
            "events": broker.events()["events"],
            "logs": server.supervisor.logs.read_lines(
                service.name,
                lines=200,
                include_previous=True,
            ),
            "server_errors": [repr(error) for error in server_errors],
        }

    def run_server():
        try:
            server.run()
        except BaseException as exc:
            server_errors.append(exc)

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not broker.running():
            time.sleep(0.1)
        assert broker.running(), server_errors

        broker.start_services(service.name, timeout_s=5)
        initial_snapshot = broker.wait(service.name, timeout_s=180)
        assert initial_snapshot.services
        initial = initial_snapshot.services[0]
        assert initial.ready, diagnostics(initial)
        assert initial.pid is not None
        managed = server.supervisor.process(service.name)
        assert managed is not None
        # Move the healthy instance past its production startup grace period.
        managed.started_monotonic -= service.health_check.start_period_s + 1

        initial_process = psutil.Process(initial.pid)
        worker_process = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and worker_process is None:
            try:
                children = initial_process.children()
            except psutil.Error:
                children = []
            for child in children:
                try:
                    command = " ".join(child.cmdline())
                except psutil.Error:
                    continue
                if "spawn_main" in command:
                    worker_process = child
                    break
            if worker_process is None:
                time.sleep(0.1)
        assert worker_process is not None, diagnostics(initial)
        managed_pids = {
            initial_process.pid,
            *(child.pid for child in initial_process.children(recursive=True)),
        }

        worker_process.kill()
        worker_process.wait(timeout=5)

        replacement = initial
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            replacement = broker.status(service.name).services[0]
            if replacement.pid not in {None, initial.pid} and replacement.ready:
                break
            time.sleep(0.1)
        assert replacement.pid not in {None, initial.pid}, diagnostics(replacement)
        assert replacement.ready, diagnostics(replacement)

        event_types = [event["type"] for event in broker.events()["events"]]
        assert "service.unhealthy" in event_types, event_types
        assert "service.restart_scheduled" in event_types, event_types

        replacement_process = psutil.Process(replacement.pid)
        managed_pids.update(
            {
                replacement_process.pid,
                *(child.pid for child in replacement_process.children(recursive=True)),
            }
        )
    finally:
        try:
            if broker.running():
                broker.stop(timeout_s=15)
        finally:
            server.stop()
            server_thread.join(15)

    assert not server_thread.is_alive()
    assert server_errors == []

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(
        psutil.pid_exists(pid) for pid in managed_pids
    ):
        time.sleep(0.1)
    assert not [pid for pid in managed_pids if psutil.pid_exists(pid)]
