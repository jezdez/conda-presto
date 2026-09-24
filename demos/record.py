"""Record the executable documentation examples with VHS."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = (
    "cli",
    "workspace",
    "locks",
    "http",
    "http-workspace",
    "http-update",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", help=f"Default: {', '.join(NAMES)}")
    args = parser.parse_args()
    names = args.names or NAMES
    for name in names:
        if name not in NAMES:
            parser.error(f"Unknown demo: {name}")
    output = ROOT / "demos"
    output.mkdir(parents=True, exist_ok=True)
    for name in names:
        print(f"Recording {name}", flush=True)
        status = output / f"{name}.status"
        status.unlink(missing_ok=True)
        media = [output / f"{name}.{suffix}" for suffix in ("gif", "png", "txt")]
        for path in media:
            path.unlink(missing_ok=True)
        try:
            example = subprocess.run(
                ["bash", f"demos/{name}.sh"],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=600,
            )
            transcript = example.stdout.replace(
                sys.prefix, "<demo environment>"
            ).replace(str(ROOT), "<checkout>")
            (output / f"{name}.txt").write_text(
                "\n".join(line.rstrip() for line in transcript.splitlines()) + "\n"
            )
            example.check_returncode()
            subprocess.run(
                ["vhs", f"demos/{name}.tape"],
                cwd=ROOT,
                check=True,
                timeout=600,
            )
            if not status.exists() or status.read_text().strip() != "0":
                raise SystemExit(
                    f"The {name} tape did not complete. Check the VHS output above."
                )
            if any(not path.exists() or path.stat().st_size == 0 for path in media):
                raise SystemExit(f"VHS did not produce all media for {name}")
        finally:
            status.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
