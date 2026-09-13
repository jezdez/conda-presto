"""Tests for Docker image definitions."""

from __future__ import annotations

import re
from pathlib import Path
from tomllib import loads

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_pins_images_and_locks_runtime_files():
    text = (ROOT / "Dockerfile").read_text()

    for image in (
        r"ghcr\.io/prefix-dev/pixi:\d+\.\d+\.\d+",
        "debian:bookworm-slim",
    ):
        assert re.search(
            rf"^FROM {image}@sha256:[0-9a-f]{{64}}(?: AS \w+)?$", text, re.MULTILINE
        )
    assert "COPY pyproject.toml pixi.lock README.md ./" in text
    assert "find / -xdev -type f -perm /6000 -exec chmod a-s {} +" in text
    assert "chmod -R a-w /app/conda_presto" in text
    assert "http.client.HTTPConnection('127.0.0.1'" in text
    assert "sys.exit(c.getresponse().status != 200)" in text


def test_dockerfile_has_server_healthcheck():
    text = (ROOT / "Dockerfile").read_text()

    assert "HEALTHCHECK" in text
    assert "/health" in text
    assert "CMD /app/entrypoint.sh python -c" in text
    assert "CONDA_NO_LOCK=false" in text
    assert (
        'ENTRYPOINT ["/app/entrypoint.sh", "env", "CONDA_NO_LOCK=false", '
        '"conda", "presto"]'
    ) in text
    assert "CONDA_PRESTO_CONCURRENCY=1" in text
    assert "CONDA_PRESTO_PERSISTENT_WORKER=1" in text
    assert "--start-period=120s" in text
    assert "os.environ.get('CONDA_PRESTO_PORT', '8000')" in text
    assert "--only-upgrade --no-install-recommends -y libpcre2-8-0" in text


def test_server_environment_has_redis_client():
    config = loads((ROOT / "pyproject.toml").read_text())
    dependencies = config["tool"]["pixi"]["feature"]["server"]["dependencies"]

    assert "redis-py" in dependencies
