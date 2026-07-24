from __future__ import annotations

import sys

import pytest
import yaml
from conda.base.context import context
from conda.core.package_cache_data import PackageCacheData, ProgressiveFetchExtract
from conda.exceptions import CondaValueError

from conda_presto.lockfile_transcode import CondaLockfilesTranscoder

CONDA_URL = "https://conda.anaconda.org/conda-forge/linux-64/probe-1.0-h123_0.conda"
OSX_URL = "https://conda.anaconda.org/conda-forge/osx-64/probe-1.0-h123_0.conda"
WHEEL_URL = "https://files.pythonhosted.org/packages/ab/cd/probe-1.0-py3-none-any.whl"

CONDA_LOCK = f"""\
version: 1
metadata:
  channels:
    - url: conda-forge
  platforms:
    - linux-64
package:
  - name: probe
    version: '1.0'
    manager: conda
    platform: linux-64
    dependencies:
      python: '>=3.13'
    url: {CONDA_URL}
    hash:
      sha256: {"a" * 64}
      md5: {"b" * 32}
"""

MULTIPLATFORM_CONDA_LOCK = (
    CONDA_LOCK.replace(
        "  platforms:\n    - linux-64",
        "  platforms:\n    - linux-64\n    - osx-64",
    )
    + f"""\
  - name: probe
    version: '1.0'
    manager: conda
    platform: osx-64
    url: {OSX_URL}
"""
)

RATTLER_LOCK = f"""\
version: 6
environments:
  default:
    channels:
      - url: conda-forge
    packages:
      linux-64:
        - conda: {CONDA_URL}
packages:
  - conda: {CONDA_URL}
    sha256: {"a" * 64}
    md5: {"b" * 32}
    depends:
      - python >=3.13
"""


@pytest.fixture()
def lockfile_loader(tmp_path):
    def load(content, filename):
        path = tmp_path / filename
        path.write_text(content, encoding="utf-8")
        plugin = context.plugin_manager.detect_environment_specifier(str(path))
        specifier = plugin.environment_spec(path)
        assert specifier.can_handle()
        return specifier

    return load


@pytest.mark.parametrize(
    ("source", "filename", "target", "expected_version"),
    [
        pytest.param(
            MULTIPLATFORM_CONDA_LOCK,
            "conda-lock.yml",
            "conda-lock-v1",
            1,
            id="v1-v1",
        ),
        pytest.param(CONDA_LOCK, "conda-lock.yml", "conda-lock", 1, id="v1-alias"),
        pytest.param(
            CONDA_LOCK,
            "conda-lock.yml",
            "rattler-lock-v6",
            6,
            id="v1-v6",
        ),
        pytest.param(CONDA_LOCK, "conda-lock.yml", "pixi", 6, id="v1-pixi"),
        pytest.param(
            CONDA_LOCK,
            "conda-lock.yml",
            "pixi-lock-v6",
            6,
            id="v1-pixi-lock",
        ),
        pytest.param(RATTLER_LOCK, "pixi.lock", "conda-lock-v1", 1, id="v6-v1"),
        pytest.param(RATTLER_LOCK, "pixi.lock", "conda-lock", 1, id="v6-v1-alias"),
        pytest.param(
            RATTLER_LOCK,
            "pixi.lock",
            "rattler-lock-v6",
            6,
            id="v6-v6",
        ),
        pytest.param(RATTLER_LOCK, "pixi.lock", "pixi", 6, id="v6-pixi"),
        pytest.param(
            RATTLER_LOCK,
            "pixi.lock",
            "pixi-lock-v6",
            6,
            id="v6-pixi-lock",
        ),
    ],
)
def test_transcoder_renders_registered_lockfile_formats_without_fetching(
    lockfile_loader,
    monkeypatch,
    source,
    filename,
    target,
    expected_version,
):
    def fail(*_args, **_kwargs):
        raise AssertionError("package fetch or cache lookup attempted")

    monkeypatch.setattr(ProgressiveFetchExtract, "execute", fail)
    monkeypatch.setattr(PackageCacheData, "query_all", fail)
    specifier = lockfile_loader(source, filename)

    rendered = CondaLockfilesTranscoder(specifier).render(
        ("linux-64", "linux-64"),
        format_name=target,
    )

    data = yaml.safe_load(rendered)
    assert data["version"] == expected_version
    if expected_version == 1:
        assert data["metadata"]["platforms"] == ["linux-64"]
        assert len(data["package"]) == 1
    else:
        assert list(data["environments"]["default"]["packages"]) == ["linux-64"]
        assert len(data["packages"]) == 1


