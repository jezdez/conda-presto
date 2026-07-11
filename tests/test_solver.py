"""Tests for the broker-backed Presto conda solver."""

from __future__ import annotations

from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from threading import Thread
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import ProxyHandler

import msgspec
import pytest
from conda.base.constants import ChannelPriority, UpdateModifier
from conda.base.context import context
from conda.exceptions import (
    PackagesNotFoundError,
    SpecsConfigurationConflictError,
    UnsatisfiableError,
)
from conda.models.channel import Channel
from conda.models.match_spec import MatchSpec
from conda.models.records import PackageRecord, PrefixRecord
from conda_rattler_solver.exceptions import RattlerUnsatisfiableError
from conda_rattler_solver.solver import RattlerSolver

import conda_presto.solver as solver_module
from conda_presto.resolve import RepodataSnapshot
from conda_presto.solver import (
    PrestoSolveError,
    PrestoSolveOutcome,
    PrestoSolver,
    PrestoSolverClient,
    PrestoSolveRequest,
    PrestoSolverError,
    PrestoSolveResponse,
    PrestoSolverInputState,
)


@pytest.fixture()
def package_record():
    return PackageRecord(name="zlib", version="1.3.1", build="h1", build_number=0)


@pytest.fixture()
def solver_request(package_record):
    return PrestoSolveRequest(
        channels=[Channel("conda-forge").dump()],
        subdirs=["linux-64", "noarch"],
        specs_to_add=["zlib"],
        specs_to_remove=[],
        installed=[package_record.dump()],
        history=["zlib"],
        pinned=[],
        virtual=[],
        aggressive_updates=[],
        always_update=["zlib"],
        update_modifier="UPDATE_SPECS",
        deps_modifier="NOT_SET",
        ignore_pinned=False,
        force_remove=False,
        prune=False,
        command="install",
        repodata_fn="repodata.json",
        offline=False,
        channel_priority="strict",
        use_only_tar_bz2=False,
        add_pip_as_python_dependency=True,
        allow_cycles=True,
        restore_free_channel=False,
        repodata_use_shards=True,
        use_index_cache=False,
    )


@pytest.fixture()
def solver_repodata():
    return RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 10, 1),),
        False,
    )


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(
            {"installed": [{"name": "python", "version": "3.13"}]},
            id="installed",
        ),
        pytest.param({"subdirs": ["osx-arm64", "noarch"]}, id="subdirs"),
        pytest.param({"specs_to_add": ["python"]}, id="specs-to-add"),
        pytest.param({"specs_to_remove": ["zlib"]}, id="specs-to-remove"),
        pytest.param({"history": ["python=3.13"]}, id="history"),
        pytest.param({"pinned": ["python<3.14"]}, id="pinned"),
        pytest.param(
            {"virtual": [{"name": "__linux", "version": "6"}]},
            id="virtual",
        ),
        pytest.param(
            {"aggressive_updates": ["openssl"]},
            id="aggressive-updates",
        ),
        pytest.param({"always_update": ["python"]}, id="always-update"),
        pytest.param({"update_modifier": "UPDATE_ALL"}, id="update-modifier"),
        pytest.param({"deps_modifier": "NO_DEPS"}, id="deps-modifier"),
        pytest.param({"ignore_pinned": True}, id="ignore-pinned"),
        pytest.param({"force_remove": True}, id="force-remove"),
        pytest.param({"prune": True}, id="prune"),
        pytest.param({"command": "create"}, id="command"),
        pytest.param({"offline": True}, id="offline"),
        pytest.param({"channel_priority": "flexible"}, id="channel-priority"),
        pytest.param({"use_only_tar_bz2": True}, id="package-format"),
        pytest.param(
            {"add_pip_as_python_dependency": False},
            id="add-pip-as-python-dependency",
        ),
        pytest.param({"allow_cycles": False}, id="allow-cycles"),
        pytest.param(
            {"restore_free_channel": True},
            id="restore-free-channel",
        ),
        pytest.param({"repodata_use_shards": False}, id="repodata-shards"),
        pytest.param({"use_index_cache": True}, id="index-cache"),
        pytest.param({"channels": [Channel("bioconda").dump()]}, id="channels"),
        pytest.param(
            {
                "channels": [
                    {
                        **Channel("conda-forge").dump(),
                        "auth": "user:password",
                    }
                ]
            },
            id="channel-auth",
        ),
        pytest.param(
            {
                "channels": [
                    {
                        **Channel("conda-forge").dump(),
                        "token": "secret",
                    }
                ]
            },
            id="channel-token",
        ),
    ],
)
def test_solver_cache_key_covers_final_state(solver_request, change):
    changed = msgspec.structs.replace(solver_request, **change)

    assert changed.cache_key() != solver_request.cache_key()


