"""Internal conda solver backend backed by conda-presto."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from importlib.metadata import version as pkg_version
from types import MappingProxyType
from typing import Any, Literal, NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import msgspec
from conda.auxlib import NULL
from conda.base.constants import ChannelPriority, UpdateModifier
from conda.base.context import context
from conda.exceptions import (
    CondaError,
    PackagesNotFoundError,
    SpecsConfigurationConflictError,
    UnsatisfiableError,
)
from conda.gateways.shards import build_repodata_subset
from conda.models.channel import Channel
from conda.models.match_spec import MatchSpec
from conda.models.records import PackageRecord, PrefixRecord
from conda_broker import Broker
from conda_rattler_solver.exceptions import RattlerUnsatisfiableError
from conda_rattler_solver.solver import RattlerSolver
from conda_rattler_solver.state import SolverInputState, SolverOutputState

from .config import SOLVE_TIMEOUT_S
from .resolve import RepodataSnapshot, platform_lock

log = logging.getLogger(__name__)

SOLVER_CACHE_ENVELOPE_VERSION = 3
SOLVER_CACHE_DEPENDENCY_PACKAGES = (
    "conda-presto",
    "conda",
    "conda-rattler-solver",
    "py-rattler",
)


class PrestoSolverError(CondaError):
    """The internal conda-presto solver service cannot complete a request."""


class PrestoSolveResponse(msgspec.Struct):
    """The private response exchanged by the Presto solver backend."""

    records: list[dict[str, Any]]
    neutered: list[str]


class PrestoSolveError(msgspec.Struct):
    """A conda error that can be reconstructed by the local client."""

    kind: Literal[
        "packages-not-found",
        "specs-configuration-conflict",
        "unsatisfiable",
        "solver",
    ]
    message: str
    packages: list[str] = msgspec.field(default_factory=list)
    channel_urls: list[str] = msgspec.field(default_factory=list)
    requested_specs: list[str] = msgspec.field(default_factory=list)
    pinned_specs: list[str] = msgspec.field(default_factory=list)
    allow_retry: bool | None = None

    @classmethod
    def from_exception(cls, error: CondaError) -> PrestoSolveError:
        """Capture the public conda error data used by CLI retry handling."""
        common = {
            "message": str(error),
            "allow_retry": getattr(error, "allow_retry", None),
        }
        if isinstance(error, PackagesNotFoundError):
            return cls(
                kind="packages-not-found",
                packages=[str(package) for package in error.packages],
                channel_urls=[str(channel) for channel in error.channel_urls],
                **common,
            )
        if isinstance(error, SpecsConfigurationConflictError):
            # Conda exposes no public structured accessors for this error. This
            # explicitly private protocol mirrors its constructor data.
            return cls(
                kind="specs-configuration-conflict",
                requested_specs=[
                    str(spec) for spec in error._kwargs["requested_specs"]
                ],
                pinned_specs=[str(spec) for spec in error._kwargs["pinned_specs"]],
                **common,
            )
        if isinstance(error, UnsatisfiableError):
            return cls(kind="unsatisfiable", **common)
        return cls(kind="solver", **common)

    def raise_exception(self, prefix: str) -> NoReturn:
        """Raise the corresponding conda exception in the client process."""
        if self.kind == "packages-not-found":
            error = PackagesNotFoundError(self.packages, self.channel_urls)
        elif self.kind == "specs-configuration-conflict":
            error = SpecsConfigurationConflictError(
                [MatchSpec(spec) for spec in self.requested_specs],
                [MatchSpec(spec) for spec in self.pinned_specs],
                prefix,
            )
        elif self.kind == "unsatisfiable":
            error = RattlerUnsatisfiableError(self.message)
        else:
            error = PrestoSolverError(self.message)
        if self.allow_retry is not None:
            error.allow_retry = self.allow_retry
        raise error


class PrestoSolveOutcome(msgspec.Struct):
    """A worker result and any repodata state observed while producing it."""

    result: PrestoSolveResponse | PrestoSolveError
    metadata_before: RepodataSnapshot | None
    metadata_used: RepodataSnapshot | None

    def is_cacheable_with(self, current: RepodataSnapshot) -> bool:
        """Return whether the response can be published for *current* metadata."""
        if (
            isinstance(self.result, PrestoSolveError)
            or self.metadata_before is None
            or self.metadata_used is None
            or current.stale
            or current.records != self.metadata_used.records
        ):
            return False
        if not self.metadata_used.is_cacheable_after(self.metadata_before):
            return False
        return self.metadata_before.stale or (
            self.metadata_before.records == self.metadata_used.records
        )


class PrestoSolveRequest(msgspec.Struct, forbid_unknown_fields=True):
    """A private serialized ``conda-rattler-solver`` input state."""

    channels: list[dict[str, Any]]
    subdirs: list[str]
    specs_to_add: list[str]
    specs_to_remove: list[str]
    installed: list[dict[str, Any]]
    history: list[str]
    pinned: list[str]
    virtual: list[dict[str, Any]]
    aggressive_updates: list[str]
    always_update: list[str]
    update_modifier: str
    deps_modifier: str
    ignore_pinned: bool
    force_remove: bool
    prune: bool
    command: str | None
    repodata_fn: str
    local_repodata_ttl: int
    offline: bool
    channel_priority: str
    use_only_tar_bz2: bool
    add_pip_as_python_dependency: bool
    allow_cycles: bool
    repodata_use_shards: bool
    use_index_cache: bool

    @classmethod
    def from_solver(
        cls,
        solver: RattlerSolver,
        input_state: SolverInputState,
    ) -> PrestoSolveRequest:
        """Capture the private rattler state that affects a final solve."""
        if context.offline:
            raise PrestoSolverError(
                "The internal Presto solver does not support offline mode."
            )
        if input_state.update_modifier == UpdateModifier.UPDATE_DEPS:
            raise PrestoSolverError(
                "The internal Presto solver does not support --update-deps."
            )
        if getattr(solver, "_build_repodata_subset", None) not in (
            None,
            build_repodata_subset,
        ):
            raise PrestoSolverError(
                "The internal Presto solver does not support caller-provided "
                "repodata subsets."
            )
        channels = solver._collect_channel_list(input_state)
        request = cls(
            channels=[channel.dump() for channel in channels],
            subdirs=list(solver.subdirs),
            specs_to_add=sorted(str(spec) for spec in solver.unmerged_specs_to_add),
            specs_to_remove=sorted(
                str(spec) for spec in solver.unmerged_specs_to_remove
            ),
            installed=sorted(
                (
                    PackageRecord.from_objects(record).dump()
                    for record in input_state.installed.values()
                ),
                key=lambda record: record["name"],
            ),
            history=sorted(str(spec) for spec in input_state.history.values()),
            pinned=sorted(str(spec) for spec in input_state.pinned.values()),
            virtual=sorted(
                (record.dump() for record in input_state.virtual.values()),
                key=lambda record: record["name"],
            ),
            aggressive_updates=sorted(
                str(spec) for spec in input_state.aggressive_updates.values()
            ),
            always_update=sorted(
                str(spec) for spec in input_state.always_update.values()
            ),
            update_modifier=input_state.update_modifier.name,
            deps_modifier=input_state.deps_modifier.name,
            ignore_pinned=input_state.ignore_pinned,
            force_remove=input_state.force_remove,
            prune=False if input_state.prune is NULL else input_state.prune,
            command=(
                input_state._command if isinstance(input_state._command, str) else None
            ),
            repodata_fn=solver._repodata_fn,
            local_repodata_ttl=int(context.local_repodata_ttl),
            offline=context.offline,
            channel_priority=context.channel_priority.value,
            use_only_tar_bz2=bool(context.use_only_tar_bz2),
            add_pip_as_python_dependency=bool(context.add_pip_as_python_dependency),
            allow_cycles=bool(context.allow_cycles),
            repodata_use_shards=bool(context.repodata_use_shards),
            use_index_cache=bool(context.use_index_cache),
        )
        request.target_subdir()
        return request

    def cache_key(self) -> str:
        """Return the cache key for this request and dependency versions."""
        versions = {}
        for package in SOLVER_CACHE_DEPENDENCY_PACKAGES:
            try:
                versions[package] = pkg_version(package)
            except Exception:
                versions[package] = "unknown"
        request = msgspec.to_builtins(self)
        # TTL controls freshness checks but not the final state for matching
        # repodata markers, so callers with different TTLs can share an entry.
        del request["local_repodata_ttl"]
        envelope = {
            "version": SOLVER_CACHE_ENVELOPE_VERSION,
            "operation": "solver/v1",
            "request": request,
            "dependency_versions": versions,
        }
        body = json.dumps(
            envelope,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(body).hexdigest()

    def repodata_snapshot(self) -> RepodataSnapshot:
        """Capture the server metadata that can affect this solve."""
        with platform_lock:
            with self.override_context():
                solver = self.rattler_solver()
                return self.capture_repodata(
                    solver._collect_channel_list(PrestoSolverInputState(self))
                )

    @contextmanager
    def override_context(self) -> Iterator[None]:
        """Apply the conda context captured by this request."""
        with ExitStack() as overrides:
            for key, value in (
                ("offline", self.offline),
                ("channel_priority", ChannelPriority(self.channel_priority)),
                ("_use_only_tar_bz2", self.use_only_tar_bz2),
                (
                    "add_pip_as_python_dependency",
                    self.add_pip_as_python_dependency,
                ),
                ("allow_cycles", self.allow_cycles),
                ("repodata_use_shards", self.repodata_use_shards),
                ("use_index_cache", self.use_index_cache),
                ("local_repodata_ttl", self.local_repodata_ttl),
                ("_subdir", self.target_subdir()),
            ):
                overrides.enter_context(context._override(key, value))
            yield

    def capture_repodata(self, channels: list[Channel]) -> RepodataSnapshot:
        """Capture repodata for an effective channel list under the caller's lock."""
        return RepodataSnapshot.capture(
            channels,
            [self.target_subdir()],
            repodata_fn=self.repodata_fn,
            use_shards=self.repodata_use_shards,
            use_index_cache=self.use_index_cache,
        )

    def rattler_solver(self) -> RattlerSolver:
        """Restore the rattler backend captured by this request."""
        backend = context.plugin_manager.get_solver_backend("rattler")
        solver = backend(
            prefix="/conda-presto/solver",
            channels=[Channel(**channel) for channel in self.channels],
            subdirs=self.subdirs,
            specs_to_add=self.specs_to_add,
            specs_to_remove=self.specs_to_remove,
            repodata_fn=self.repodata_fn,
            command=self.command if self.command is not None else NULL,
            build_repodata_subset=(
                build_repodata_subset if self.repodata_use_shards else None
            ),
        )
        # The client captured its already-effective filename. The rattler
        # constructor must not replace it using the worker's configuration.
        solver._repodata_fn = self.repodata_fn
        return solver

    def target_subdir(self) -> str:
        """Return the single target subdir accepted by this private protocol."""
        target_subdir = self.subdirs[0] if self.subdirs else ""
        valid_subdirs = ([target_subdir], [target_subdir, "noarch"])
        if (
            not target_subdir
            or target_subdir == "noarch"
            or self.subdirs not in valid_subdirs
        ):
            raise PrestoSolverError(
                "The internal Presto solver requires exactly one target "
                "subdir and optional 'noarch'."
            )
        return target_subdir

    def solve(self) -> PrestoSolveOutcome:
        """Solve this captured state and report the repodata observed by the worker."""
        if self.offline:
            raise PrestoSolverError(
                "The internal Presto solver does not support offline mode."
            )
        if self.update_modifier == "UPDATE_DEPS":
            raise PrestoSolverError(
                "The internal Presto solver does not support --update-deps."
            )
        with platform_lock:
            with self.override_context():
                solver = self.rattler_solver()
                input_state = PrestoSolverInputState(self)
                output_state = SolverOutputState(solver_input_state=input_state)
                channels = solver._collect_channel_list(input_state)
                if (solution := output_state.early_exit()) is not None:
                    return PrestoSolveOutcome(
                        result=PrestoSolveResponse(
                            records=[record.dump() for record in solution],
                            neutered=[],
                        ),
                        metadata_before=None,
                        metadata_used=None,
                    )
                conda_build_channels = (
                    solver._collect_channels_subdirs_from_conda_build(
                        seen=set(channels)
                    )
                )
                metadata_before = None
                metadata_used = None
                try:
                    metadata_before = self.capture_repodata(channels)
                except Exception:
                    log.warning(
                        "Worker repodata metadata unavailable before index collection",
                    )
                try:
                    index = solver._collect_all_metadata(
                        channels=channels,
                        conda_build_channels=conda_build_channels,
                        subdirs=solver.subdirs,
                        in_state=input_state,
                    )
                    try:
                        metadata_used = self.capture_repodata(channels)
                    except Exception:
                        log.warning(
                            "Worker repodata metadata unavailable after "
                            "index collection",
                        )
                    output_state.check_for_pin_conflicts(index)
                    output_state = solver._solving_loop(
                        input_state,
                        output_state,
                        index,
                    )
                except CondaError as exc:
                    if metadata_used is None:
                        try:
                            metadata_used = self.capture_repodata(channels)
                        except Exception:
                            log.warning(
                                "Worker repodata metadata unavailable after "
                                "solver error",
                            )
                    return PrestoSolveOutcome(
                        result=PrestoSolveError.from_exception(exc),
                        metadata_before=metadata_before,
                        metadata_used=metadata_used,
                    )
                return PrestoSolveOutcome(
                    result=PrestoSolveResponse(
                        records=[
                            record.dump() for record in output_state.current_solution
                        ],
                        neutered=[str(spec) for spec in output_state.neutered.values()],
                    ),
                    metadata_before=metadata_before,
                    metadata_used=metadata_used,
                )


