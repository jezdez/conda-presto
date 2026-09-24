"""Check that a selective update keeps every unselected package reference."""

from __future__ import annotations

import sys
from pathlib import Path

from conda_workspaces.lockfile import load_lockfile_data

before, after = (load_lockfile_data(Path(path).read_text()) for path in sys.argv[1:])
assert set(after["environments"]) == set(before["environments"])
unchanged = []
for environment, data in before["environments"].items():
    assert set(after["environments"][environment]["packages"]) == set(data["packages"])
    for target, references in data["packages"].items():
        if (environment, target) != ("tools", "cpu"):
            assert after["environments"][environment]["packages"][target] == references
            unchanged.append(f"{environment}/{target}")
assert len(unchanged) == 3
print(f"Unchanged package references: {', '.join(unchanged)}")