def test_solver_cache_key_preserves_channel_order(solver_request):
    first = msgspec.structs.replace(
        solver_request,
        channels=[Channel("conda-forge").dump(), Channel("bioconda").dump()],
    )
    second = msgspec.structs.replace(
        solver_request,
        channels=[Channel("bioconda").dump(), Channel("conda-forge").dump()],
    )

    assert first.cache_key() != second.cache_key()


def test_solver_cache_key_covers_protocol_version(monkeypatch, solver_request):
    first = solver_request.cache_key()
    monkeypatch.setattr(solver_module, "SOLVER_CACHE_ENVELOPE_VERSION", 3)

    assert solver_request.cache_key() != first


def test_solver_cache_key_covers_effective_repodata_filename(
    monkeypatch,
    solver_request,
):
    monkeypatch.setattr(
        solver_module,
        "maybe_ignore_current_repodata",
        lambda value: value,
    )
    changed = msgspec.structs.replace(
        solver_request,
        repodata_fn="current_repodata.json",
    )

    assert changed.cache_key() != solver_request.cache_key()


def test_solver_cache_key_is_stable_across_repodata(
    monkeypatch, solver_request, solver_repodata
):
    monkeypatch.setattr(
        solver_module.RepodataSnapshot,
        "capture",
        lambda *_args, **_kwargs: solver_repodata,
    )
    first = solver_request.cache_key()
    changed = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 20, 2),),
        False,
    )
    monkeypatch.setattr(
        solver_module.RepodataSnapshot,
        "capture",
        lambda *_args, **_kwargs: changed,
    )

    assert solver_request.cache_key() == first


@pytest.mark.parametrize(
    ("metadata_before", "metadata_used", "current", "expected"),
    [
        pytest.param(
            RepodataSnapshot((("channel", "repodata.json", 10, 1),), False),
            RepodataSnapshot((("channel", "repodata.json", 10, 1),), False),
            RepodataSnapshot((("channel", "repodata.json", 10, 1),), False),
            True,
            id="fresh-unchanged",
        ),
        pytest.param(
            RepodataSnapshot((("channel", "repodata.json", 10, 1),), False),
            RepodataSnapshot((("channel", "repodata.json", 20, 2),), False),
            RepodataSnapshot((("channel", "repodata.json", 20, 2),), False),
            False,
            id="fresh-changed-during-index",
        ),
        pytest.param(
            RepodataSnapshot((("channel", "repodata.json", 10, 1),), True),
            RepodataSnapshot((("channel", "repodata.json", 20, 2),), False),
            RepodataSnapshot((("channel", "repodata.json", 20, 2),), False),
            True,
            id="stale-refreshed",
        ),
        pytest.param(
            RepodataSnapshot((("channel", "repodata.json", 10, 1),), False),
            RepodataSnapshot((("channel", "repodata.json", 10, 1),), False),
            RepodataSnapshot((("channel", "repodata.json", 20, 2),), False),
            False,
            id="changed-after-index",
        ),
        pytest.param(
            RepodataSnapshot((("channel", "repodata.json", 10, 1),), False),
            RepodataSnapshot((("channel", "repodata.json", 10, 1),), False),
            RepodataSnapshot((("channel", "repodata.json", 10, 1),), True),
            False,
            id="current-stale",
        ),
        pytest.param(
            RepodataSnapshot(
                (("channel", "repodata_shards.msgpack.zst", 10, 1),),
                True,
            ),
            RepodataSnapshot(
                (("channel", "repodata_shards.msgpack.zst", 10, 1),),
                False,
            ),
            RepodataSnapshot(
                (("channel", "repodata_shards.msgpack.zst", 10, 1),),
                False,
            ),
            False,
            id="transient-shard-fallback",
        ),
    ],
)
def test_solver_outcome_validates_worker_observed_repodata(
    metadata_before,
    metadata_used,
    current,
    expected,
):
    outcome = PrestoSolveOutcome(
        result=PrestoSolveResponse(records=[], neutered=[]),
        metadata_before=metadata_before,
        metadata_used=metadata_used,
    )

    assert outcome.is_cacheable_with(current) is expected


@pytest.mark.parametrize("package", solver_module.SOLVER_CACHE_DEPENDENCY_PACKAGES)
def test_solver_cache_key_covers_dependency_versions(
    monkeypatch, solver_request, package
):
    monkeypatch.setattr(
        solver_module,
        "pkg_version",
        lambda name: "one" if name == package else "same",
    )
    first = solver_request.cache_key()
    monkeypatch.setattr(
        solver_module,
        "pkg_version",
        lambda name: "two" if name == package else "same",
    )

    assert solver_request.cache_key() != first


