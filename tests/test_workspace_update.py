"""Selective updates retain complete baseline selections through Workspaces."""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from dataclasses import replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conda.base.context import context
from conda.common.serialize.yaml import dumps as yaml_dumps
from conda.core.prefix_data import PrefixData
from conda.models.channel import Channel
from conda.models.environment import Environment, EnvironmentConfig
from conda.models.records import PackageRecord
from conda_workspaces.lockfile import CondaLockLoader, load_lockfile_data

from conda_presto import workspace_lock
from conda_presto.exceptions import WorkspaceSolveError
from conda_presto.inputs import ParsedInputFile
from conda_presto.workspace_lock import WorkspaceLockInput


@pytest.fixture
def baseline(workspace_consistent_lock_path, workspace_consistent_manifest_text):
    return WorkspaceLockInput.from_path(workspace_consistent_lock_path).with_manifest(
        workspace_consistent_manifest_text, "conda.toml"
    )


@pytest.mark.parametrize(
    "environment,platform,packages,reason",
    [
        ("test", "cpu", (), "require"),
        ("", "cpu", ("probe",), "require"),
        ("test", "", ("probe",), "require"),
        ("missing", "cpu", ("probe",), "Unknown workspace environment"),
        ("test", "linux-64", ("probe",), "ambiguous"),
        ("test", "cpu", ("probe>=2",), "exact declared"),
        ("test", "cpu", ("gpu-probe",), "exact declared"),
        ("test", "cpu", ("*",), "exact declared"),
    ],
)
def test_update_requires_exact_direct_roots_and_explicit_target(
    baseline, environment, platform, packages, reason
):
    with pytest.raises(ValueError, match=reason):
        baseline.prepare_update(environment, platform, packages)


def test_update_rejects_stale_unselected_target(baseline):
    baseline.source_data["packages"][1]["depends"] = ["missing-package >=1"]
    with pytest.raises(ValueError, match="Baseline lock is inconsistent"):
        baseline.prepare_update("test", "cpu", ("probe",))


def test_update_uses_bounded_companion_parser(baseline):
    parsed = ParsedInputFile.from_content_until(
        baseline.loader.path.read_text(),
        "conda.lock",
        None,
        time.monotonic() + 30,
        manifest_content=baseline.manifest_content,
        manifest_filename="conda.toml",
        update=("test", "cpu", ("probe",)),
    )
    assert parsed.workspace_update.target.environment == "test"
    assert parsed.workspace_update.target.platform == "cpu"
    assert parsed.workspace_update.packages == ("probe",)
    assert not parsed.workspace_update.lock.loader.path.exists()


@pytest.mark.parametrize("corrupt", [False, True], ids=["complete", "inconsistent"])
def test_update_checks_complete_result_and_preserves_input(
    monkeypatch, baseline, corrupt
):
    original = baseline.loader.path.read_bytes()
    source = copy.deepcopy(baseline.source_data)
    observed = []

    def render(ctx, resolved_envs, **kwargs):
        assert Path(ctx.config.manifest_path).parent.exists()
        assert set(resolved_envs) == {"test"}
        assert kwargs["update_targets"] == {("test", "cpu"): {"probe"}}
        assert "config" not in kwargs
        observed.append((context.subdir, dict(kwargs["baseline_data"])))
        data = copy.deepcopy(kwargs["baseline_data"])
        if corrupt:
            del data["environments"]["default"]["packages"]["gpu"]
        return yaml_dumps(data)

    monkeypatch.setattr(workspace_lock, "render_lockfile", render)
    prepared = baseline.prepare_update("test", "cpu", ("probe",)).configured()
    if corrupt:
        with pytest.raises(WorkspaceSolveError, match="Updated lock is inconsistent"):
            prepared.solve()
    else:
        content, media_type = prepared.solve()
        assert load_lockfile_data(content) == source
        assert media_type == "application/yaml"
    assert len(observed) == 1
    assert observed[0][0] == "linux-64"
    assert baseline.loader.path.read_bytes() == original
    assert baseline.source_data == source


