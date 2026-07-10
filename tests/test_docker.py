"""Tests for Docker image definitions."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_has_server_healthcheck():
    text = (ROOT / "docker" / "Dockerfile").read_text()

    assert "FROM production AS server" in text
    assert "HEALTHCHECK" in text
    assert "/health" in text
    assert "/app/.pixi/envs/prod/bin/python" in text


def test_dockerfile_has_cli_target():
    text = (ROOT / "docker" / "Dockerfile").read_text()

    assert "FROM production AS cli" in text
    assert "PIXI_ENV" in text
