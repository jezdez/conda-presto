"""Exercise two real HTTP servers with independent conda caches and shared Redis."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


class ChannelHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass


def request(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    with urlopen(Request(url, data=data, headers=headers), timeout=60) as response:
        return response.status, dict(response.headers), response.read()


def unused_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def start_server(stack, directory, channel_url, redis_url, namespace, name):
    cache = directory / name / "pkgs"
    cache.mkdir(parents=True, exist_ok=True)
    condarc = directory / name / "condarc"
    condarc.write_text("{}\n")
    port = unused_port()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("CONDA_") and key != "CONDARC"
    }
    env.update(
        {
            "CONDARC": str(condarc),
            "CONDA_PKGS_DIRS": str(cache),
            "CONDA_SOLVER": "rattler",
            "CONDA_REPODATA_USE_SHARDS": "false",
            "CONDA_REPODATA_USE_ZST": "false",
            "CONDA_LOCAL_REPODATA_TTL": "3600",
            "CONDA_PRESTO_CHANNELS": channel_url,
            "CONDA_PRESTO_ALLOWED_CHANNELS": channel_url,
            "CONDA_PRESTO_PLATFORMS": "linux-64",
            "CONDA_PRESTO_PERSISTENT_WORKER": "true",
            "CONDA_PRESTO_CONCURRENCY": "1",
            "CONDA_PRESTO_WORKERS": "1",
            "CONDA_PRESTO_RATE_LIMIT": "0",
            "CONDA_PRESTO_RESULT_CACHE_BACKEND": "redis",
            "CONDA_PRESTO_RESULT_CACHE_REDIS_URL": redis_url,
            "CONDA_PRESTO_RESULT_CACHE_REDIS_NAMESPACE": namespace,
        }
    )
    log_path = directory / f"{name}.log"
    log = stack.enter_context(log_path.open("ab"))
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "conda_presto.cli",
            "--serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    stack.callback(stop, process)
    endpoint = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            if request(f"{endpoint}/health")[0] == 200:
                return process, endpoint, cache
        except (OSError, HTTPError, URLError):
            pass
        time.sleep(0.1)
    log.flush()
    details = log_path.read_text(errors="replace")[-4000:]
    details = details.replace(str(directory), "<integration>").replace(
        sys.prefix, "<environment>"
    )
    raise RuntimeError(
        f"{name} did not become ready, exit status {process.poll()}:\n{details}"
    )


def solve(endpoint, channel_url, package):
    started = time.monotonic()
    status, headers, body = request(
        f"{endpoint}/resolve",
        {"specs": [package], "channels": [channel_url], "platforms": ["linux-64"]},
    )
    result = json.loads(body)
    if (
        status != 200
        or len(result) != 1
        or result[0]["error"] is not None
        or [item["name"] for item in result[0]["packages"]] != [package]
    ):
        raise RuntimeError("The controlled package solve did not succeed")
    return headers, body, time.monotonic() - started


def workload(endpoints, channel_url, name, count):
    def run(index):
        try:
            _, _, elapsed = solve(
                endpoints[index % len(endpoints)], channel_url, f"{name}-{index}"
            )
            return {"completed": True, "seconds": round(elapsed, 4)}
        except (OSError, ValueError, RuntimeError) as exc:
            return {"completed": False, "error": type(exc).__name__}

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=count) as executor:
        results = list(executor.map(run, range(count)))
    return {
        "instances": len(endpoints),
        "requests": count,
        "completed": sum(item["completed"] for item in results),
        "errors": sum(not item["completed"] for item in results),
        "wall_seconds": round(time.monotonic() - started, 4),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--redis-url", required=True)
    parser.add_argument("--requests", type=int, default=8)
    args = parser.parse_args()
    if not 2 <= args.requests <= 32:
        parser.error("--requests must be between 2 and 32")
    with (
        tempfile.TemporaryDirectory(prefix="presto-service-") as temporary,
        ExitStack() as stack,
    ):
        directory = Path(temporary)
        channel = directory / "channel"
        names = ["presto-shared"] + [
            f"{mode}-{index}"
            for mode in ("one-instance", "two-instances")
            for index in range(args.requests)
        ]
        for subdir in ("linux-64", "noarch"):
            location = channel / subdir
            location.mkdir(parents=True)
            packages = {}
            if subdir == "linux-64":
                packages = {
                    f"{name}-1.0-0.tar.bz2": {
                        "name": name,
                        "version": "1.0",
                        "build": "0",
                        "build_number": 0,
                        "depends": [],
                        "subdir": subdir,
                        "md5": "0" * 32,
                        "sha256": "0" * 64,
                        "size": 1,
                        "timestamp": 1_700_000_000_000,
                    }
                    for name in names
                }
            (location / "repodata.json").write_text(
                json.dumps(
                    {
                        "info": {"subdir": subdir},
                        "packages": packages,
                        "packages.conda": {},
                        "repodata_version": 1,
                    }
                )
            )
        channel_server = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(ChannelHandler, directory=str(channel))
        )
        stack.callback(channel_server.server_close)
        stack.callback(channel_server.shutdown)
        threading.Thread(target=channel_server.serve_forever, daemon=True).start()
        channel_url = f"http://127.0.0.1:{channel_server.server_port}"
        namespace = f"presto-integration-{uuid4().hex}"
        first, first_url, first_cache = start_server(
            stack, directory, channel_url, args.redis_url, namespace, "replica-a"
        )
        second, second_url, second_cache = start_server(
            stack, directory, channel_url, args.redis_url, namespace, "replica-b"
        )
        headers, body, _ = solve(first_url, channel_url, "presto-shared")
        location = headers.get("location") or headers.get("Location")
        if not location:
            raise RuntimeError("The shared store did not retain the solve result")
        if request(f"{second_url}{location}")[2] != body:
            raise RuntimeError("The other replica returned different result bytes")
        stop(first)
        if first.poll() is None or request(f"{second_url}{location}")[2] != body:
            raise RuntimeError("The shared result did not survive producer shutdown")
        first, first_url, first_cache = start_server(
            stack, directory, channel_url, args.redis_url, namespace, "replica-a"
        )
        runs = [
            workload([second_url], channel_url, "one-instance", args.requests),
            workload(
                [first_url, second_url], channel_url, "two-instances", args.requests
            ),
        ]
        cache_counts = [
            len(list(path.rglob("*.json"))) for path in (first_cache, second_cache)
        ]
        if not all(cache_counts) or first_cache == second_cache:
            raise RuntimeError("Independent conda metadata caches were not populated")
        report = {
            "shared_result_sha256": hashlib.sha256(body).hexdigest(),
            "retrieved_by_other_replica": True,
            "retrieved_after_producer_exit": True,
            "independent_metadata_cache_files": cache_counts,
            "uncached_workloads": runs,
            "timing_interpretation": "Observed durations only, no speedup assertion.",
        }
        print(json.dumps(report, indent=2))
        if any(run["errors"] for run in runs):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
