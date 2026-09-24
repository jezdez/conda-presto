"""Run the repository's two composite Action scripts for the local demo."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ruamel.yaml import YAML

reader = YAML(typ="safe")
action = reader.load(Path("action.yml"))
steps = {step["id"]: step for step in action["runs"]["steps"]}
inputs = {
    "INPUT_ENDPOINT": sys.argv[1],
    "INPUT_FILE": "conda.toml",
    "INPUT_SPECS": "",
    "INPUT_CHANNELS": "",
    "INPUT_ENVIRONMENTS": "test",
    "INPUT_PLATFORMS": "linux-64,osx-arm64",
    "INPUT_FORMAT": "conda-workspaces-lock-v1",
    "INPUT_OUTPUT": "conda.lock",
}
remote_output = Path("remote-output.txt").resolve()
remote_output.write_text("")
subprocess.run(
    ["bash", "-c", steps["remote"]["run"]],
    env={**os.environ, **inputs, "GITHUB_OUTPUT": str(remote_output)},
    check=True,
)
remote = dict(line.split("=", 1) for line in remote_output.read_text().splitlines())
action_output = Path("action-output.txt").resolve()
action_output.write_text("")
subprocess.run(
    ["bash", "-c", steps["output"]["run"]],
    env={
        **os.environ,
        **inputs,
        "GITHUB_OUTPUT": str(action_output),
        "REMOTE_EXIT": remote["exit_code"],
        "REMOTE_TMP": remote["tmpout"],
    },
    check=True,
)
assert "solved=true" in action_output.read_text().splitlines()
lock = reader.load(Path("conda.lock"))
assert lock["version"] == 1
assert set(lock["environments"]) == {"test"}
packages = lock["environments"]["test"]["packages"]
assert set(packages) == {"linux-64", "osx-arm64"}
assert all(
    any(ref["conda"].rsplit("/", 1)[-1].startswith("zlib-") for ref in refs)
    for refs in packages.values()
)
print("solved=true")
print("Saved test environment for linux-64 and osx-arm64 in conda.lock")
