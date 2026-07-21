"""Input-file parsing helpers shared by the CLI and HTTP API."""

from __future__ import annotations

import multiprocessing
import os
import tempfile
import time
import tomllib
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

from conda.base.context import context
from conda.exceptions import CondaError
from conda.models.environment import Environment
from conda.plugins.types import EnvironmentFormat
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.events import AliasEvent, NodeEvent

from .exceptions import CredentialRedactionFilter, redact_safe_error

ALLOWED_EXTENSIONS = {".yml", ".yaml", ".txt", ".lock", ".toml", ".json"}
HTTP_INPUT_MAX_NODES = 10_000


@dataclass(frozen=True)
class ParsedInputFile:
    """Parsed conda input file with optional lockfile environments."""

    specs: list[str]
    channels: list[str]
    environment_format: EnvironmentFormat
    source_format: str
    available_platforms: tuple[str, ...] = ()
    environments: tuple[Environment, ...] = ()

    @property
    def is_lockfile(self) -> bool:
        return self.environment_format == EnvironmentFormat.lockfile

    @classmethod
    def from_path(
        cls,
        path: str | os.PathLike[str],
        target_platforms: list[str] | tuple[str, ...] | None = None,
        *,
        materialize_lockfiles: bool = True,
    ) -> ParsedInputFile:
        """Parse an input file through conda's plugin registry.

        ``target_platforms`` is only used for lockfiles. When every target
        platform is present and ``materialize_lockfiles`` is true,
        ``environments`` contains the corresponding parsed ``Environment``
        objects. When materialization is disabled or a target is missing,
        parsing still succeeds but ``environments`` is empty so callers can
        decide whether to fall back to solving or fail a no-solve request.
        """
        path_str = str(path)
        specifier = context.plugin_manager.detect_environment_specifier(path_str)
        spec = specifier.environment_spec(path_str)
        if not spec.can_handle():
            raise ValueError(f"No conda environment spec plugin can handle: {path_str}")

        environment_format = specifier.environment_format
        if environment_format == EnvironmentFormat.lockfile:
            available = tuple(getattr(spec, "available_platforms", ()) or ())
            targets = tuple(target_platforms or ())
            envs: tuple[Environment, ...] = ()
            if (
                materialize_lockfiles
                and targets
                and available
                and set(targets).issubset(available)
            ):
                envs = tuple(spec.env_for(platform) for platform in targets)
            return cls(
                specs=[str(spec) for env in envs for spec in env.requested_packages],
                channels=list(
                    dict.fromkeys(
                        channel
                        for env in envs
                        if env.config and env.config.channels
                        for channel in env.config.channels
                    )
                ),
                environment_format=environment_format,
                source_format=specifier.name,
                available_platforms=available,
                environments=envs,
            )

        env = spec.env
        channels: list[str] = []
        if env.config and env.config.channels:
            channels.extend(env.config.channels)
        return cls(
            specs=[str(spec) for spec in env.requested_packages],
            channels=channels,
            environment_format=environment_format,
            source_format=specifier.name,
            environments=(env,),
        )

    @classmethod
    def from_content_until(
        cls,
        content: str,
        filename: str | None,
        target_platforms: list[str] | tuple[str, ...] | None,
        deadline: float,
    ) -> ParsedInputFile:
        """Parse content in an isolated process before an absolute deadline."""
        if deadline <= time.monotonic():
            raise TimeoutError
        with tempfile.TemporaryDirectory() as tmpdir:
            filename = os.path.basename(filename or "environment.yml")
            if len(filename.encode()) > 240:
                raise ValueError("Input filename is too long")
            if not filename.isprintable():
                raise ValueError("Input filename contains unsupported characters")
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise ValueError(
                    f"Unsupported file extension '{ext}', "
                    f"allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
                )
            for line in content.splitlines():
                stripped = line.strip().lstrip("\ufeff")
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped == "@EXPLICIT":
                    raise ValueError(
                        "Explicit package URL lockfiles are not accepted "
                        "by the HTTP parser"
                    )
                break
            path = Path(tmpdir) / filename
            path.write_text(content, encoding="utf-8")
            process_context = multiprocessing.get_context("spawn")
            receiver, sender = process_context.Pipe(duplex=False)
            process = None
            try:
                process = process_context.Process(
                    target=cls._from_path_process,
                    args=(sender, path, target_platforms, deadline),
                )
                process.start()
            except BaseException:
                receiver.close()
                sender.close()
                if process is not None and process.is_alive():
                    process.terminate()
                    process.join(5)
                raise
            sender.close()

            try:
                if not receiver.poll(max(0.0, deadline - time.monotonic())):
                    raise TimeoutError
                status, payload = receiver.recv()
            except EOFError as exc:
                raise RuntimeError(
                    f"Input parser exited with code {process.exitcode}"
                ) from exc
            finally:
                receiver.close()
                if process.is_alive():
                    process.terminate()
                    process.join(max(0.0, min(5.0, deadline - time.monotonic())))
                if process.is_alive():
                    process.kill()
                process.join(5)
                if process.is_alive():
                    raise RuntimeError("Input parser did not exit after kill")

        if status == "ok":
            return payload
        if status == "invalid":
            raise ValueError(payload)
        if status == "timeout":
            raise TimeoutError
        raise RuntimeError("Input parser failed")

    @staticmethod
    def _from_path_process(
        sender,
        path: Path,
        target_platforms: list[str] | tuple[str, ...] | None,
        deadline: float,
    ) -> None:
        """Send an input parse result from an isolated process."""
        CredentialRedactionFilter.install()
        # Uploaded files must not expand values from the server environment.
        os.environ.clear()
        try:
            with open(os.devnull, "w") as output:
                with redirect_stdout(output), redirect_stderr(output):
                    if path.suffix.lower() in {".yml", ".yaml", ".json", ".lock"}:
                        nodes = 0
                        with path.open(encoding="utf-8") as source:
                            for event in YAML(typ="safe", pure=True).parse(source):
                                if time.monotonic() >= deadline:
                                    raise TimeoutError
                                if isinstance(event, AliasEvent):
                                    raise ValueError(
                                        "YAML aliases are not accepted by the "
                                        "HTTP parser"
                                    )
                                if isinstance(event, NodeEvent):
                                    nodes += 1
                                    if nodes > HTTP_INPUT_MAX_NODES:
                                        raise ValueError(
                                            "Input file exceeds the structural "
                                            "complexity limit"
                                        )
                    elif path.suffix.lower() == ".toml":
                        with path.open("rb") as source:
                            pending = [tomllib.load(source)]
                        nodes = 0
                        while pending:
                            if time.monotonic() >= deadline:
                                raise TimeoutError
                            value = pending.pop()
                            nodes += 1
                            if nodes > HTTP_INPUT_MAX_NODES:
                                raise ValueError(
                                    "Input file exceeds the structural complexity limit"
                                )
                            children = (
                                value.values() if isinstance(value, dict) else value
                            )
                            if isinstance(value, (dict, list)):
                                if (
                                    len(pending) + len(value)
                                    > HTTP_INPUT_MAX_NODES - nodes
                                ):
                                    raise ValueError(
                                        "Input file exceeds the structural "
                                        "complexity limit"
                                    )
                                pending.extend(children)
                    else:
                        items = 0
                        with path.open(encoding="utf-8") as source:
                            for line in source:
                                if time.monotonic() >= deadline:
                                    raise TimeoutError
                                stripped = line.strip()
                                if stripped and not stripped.startswith("#"):
                                    items += 1
                                    if items > HTTP_INPUT_MAX_NODES:
                                        raise ValueError(
                                            "Input file exceeds the structural "
                                            "complexity limit"
                                        )
                    parsed = ParsedInputFile.from_path(
                        path,
                        target_platforms,
                        materialize_lockfiles=False,
                    )
            sender.send(("ok", parsed))
        except TimeoutError:
            sender.send(("timeout", None))
        except (CondaError, ValueError) as exc:
            message = str(exc).replace(str(path), path.name)
            message = message.replace(str(path.parent), "[temporary-directory]")
            sender.send(("invalid", redact_safe_error(message)))
        except YAMLError:
            sender.send(("invalid", "Input file contains invalid YAML"))
        except Exception:
            sender.send(("error", None))
        finally:
            sender.close()
