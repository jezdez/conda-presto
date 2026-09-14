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

Resolve is the default action. Use ``--parse`` to inspect an input file,
``--export`` to render declarations or saved records, ``--check-lock`` to compare
a workspace manifest with its saved lock, or ``--serve`` to start the HTTP API.
The ``--host`` and ``--port`` defaults use
``CONDA_PRESTO_HOST`` and ``CONDA_PRESTO_PORT`` environment variables
(see :mod:`conda_presto.config`).

When no channels are provided via ``-c`` or environment files, the CLI
falls back to ``CONDA_PRESTO_CHANNELS`` (default: ``conda-forge``).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import msgspec
from conda.base.context import context
from conda.cli.helpers import (
    add_parser_channels,
    add_parser_networking,
    add_parser_solver,
)
from conda.exceptions import CondaError

from .config import DEFAULT_CHANNELS, DEFAULT_HOST, DEFAULT_PORT, PARSE_TIMEOUT_S
from .exceptions import (
    SAFE_ERROR_TYPES,
    UnknownFormatError,
    WorkspaceSolveError,
    redact_safe_error,
    safe_error_message,
)
from .exporter import OutputFormat
from .inputs import ParsedInputFile
from .resolve import solve, solve_environments


def configure_parser(parser: argparse.ArgumentParser):
    """Add solve, parse, export, lock check, and server arguments to *parser*.

    Used by both the conda plugin hook and the standalone ``main()``.
    """
    add_parser_channels(parser)
    add_parser_networking(parser)
    add_parser_solver(parser)

    parser.add_argument(
        "-p",
        "--platform",
        action="append",
        default=[],
        dest="platforms",
        metavar="PLATFORM",
        help="Target platform (e.g. linux-64, osx-arm64), or a declared "
        "workspace target. May be specified multiple times.",
    )

    parser.add_argument(
        "-e",
        "--environment",
        action="append",
        default=[],
        dest="environments",
        metavar="NAME",
        help="Select a workspace environment to inspect, export or solve. "
        "May be specified multiple times.",
    )

    parser.add_argument(
        "-f",
        "--file",
        action="append",
        default=[],
        dest="files",
        help="Read package specs from a supported environment or lockfile "
        "through conda's env spec plugins. Explicit package-list files are "
        "output-only. May be specified multiple times.",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        metavar="PATH",
        help="Match a workspace manifest to the saved lock. "
        "Requires --export or --check-lock.",
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
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--parse",
        action="store_true",
        default=False,
        help="Inspect one input file without solving and write JSON.",
    )
    mode_group.add_argument(
        "--export",
        action="store_true",
        default=False,
        help="Export one input file without solving. Requires --file and --format.",
    )
    mode_group.add_argument(
        "--check-lock",
        action="store_true",
        default=False,
        help="Check all manifest targets against a saved workspace lock without "
        "solving. Requires --file and --manifest.",
    )
    mode_group.add_argument(
        "--serve",
        action="store_true",
        default=False,
        help="Start the HTTP API server instead of resolving.",
    )
    server_group = parser.add_argument_group("HTTP Server")
    server_group.add_argument(
        "--host", default=DEFAULT_HOST, help="Server bind address."
    )
    server_group.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="Server port."
    )

    parser.add_argument("specs", nargs="*", help="Inline package specs")


def execute(args: argparse.Namespace):
    """Dispatch the requested CLI operation."""
    if getattr(args, "manifest", None) is not None and not (
        getattr(args, "export", False) or getattr(args, "check_lock", False)
    ):
        print("--manifest requires --export or --check-lock.", file=sys.stderr)
        raise SystemExit(1)
    if getattr(args, "environments", None) and getattr(args, "serve", False):
        print("Environment selection requires a workspace manifest.", file=sys.stderr)
        raise SystemExit(1)
    if (
        getattr(args, "parse", False)
        or getattr(args, "export", False)
        or getattr(args, "check_lock", False)
    ):
        cmd_parse(args)
    elif args.serve:
        cmd_serve(args)
    else:
        cmd_solve(args)


