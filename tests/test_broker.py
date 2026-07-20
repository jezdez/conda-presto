"""Tests for the optional conda-broker provider."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tomllib import loads
from types import SimpleNamespace

import pytest
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
