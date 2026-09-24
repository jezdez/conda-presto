"""Record the executable documentation examples with VHS."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAMES = (
    "cli",
    "workspace",
    "locks",
    "http",
    "cache",
    "trust",
    "action",
    "docker",
    "operations",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", help=f"Default: {', '.join(NAMES)}")
    args = parser.parse_args()
    names = args.names or NAMES
    for name in names:
        if name not in NAMES:
            parser.error(f"Unknown demo: {name}")
    output = ROOT / "docs" / "_static" / "demos"
    output.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "DEMO_PAUSE": "0.8", "DEMO_DOCKER_BUILT": "1"}
    if "docker" in names:
        print("Building the Docker demo image", flush=True)
        subprocess.run(
            ["docker", "build", "--tag", "conda-presto:docs-demo", "."],
            cwd=ROOT,
            check=True,
        )
    for name in names:
        print(f"Recording {name}", flush=True)
        status = output / f"{name}.status"
        status.unlink(missing_ok=True)
        media = [output / f"{name}.{suffix}" for suffix in ("mp4", "png", "txt")]
        for path in media:
            path.unlink(missing_ok=True)
        try:
            subprocess.run(
                ["vhs", "--quiet", f"docs/demos/tapes/{name}.tape"],
                cwd=ROOT,
                env=env,
                check=True,
                timeout=600,
            )
            if not status.exists() or status.read_text().strip() != "0":
                raise SystemExit(
                    f"The {name} demo failed. Read {output / f'{name}.txt'}"
                )
            if any(not path.exists() or path.stat().st_size == 0 for path in media):
                raise SystemExit(f"VHS did not produce all media for {name}")
        finally:
            status.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