def test_solver_uses_server_effective_repodata_fn(
    monkeypatch, solver_request, solver_repodata
):
    captured = {}
    request = msgspec.structs.replace(
        solver_request,
        repodata_fn="current_repodata.json",
        repodata_use_shards=False,
    )

    def capture(channels, platforms, **kwargs):
        captured.update(kwargs)
        return RepodataSnapshot((), False)

    monkeypatch.setattr(
        solver_module,
        "maybe_ignore_current_repodata",
        lambda _: "repodata.json",
    )
    monkeypatch.setattr(solver_module.RepodataSnapshot, "capture", capture)

    request.repodata_snapshot()

    assert captured["repodata_fn"] == "repodata.json"
    assert (
        request.cache_key()
        == msgspec.structs.replace(
            request,
            repodata_fn="repodata.json",
        ).cache_key()
    )


@pytest.mark.parametrize(
    ("change", "derived_url"),
    [
        pytest.param(
            {"specs_to_add": ["bioconda::zlib"]},
            "https://conda.anaconda.org/bioconda",
            id="qualified-spec",
        ),
        pytest.param(
            {"restore_free_channel": True},
            "https://repo.anaconda.com/pkgs/free",
            id="restored-free-channel",
        ),
    ],
)
def test_solver_snapshot_derives_effective_channels_on_server(
    monkeypatch, solver_request, change, derived_url
):
    captured = []

    def capture(channels, platforms, **kwargs):
        captured.extend(channel.base_url for channel in channels)
        return RepodataSnapshot((), False)

    monkeypatch.setattr(solver_module.RepodataSnapshot, "capture", capture)

    msgspec.structs.replace(solver_request, **change).repodata_snapshot()

    assert captured == [
        "https://conda.anaconda.org/conda-forge",
        derived_url,
    ]


def test_presto_input_state_restores_client_snapshot(solver_request):
    state = PrestoSolverInputState(solver_request)

    assert list(state.installed) == ["zlib"]
    assert list(state.history) == ["zlib"]
    assert list(state.always_update) == ["zlib"]


def test_presto_request_minimizes_large_installed_prefix():
    files = tuple(
        f"lib/python3.13/site-packages/example/file-{index}.py" for index in range(500)
    )
    installed = {
        f"package-{index}": PrefixRecord(
            name=f"package-{index}",
            version="1.0",
            build="0",
            build_number=0,
            channel="conda-forge",
            subdir="noarch",
            files=files,
            extracted_package_dir=f"/tmp/pkgs/package-{index}-1.0-0",
        )
        for index in range(200)
    }
    input_state = SimpleNamespace(
        installed=installed,
        history={},
        pinned={},
        virtual={},
        aggressive_updates={},
        always_update={},
        update_modifier=UpdateModifier.UPDATE_SPECS,
        _update_modifier=UpdateModifier.UPDATE_SPECS,
        _deps_modifier="not_set",
        ignore_pinned=False,
        force_remove=False,
        prune=False,
        _command="install",
    )
    solver = SimpleNamespace(
        channels=[Channel("conda-forge")],
        _collect_channel_list=lambda _: [
            Channel("conda-forge"),
            Channel("bioconda"),
        ],
        subdirs=["linux-64", "noarch"],
        _unmerged_specs_to_add=[MatchSpec("zlib")],
        unmerged_specs_to_remove=[],
        _repodata_fn="repodata.json",
        _build_repodata_subset=solver_module.build_repodata_subset,
    )

    with (
        context._override("channel_priority", ChannelPriority.DISABLED),
        context._override("_use_only_tar_bz2", True),
        context._override("add_pip_as_python_dependency", False),
        context._override("allow_cycles", False),
        context._override("_restore_free_channel", True),
        context._override("repodata_use_shards", False),
        context._override("use_index_cache", True),
    ):
        request = PrestoSolveRequest.from_solver(solver, input_state)

    assert (
        len(msgspec.json.encode([record.dump() for record in installed.values()]))
        > 1_048_576
    )
    assert len(msgspec.json.encode(request)) < 1_048_576
    assert not (
        {"files", "extracted_package_dir", "paths_data"} & request.installed[0].keys()
    )
    assert [record["name"] for record in request.installed] == sorted(installed)
    assert request.channels == [
        Channel("conda-forge").dump(),
        Channel("bioconda").dump(),
    ]
    assert request.channel_priority == "disabled"
    assert request.use_only_tar_bz2 is True
    assert request.add_pip_as_python_dependency is False
    assert request.allow_cycles is False
    assert request.restore_free_channel is True
    assert request.repodata_use_shards is False
    assert request.use_index_cache is True


