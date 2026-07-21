"""Tests for Docker image definitions."""

from __future__ import annotations

from pathlib import Path
from tomllib import loads

import pytest

ROOT = Path(__file__).resolve().parents[1]

PIXI_IMAGE = (
    "ghcr.io/prefix-dev/pixi:0.70.1@sha256:"
    "2537738f8b7e2c7a7f070f56928ab959c4559a8d7e04f71eb16b0f779f0588f6"
)
DEBIAN_IMAGE = (
    "debian:bookworm-slim@sha256:"
    "7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818"
)


@pytest.mark.parametrize("path", ["Dockerfile", "docker/Dockerfile"])
def test_dockerfile_pins_images_and_locks_runtime_files(path):
    text = (ROOT / path).read_text()

    assert f"FROM {PIXI_IMAGE}" in text
    assert f"FROM {DEBIAN_IMAGE}" in text
    assert "COPY pyproject.toml pixi.lock README.md ./" in text
    assert "find / -xdev -type f -perm /6000 -exec chmod a-s {} +" in text
    assert "chmod -R a-w /app/conda_presto" in text
    assert "http.client.HTTPConnection('127.0.0.1'" in text
    assert "sys.exit(c.getresponse().status != 200)" in text


def test_dockerfile_has_server_healthcheck():
    text = (ROOT / "docker" / "Dockerfile").read_text()
    server_stage = text.split("FROM production AS server", 1)[1].split(
        "FROM production AS cli", 1
    )[0]

    assert "COPY pyproject.toml pixi.lock README.md ./" in text
    assert "FROM production AS server" in text
    assert "HEALTHCHECK" in server_stage
    assert "/health" in server_stage
    assert "/app/.pixi/envs/prod/bin/python" in server_stage
    assert "CONDA_NO_LOCK=false" in server_stage
    assert (
        'ENTRYPOINT ["/app/entrypoint.sh", "env", "CONDA_NO_LOCK=false", '
        '"conda", "presto"]'
    ) in server_stage
    assert "CONDA_PRESTO_CONCURRENCY=1" in server_stage
    assert "CONDA_PRESTO_PERSISTENT_WORKER=1" in server_stage
    assert "--start-period=120s" in server_stage


def test_dockerfile_has_cli_target():
    text = (ROOT / "docker" / "Dockerfile").read_text()

    assert "FROM production AS cli" in text
    assert "PIXI_ENV" in text


def test_server_environment_has_redis_client():
    config = loads((ROOT / "pyproject.toml").read_text())
    dependencies = config["tool"]["pixi"]["feature"]["server"]["dependencies"]

    assert "redis-py" in dependencies