class PrestoSolverInputState(SolverInputState):
    """Restore a captured input state without reading the client's prefix."""

    def __init__(self, request: PrestoSolveRequest) -> None:
        super().__init__(
            prefix="/conda-presto/solver",
            requested=MatchSpec.merge(
                MatchSpec(spec)
                for spec in (request.specs_to_add or request.specs_to_remove)
            ),
            update_modifier=request.update_modifier.upper(),
            deps_modifier=request.deps_modifier.upper(),
            ignore_pinned=request.ignore_pinned,
            force_remove=request.force_remove,
            prune=request.prune,
            command=request.command,
        )
        records = self.prefix_data._prefix_records
        records.clear()
        records.update(
            {
                record.name: record
                for record in (PrefixRecord(**data) for data in request.installed)
            }
        )
        self._history = {
            spec.name: spec for spec in (MatchSpec(value) for value in request.history)
        }
        self._pinned = {
            spec.name: spec for spec in (MatchSpec(value) for value in request.pinned)
        }
        # conda-rattler-solver 0.1.1 cannot accept captured virtual packages.
        # Remove this assignment after conda/conda-rattler-solver#98 is released.
        self._virtual = {
            record.name: record
            for record in (PackageRecord(**data) for data in request.virtual)
        }
        self._aggressive_updates = {
            spec.name: spec
            for spec in (MatchSpec(value) for value in request.aggressive_updates)
        }
        self._always_update = {
            spec.name: spec
            for spec in (MatchSpec(value) for value in request.always_update)
        }

    @property
    def always_update(self) -> dict[str, MatchSpec]:
        """Use the client's computed update set instead of server configuration."""
        return MappingProxyType(self._always_update)