@pytest.mark.parametrize(
    ("error", "expected_type"),
    [
        pytest.param(
            PackagesNotFoundError(["missing"], [Channel("https://example.invalid")]),
            PackagesNotFoundError,
            id="packages-not-found",
        ),
        pytest.param(
            RattlerUnsatisfiableError("incompatible specs"),
            UnsatisfiableError,
            id="unsatisfiable",
        ),
        pytest.param(
            SpecsConfigurationConflictError(
                [MatchSpec("zlib")], [MatchSpec("zlib<1")], "/server-prefix"
            ),
            SpecsConfigurationConflictError,
            id="specs-configuration-conflict",
        ),
        pytest.param(
            PrestoSolverError("unsupported state"),
            PrestoSolverError,
            id="solver",
        ),
    ],
)
def test_presto_error_roundtrip(error, expected_type):
    error.allow_retry = False
    serialized = PrestoSolveError.from_exception(error)
    serialized = msgspec.json.decode(
        msgspec.json.encode(serialized), type=PrestoSolveError
    )

    with pytest.raises(expected_type) as raised:
        serialized.raise_exception("/client-prefix")

    assert raised.value.allow_retry is False
    if isinstance(raised.value, SpecsConfigurationConflictError):
        assert "/client-prefix/conda-meta/pinned" in str(raised.value)


def test_presto_request_uses_rattler_backend(
    monkeypatch, package_record, solver_request
):
    calls = []
    order = []

    class Backend:
        def __call__(self, **kwargs):
            calls.append(kwargs)

            def solve(*_):
                order.append("sat")
                return SimpleNamespace(
                    current_solution=[package_record],
                    neutered=OrderedDict([(package_record.name, MatchSpec("zlib"))]),
                )

            solver = SimpleNamespace(
                subdirs=kwargs["subdirs"],
                _repodata_fn="repodata.json",
                _collect_channel_list=lambda _: ["conda-forge"],
                _collect_channels_subdirs_from_conda_build=lambda **_: [],
                _solving_loop=solve,
            )
            solver._collect_all_metadata = lambda **_: (
                order.append("index")
                or calls.append(
                    {
                        "offline": context.offline,
                        "channel_priority": context.channel_priority.value,
                        "use_only_tar_bz2": context.use_only_tar_bz2,
                        "add_pip_as_python_dependency": (
                            context.add_pip_as_python_dependency
                        ),
                        "allow_cycles": context.allow_cycles,
                        "restore_free_channel": context._restore_free_channel,
                        "repodata_use_shards": context.repodata_use_shards,
                        "use_index_cache": context.use_index_cache,
                        "subdir": context.subdir,
                        "repodata_fn": solver._repodata_fn,
                    }
                )
                or "index"
            )
            return solver

    class OutputState:
        def __init__(self, *, solver_input_state):
            self.solver_input_state = solver_input_state

        def early_exit(self):
            return None

        def check_for_pin_conflicts(self, index):
            assert index == "index"

    monkeypatch.setattr(
        solver_module.context.plugin_manager,
        "get_solver_backend",
        lambda name: Backend() if name == "rattler" else None,
    )
    monkeypatch.setattr(solver_module, "SolverOutputState", OutputState)
    repodata = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 10, 1),),
        False,
    )
    monkeypatch.setattr(
        solver_module.RepodataSnapshot,
        "capture",
        lambda *_args, **_kwargs: order.append("metadata") or repodata,
    )
    request = msgspec.structs.replace(
        solver_request,
        channel_priority="disabled",
        use_only_tar_bz2=True,
        add_pip_as_python_dependency=False,
        allow_cycles=False,
        restore_free_channel=True,
        repodata_use_shards=False,
        use_index_cache=True,
        repodata_fn="current_repodata.json",
    )
    outcome = request.solve()
    response = outcome.result

    assert response.records[0]["name"] == "zlib"
    assert response.neutered == ["zlib"]
    assert outcome.metadata_before == outcome.metadata_used == repodata
    assert order == ["metadata", "index", "metadata", "sat"]
    assert [str(channel) for channel in calls[0]["channels"]] == [
        "https://conda.anaconda.org/conda-forge"
    ]
    assert calls[0]["subdirs"] == ["linux-64", "noarch"]
    assert calls[0]["repodata_fn"] == "repodata.json"
    assert calls[1] == {
        "offline": False,
        "channel_priority": "disabled",
        "use_only_tar_bz2": True,
        "add_pip_as_python_dependency": False,
        "allow_cycles": False,
        "restore_free_channel": True,
        "repodata_use_shards": False,
        "use_index_cache": True,
        "subdir": "linux-64",
        "repodata_fn": "repodata.json",
    }


