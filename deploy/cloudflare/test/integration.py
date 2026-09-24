"""Probe real local Workers, native containers and retained R2 outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEPLOY = Path(__file__).resolve().parents[1]
PREFIX = "workerd-conda-presto-edge-PrestoContainer-"


class ChannelHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass


def command(*args, merge_output=False):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"{args[0]} command failed")
    return result.stdout + result.stderr if merge_output else result.stdout


def request(url, payload=None, method=None, timeout=60):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    try:
        response = urlopen(Request(url, body, headers, method=method), timeout=timeout)
    except HTTPError as response_error:
        response = response_error
    with response:
        return (
            response.status,
            {k.lower(): v for k, v in response.headers.items()},
            response.read(),
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel-host", default="host.docker.internal")
    args = parser.parse_args()
    existing = command(
        "docker",
        "ps",
        "-a",
        "--format",
        "{{.Names}}",
        "--filter",
        f"name=^/{PREFIX}[0-9a-f]{{64}}(-proxy)?$",
    )
    if existing.strip():
        raise SystemExit(
            "Stop the existing local edge experiment before running this probe"
        )
    with tempfile.TemporaryDirectory(prefix="presto-edge-probe-") as temporary:
        directory = Path(temporary)
        graph = {
            "presto-edge-leaf": [],
            "presto-edge-mid": ["presto-edge-leaf >=1"],
            "presto-edge-root": ["presto-edge-mid >=1", "presto-edge-leaf >=1"],
        }
        records = {
            f"{name}-1.0-0.tar.bz2": {
                "name": name,
                "version": "1.0",
                "build": "0",
                "build_number": 0,
                "depends": depends,
                "subdir": "linux-64",
                "sha256": hashlib.sha256(name.encode()).hexdigest(),
                "md5": hashlib.md5(name.encode(), usedforsecurity=False).hexdigest(),
                "size": 1,
                "timestamp": 1_700_000_000_000,
            }
            for name, depends in graph.items()
        }
        metadata = {
            subdir: json.dumps(
                {
                    "info": {"subdir": subdir},
                    "packages": records if subdir == "linux-64" else {},
                    "packages.conda": {},
                    "repodata_version": 1,
                },
                sort_keys=True,
            ).encode()
            for subdir in ("linux-64", "noarch")
        }
        digest = hashlib.sha256(b"".join(metadata.values())).hexdigest()
        channel_root = directory / "channel"
        for subdir, content in metadata.items():
            target = channel_root / f"snapshot-{digest}" / subdir
            target.mkdir(parents=True)
            (target / "repodata.json").write_bytes(content)
        server = ThreadingHTTPServer(
            ("0.0.0.0", 0),
            partial(ChannelHandler, directory=str(channel_root)),
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        channel = f"http://{args.channel_host}:{server.server_port}/snapshot-{digest}"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        endpoint = f"http://127.0.0.1:{port}"
        log_path = directory / "wrangler.log"
        process = None
        owned = set()
        stage = "startup"
        report = {"metadata_sha256": digest, "geographic_execution_verified": False}
        try:
            with log_path.open("wb") as log:
                process = subprocess.Popen(
                    [
                        "uv",
                        "run",
                        "--locked",
                        "pywrangler",
                        "dev",
                        "--local",
                        "--ip",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--inspector-port",
                        "0",
                        "--persist-to",
                        str(directory / "state"),
                        "--var",
                        f"CONDA_PRESTO_CHANNELS:{channel}",
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
                instances = []
                startup = time.monotonic()
                for index in (0, 1):
                    deadline = time.monotonic() + 180
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise RuntimeError("Wrangler exited during startup")
                        try:
                            status, headers, _ = request(
                                f"{endpoint}/_instances/{index}/health",
                                timeout=10,
                            )
                            identity = headers.get("x-presto-instance", "")
                            if re.fullmatch(r"[0-9a-f]{64}", identity):
                                owned.add(PREFIX + identity)
                            if status == 200 and identity:
                                instances.append(identity)
                                break
                        except (OSError, URLError):
                            pass
                        time.sleep(0.5)
                    else:
                        raise RuntimeError("Native instance did not become ready")
                assert len(set(instances)) == 2, (
                    "Instance selectors did not select distinct containers"
                )
                report["startup_seconds"] = round(time.monotonic() - startup, 4)
                report["distinct_native_instances"] = 2
                outputs, timings = [], []
                expected = {
                    record["name"]: record["sha256"] for record in records.values()
                }
                payload = {
                    "specs": ["presto-edge-root"],
                    "channels": [channel],
                    "platforms": ["linux-64"],
                }
                native = None
                rendered_hashes = []
                for index in (0, 1):
                    for mode in ("first", "repeat", "exporter", "sbom"):
                        stage = f"instance_{index}_{mode}"
                        suffix = "?format=conda-lock-v1" if mode == "exporter" else ""
                        started = time.monotonic()
                        route = "sbom" if mode == "sbom" else "resolve"
                        status, headers, body = request(
                            f"{endpoint}/_instances/{index}/{route}{suffix}",
                            payload,
                        )
                        timings.append(
                            {
                                "instance": index,
                                "mode": mode,
                                "seconds": round(time.monotonic() - started, 4),
                            }
                        )
                        assert status == 200, "Controlled solve failed"
                        assert headers.get("x-presto-instance") == instances[index], (
                            "Wrong native instance"
                        )
                        if mode == "sbom":
                            documents = json.loads(body)["sboms"]
                            assert len(documents) == 1
                            document = documents[0]
                            assert document["platform"] == "linux-64"
                            content = document["content"].encode()
                            assert (
                                hashlib.sha256(content).hexdigest()
                                == document["sha256"]
                            )
                            sbom = json.loads(content)
                            assert {item["name"] for item in sbom["components"]} == set(
                                graph
                            )
                            assert re.fullmatch(
                                r"/r/[0-9a-f]{64}", document["location"]
                            )
                            outputs.append((index, document["location"], content))
                            continue
                        if mode == "exporter":
                            assert body and all(
                                name.encode() in body for name in graph
                            ), "Exporter lost packages"
                            assert all(
                                value.encode() in body for value in expected.values()
                            ), "Exporter lost hashes"
                            rendered_hashes.append(hashlib.sha256(body).hexdigest())
                        else:
                            result = json.loads(body)
                            assert len(result) == 1 and result[0]["error"] is None, (
                                "Solve returned an error"
                            )
                            actual = {
                                item["name"]: item["sha256"]
                                for item in result[0]["packages"]
                            }
                            assert actual == expected, (
                                "Solved dependency graph or hashes differ"
                            )
                            native = body if native is None else native
                            assert body == native, "Native result bytes differ"
                        location = headers.get("location", "")
                        assert re.fullmatch(r"/r/[0-9a-f]{64}", location), (
                            "Result was not published"
                        )
                        outputs.append((index, location, body))
                report["requests"] = timings
                report["native_output_sha256"] = hashlib.sha256(native).hexdigest()
                report["exporter_output_sha256"] = rendered_hashes
                stage = "native_evidence"
                evidence = []
                for identity in instances:
                    name = PREFIX + identity
                    assert (
                        command(
                            "docker", "inspect", "--format", "{{.Name}}", name
                        ).strip()
                        == "/" + name
                    )
                    count = int(
                        command(
                            "docker",
                            "exec",
                            name,
                            "/app/entrypoint.sh",
                            "python",
                            "-c",
                            "import os; from pathlib import Path; "
                            "from conda.base.context import context; "
                            "marker=os.environ['CONDA_PRESTO_CHANNELS']"
                            ".rsplit('/',1)[-1].encode(); "
                            "print(sum(marker in p.read_bytes() "
                            "for d in context.pkgs_dirs "
                            "for p in (Path(d)/'cache').glob('*.json')))",
                        )
                    )
                    logs = command("docker", "logs", name, merge_output=True)
                    requests = logs.count("/resolve")
                    assert count > 0, (
                        "Native container has no controlled metadata cache"
                    )
                    assert requests >= 2, (
                        "Native container has no solve request log evidence"
                    )
                    evidence.append(
                        {
                            "controlled_metadata_cache_files": count,
                            "solve_log_mentions": requests,
                        }
                    )
                report["native_evidence"] = evidence
                stage = "retained_reads_after_stop"
                command(
                    "docker",
                    "stop",
                    "--time",
                    "10",
                    *(PREFIX + value for value in instances),
                )
                retained_reads = 0
                for index, location, body in outputs:
                    for prefix in ("", f"/_instances/{1 - index}"):
                        for method in ("GET", "HEAD"):
                            status, headers, result = request(
                                endpoint + prefix + location, method=method
                            )
                            assert (
                                status == 200
                                and headers.get("x-presto-result-store") == "r2"
                            )
                            assert "x-presto-instance" not in headers
                            assert int(headers["content-length"]) == len(body)
                            assert result == (body if method == "GET" else b""), (
                                "Retained bytes changed"
                            )
                            retained_reads += 1
                for identity in instances:
                    assert not command(
                        "docker",
                        "ps",
                        "--format",
                        "{{.Names}}",
                        "--filter",
                        f"name=^/{PREFIX}{identity}$",
                    ).strip(), "Retained read restarted a container"
                report.update(
                    retained_reads=retained_reads,
                    retained_after_stop=True,
                    containers_restarted=False,
                    timing_interpretation="Observed local durations only",
                )
                stage = "concurrent_solve_after_stop"
                with ThreadPoolExecutor(max_workers=2) as callers:
                    resumed = list(
                        callers.map(
                            lambda _: request(
                                f"{endpoint}/_instances/0/resolve", payload
                            ),
                            range(2),
                        )
                    )
                for status, headers, body in resumed:
                    assert status == 200 and body == native, (
                        "Concurrent solve failed after container stop"
                    )
                    assert headers.get("x-presto-instance") == instances[0]
                    assert re.fullmatch(r"/r/[0-9a-f]{64}", headers.get("location", ""))
                report["concurrent_solve_after_restart"] = True
                print(json.dumps(report, indent=2))
        except Exception as error:
            log_text = log_path.read_text(errors="replace") if log_path.exists() else ""
            print(
                json.dumps(
                    {
                        "failed_stage": stage,
                        "error_type": type(error).__name__,
                        "check": str(error)
                        if isinstance(error, (AssertionError, RuntimeError))
                        else "request/runtime failure",
                        "wrangler_exit_code": process.poll() if process else None,
                        "log_signals": {
                            word: log_text.lower().count(word)
                            for word in (
                                "error",
                                "timeout",
                                "tls",
                                "certificate",
                                "ready",
                            )
                        },
                    },
                    indent=2,
                )
            )
            raise SystemExit(1) from None
        finally:
            if process is not None:
                # The preflight refuses other runs of this exact experiment.
                created = command(
                    "docker",
                    "ps",
                    "-a",
                    "--format",
                    "{{.Names}}",
                    "--filter",
                    f"name=^/{PREFIX}[0-9a-f]{{64}}(-proxy)?$",
                )
                owned.update(
                    name.removesuffix("-proxy") for name in created.splitlines()
                )
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            for name in sorted(owned):
                subprocess.run(
                    ["docker", "rm", "-f", name, name + "-proxy"],
                    capture_output=True,
                    timeout=30,
                )
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            json.dumps(
                {"failed_stage": "setup_or_cleanup", "error_type": type(error).__name__}
            )
        )
        raise SystemExit(1) from None
