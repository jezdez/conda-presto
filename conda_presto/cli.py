"""CLI for conda-presto.

Exposes ``configure_parser`` and ``execute`` for the conda plugin hook
(``conda presto ...``), and ``main`` for standalone use via the
``conda-presto`` script entry point.

Default output is a pretty-printed JSON array of ``SolveResult``
objects (one entry per platform), produced directly by ``msgspec.json``
— byte-identical to what the HTTP API returns on ``/resolve``.
Pass ``--format <name>`` to route through conda's exporter plugins
(``explicit``, ``environment-yaml``, ``conda-lock-v1``,
``rattler-lock-v6``/``pixi-lock-v6``, …) instead.

Resolve is the default action.  Use ``--serve`` to start the HTTP API
server instead.  The ``--host`` and ``--port`` defaults can be set via
``CONDA_PRESTO_HOST`` and ``CONDA_PRESTO_PORT`` environment variables
(see :mod:`conda_presto.config`).

When no channels are provided via ``-c`` or environment files, the CLI
falls back to ``CONDA_PRESTO_CHANNELS`` (default: ``conda-forge``).
"""
from __future__ import annotations

import argparse
import sys

import msgspec
from conda.base.context import context
from conda.cli.helpers import (
    add_parser_channels,
    add_parser_networking,
    add_parser_solver,
)

from .config import DEFAULT_CHANNELS, DEFAULT_HOST, DEFAULT_PORT
from .exceptions import SAFE_ERROR_TYPES, UnknownFormatError
from .exporter import render_envs
from .resolve import solve, solve_environments


def configure_parser(parser: argparse.ArgumentParser):
    """Add resolve arguments and the ``--serve`` flag to *parser*.

    Used by both the conda plugin hook and the standalone ``main()``.
    """
    add_parser_channels(parser)
    add_parser_networking(parser)
    add_parser_solver(parser)

    from conda.base.constants import KNOWN_SUBDIRS

    parser.add_argument(
        "-p",
        "--platform",
        action="append",
        default=[],
        dest="platforms",
        choices=sorted(KNOWN_SUBDIRS),
        metavar="SUBDIR",
        help="Target platform (e.g. linux-64, osx-arm64). "
        "May be specified multiple times for parallel solves.",
    )

    parser.add_argument(
        "-f",
        "--file",
        action="append",
        default=[],
        dest="files",
        help="Read package specs from file (any format supported by "
        "conda's env spec plugins: environment.yml, requirements.txt, "
        "explicit lockfiles, etc.). May be specified multiple times.",
    )

    output_group = parser.add_argument_group("Output Format")
    output_group.add_argument(
        "--format",
        default=None,
        dest="output_format",
        metavar="FORMAT",
        help="Route output through a conda exporter plugin "
        "(e.g. explicit, environment-yaml, conda-lock-v1, "
        "rattler-lock-v6).  Omit for the default pretty-printed JSON "
        "output, which matches the HTTP API's response shape.",
    )
    server_group = parser.add_argument_group("HTTP Server")
    server_group.add_argument(
        "--serve",
        action="store_true",
        default=False,
        help="Start the HTTP API server instead of resolving.",
    )
    server_group.add_argument(
        "--host", default=DEFAULT_HOST, help="Server bind address."
    )
    server_group.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="Server port."
    )

    migrate_group = parser.add_argument_group("Migration")
    migrate_group.add_argument(
        "--migrate",
        action="store_true",
        default=False,
        help="Migrate pip spec files to conda equivalents. "
        "Requires -f to specify input file(s).",
    )
    migrate_group.add_argument(
        "--json",
        action="store_true",
        default=False,
        dest="json_output",
        help="Output machine-readable JSON.",
    )

    parser.add_argument(
        "specs", nargs="*", help="Inline package specs"
    )


def execute(args: argparse.Namespace):
    """Dispatch based on mode flags."""
    if args.serve:
        cmd_serve(args)
    elif args.migrate:
        cmd_migrate(args)
    else:
        cmd_solve(args)


def load_files(
    files: list[str],
) -> tuple[list[str], list[str]]:
    """Parse input files via conda's env-spec plugin registry.

    Returns accumulated *(dependencies, channels)* from all files.
    Each file is routed through ``detect_environment_specifier`` and
    parsed into a conda ``Environment`` model, so any installed
    env-spec plugin (environment.yml, requirements.txt,
    ``pixi.lock`` via conda-lockfiles, …) works automatically.
    """
    deps: list[str] = []
    channels: list[str] = []
    for fpath in files:
        spec_plugin = context.plugin_manager.detect_environment_specifier(fpath)
        spec = spec_plugin.environment_spec(filename=fpath)
        if not spec.can_handle():
            print(
                f"No environment spec plugin can handle: {fpath}",
                file=sys.stderr,
            )
            raise SystemExit(1)
        env = spec.env
        deps.extend(str(s) for s in env.requested_packages)
        if env.config and env.config.channels:
            channels.extend(env.config.channels)
    return deps, channels