class PrestoSolverClient:
    """Call the broker-discovered internal Presto solver endpoint."""

    service_name = "conda-presto.server"

    class RedirectHandler(HTTPRedirectHandler):
        """Reject redirects so solve state never leaves the loopback endpoint."""

        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = build_opener(ProxyHandler({}), RedirectHandler())

    @staticmethod
    def is_loopback(host: str) -> bool:
        """Return whether *host* is a loopback address accepted by this protocol."""
        if host.lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def solve(
        self,
        request: PrestoSolveRequest,
        prefix: str,
    ) -> PrestoSolveResponse:
        """Submit a serialized solver request without managing the service."""
        endpoint = Broker.current().service(self.service_name).endpoint(ready=True)
        parsed_url = urlparse(endpoint.url) if endpoint and endpoint.url else None
        if (
            endpoint is None
            or endpoint.protocol != "http"
            or parsed_url is None
            or parsed_url.scheme != "http"
            or parsed_url.hostname is None
            or not self.is_loopback(parsed_url.hostname)
        ):
            raise PrestoSolverError(
                "The internal Presto solver requires a ready loopback service. "
                "Run 'conda broker start conda-presto.server' and "
                "'conda broker wait conda-presto.server'."
            )
        url = urljoin(f"{endpoint.url.rstrip('/')}/", "solver/v1")
        http_request = Request(
            url,
            data=msgspec.json.encode(request),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener.open(
                http_request, timeout=SOLVE_TIMEOUT_S + 5
            ) as response:
                return msgspec.json.decode(response.read(), type=PrestoSolveResponse)
        except HTTPError as exc:
            body = exc.read()
            if exc.code == 422:
                try:
                    error = msgspec.json.decode(body, type=PrestoSolveError)
                except (msgspec.DecodeError, msgspec.ValidationError):
                    pass
                else:
                    error.raise_exception(prefix)
            detail = body.decode("utf-8", errors="replace")
            raise PrestoSolverError(
                f"The internal Presto solver returned HTTP {exc.code}: {detail}"
            ) from None
        except (OSError, URLError, msgspec.DecodeError, msgspec.ValidationError) as exc:
            raise PrestoSolverError(
                f"The internal Presto solver is unavailable: {exc}"
            ) from None


class PrestoSolver(RattlerSolver):
    """Delegate final-state solves to conda-presto's internal broker service."""

    def solve_final_state(
        self,
        update_modifier=NULL,
        deps_modifier=NULL,
        prune=NULL,
        ignore_pinned=NULL,
        force_remove=NULL,
        should_retry_solve: bool = False,
    ) -> tuple[PackageRecord, ...]:
        """Return the service-computed final state for this conda operation."""
        del should_retry_solve
        with platform_lock:
            if getattr(self, "_index", None) is not None:
                raise PrestoSolverError(
                    "The internal Presto solver does not support conda-build "
                    "caller-provided indexes."
                )
            input_state = SolverInputState(
                prefix=self.prefix,
                requested=self.specs_to_add or self.specs_to_remove,
                update_modifier=update_modifier,
                deps_modifier=deps_modifier,
                prune=prune,
                ignore_pinned=ignore_pinned,
                force_remove=force_remove,
                command=self._command,
            )
            request = PrestoSolveRequest.from_solver(self, input_state)
        response = PrestoSolverClient().solve(request, str(self.prefix))
        self.neutered_specs = tuple(MatchSpec(spec) for spec in response.neutered)
        return tuple(PackageRecord(**data) for data in response.records)
