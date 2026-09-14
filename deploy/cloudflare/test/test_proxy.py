from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest

DEPLOY = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def proxy_runtime(tmp_path_factory):
    directory = tmp_path_factory.mktemp("proxy-runtime")
    source = directory / "src"
    source.mkdir()
    for name in ("proxy.py", "entry.py"):
        shutil.copyfile(DEPLOY / "src" / name, source / name)
    shutil.copyfile(DEPLOY / "test" / "worker.py", source / "worker.py")
    (directory / "python_modules").symlink_to(
        DEPLOY / "python_modules", target_is_directory=True
    )
    config = directory / "wrangler.json"
    config.write_text(
        json.dumps(
            {
                "name": "presto-proxy-test",
                "main": "src/worker.py",
                "compatibility_date": "2026-09-13",
                "compatibility_flags": ["python_workers"],
                "r2_buckets": [
                    {"binding": "RESULTS", "bucket_name": "proxy-test-results"}
                ],
            }
        )
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    log_path = directory / "runtime.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [
                "uv",
                "run",
                "--locked",
                "pywrangler",
                "dev",
                "--config",
                str(config),
                "--local",
                "--ip",
                "127.0.0.1",
                "--port",
                str(port),
                "--inspector-port",
                "0",
                "--persist-to",
                str(directory / "state"),
            ],
            cwd=DEPLOY,
            stdout=log,
            stderr=log,
            start_new_session=True,
            env={
                **os.environ,
                "CI": "true",
                "WRANGLER_SEND_METRICS": "false",
                "NO_COLOR": "1",
            },
        )
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail(
                        "Proxy runtime exited during startup\n" + log_path.read_text()
                    )
                try:
                    with urlopen(endpoint + "/__health", timeout=1) as result:
                        if result.status == 200:
                            break
                except (OSError, URLError):
                    pass
                time.sleep(0.2)
            else:
                pytest.fail(
                    "Proxy runtime did not become ready\n" + log_path.read_text()
                )
            yield endpoint
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)


@pytest.mark.parametrize(
    "case",
    [
        "roundtrip",
        "forwarding",
        "publication_waits",
        "publication_failures",
        "retention",
        "sbom_mixed",
        "sbom_unchanged",
        "sbom_invalid",
        "errors",
        "routing",
    ],
)
def test_proxy_in_workerd(proxy_runtime, case):
    try:
        result = urlopen(f"{proxy_runtime}/{case}", timeout=30)
    except HTTPError as error:
        result = error
    with result:
        body = result.read().decode()
        assert result.status == 200, body
        assert body == "passed"
