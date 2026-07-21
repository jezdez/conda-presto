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

    assert 'bash "$GITHUB_ACTION_PATH/.github/scripts/install-pixi"' in text
    assert "prefix-dev/setup-pixi@" not in text
    assert 'pixi run --manifest-path "${GITHUB_ACTION_PATH}/pyproject.toml"' in text


def test_action_logs_only_request_metadata():
    text = action_text()

    assert "args: ${args[*]}" not in text
    assert 'echo "URL:' not in text
    assert 'echo "Body:' not in text
    assert "body_fields:" in text


def test_action_uses_dynamic_github_output_delimiter():
    text = action_text()

    assert "make_delimiter()" in text


def test_action_does_not_print_response_bodies():
    text = action_text()

    assert "print_response" not in text
    assert "response truncated" not in text
    assert "::stop-commands::" not in text
    assert "omit for stdout" not in text


def test_action_bounds_remote_requests():
    text = action_text()

    assert "curl --disable --silent" in text
    assert "--proto '=http,https'" in text
    assert "--noproxy '127.0.0.1,localhost,::1'" in text
    assert "--connect-timeout 10" in text
    assert "--max-time 300" in text
    assert "--max-filesize 104857600" in text
    assert "'$format | @uri'" in text
    assert "if http_code=$(" in text
    assert 'echo "tmpout=${tmpout}" >> "$GITHUB_OUTPUT"' in text
    assert '"${http_code}" -lt 200 || "${http_code}" -ge 300' in text


def test_action_streams_request_body_and_restricts_plain_http():
    text = action_text()

    assert "printf '%s' \"${body}\"" in text
    assert "--data-binary @-" in text
    assert '--data "${body}"' not in text
    assert '--rawfile file "${INPUT_FILE}"' in text
    assert text.count('$value | split(",")') == 3
    assert "^http://(127[.]0[.]0[.]1|localhost)" in text
    assert "^http://\\[::1\\]" in text
    assert "must use HTTPS unless it is a loopback URL" in text


def test_action_validates_native_responses_and_bounds_result_output():
    text = action_text()

    assert 'if type == "array"' in text
    assert "returned an invalid native response" in text
    assert "result_size=${#response_body}" in text
    assert "result=(output too large)" in text
