"""Keep machine-specific runtime paths out of recorded terminal output."""

from __future__ import annotations

import sys
from pathlib import Path

checkout = str(Path(__file__).resolve().parents[1])
for line in sys.stdin:
    print(
        line.replace(sys.prefix, "<demo environment>").replace(checkout, "<checkout>"),
        end="",
        flush=True,
    )