def cmd_solve(args: argparse.Namespace):
    """Resolve packages and write output to stdout."""
    context.__init__(argparse_args=args)

    file_deps, file_channels = load_files(args.files)

    specs = [s.strip("\"'") for s in args.specs]
    deps = file_deps + specs

    if not deps:
        print(
            "Provide an environment file (--file) or package specs.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    channels = list(context.channels)
    if not channels or channels == ["defaults"]:
        channels = file_channels or list(DEFAULT_CHANNELS)

    platforms = args.platforms or None

    if args.output_format is None:
        results = solve(channels, deps, platforms)
        body = msgspec.json.format(msgspec.json.encode(results), indent=2)
        sys.stdout.buffer.write(body + b"\n")
    else:
        try:
            envs = solve_environments(channels, deps, platforms)
            body, _ = render_envs(envs, args.output_format)
        except UnknownFormatError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1)
        except SAFE_ERROR_TYPES as exc:
            print(f"Solver error: {exc}", file=sys.stderr)
            raise SystemExit(1)
        sys.stdout.write(body.rstrip() + "\n")


def cmd_serve(args: argparse.Namespace):
    """Start the HTTP API server via uvicorn.

    Workaround: ``uvicorn`` is an optional dependency (pixi
    ``server`` feature only).  Importing it at module top would
    break ``conda presto`` as a CLI when the server deps aren't
    installed, so it is imported here only when actually needed.
    """
    import uvicorn

    uvicorn.run(
        "conda_presto.app:app", host=args.host, port=args.port
    )


def cmd_migrate(args: argparse.Namespace):
    """Migrate pip spec files to conda equivalents.

    Default output: environment.yml to stdout (pipeable to file).
    Summary and per-package details to stderr.
    --json: structured MigrationResult array to stdout.
    """
    from .migrate import migrate
    from .migrate.models import MigrationResult
    from .migrate.name_mapping import cf_mapping_available
    from .resolve import NATIVE_SUBDIR

    if not args.files:
        print(
            "Usage: conda presto --migrate -f <spec_file> "
            "[-p platform] [-c channel] [--json]",
            file=sys.stderr,
        )
        raise SystemExit(1)

    context.__init__(argparse_args=args)

    channels = list(context.channels)
    if not channels or channels == ["defaults"]:
        channels = list(DEFAULT_CHANNELS)

    platform = args.platforms[0] if args.platforms else NATIVE_SUBDIR

    results: list[MigrationResult] = []
    for fpath in args.files:
        try:
            content = open(fpath).read()
        except OSError as exc:
            print(f"Cannot read {fpath}: {exc}", file=sys.stderr)
            raise SystemExit(1)

        result = migrate(
            spec_content=content,
            channels=channels,
            platform=platform,
        )
        results.append(result)

    if not cf_mapping_available():
        print(
            "Warning: cf-graph name mapping unavailable. "
            "Some packages may not match their conda equivalent.",
            file=sys.stderr,
        )

    if args.json_output:
        body = msgspec.json.format(msgspec.json.encode(results), indent=2)
        sys.stdout.buffer.write(body + b"\n")
    else:
        for i, result in enumerate(results):
            if i > 0:
                sys.stdout.write("---\n")
            sys.stdout.write(result.environment_yml)
        _print_summary(results, sys.stderr)


def _print_summary(results: list, file) -> None:
    """Print migration summary with per-package details to stderr."""
    for result in results:
        available = [p for p in result.packages if p.status == "available"]
        unavailable = [p for p in result.packages if p.status != "available"]
        total = len(result.packages)
        if not total:
            continue

        coverage = len(available) / total * 100
        print(
            f"\n# {result.platform} | {result.source_format} | "
            f"{len(available)}/{total} conda ({coverage:.0f}%)",
            file=file,
        )

        if unavailable:
            print("# pip fallback:", file=file)
            for pkg in unavailable:
                detail = ""
                if pkg.reason == "version_not_available":
                    versions = ", ".join(pkg.available_versions or [])
                    detail = f" (available: {versions})" if versions else ""
                elif pkg.reason == "wrong_arch":
                    detail = f" (not built for {result.platform})"
                elif pkg.reason == "not_in_conda":
                    detail = " (pip-only)"
                print(f"#   {pkg.pip_name}{detail}", file=file)

        if not result.solve_success and result.solve_error:
            print(
                f"# solve warning: {result.solve_error.splitlines()[0]}",
                file=file,
            )


def main():
    """Standalone entry point for the ``conda-presto`` script."""
    parser = argparse.ArgumentParser(
        description="Resolve conda environments to fully pinned "
        "packages with SHA256 hashes.",
    )
    configure_parser(parser)
    args = parser.parse_args()
    execute(args)


if __name__ == "__main__":
    main()