@pytest.mark.parametrize(
    "failure_index",
    [pytest.param(0, id="before-index"), pytest.param(1, id="after-index")],
)
def test_presto_request_metadata_failure_does_not_fail_solve(
    monkeypatch,
    package_record,
    solver_request,
    failure_index,
):
    class Backend:
        def __call__(self, **kwargs):
            return SimpleNamespace(
                subdirs=kwargs["subdirs"],
                _repodata_fn=kwargs["repodata_fn"],
                _collect_channel_list=lambda _: [Channel("conda-forge")],
                _collect_channels_subdirs_from_conda_build=lambda **_: [],
                _collect_all_metadata=lambda **_: "index",
                _solving_loop=lambda *_: SimpleNamespace(
                    current_solution=[package_record],
                    neutered=OrderedDict(),
                ),
            )

    class OutputState:
        def __init__(self, *, solver_input_state):
            self.solver_input_state = solver_input_state

        def early_exit(self):
            return None

        def check_for_pin_conflicts(self, index):
            assert index == "index"

    repodata = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 10, 1),),
        False,
    )
    snapshots = iter(
        [
            RuntimeError("metadata unavailable") if index == failure_index else repodata
            for index in range(2)
        ]
    )

    def capture(*_args, **_kwargs):
        snapshot = next(snapshots)
        if isinstance(snapshot, Exception):
            raise snapshot
        return snapshot

    monkeypatch.setattr(
        solver_module.context.plugin_manager,
        "get_solver_backend",
        lambda name: Backend() if name == "rattler" else None,
    )
    monkeypatch.setattr(solver_module, "SolverOutputState", OutputState)
    monkeypatch.setattr(solver_module.RepodataSnapshot, "capture", capture)

    outcome = solver_request.solve()

    assert outcome.result.records[0]["name"] == "zlib"
    assert (outcome.metadata_before is None) is (failure_index == 0)
    assert (outcome.metadata_used is None) is (failure_index == 1)


def test_presto_request_error_retains_worker_metadata(
    monkeypatch,
    solver_request,
):
    error = PackagesNotFoundError(["missing"], ["https://example.invalid"])

    class Backend:
        def __call__(self, **kwargs):
            return SimpleNamespace(
                subdirs=kwargs["subdirs"],
                _repodata_fn=kwargs["repodata_fn"],
                _collect_channel_list=lambda _: [Channel("conda-forge")],
                _collect_channels_subdirs_from_conda_build=lambda **_: [],
                _collect_all_metadata=lambda **_: "index",
                _solving_loop=lambda *_: (_ for _ in ()).throw(error),
            )

    class OutputState:
        def __init__(self, *, solver_input_state):
            self.solver_input_state = solver_input_state

        def early_exit(self):
            return None

        def check_for_pin_conflicts(self, index):
            assert index == "index"

    repodata = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 10, 1),),
        False,
    )
    monkeypatch.setattr(
        solver_module.context.plugin_manager,
        "get_solver_backend",
        lambda name: Backend() if name == "rattler" else None,
    )
    monkeypatch.setattr(solver_module, "SolverOutputState", OutputState)
    monkeypatch.setattr(
        solver_module.RepodataSnapshot,
        "capture",
        lambda *_args, **_kwargs: repodata,
    )

    outcome = solver_request.solve()

    assert outcome.result.kind == "packages-not-found"
    assert outcome.metadata_before == outcome.metadata_used == repodata


def test_presto_request_accepts_empty_early_exit(monkeypatch, solver_request):
    class Backend:
        def __call__(self, **kwargs):
            return SimpleNamespace(
                _repodata_fn=kwargs["repodata_fn"],
                _collect_channel_list=lambda _: [Channel("conda-forge")],
                _collect_all_metadata=lambda **_: pytest.fail(
                    "empty early exit must not collect metadata"
                ),
            )

    monkeypatch.setattr(
        solver_module.context.plugin_manager,
        "get_solver_backend",
        lambda name: Backend() if name == "rattler" else None,
    )
    monkeypatch.setattr(
        solver_module,
        "SolverOutputState",
        lambda **_: SimpleNamespace(early_exit=lambda: []),
    )
    repodata = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 10, 1),),
        False,
    )
    monkeypatch.setattr(
        solver_module.RepodataSnapshot,
        "capture",
        lambda *_args, **_kwargs: repodata,
    )

    outcome = solver_request.solve()

    assert outcome == PrestoSolveOutcome(
        result=PrestoSolveResponse(records=[], neutered=[]),
        metadata_before=None,
        metadata_used=None,
    )