@pytest.mark.parametrize(
    ("source", "filename", "platforms", "error"),
    [
        pytest.param(CONDA_LOCK, "conda-lock.yml", (), "At least one", id="v1-empty"),
        pytest.param(
            CONDA_LOCK,
            "conda-lock.yml",
            ("osx-64",),
            "not in lockfile",
            id="v1-missing",
        ),
        pytest.param(
            RATTLER_LOCK,
            "pixi.lock",
            ("osx-64",),
            "not in lockfile",
            id="v6-missing",
        ),
    ],
)
def test_transcoder_rejects_invalid_platform_selection(
    lockfile_loader,
    source,
    filename,
    platforms,
    error,
):
    specifier = lockfile_loader(source, filename)

    with pytest.raises(CondaValueError, match=error):
        CondaLockfilesTranscoder(specifier).render(
            platforms,
            format_name="conda-lock-v1",
        )


@pytest.mark.parametrize(
    ("source", "target", "error"),
    [
        pytest.param(
            CONDA_LOCK.replace("manager: conda", "manager: pypi"),
            "conda-lock-v1",
            "without losing lockfile data",
            id="pypi",
        ),
        pytest.param(
            CONDA_LOCK.replace(
                "platform: linux-64", "platform: linux-64\n    optional: true"
            ),
            "conda-lock-v1",
            "without losing lockfile data",
            id="optional",
        ),
        pytest.param(
            CONDA_LOCK.replace(
                "platform: linux-64", "platform: linux-64\n    category: dev"
            ),
            "conda-lock-v1",
            "without losing lockfile data",
            id="non-main",
        ),
        pytest.param(
            CONDA_LOCK + CONDA_LOCK.split("package:\n", 1)[1],
            "conda-lock-v1",
            "duplicate package entries",
            id="duplicate",
        ),
        pytest.param(
            CONDA_LOCK.replace("name: probe", "name: other"),
            "conda-lock-v1",
            "identity does not match",
            id="name",
        ),
        pytest.param(
            CONDA_LOCK.replace(CONDA_URL, OSX_URL),
            "conda-lock-v1",
            "subdir does not match",
            id="subdir",
        ),
        pytest.param(
            CONDA_LOCK.replace(CONDA_URL, WHEEL_URL),
            "rattler-lock-v6",
            "without losing package identity",
            id="wheel-identity",
        ),
    ],
)
def test_conda_lock_transcode_rejects_lossy_or_inconsistent_packages(
    lockfile_loader,
    source,
    target,
    error,
):
    specifier = lockfile_loader(source, "conda-lock.yml")

    with pytest.raises(CondaValueError, match=error):
        CondaLockfilesTranscoder(specifier).render(
            ("linux-64",),
            format_name=target,
        )


@pytest.mark.parametrize(
    ("source", "target", "error"),
    [
        pytest.param(
            RATTLER_LOCK.replace(
                "  default:\n",
                "  dev:\n"
                "    channels: []\n"
                "    packages:\n"
                "      linux-64: []\n"
                "  default:\n",
            ),
            "rattler-lock-v6",
            "multiple environments",
            id="multiple-environments",
        ),
        pytest.param(
            RATTLER_LOCK.replace("- conda:", "- pypi:"),
            "rattler-lock-v6",
            "PyPI packages",
            id="pypi",
        ),
        pytest.param(
            RATTLER_LOCK.split("\npackages:\n", 1)[0] + "\npackages: []\n",
            "rattler-lock-v6",
            "missing from the packages list",
            id="dangling",
        ),
        pytest.param(
            RATTLER_LOCK.replace(
                f"packages:\n  - conda: {CONDA_URL}",
                f"packages:\n  - pypi: {CONDA_URL}",
            ),
            "rattler-lock-v6",
            "missing from the packages list",
            id="manager-mismatch",
        ),
        pytest.param(
            RATTLER_LOCK + f"  - conda: {CONDA_URL}\n",
            "rattler-lock-v6",
            "duplicate package metadata",
            id="duplicate-metadata",
        ),
        pytest.param(
            RATTLER_LOCK.replace(
                f"        - conda: {CONDA_URL}",
                f"        - conda: {CONDA_URL}\n        - conda: {CONDA_URL}",
            ),
            "rattler-lock-v6",
            "duplicate package references",
            id="duplicate-reference",
        ),
        pytest.param(
            RATTLER_LOCK.replace(CONDA_URL, WHEEL_URL),
            "conda-lock-v1",
            "without package metadata",
            id="wheel-identity",
        ),
        pytest.param(
            RATTLER_LOCK + "    constrains:\n      - python <3.14\n",
            "conda-lock-v1",
            "losing solver metadata",
            id="constraints",
        ),
        pytest.param(
            RATTLER_LOCK.replace(
                "      - python >=3.13",
                "      - python >=3.10\n      - python <3.14",
            ),
            "conda-lock-v1",
            "duplicate dependency names",
            id="duplicate-dependency-name",
        ),
        pytest.param(
            RATTLER_LOCK.replace("python >=3.13", "zlib 1.3 h123_0"),
            "conda-lock-v1",
            "dependency selectors",
            id="dependency-build",
        ),
        pytest.param(
            RATTLER_LOCK.replace(
                f"    md5: {'b' * 32}",
                f"    md5: {'b' * 32}\n"
                "    run_exports:\n"
                "      strong:\n"
                "        - zlib >=1.3",
            ),
            "rattler-lock-v6",
            "package.run_exports",
            id="unsupported-package-field",
        ),
        pytest.param(
            RATTLER_LOCK.replace(
                "      - url: conda-forge\n    packages:",
                "      - url: conda-forge\n"
                "    indexes:\n"
                "      - https://pypi.org/simple\n"
                "    packages:",
            ),
            "rattler-lock-v6",
            "environment.indexes",
            id="unsupported-environment-field",
        ),
        pytest.param(
            RATTLER_LOCK.replace(CONDA_URL, WHEEL_URL).replace(
                "    depends:",
                "    name: other\n    depends:",
            ),
            "rattler-lock-v6",
            "package.name",
            id="wheel-identity-field",
        ),
        pytest.param(
            RATTLER_LOCK.split("\npackages:\n", 1)[0] + "\npackages:\n  - {}\n",
            "rattler-lock-v6",
            "metadata must identify exactly one",
            id="metadata-without-manager",
        ),
        pytest.param(
            RATTLER_LOCK.replace(f"- conda: {CONDA_URL}", "- {}", 1),
            "rattler-lock-v6",
            "references must identify exactly one",
            id="reference-without-manager",
        ),
    ],
)
def test_rattler_lock_transcode_rejects_lossy_or_inconsistent_packages(
    lockfile_loader,
    source,
    target,
    error,
):
    specifier = lockfile_loader(source, "pixi.lock")

    with pytest.raises(CondaValueError, match=error):
        CondaLockfilesTranscoder(specifier).render(
            ("linux-64",),
            format_name=target,
        )


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("https://example.com/noarch/bad.conda", id="archive"),
        pytest.param("https://example.com/probe-1.0.conda", id="inexact-archive"),
        pytest.param(
            "https://files.pythonhosted.org/packages/ab/cd/bad.whl",
            id="wheel",
        ),
        pytest.param(
            "https://files.pythonhosted.org/packages/ab/cd/"
            "probe-1.0-cp313-cp313-manylinux_x86_64.whl",
            id="platform-wheel",
        ),
    ],
)
def test_transcoder_rejects_unusable_package_urls(url):
    with pytest.raises(CondaValueError, match="Unable to reconstruct"):
        CondaLockfilesTranscoder.records_for_export(
            {url: {}},
            platform="linux-64",
        )


