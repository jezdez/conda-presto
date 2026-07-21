"""Tests for Docker image definitions."""

from __future__ import annotations

from pathlib import Path
from tomllib import loads

ROOT = Path(__file__).resolve().parents[1]


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
