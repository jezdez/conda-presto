"""Persistent process management for HTTP solver requests."""

from __future__ import annotations

import logging
import multiprocessing
import threading
import time
from contextlib import suppress
from typing import Any

from conda.exceptions import CondaError

from .exceptions import UnknownFormatError
from .exporter import OutputFormat
from .resolve import shutdown_process_pool, solve, solve_environments, warmup
from .solver import PrestoSolveError, PrestoSolveRequest

log = logging.getLogger(__name__)

PERSISTENT_WORKER_STARTUP_TIMEOUT_S = 120
PERSISTENT_WORKER_STOP_TIMEOUT_S = 5


class PersistentSolveWorker:
    """Run HTTP solves in a persistent process and replace it after failure."""

    def __init__(
        self,
        channels: list[str],
        platforms: list[str],
        *,
        restart_on_failure: bool = True,
        startup_timeout_s: float = PERSISTENT_WORKER_STARTUP_TIMEOUT_S,
    ) -> None:
        self.channels = channels
        self.platforms = platforms
        self.restart_on_failure = restart_on_failure
        self.startup_timeout_s = startup_timeout_s
        self.connection: Any | None = None
        self.process: Any | None = None
        self.restart_thread: threading.Thread | None = None
        self.is_ready = False
        self.operation_lock = threading.RLock()
        self._shutdown_requested = threading.Event()

    @property
    def running(self) -> bool:
        """Return whether the worker process is alive."""
        return self.process is not None and self.process.is_alive()

    @property
    def ready(self) -> bool:
        """Return whether the worker has loaded its configured indexes."""
        return self.is_ready and self.running

    def start(self) -> None:
        """Start the worker and wait within a bounded readiness period."""
        with self.operation_lock:
            if self.ready or self._shutdown_requested.is_set():
                return

            if not self.stop():
                raise RuntimeError("Persistent solve worker could not be stopped")
            context = multiprocessing.get_context("spawn")
            parent, child = context.Pipe()
            process = None
            try:
                process = context.Process(
                    target=persistent_solve_worker_entrypoint,
                    args=(child, self.channels, self.platforms),
                )
                process.start()
            except BaseException:
                with suppress(Exception):
                    parent.close()
                with suppress(Exception):
                    child.close()
                if process is not None and getattr(process, "pid", None) is not None:
                    self.process = process
                    with suppress(Exception):
                        self.stop()
                raise

            self.process = process
            try:
                child.close()
            except BaseException:
                with suppress(Exception):
                    parent.close()
                with suppress(Exception):
                    self.stop()
                raise
            self.connection = parent

            try:
                deadline = time.monotonic() + self.startup_timeout_s
                while True:
                    if self._shutdown_requested.is_set():
                        self.stop()
                        return
                    remaining_s = deadline - time.monotonic()
                    if remaining_s <= 0:
                        raise TimeoutError("Persistent solve worker startup timed out")
                    if parent.poll(min(0.1, remaining_s)):
                        break
                status, _ = parent.recv()
                if status != "ready":
                    raise RuntimeError("Persistent solve worker failed during startup")
            except EOFError as exc:
                with suppress(Exception):
                    self.stop()
                raise RuntimeError(
                    "Persistent solve worker exited during startup"
                ) from exc
            except BaseException:
                with suppress(Exception):
                    self.stop()
                raise
            self.is_ready = True

    def solve(
        self,
        channels: list[str],
        specs: list[str],
        platforms: list[str] | None,
        format_name: str | None,
        timeout_s: float,
    ) -> list | tuple[str, str]:
        """Return a solve result, replacing the worker after a timeout."""
        return self.execute((channels, specs, platforms, format_name), timeout_s)

    def solve_final_state(
        self,
        request: PrestoSolveRequest,
        timeout_s: float,
    ) -> object:
        """Run an internal solver request in the persistent worker."""
        return self.execute(("solver", request), timeout_s)

    def execute(self, request: object, timeout_s: float) -> object:
        """Exchange one request with the persistent worker process."""
        with self.operation_lock:
            if self.connection is None or not self.ready:
                self.recover_if_stopped()
                raise RuntimeError("Persistent solve worker is unavailable")

            try:
                self.connection.send(request)
            except (BrokenPipeError, EOFError, OSError) as exc:
                self.stop(restart=True)
                raise RuntimeError("Persistent solve worker exited") from exc
            if not self.connection.poll(timeout_s):
                self.stop(restart=True)
                raise TimeoutError
            try:
                status, payload = self.connection.recv()
            except EOFError as exc:
                self.stop(restart=True)
                raise RuntimeError("Persistent solve worker exited") from exc

            if status == "ok":
                return payload
            if status == "unknown-format":
                raise UnknownFormatError(payload["format_name"], payload["available"])
            raise RuntimeError("Persistent solve worker failed")

    def restart(self) -> None:
        """Start a replacement worker without surfacing background errors."""
        try:
            self.start()
        except Exception:
            log.exception("Persistent solve worker restart failed")

    def recover_if_stopped(self) -> None:
        """Schedule recovery when the worker process is no longer running."""
        with self.operation_lock:
            if self.running:
                return
            self.stop(restart=True)

    def stop(self, *, restart: bool = False) -> bool:
        """Stop the worker process and retain its handle if it survives."""
        with self.operation_lock:
            connection, self.connection = self.connection, None
            process = self.process
            self.is_ready = False
            if connection is not None:
                try:
                    connection.send(None)
                except (BrokenPipeError, EOFError, OSError):
                    pass
                connection.close()
            process_started = (
                process is not None and getattr(process, "pid", True) is not None
            )
            if process_started:
                if process.is_alive():
                    process.terminate()
                process.join(PERSISTENT_WORKER_STOP_TIMEOUT_S)
                if process.is_alive():
                    process.kill()
                    process.join(PERSISTENT_WORKER_STOP_TIMEOUT_S)
                if process.is_alive():
                    log.warning("Persistent solve worker did not exit after kill")
            stopped = not process_started or not process.is_alive()
            if stopped:
                self.process = None
            if (
                stopped
                and restart
                and self.restart_on_failure
                and not self._shutdown_requested.is_set()
                and (self.restart_thread is None or not self.restart_thread.is_alive())
            ):
                self.restart_thread = threading.Thread(
                    target=self.restart,
                    daemon=True,
                )
                self.restart_thread.start()
        return stopped

    def shutdown(self) -> bool:
        """Stop permanently and wait for any pending restart to finish."""
        self._shutdown_requested.set()
        stopped = self.stop()
        restart_thread = self.restart_thread
        if (
            restart_thread is not None
            and restart_thread is not threading.current_thread()
        ):
            restart_thread.join(PERSISTENT_WORKER_STOP_TIMEOUT_S)
            if restart_thread.is_alive():
                log.warning("Persistent solve worker restart did not stop")
                return False
        return stopped


