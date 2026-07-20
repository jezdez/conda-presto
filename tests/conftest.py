"""Shared fixtures for conda-presto tests."""

from __future__ import annotations

from typing import Any

import pytest
from conda.models.channel import Channel
from conda.models.records import PackageRecord

from conda_presto.resolve import RepodataSnapshot, ResolvedPackage, SolveResult
from conda_presto.solver import PrestoSolveRequest


@pytest.fixture()
def sample_channel():
    return Channel("conda-forge/linux-64")


@pytest.fixture()
def make_package_record(sample_channel):
    """Factory fixture returning a PackageRecord with sensible defaults."""

    def _make(
        name: str = "zlib",
        version: str = "1.3.1",
        build: str = "h68df207_2",
        build_number: int = 2,
        subdir: str = "linux-64",
        sha256: str = "abc123",
        md5: str = "def456",
        size: int = 102400,
        depends: tuple[str, ...] = ("libgcc-ng >=12",),
        constrains: tuple[str, ...] = (),
    ) -> PackageRecord:
        return PackageRecord(
            name=name,
            version=version,
            build=build,
            build_number=build_number,
            channel=sample_channel,
            subdir=subdir,
            fn=f"{name}-{version}-{build}.conda",
            url=f"https://conda.anaconda.org/conda-forge/{subdir}/{name}-{version}-{build}.conda",
            sha256=sha256,
            md5=md5,
            size=size,
            depends=depends,
            constrains=constrains,
        )

    return _make


@pytest.fixture()
def sample_record(make_package_record):
    return make_package_record()


@pytest.fixture()
def minimal_record(sample_channel):
    """PackageRecord with no optional fields (sha256, md5, size)."""
    return PackageRecord(
        name="minimal",
        version="1.0",
        build="h0",
        build_number=0,
        channel=sample_channel,
        subdir="linux-64",
        fn="minimal-1.0-h0.conda",
        depends=(),
        constrains=(),
    )


@pytest.fixture()
def sample_resolved_package():
    return ResolvedPackage(
        name="zlib",
        version="1.3.1",
        build="h68df207_2",
        build_number=2,
        channel="conda-forge",
        subdir="linux-64",
        url="https://conda.anaconda.org/conda-forge/linux-64/zlib-1.3.1-h68df207_2.conda",
        sha256="abc123",
        md5="def456",
        size=102400,
        depends=("libgcc-ng >=12",),
        constrains=(),
    )


@pytest.fixture()
def sample_solve_result(sample_resolved_package):
    return SolveResult(
        platform="linux-64",
        packages=[sample_resolved_package],
    )


@pytest.fixture()
def make_presto_solver_request():
    def create(**changes: Any) -> PrestoSolveRequest:
        values = {
            "channels": [Channel("conda-forge").dump()],
            "subdirs": ["linux-64", "noarch"],
            "specs_to_add": ["zlib"],
            "specs_to_remove": [],
            "installed": [],
            "history": [],
            "pinned": [],
            "virtual": [],
            "aggressive_updates": [],
            "always_update": [],
            "update_modifier": "UPDATE_SPECS",
            "deps_modifier": "NOT_SET",
            "ignore_pinned": False,
            "force_remove": False,
            "prune": False,
            "command": "install",
            "repodata_fn": "repodata.json",
            "local_repodata_ttl": 300,
            "offline": False,
            "channel_priority": "strict",
            "use_only_tar_bz2": False,
            "add_pip_as_python_dependency": True,
            "allow_cycles": True,
            "restore_free_channel": False,
            "repodata_use_shards": True,
            "use_index_cache": False,
        }
        values.update(changes)
        return PrestoSolveRequest(**values)

    return create


@pytest.fixture()
def presto_solver_request(make_presto_solver_request):
    return make_presto_solver_request()


@pytest.fixture()
def fresh_repodata_snapshot():
    return RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 10, 1),),
        False,
    )


@pytest.fixture()
def many_records(make_package_record):
    """100 distinct PackageRecords for benchmark fixtures."""
    return [
        make_package_record(
            name=f"pkg-{i:03d}",
            version=f"1.0.{i}",
            build=f"h0_{i}",
            build_number=i,
            depends=(f"dep-a >={i}", f"dep-b >={i}"),
            constrains=(f"conflict-{i} <2",),
        )
        for i in range(100)
    ]


@pytest.fixture()
def environment_yml_bytes():
    return b"""\
name: test
channels:
  - conda-forge
dependencies:
  - python=3.12
  - numpy
  - pandas
"""


@pytest.fixture()
def environment_yml_path(tmp_path, environment_yml_bytes):
    path = tmp_path / "environment.yml"
    path.write_bytes(environment_yml_bytes)
    return path


@pytest.fixture()
def pixi_lock_v6_text():
    return """\
version: 6
environments:
  default:
    channels: []
    packages:
      linux-64:
        - conda:
            https://conda.anaconda.org/conda-forge/linux-64/libzlib-1.3.2-h25fd6f3_2.conda
        - conda:
            https://conda.anaconda.org/conda-forge/linux-64/zlib-1.3.2-h25fd6f3_2.conda
packages:
  - conda:
      https://conda.anaconda.org/conda-forge/linux-64/libzlib-1.3.2-h25fd6f3_2.conda
    sha256: 55044c403570f0dc26e6364de4dc5368e5f3fc7ff103e867c487e2b5ab2bcda9
    md5: d87ff7921124eccd67248aa483c23fec
    license: Zlib
    license_family: Other
    size: 63629
    depends:
      - __glibc >=2.17,<3.0.a0
    constrains:
      - zlib 1.3.2 *_2
  - conda:
      https://conda.anaconda.org/conda-forge/linux-64/zlib-1.3.2-h25fd6f3_2.conda
    sha256: 245c9ee8d688e23661b95e3c6dd7272ca936fabc03d423cdb3cdee1bbcf9f2f2
    md5: c2a01a08fc991620a74b32420e97868a
    license: Zlib
    license_family: Other
    size: 95931
    depends:
      - __glibc >=2.17,<3.0.a0
      - libzlib 1.3.2 h25fd6f3_2
"""