def test_transcoder_only_requires_wheel_parser_for_wheels(monkeypatch):
    monkeypatch.setitem(sys.modules, "installer.utils", None)

    records = CondaLockfilesTranscoder.records_for_export(
        {CONDA_URL: {}},
        platform="linux-64",
    )

    assert records[0].name == "probe"
    with pytest.raises(CondaValueError, match="Wheel parsing support is unavailable"):
        CondaLockfilesTranscoder.records_for_export(
            {WHEEL_URL: {}},
            platform="linux-64",
        )


def test_transcoder_accepts_rattler_package_without_dependencies(lockfile_loader):
    specifier = lockfile_loader(
        RATTLER_LOCK.replace("    depends:\n      - python >=3.13\n", ""),
        "pixi.lock",
    )

    rendered = CondaLockfilesTranscoder(specifier).render(
        ("linux-64",),
        format_name="conda-lock-v1",
    )

    assert yaml.safe_load(rendered)["package"][0]["dependencies"] == {}


@pytest.mark.parametrize(
    ("source", "filename", "target"),
    [
        pytest.param(
            CONDA_LOCK.replace("url: conda-forge", "url: conda-pypi").replace(
                CONDA_URL,
                WHEEL_URL,
            ),
            "conda-lock.yml",
            "conda-lock-v1",
            id="conda-lock",
        ),
        pytest.param(
            RATTLER_LOCK.replace("url: conda-forge", "url: conda-pypi").replace(
                CONDA_URL,
                WHEEL_URL,
            ),
            "pixi.lock",
            "rattler-lock-v6",
            id="rattler-lock",
        ),
    ],
)
def test_transcoder_preserves_conda_pypi_wheel_metadata(
    lockfile_loader,
    source,
    filename,
    target,
):
    specifier = lockfile_loader(source, filename)

    rendered = CondaLockfilesTranscoder(specifier).render(
        ("linux-64",),
        format_name=target,
    )

    data = yaml.safe_load(rendered)
    if target == "conda-lock-v1":
        package = data["package"][0]
        assert package["name"] == "probe"
        assert package["version"] == "1.0"
        assert package["url"] == WHEEL_URL
    else:
        package = data["packages"][0]
        assert package["conda"] == WHEEL_URL


def test_transcoder_ignores_other_lockfile_plugins(lockfile_loader):
    specifier = lockfile_loader(CONDA_LOCK, "conda-lock.yml")

    assert (
        CondaLockfilesTranscoder(specifier).render(
            ("linux-64",),
            format_name="explicit",
        )
        is None
    )
    assert (
        CondaLockfilesTranscoder(object()).render(
            ("linux-64",),
            format_name="conda-lock-v1",
        )
        is None
    )