def persistent_solve_worker_entrypoint(
    connection: Any,
    warmup_channels: list[str],
    warmup_platforms: list[str],
) -> None:
    """Serve solve requests while retaining the normal process pool."""
    try:
        warmup(warmup_channels, warmup_platforms)
    except Exception:
        log.exception("Persistent solve worker startup failed")
        connection.send(("startup-failed", None))
        connection.close()
        return

    connection.send(("ready", None))
    try:
        while True:
            try:
                request = connection.recv()
            except EOFError:
                break
            if request is None:
                break

            try:
                if isinstance(request, tuple) and request[0] == "solver":
                    try:
                        result = request[1].solve()
                    except CondaError as exc:
                        result = PrestoSolveError.from_exception(exc)
                else:
                    channels, specs, platforms, format_name = request
                    if format_name is None:
                        result = solve(channels, specs, platforms)
                    else:
                        result = OutputFormat.named(format_name).render(
                            solve_environments(channels, specs, platforms)
                        )
            except UnknownFormatError as exc:
                connection.send(
                    (
                        "unknown-format",
                        {"format_name": exc.format_name, "available": exc.available},
                    )
                )
            except Exception:
                log.exception("Persistent solve worker failed")
                connection.send(("error", None))
            else:
                connection.send(("ok", result))
    finally:
        shutdown_process_pool()
        connection.close()
