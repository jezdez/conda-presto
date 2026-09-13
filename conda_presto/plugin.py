"""Conda plugin registration for conda-presto.

This module is imported on every ``conda`` invocation via the entry
point system.  See :func:`conda_subcommands` for why the CLI module
is imported lazily inside the hook rather than at module top.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from conda.plugins import hookimpl
from conda.plugins.types import CondaSubcommand

if TYPE_CHECKING:
    from collections.abc import Iterable


@hookimpl
def conda_subcommands() -> Iterable[CondaSubcommand]:
    """Register ``conda presto`` as a conda subcommand.

    Workaround: importing ``.cli`` eagerly at module top costs
    ~170 ms (msgspec + conda_rattler_solver + conda.cli.helpers).
    Conda loads every registered plugin on every invocation —
    including ``conda --version`` and ``conda activate`` — so we
    defer the cost to the one hook call where the CLI is actually
    needed.
    """
    from .cli import configure_parser, execute

    yield CondaSubcommand(
        name="presto",
        summary=(
            "Dry-run solver: resolve specs or environment.yml"
            " to pinned packages, or serve as an HTTP API."
        ),
        action=execute,
        configure_parser=configure_parser,
    )