def cmd_parse(args: argparse.Namespace):
    """Inspect, export or check an input through the bounded content parser."""
    export = getattr(args, "export", False)
    check_lock = getattr(args, "check_lock", False)
    mode = "--check-lock" if check_lock else "--export" if export else "--parse"
    error_status = 2 if check_lock else 1
    error = None
    if len(args.files) != 1:
        error = f"{mode} requires exactly one --file."
    elif args.specs:
        error = f"{mode} does not accept inline package specs."
    elif (
        getattr(args, "channel", None)
        or getattr(args, "override_channels", False)
        or getattr(args, "use_local", False) is True
    ):
        error = f"{mode} does not accept channel overrides."
    elif export and args.output_format is None:
        error = "--export requires --format."
    elif not export and args.output_format is not None:
        error = f"{mode} does not accept --format."
    elif check_lock and not getattr(args, "manifest", None):
        error = "--check-lock requires --manifest."
    elif check_lock and args.platforms:
        error = "--check-lock checks every target and does not accept --platform."
    elif check_lock and getattr(args, "environments", None):
        error = (
            "--check-lock checks every environment and does not accept --environment."
        )
    if error:
        print(error, file=sys.stderr)
        raise SystemExit(error_status)

    path = Path(args.files[0])
    try:
        output_format = OutputFormat.named(args.output_format) if export else None
        content = path.read_text(encoding="utf-8")
        manifest_args = {}
        if getattr(args, "manifest", None) is not None:
            manifest_path = Path(args.manifest)
            manifest_args = {
                "manifest_content": manifest_path.read_text(encoding="utf-8"),
                "manifest_filename": manifest_path.name,
            }
        if check_lock:
            manifest_args["check_lock"] = True
        parsed = ParsedInputFile.from_content_until(
            content,
            path.name,
            args.platforms or None,
            time.monotonic() + PARSE_TIMEOUT_S,
            export_format=output_format.exporter.name if output_format else None,
            target_environments=getattr(args, "environments", None) or None,
            **manifest_args,
        )
        if check_lock:
            if parsed.lock_check is None:
                raise ValueError(
                    "This input cannot be checked against a workspace manifest"
                )
        elif export:
            if parsed.exported_content is None:
                raise ValueError(
                    "This input cannot be exported with this format and selection"
                )
        elif (
            parsed.workspace is None
            and parsed.workspace_lock is None
            and args.platforms
        ):
            raise ValueError(
                "Platform selection requires a workspace manifest or workspace lockfile"
            )
    except TimeoutError:
        error = f"Parse exceeded {PARSE_TIMEOUT_S}s timeout"
    except UnicodeError:
        error = "Input file is not valid UTF-8"
    except OSError:
        error = "Cannot read input file"
    except (CondaError, ValueError) as exc:
        error = redact_safe_error(str(exc))
    except RuntimeError:
        error = "Input parser failed"
    else:
        if export:
            sys.stdout.buffer.write(parsed.exported_content.encode("utf-8"))
        else:
            body = msgspec.json.format(
                msgspec.json.encode(
                    parsed.lock_check if check_lock else parsed.parse_result
                ),
                indent=2,
            )
            sys.stdout.buffer.write(body + b"\n")
            if check_lock and not parsed.lock_check.consistent:
                raise SystemExit(1)
        return
    print(f"Input error: {error}", file=sys.stderr)
    raise SystemExit(error_status)


def load_parsed_files(
    files: list[str],
    target_platforms: list[str] | None = None,
    *,
    target_environments: list[str] | None = None,
) -> tuple[list[str], list[str], list[ParsedInputFile]]:
    """Parse input files via conda's env-spec plugin registry.

    Returns accumulated *(dependencies, channels, parsed_files)*.
    Each file is routed through ``detect_environment_specifier`` and
    parsed into a conda ``Environment`` model, so any installed
    env-spec plugin (environment.yml, requirements.txt,
    ``pixi.lock`` via conda-lockfiles, …) works automatically.
    """
    deps: list[str] = []
    channels: list[str] = []
    parsed_files: list[ParsedInputFile] = []
    for fpath in files:
        try:
            parsed = ParsedInputFile.from_path(
                fpath,
                target_platforms,
                target_environments=target_environments,
            )
        except (CondaError, ValueError) as exc:
            print(f"Input error: {redact_safe_error(str(exc))}", file=sys.stderr)
            raise SystemExit(1) from exc
        parsed_files.append(parsed)
        deps.extend(parsed.specs)
        channels.extend(parsed.channels)
    return deps, channels, parsed_files