def test_presto_request_requires_rattler_backend(monkeypatch, solver_request):
    monkeypatch.setattr(
        solver_module.context.plugin_manager,
        "get_solver_backend",
        lambda name: None,
    )

    with pytest.raises(RuntimeError, match="rattler solver backend"):
        solver_request.solve()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        pytest.param({"offline": True}, "offline mode", id="offline"),
        pytest.param(
            {"update_modifier": "UPDATE_DEPS"},
            "--update-deps",
            id="update-deps",
        ),
        pytest.param({"subdirs": []}, "exactly one target", id="no-subdirs"),
        pytest.param(
            {"subdirs": ["noarch"]},
            "exactly one target",
            id="noarch-only",
        ),
        pytest.param(
            {"subdirs": ["linux-64", "osx-64", "noarch"]},
            "exactly one target",
            id="multiple-targets",
        ),
        pytest.param(
            {"subdirs": ["linux-64", "linux-64"]},
            "exactly one target",
            id="duplicate-target",
        ),
        pytest.param(
            {"subdirs": ["noarch", "linux-64"]},
            "exactly one target",
            id="noarch-first",
        ),
    ],
)
def test_presto_request_rejects_unsupported_state(solver_request, change, message):
    request = msgspec.structs.replace(solver_request, **change)

    with pytest.raises(PrestoSolverError, match=message):
        request.solve()


@pytest.mark.parametrize(
    "subdirs",
    [
        pytest.param([], id="no-subdirs"),
        pytest.param(["noarch"], id="noarch-only"),
        pytest.param(["linux-64", "osx-64", "noarch"], id="multiple-targets"),
        pytest.param(["linux-64", "linux-64"], id="duplicate-target"),
        pytest.param(["noarch", "linux-64"], id="noarch-first"),
    ],
)
def test_presto_snapshot_rejects_invalid_subdirs(subdirs):
    solver = SimpleNamespace(
        channels=[Channel("conda-forge")],
        subdirs=subdirs,
        _unmerged_specs_to_add=[MatchSpec("zlib")],
        unmerged_specs_to_remove=[],
        _repodata_fn="repodata.json",
        _build_repodata_subset=None,
    )
    input_state = SimpleNamespace(
        installed={},
        history={},
        pinned={},
        virtual={},
        aggressive_updates={},
        always_update={},
        update_modifier=UpdateModifier.UPDATE_SPECS,
        _update_modifier=UpdateModifier.UPDATE_SPECS,
        _deps_modifier="not_set",
        ignore_pinned=False,
        force_remove=False,
        prune=False,
        _command="install",
    )

    with pytest.raises(PrestoSolverError, match="exactly one target"):
        PrestoSolveRequest.from_solver(solver, input_state)


@pytest.mark.parametrize(
    "subdirs",
    [
        pytest.param(["linux-64"], id="target-only"),
        pytest.param(["linux-64", "noarch"], id="target-and-noarch"),
    ],
)
def test_presto_request_accepts_one_target_subdir(solver_request, subdirs):
    request = msgspec.structs.replace(solver_request, subdirs=subdirs)

    assert request.target_subdir() == "linux-64"


@pytest.mark.parametrize(
    ("offline", "update_modifier", "build_repodata_subset", "message"),
    [
        pytest.param(
            True,
            UpdateModifier.UPDATE_SPECS,
            None,
            "offline mode",
            id="offline",
        ),
        pytest.param(
            False,
            UpdateModifier.UPDATE_DEPS,
            None,
            "--update-deps",
            id="update-deps",
        ),
        pytest.param(
            False,
            UpdateModifier.UPDATE_SPECS,
            object(),
            "repodata subsets",
            id="repodata-subset",
        ),
    ],
)
def test_presto_snapshot_rejects_unsupported_state(
    offline, update_modifier, build_repodata_subset, message
):
    solver = SimpleNamespace(_build_repodata_subset=build_repodata_subset)
    input_state = SimpleNamespace(update_modifier=update_modifier)

    with context._override("offline", offline):
        with pytest.raises(PrestoSolverError, match=message):
            PrestoSolveRequest.from_solver(solver, input_state)


