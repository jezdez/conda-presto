"""Tests for persistent HTTP solver process management."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from conda.exceptions import PackagesNotFoundError
from conda.models.environment import Environment

import conda_presto.worker as worker_module
from conda_presto.resolve import SolveResult
from conda_presto.solver import PrestoSolveError, PrestoSolveOutcome


@pytest.fixture()
def persistent_solve_worker(monkeypatch):
    def create(
        messages,
        *,
        poll=True,
        startup_poll=True,
        startup_poll_error: Exception | None = None,
        start=True,
        send_error_on: str | None = None,
        terminate_stops=True,
        kill_stops=True,
        restart_on_failure=True,
        process_create_error: Exception | None = None,
        process_start_error: Exception | None = None,
        startup_timeout_s=worker_module.PERSISTENT_WORKER_STARTUP_TIMEOUT_S,
    ):
        calls = []
        restart_targets = []
        alive = {"value": True}
        received = iter(messages)
        awaiting_startup = False

        def send(value):
            if value is None and send_error_on == "stop":
                raise BrokenPipeError
            if value is not None and send_error_on == "request":
                raise BrokenPipeError
            calls.append(("send", value))

        def receive():
            nonlocal awaiting_startup
            value = next(received)
            awaiting_startup = False
            if isinstance(value, Exception):
                raise value
            return value

        def terminate():
            if terminate_stops:
                alive["value"] = False

        def kill():
            calls.append(("process", "kill"))
            if kill_stops:
                alive["value"] = False

        def start_process():
            nonlocal awaiting_startup
            awaiting_startup = True
            alive["value"] = True
            calls.append(("process", "start"))
            if process_start_error is not None:
                raise process_start_error

        def can_receive(_):
            if awaiting_startup:
                if startup_poll_error is not None:
                    raise startup_poll_error
                return startup_poll
            return poll

        parent = SimpleNamespace(
            send=send,
            poll=can_receive,
            recv=receive,
            close=lambda: calls.append(("parent", "close")),
        )
        child = SimpleNamespace(close=lambda: calls.append(("child", "close")))
        process = SimpleNamespace(
            start=start_process,
            is_alive=lambda: alive["value"],
            terminate=terminate,
            kill=kill,
            join=lambda timeout=None: calls.append(("process", f"join:{timeout}")),
        )

        def create_process(**_):
            if process_create_error is not None:
                raise process_create_error
            return process

        context = SimpleNamespace(Pipe=lambda: (parent, child), Process=create_process)
        monkeypatch.setattr(
            worker_module.multiprocessing, "get_context", lambda _: context
        )
        monkeypatch.setattr(
            worker_module.threading,
            "Thread",
            lambda *, target, daemon: SimpleNamespace(
                start=lambda: restart_targets.append(target),
                is_alive=lambda: False,
                join=lambda *_: None,
            ),
        )
        worker = worker_module.PersistentSolveWorker(
            ["conda-forge"],
            ["linux-64"],
            restart_on_failure=restart_on_failure,
            startup_timeout_s=startup_timeout_s,
        )
        if start:
            worker.start()
        worker.restart_targets = restart_targets
        return worker, calls

    return create


def test_persistent_solve_worker_returns_result(persistent_solve_worker):
    worker, calls = persistent_solve_worker([("ready", None), ("ok", [])])

    assert worker.ready
    assert worker.solve(["conda-forge"], ["zlib"], ["linux-64"], None, 60) == []

    worker.stop()
    assert not worker.ready
    assert calls == [
        ("process", "start"),
        ("child", "close"),
        ("send", (["conda-forge"], ["zlib"], ["linux-64"], None)),
        ("send", None),
        ("parent", "close"),
        ("process", "join:5"),
    ]


def test_persistent_solve_worker_uses_remaining_deadline(monkeypatch):
    calls = []
    worker = worker_module.PersistentSolveWorker([], [])
    monkeypatch.setattr(worker_module.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(
        worker,
        "execute",
        lambda request, timeout_s: calls.append((request, timeout_s)) or "result",
    )

    result = worker.solve_final_state("request", 12.5)

    assert result == "result"
    assert calls == [(("solver", "request"), 2.5)]


def test_persistent_solve_worker_rejects_expired_deadline(monkeypatch):
    worker = worker_module.PersistentSolveWorker([], [])
    monkeypatch.setattr(worker_module.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(
        worker,
        "execute",
        lambda *_: pytest.fail("expired request must not reach worker"),
    )

    with pytest.raises(TimeoutError):
        worker.solve_final_state("request", 10.0)


def test_persistent_solve_worker_start_is_idempotent(persistent_solve_worker):
    worker, calls = persistent_solve_worker([("ready", None)])

    worker.start()

    assert calls == [("process", "start"), ("child", "close")]


def test_persistent_solve_worker_closes_pipes_when_process_start_fails(
    persistent_solve_worker,
):
    worker, calls = persistent_solve_worker(
        [],
        start=False,
        process_start_error=OSError("spawn failed"),
    )

    with pytest.raises(OSError, match="spawn failed"):
        worker.start()

    assert worker.connection is None
    assert worker.process is None
    assert calls == [
        ("process", "start"),
        ("parent", "close"),
        ("child", "close"),
    ]


def test_persistent_solve_worker_closes_pipes_when_process_creation_fails(
    persistent_solve_worker,
):
    worker, calls = persistent_solve_worker(
        [],
        start=False,
        process_create_error=OSError("process construction failed"),
    )

    with pytest.raises(OSError, match="process construction failed"):
        worker.start()

    assert worker.connection is None
    assert worker.process is None
    assert calls == [("parent", "close"), ("child", "close")]


def test_persistent_solve_worker_rejects_startup_failure(persistent_solve_worker):
    worker, _ = persistent_solve_worker([("startup-failed", None)], start=False)

    with pytest.raises(RuntimeError, match="failed during startup"):
        worker.start()

    assert worker.connection is None
    assert worker.process is None


def test_persistent_solve_worker_bounds_startup(persistent_solve_worker):
    worker, _ = persistent_solve_worker(
        [],
        start=False,
        startup_poll=False,
        startup_timeout_s=0.001,
    )

    with pytest.raises(TimeoutError, match="startup timed out"):
        worker.start()

    assert worker.connection is None
    assert worker.process is None


def test_persistent_solve_worker_cleans_up_startup_ipc_error(
    persistent_solve_worker,
):
    worker, _ = persistent_solve_worker(
        [],
        start=False,
        startup_poll_error=OSError("readiness pipe failed"),
    )

    with pytest.raises(OSError, match="readiness pipe failed"):
        worker.start()

    assert worker.connection is None
    assert worker.process is None
    assert not worker.ready


def test_persistent_solve_worker_logs_restart_failure(persistent_solve_worker):
    worker, _ = persistent_solve_worker([("ready", None), EOFError()])

    worker.stop()
    worker.restart()

    assert not worker.ready


def test_persistent_solve_worker_rejects_unavailable_worker(persistent_solve_worker):
    worker, _ = persistent_solve_worker([], start=False)

    with pytest.raises(RuntimeError, match="unavailable"):
        worker.solve(["conda-forge"], ["zlib"], ["linux-64"], None, 60)


def test_persistent_solve_worker_handles_broken_request_pipe(persistent_solve_worker):
    worker, _ = persistent_solve_worker([("ready", None)], send_error_on="request")

    with pytest.raises(RuntimeError, match="exited"):
        worker.solve(["conda-forge"], ["zlib"], ["linux-64"], None, 60)

    assert worker.connection is None
    assert worker.process is None


def test_persistent_solve_worker_handles_closed_response_pipe(persistent_solve_worker):
    worker, _ = persistent_solve_worker([("ready", None), EOFError()])

    with pytest.raises(RuntimeError, match="exited"):
        worker.solve(["conda-forge"], ["zlib"], ["linux-64"], None, 60)

    assert worker.connection is None
    assert worker.process is None


def test_persistent_solve_worker_kills_unstoppable_process(persistent_solve_worker):
    worker, calls = persistent_solve_worker([("ready", None)], terminate_stops=False)

    worker.stop()

    assert ("process", "kill") in calls


def test_persistent_solve_worker_bounds_post_kill_wait(
    persistent_solve_worker,
    caplog,
):
    worker, calls = persistent_solve_worker(
        [("ready", None)],
        terminate_stops=False,
        kill_stops=False,
    )

    with caplog.at_level("WARNING", logger="conda_presto.worker"):
        stopped = worker.stop()

    assert not stopped
    assert worker.process is not None
    assert calls.count(("process", "join:5")) == 2
    assert "did not exit after kill" in caplog.text


def test_persistent_solve_worker_ignores_closed_pipe_on_stop(persistent_solve_worker):
    worker, _ = persistent_solve_worker([("ready", None)], send_error_on="stop")

    worker.stop()

    assert worker.connection is None
    assert worker.process is None


def test_persistent_solve_worker_discards_an_unstarted_process():
    worker = worker_module.PersistentSolveWorker([], [])
    worker.process = SimpleNamespace(
        pid=None,
        is_alive=lambda: pytest.fail("unstarted process was inspected"),
        join=lambda *_: pytest.fail("unstarted process was joined"),
    )

    assert worker.stop()
    assert worker.process is None


@pytest.mark.parametrize(
    ("message", "error"),
    [
        pytest.param(
            ("unknown-format", {"format_name": "toml", "available": ["yaml"]}),
            worker_module.UnknownFormatError,
            id="unknown-format",
        ),
        pytest.param(("error", None), RuntimeError, id="worker-error"),
    ],
)
def test_persistent_solve_worker_raises_worker_error(
    persistent_solve_worker, message, error
):
    worker, _ = persistent_solve_worker([("ready", None), message])

    with pytest.raises(error):
        worker.solve(["conda-forge"], ["zlib"], ["linux-64"], None, 60)


def test_persistent_solve_worker_kills_timed_out_process(persistent_solve_worker):
    worker, calls = persistent_solve_worker([("ready", None)], poll=False)

    with pytest.raises(TimeoutError):
        worker.solve(["conda-forge"], ["zlib"], ["linux-64"], None, 60)

    assert worker.connection is None
    assert worker.process is None
    assert calls[-3:] == [
        ("send", None),
        ("parent", "close"),
        ("process", "join:5"),
    ]


def test_persistent_solve_worker_restarts_after_timeout(persistent_solve_worker):
    worker, _ = persistent_solve_worker(
        [("ready", None), ("ready", None)],
        poll=False,
    )

    with pytest.raises(TimeoutError):
        worker.solve(["conda-forge"], ["zlib"], ["linux-64"], None, 60)

    assert not worker.ready
    assert len(worker.restart_targets) == 1
    worker.restart_targets[0]()
    assert worker.ready


def test_persistent_solve_worker_recovers_after_idle_exit(persistent_solve_worker):
    worker, _ = persistent_solve_worker([("ready", None), ("ready", None)])
    worker.recover_if_stopped()
    assert worker.restart_targets == []

    worker.process.terminate()

    worker.recover_if_stopped()

    worker.restart_targets[0]()
    assert worker.ready


def test_persistent_solve_worker_retries_failed_recovery(persistent_solve_worker):
    worker, _ = persistent_solve_worker([("ready", None), EOFError(), ("ready", None)])
    worker.process.terminate()

    with pytest.raises(RuntimeError, match="unavailable"):
        worker.solve(["conda-forge"], ["zlib"], ["linux-64"], None, 60)
    worker.restart_targets[0]()
    assert not worker.ready

    with pytest.raises(RuntimeError, match="unavailable"):
        worker.solve(["conda-forge"], ["zlib"], ["linux-64"], None, 60)
    worker.restart_targets[1]()
    assert worker.ready


def test_persistent_solve_worker_leaves_recovery_to_broker(persistent_solve_worker):
    worker, _ = persistent_solve_worker(
        [("ready", None)],
        restart_on_failure=False,
    )
    worker.process.terminate()

    with pytest.raises(RuntimeError, match="unavailable"):
        worker.solve(["conda-forge"], ["zlib"], ["linux-64"], None, 60)

    assert worker.restart_targets == []


def test_persistent_solve_worker_shutdown_prevents_pending_restart(
    persistent_solve_worker,
):
    worker, calls = persistent_solve_worker([("ready", None)])
    worker.process.terminate()
    worker.recover_if_stopped()
    restart = worker.restart_targets[0]

    assert worker.shutdown()
    restart()

    assert calls.count(("process", "start")) == 1
    assert worker.process is None


def test_persistent_solve_worker_shutdown_interrupts_restart_startup(monkeypatch):
    processes = []
    second_startup_polled = threading.Event()

    def create_pipe():
        index = len(processes)

        def poll(timeout_s):
            if index == 0:
                return True
            second_startup_polled.set()
            time.sleep(timeout_s)
            return False

        return (
            SimpleNamespace(
                send=lambda _: None,
                poll=poll,
                recv=lambda: ("ready", None),
                close=lambda: None,
            ),
            SimpleNamespace(close=lambda: None),
        )

    def create_process(**_):
        alive = {"value": False}
        process = SimpleNamespace(pid=None)

        def start():
            process.pid = len(processes) + 1
            alive["value"] = True

        process.start = start
        process.is_alive = lambda: alive["value"]
        process.terminate = lambda: alive.update(value=False)
        process.kill = lambda: alive.update(value=False)
        process.join = lambda *_: None
        processes.append(process)
        return process

    context = SimpleNamespace(Pipe=create_pipe, Process=create_process)
    monkeypatch.setattr(
        worker_module.multiprocessing,
        "get_context",
        lambda _: context,
    )
    worker = worker_module.PersistentSolveWorker([], [])
    worker.start()
    worker.process.terminate()
    worker.recover_if_stopped()
    assert second_startup_polled.wait(1)

    started = time.monotonic()
    assert worker.shutdown()

    assert time.monotonic() - started < 2
    assert len(processes) == 2
    assert worker.process is None


def test_persistent_solve_worker_entrypoint_handles_native_requests(monkeypatch):
    sent = []
    platforms = ["linux-64", "osx-arm64"]
    requests = iter([(["conda-forge"], ["zlib"], platforms, None), None])
    connection = SimpleNamespace(
        recv=lambda: next(requests),
        send=sent.append,
        close=lambda: sent.append("closed"),
    )
    result = [
        SolveResult(platform="linux-64", packages=[]),
        SolveResult(platform="osx-arm64", packages=[]),
    ]
    calls = []
    monkeypatch.setattr(worker_module, "warmup", lambda *args: calls.append(args))
    monkeypatch.setattr(
        worker_module,
        "solve",
        lambda channels, specs, platforms: (
            calls.append((channels, specs, platforms)) or result
        ),
    )
    monkeypatch.setattr(
        worker_module, "shutdown_process_pool", lambda: calls.append("shutdown")
    )

    worker_module.persistent_solve_worker_entrypoint(
        connection, ["conda-forge"], ["linux-64"]
    )

    assert sent == [("ready", None), ("ok", result), "closed"]
    assert calls == [
        (["conda-forge"], ["linux-64"]),
        (["conda-forge"], ["zlib"], platforms),
        "shutdown",
    ]


def test_persistent_solve_worker_entrypoint_captures_platform_errors(monkeypatch):
    sent = []
    requests = iter([(["conda-forge"], ["zlib"], ["linux-64"], None), None])
    connection = SimpleNamespace(
        recv=lambda: next(requests),
        send=sent.append,
        close=lambda: sent.append("closed"),
    )
    monkeypatch.setattr(worker_module, "warmup", lambda *_: None)
    monkeypatch.setattr(
        worker_module,
        "solve",
        lambda *_: (_ for _ in ()).throw(RuntimeError),
    )
    monkeypatch.setattr(worker_module, "shutdown_process_pool", lambda: None)

    worker_module.persistent_solve_worker_entrypoint(
        connection, ["conda-forge"], ["linux-64"]
    )

    assert sent == [("ready", None), ("error", None), "closed"]


def test_persistent_solve_worker_entrypoint_handles_exporters(monkeypatch):
    sent = []
    requests = iter([(["conda-forge"], ["zlib"], ["linux-64"], "explicit"), None])
    connection = SimpleNamespace(
        recv=lambda: next(requests),
        send=sent.append,
        close=lambda: sent.append("closed"),
    )
    output_format = SimpleNamespace(render=lambda envs: ("output", "text/plain"))
    monkeypatch.setattr(worker_module, "warmup", lambda *_: None)
    monkeypatch.setattr(
        worker_module,
        "solve_environments",
        lambda *_: [Environment(platform="linux-64")],
    )
    monkeypatch.setattr(worker_module.OutputFormat, "named", lambda _: output_format)
    monkeypatch.setattr(worker_module, "shutdown_process_pool", lambda: None)

    worker_module.persistent_solve_worker_entrypoint(
        connection, ["conda-forge"], ["linux-64"]
    )

    assert sent == [("ready", None), ("ok", ("output", "text/plain")), "closed"]


def test_persistent_solve_worker_entrypoint_serializes_conda_error(monkeypatch):
    sent = []
    error = PackagesNotFoundError(["missing"], ["https://example.invalid"])
    solve_request = SimpleNamespace(solve=lambda: (_ for _ in ()).throw(error))
    requests = iter([("solver", solve_request), None])
    connection = SimpleNamespace(
        recv=lambda: next(requests),
        send=sent.append,
        close=lambda: sent.append("closed"),
    )
    monkeypatch.setattr(worker_module, "warmup", lambda *_: None)
    monkeypatch.setattr(worker_module, "shutdown_process_pool", lambda: None)

    worker_module.persistent_solve_worker_entrypoint(
        connection, ["conda-forge"], ["linux-64"]
    )

    assert sent[0] == ("ready", None)
    assert sent[1][0] == "ok"
    assert isinstance(sent[1][1], PrestoSolveOutcome)
    assert isinstance(sent[1][1].result, PrestoSolveError)
    assert sent[1][1].result.kind == "packages-not-found"
    assert sent[1][1].metadata_before is None
    assert sent[1][1].metadata_used is None
    assert sent[2] == "closed"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        pytest.param(
            worker_module.UnknownFormatError("missing", []),
            (
                "unknown-format",
                {"format_name": "missing", "available": []},
            ),
            id="unknown-format",
        ),
        pytest.param(RuntimeError(), ("error", None), id="solver-error"),
    ],
)
def test_persistent_solve_worker_entrypoint_reports_export_errors(
    monkeypatch, error, expected
):
    sent = []
    requests = iter([(["conda-forge"], ["zlib"], ["linux-64"], "explicit"), None])
    connection = SimpleNamespace(
        recv=lambda: next(requests),
        send=sent.append,
        close=lambda: sent.append("closed"),
    )
    monkeypatch.setattr(worker_module, "warmup", lambda *_: None)
    monkeypatch.setattr(
        worker_module,
        "solve_environments",
        lambda *_: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(worker_module, "shutdown_process_pool", lambda: None)

    worker_module.persistent_solve_worker_entrypoint(
        connection, ["conda-forge"], ["linux-64"]
    )

    assert sent == [("ready", None), expected, "closed"]


def test_persistent_solve_worker_entrypoint_handles_closed_request_pipe(monkeypatch):
    sent = []
    connection = SimpleNamespace(
        recv=lambda: (_ for _ in ()).throw(EOFError),
        send=sent.append,
        close=lambda: sent.append("closed"),
    )
    monkeypatch.setattr(worker_module, "warmup", lambda *_: None)
    monkeypatch.setattr(worker_module, "shutdown_process_pool", lambda: None)

    worker_module.persistent_solve_worker_entrypoint(
        connection, ["conda-forge"], ["linux-64"]
    )

    assert sent == [("ready", None), "closed"]


def test_persistent_solve_worker_entrypoint_reports_startup_failure(monkeypatch):
    sent = []
    connection = SimpleNamespace(send=sent.append, close=lambda: sent.append("closed"))

    def fail_warmup(*_):
        raise RuntimeError

    monkeypatch.setattr(worker_module, "warmup", fail_warmup)

    worker_module.persistent_solve_worker_entrypoint(
        connection, ["conda-forge"], ["linux-64"]
    )

    assert sent == [("startup-failed", None), "closed"]
