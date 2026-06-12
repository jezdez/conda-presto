"""Tests for Docker image definitions."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_server_dockerfile_has_healthcheck():
    text = (ROOT / "docker" / "server.Dockerfile").read_text()

    assert "HEALTHCHECK" in text
    assert "/health" in text
    assert "/app/.pixi/envs/prod/bin/python" in text


def test_cli_dockerfile_has_no_healthcheck():
    text = (ROOT / "docker" / "cli.Dockerfile").read_text()

    assert "HEALTHCHECK" not in text