def transcode_envs(
    parsed_files: list[ParsedInputFile],
    output_format: OutputFormat,
    specs: list[str],
    has_channel_override: bool,
) -> tuple | None:
    """Return parsed lockfile environments when a no-solve path is safe."""
    if len(parsed_files) != 1 or specs or has_channel_override:
        return None
    parsed = parsed_files[0]
    if parsed.is_lockfile and parsed.environments and output_format.is_lockfile:
        return parsed.environments
    return None


def cmd_solve(args: argparse.Namespace):
    """Resolve packages and write output to stdout."""
    context.__init__(argparse_args=args)

    platforms = args.platforms or None
    environments = getattr(args, "environments", None) or None
    file_deps, file_channels, parsed_files = load_parsed_files(
        args.files, platforms, target_environments=environments
    )
    if any(parsed.workspace_lock is not None for parsed in parsed_files):
        print(
            "Workspace lockfiles contain solved records. Use --parse to inspect "
            "or --export with --format to extract them.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    workspaces = [
        parsed.workspace for parsed in parsed_files if parsed.workspace is not None
    ]
    if workspaces:
        error = None
        if len(parsed_files) != 1:
            error = "Workspace solving requires exactly one --file."
        elif args.specs:
            error = "Workspace solving does not accept inline package specs."
        elif (
            getattr(args, "channel", None)
            or getattr(args, "override_channels", False)
            or getattr(args, "use_local", False) is True
        ):
            error = "Workspace solving does not accept channel overrides."
        if error:
            print(error, file=sys.stderr)
            raise SystemExit(1)
        try:
            workspace = workspaces[0].select(
                environments=environments, platforms=platforms
            )
            workspace.validate_output(args.output_format)
            result = workspace.solve(args.output_format)
        except (CondaError, ValueError, WorkspaceSolveError) as exc:
            print(f"Workspace error: {redact_safe_error(str(exc))}", file=sys.stderr)
            raise SystemExit(1) from exc
        if isinstance(result, tuple):
            sys.stdout.write(result[0].rstrip() + "\n")
        else:
            body = msgspec.json.format(msgspec.json.encode(result), indent=2)
            sys.stdout.buffer.write(body + b"\n")
        return
    if environments:
        print("Environment selection requires a workspace manifest.", file=sys.stderr)
        raise SystemExit(1)

    specs = [s.strip("\"'") for s in args.specs]
    deps = file_deps + specs

    context_channels = list(context.channels)
    has_channel_override = bool(context_channels and context_channels != ["defaults"])
    channels = context_channels
    if not channels or channels == ["defaults"]:
        channels = file_channels or list(DEFAULT_CHANNELS)

    if args.output_format is None:
        if not deps:
            print(
                "Provide an environment file (--file) or package specs.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        results = solve(channels, deps, platforms)
        body = msgspec.json.format(msgspec.json.encode(results), indent=2)
        sys.stdout.buffer.write(body + b"\n")
    else:
        try:
            output_format = OutputFormat.named(args.output_format)
            envs = transcode_envs(
                parsed_files,
                output_format,
                specs,
                has_channel_override,
            )
            if envs is None:
                if not deps:
                    print(
                        "Provide an environment file (--file) or package specs.",
                        file=sys.stderr,
                    )
                    raise SystemExit(1)
                envs = solve_environments(channels, deps, platforms)
            body, _ = output_format.render(envs)
        except UnknownFormatError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1)
        except SAFE_ERROR_TYPES as exc:
            print(f"Solver error: {safe_error_message(exc)}", file=sys.stderr)
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
        "conda_presto.app:app",
        host=args.host,
        port=args.port,
        access_log=False,
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