def test_client_posts_to_broker_loopback(monkeypatch, solver_request):
    endpoint = SimpleNamespace(
        host="127.0.0.1",
        protocol="http",
        url="http://127.0.0.1:8765/",
    )
    broker = SimpleNamespace(
        service=lambda name: SimpleNamespace(
            endpoint=lambda *, ready: endpoint if ready else None
        )
    )
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self):
            return msgspec.json.encode(PrestoSolveResponse(records=[], neutered=[]))

    def open_request(request, timeout):
        calls.append((request, timeout))
        return Response()

    monkeypatch.setattr(
        solver_module,
        "Broker",
        SimpleNamespace(current=lambda: broker),
    )
    monkeypatch.setattr(
        PrestoSolverClient,
        "opener",
        SimpleNamespace(open=open_request),
    )

    response = PrestoSolverClient().solve(solver_request, "/tmp/prefix")

    assert response == PrestoSolveResponse(records=[], neutered=[])
    assert calls[0][0].full_url == "http://127.0.0.1:8765/solver/v1"
    assert calls[0][1] == 65


@pytest.mark.parametrize(
    "host, expected",
    [
        pytest.param("localhost", True, id="localhost"),
        pytest.param("::1", True, id="ipv6"),
        pytest.param("127.0.0.1", True, id="ipv4"),
        pytest.param("192.0.2.1", False, id="remote"),
        pytest.param("solver.example.com", False, id="hostname"),
    ],
)
def test_client_accepts_only_loopback_hosts(host, expected):
    assert PrestoSolverClient.is_loopback(host) is expected


def test_client_rejects_non_loopback_broker_endpoint(monkeypatch, solver_request):
    endpoint = SimpleNamespace(
        host="127.0.0.1",
        protocol="http",
        url="http://192.0.2.1:8765/",
    )
    broker = SimpleNamespace(
        service=lambda name: SimpleNamespace(endpoint=lambda *, ready: endpoint)
    )
    monkeypatch.setattr(
        solver_module,
        "Broker",
        SimpleNamespace(current=lambda: broker),
    )

    with pytest.raises(PrestoSolverError, match="ready loopback service"):
        PrestoSolverClient().solve(solver_request, "/tmp/prefix")


def test_client_rejects_redirects_and_environment_proxies(monkeypatch, solver_request):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.path)
            self.rfile.read(int(self.headers.get("content-length", "0")))
            self.send_response(307)
            self.send_header(
                "Location",
                f"http://127.0.0.1:{self.server.server_port}/leak",
            )
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = SimpleNamespace(
        protocol="http",
        url=f"http://127.0.0.1:{server.server_port}/",
    )
    broker = SimpleNamespace(
        service=lambda name: SimpleNamespace(endpoint=lambda *, ready: endpoint)
    )
    monkeypatch.setattr(
        solver_module,
        "Broker",
        SimpleNamespace(current=lambda: broker),
    )

    try:
        with pytest.raises(PrestoSolverError, match="HTTP 307"):
            PrestoSolverClient().solve(solver_request, "/tmp/prefix")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert requests == ["/solver/v1"]
    assert not any(
        isinstance(handler, ProxyHandler)
        for handler in PrestoSolverClient.opener.handlers
    )


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(
            PackagesNotFoundError(["missing"], ["https://example.invalid"]),
            id="packages-not-found",
        ),
        pytest.param(
            RattlerUnsatisfiableError("incompatible specs"),
            id="unsatisfiable",
        ),
    ],
)
def test_client_restores_conda_http_error(monkeypatch, solver_request, error):
    endpoint = SimpleNamespace(
        host="127.0.0.1",
        protocol="http",
        url="http://127.0.0.1:8765/",
    )
    broker = SimpleNamespace(
        service=lambda name: SimpleNamespace(endpoint=lambda *, ready: endpoint)
    )
    monkeypatch.setattr(
        solver_module,
        "Broker",
        SimpleNamespace(current=lambda: broker),
    )
    monkeypatch.setattr(
        PrestoSolverClient,
        "opener",
        SimpleNamespace(
            open=lambda request, timeout: (_ for _ in ()).throw(
                HTTPError(
                    request.full_url,
                    422,
                    "unprocessable",
                    {},
                    BytesIO(
                        msgspec.json.encode(PrestoSolveError.from_exception(error))
                    ),
                ),
            )
        ),
    )

    with pytest.raises(type(error)):
        PrestoSolverClient().solve(solver_request, "/tmp/prefix")