def test_update_settings_are_carried_to_worker_and_cache(monkeypatch, baseline):
    with (
        context._override("offline", True),
        context._override("pinned_packages", ("probe==1.0",)),
    ):
        prepared = baseline.prepare_update("test", "cpu", ("probe",)).configured()
        identity = prepared.cache_identity()
    monkeypatch.setenv("CONDA_OVERRIDE_CUDA", "99")
    monkeypatch.setenv("CONDA_OVERRIDE_GLIBC", "99")
    with context._override("offline", False):
        with prepared.solver_context(baseline.manifest):
            assert context.offline
            assert context.pinned_packages == ("probe==1.0",)
            assert workspace_lock.context.subdir == "linux-64"
            assert os.environ["CONDA_OVERRIDE_CUDA"] == ""
            assert os.environ["CONDA_OVERRIDE_GLIBC"] == "2.28"
        assert not context.offline
    assert prepared.cache_identity() == identity
    for changed in (
        replace(prepared, packages=("different",)),
        replace(prepared, lock=replace(baseline, source_digest="different")),
        replace(prepared, lock=replace(baseline, manifest_digest="different")),
        replace(prepared, settings={**prepared.settings, "offline": False}),
    ):
        assert changed.cache_identity() != identity
    json.dumps(identity)


@pytest.fixture
def update_channel(tmp_path):
    directory = tmp_path / "channel"
    records = {}
    for version in ("1.0", "2.0"):
        filename = f"probe-{version}-h0_0.conda"
        records[filename] = {
            "name": "probe",
            "version": version,
            "build": "h0_0",
            "build_number": 7,
            "depends": ["__glibc >=2.28"],
            "subdir": "linux-64",
            "sha256": version[0] * 64,
            "md5": version[0] * 32,
            "size": 100,
            "timestamp": 1700000000000,
        }
    for subdir in ("linux-64", "noarch"):
        target = directory / subdir
        target.mkdir(parents=True)
        data = {
            "info": {"subdir": subdir},
            "packages": {},
            "packages.conda": records if subdir == "linux-64" else {},
            "removed": [],
            "repodata_version": 1,
        }
        (target / "repodata.json").write_text(json.dumps(data))
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=directory)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", records
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_real_update_preserves_other_pairs_build_numbers_and_prefix_cache(
    monkeypatch, tmp_path, update_channel
):
    channel, records = update_channel
    manifest = f'''[workspace]
name = "probe"
channels = ["{channel}"]
platforms = [
    {{name = "cpu", platform = "linux-64", libc = "2.28"}},
    {{name = "gpu", platform = "linux-64", libc = "2.28", cuda = "12"}},
]
[dependencies]
probe = {{version = ">=1", build-number = ">=7"}}
[environments]
test = []
'''
    filename, metadata = next(iter(records.items()))
    record = PackageRecord(
        **metadata,
        fn=filename,
        url=f"{channel}/linux-64/{filename}",
        channel=Channel(channel),
    )
    envs = []
    for name in ("default", "test"):
        for target in ("cpu", "gpu"):
            env = Environment(
                name=name,
                platform="linux-64",
                config=EnvironmentConfig(channels=(channel,)),
                explicit_packages=[record],
            )
            env.lock_platform = target
            envs.append(env)
    path = tmp_path / "conda.lock"
    path.write_text(yaml_dumps(CondaLockLoader.compose(envs)))
    original = path.read_bytes()
    monkeypatch.setenv("CONDA_OVERRIDE_CUDA", "99")
    monkeypatch.setenv("CONDA_OVERRIDE_GLIBC", "99")
    baseline = WorkspaceLockInput.from_path(path).with_manifest(manifest, "conda.toml")
    cache = tmp_path / "pkgs"
    with (
        context._override("_pkgs_dirs", (str(cache),)),
        context._override("repodata_use_shards", False),
        context._override("offline", False),
        context._override("use_index_cache", False),
        context._override("pinned_packages", ()),
    ):
        prepared = baseline.prepare_update("test", "cpu", ("probe",)).configured()
        cached_prefixes = set(PrefixData._cache_)
        content, _ = prepared.solve()
    updated = load_lockfile_data(content)
    for name, environment in updated["environments"].items():
        for target in environment["packages"]:
            selected = CondaLockLoader.package_records_for_env_data(
                updated, name, target
            )
            assert len(selected) == 1
            assert selected[0].version == (
                "2.0" if (name, target) == ("test", "cpu") else "1.0"
            )
            assert selected[0].build_number == 7
    assert set(PrefixData._cache_) == cached_prefixes
    assert path.read_bytes() == original
    assert not list(cache.rglob("*.conda"))
    assert not list(cache.rglob("*.tar.bz2"))
