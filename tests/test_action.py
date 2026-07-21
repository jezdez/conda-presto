"""Tests for the GitHub composite action metadata."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION_YML = ROOT / "action.yml"


def action_text() -> str:
    return ACTION_YML.read_text()


def test_action_yaml_loads():
    data = yaml.safe_load(action_text())

    assert data["name"] == "conda-presto"
    assert data["runs"]["using"] == "composite"
    assert "command" not in data["inputs"]


def test_action_runs_from_checked_out_action_path():
    text = action_text()

    assert 'pixi run --manifest-path "${GITHUB_ACTION_PATH}/pyproject.toml"' in text
    assert "pixi global install" not in text
    assert "--git https://github.com/jezdez/conda-presto.git" not in text


def test_action_logs_only_request_metadata():
    text = action_text()

    assert "args: ${args[*]}" not in text
    assert 'echo "URL:' not in text
    assert 'echo "Body:' not in text
    assert "body_fields:" in text


def test_action_uses_dynamic_github_output_delimiter():
    text = action_text()

    assert "CONDA_PRESTO_EOF" not in text
    assert "make_delimiter()" in text