def test_client_surfaces_unstructured_service_http_error(monkeypatch, solver_request):
    endpoint = SimpleNamespace(
        protocol="http",
        url="http://127.0.0.1:8765/",
    )
    broker = SimpleNamespace(
        service=lambda name: SimpleNamespace(endpoint=lambda *, ready: endpoint)
    )
    monkeypatch.setattr(
        solver_module,
        "Broker",
        SimpleNamespace(current=lambda: broker),
    )
    monkeypatch.setattr(
        PrestoSolverClient,
        "opener",
        SimpleNamespace(
            open=lambda request, timeout: (_ for _ in ()).throw(
                HTTPError(
                    request.full_url, 422, "unprocessable", {}, BytesIO(b"bad state")
                )
            )
        ),
    )

    with pytest.raises(PrestoSolverError, match="HTTP 422: bad state"):
        PrestoSolverClient().solve(solver_request, "/tmp/prefix")


def test_client_surfaces_transport_error(monkeypatch, solver_request):
    endpoint = SimpleNamespace(
        protocol="http",
        url="http://127.0.0.1:8765/",
    )
    broker = SimpleNamespace(
        service=lambda name: SimpleNamespace(endpoint=lambda *, ready: endpoint)
    )
    monkeypatch.setattr(
        solver_module,
        "Broker",
        SimpleNamespace(current=lambda: broker),
    )
    monkeypatch.setattr(
        PrestoSolverClient,
        "opener",
        SimpleNamespace(
            open=lambda request, timeout: (_ for _ in ()).throw(
                OSError("connection lost")
            )
        ),
    )

    with pytest.raises(PrestoSolverError, match="connection lost"):
        PrestoSolverClient().solve(solver_request, "/tmp/prefix")


def test_presto_solver_keeps_transaction_work_local(
    monkeypatch, package_record, tmp_path
):
    captured = []

    class InputState:
        installed = {package_record.name: package_record}
        history = {package_record.name: MatchSpec("zlib")}
        pinned = {}
        virtual = {}
        aggressive_updates = {}
        always_update = {}
        update_modifier = UpdateModifier.UPDATE_SPECS
        _update_modifier = "update_specs"
        _deps_modifier = "not_set"
        _command = "install"
        ignore_pinned = False
        force_remove = False
        prune = solver_module.NULL

        @staticmethod
        def channels_from_specs():
            return ()

        @staticmethod
        def maybe_free_channel():
            return ()

    def solve(self, request, prefix):
        captured.append(request)
        return PrestoSolveResponse(
            records=[package_record.dump()],
            neutered=["zlib"],
        )

    monkeypatch.setattr(solver_module, "SolverInputState", lambda **_: InputState())
    monkeypatch.setattr(PrestoSolverClient, "solve", solve)
    solver = PrestoSolver(
        prefix=tmp_path,
        channels=["conda-forge"],
        subdirs=["linux-64"],
        specs_to_add=["zlib"],
        command="install",
    )

    records = solver.solve_final_state()

    assert [record.name for record in records] == ["zlib"]
    assert [spec.name for spec in solver.neutered_specs] == ["zlib"]
    assert captured[0].installed[0]["name"] == "zlib"
    assert captured[0].prune is False


def test_presto_solver_matches_rattler_initialization(tmp_path):
    specs = ["zlib>=1.2", "zlib<2"]
    kwargs = {
        "prefix": tmp_path,
        "channels": ["conda-forge"],
        "subdirs": ["linux-64"],
        "specs_to_add": specs,
        "repodata_fn": "current_repodata.json",
        "command": "install",
    }
    rattler = RattlerSolver(**kwargs)
    presto = PrestoSolver(**kwargs)
    input_state = SimpleNamespace(
        installed={},
        history={},
        pinned={},
        virtual={},
        aggressive_updates={},
        always_update={},
        update_modifier=UpdateModifier.UPDATE_SPECS,
        _update_modifier=UpdateModifier.UPDATE_SPECS,
        _deps_modifier="not_set",
        ignore_pinned=False,
        force_remove=False,
        prune=False,
        _command="install",
        channels_from_specs=lambda: (),
        maybe_free_channel=lambda: (),
    )

    request = PrestoSolveRequest.from_solver(presto, input_state)

    assert presto._repodata_fn == rattler._repodata_fn
    assert request.repodata_fn == rattler._repodata_fn
    assert request.specs_to_add == sorted(
        str(spec) for spec in rattler._unmerged_specs_to_add
    )
    assert len(request.specs_to_add) == 2
    restored = PrestoSolverInputState(request)
    assert str(restored.requested["zlib"]) == str(next(iter(rattler.specs_to_add)))


def test_presto_solver_rejects_conda_build_index(tmp_path):
    solver = PrestoSolver(
        prefix=tmp_path,
        channels=["conda-forge"],
        subdirs=["linux-64"],
        specs_to_add=["zlib"],
        command="install",
    )
    solver._index = {}

    with pytest.raises(PrestoSolverError, match="conda-build"):
        solver.solve_final_state()
