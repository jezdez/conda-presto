"""Tests for conda_presto.migrate.

These tests exercise the migration from the end user's perspective:
a customer hands us a pip spec file and a target platform, and we
tell them what's available, what's not, and whether it solves.

Tests hit real conda-forge repodata.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conda_presto.migrate import migrate

FIXTURES = Path(__file__).parent / "fixtures"


class TestDataScienceMigration:
    """Customer migrating a data science stack to conda-forge.

    This is the most common enterprise scenario: numpy, pandas,
    scikit-learn, etc.  All should be available and solve cleanly.
    """

    @pytest.fixture()
    def result(self):
        content = (FIXTURES / "data_science_requirements.txt").read_text()
        return migrate(content, channels=["conda-forge"], platform="linux-64")

    def test_all_packages_available(self, result):
        for pkg in result.packages:
            assert pkg.status == "available", (
                f"{pkg.pip_name} should be available on conda-forge"
            )

    def test_name_translations(self, result):
        """Verify pip names with known conda equivalents get translated."""
        by_pip = {p.pip_name: p for p in result.packages}
        assert by_pip["opencv-python"].conda_name == "opencv"
        assert by_pip["Pillow"].conda_name == "pillow"
        assert by_pip["PyYAML"].conda_name == "pyyaml"

    def test_solves_cleanly(self, result):
        assert result.solve_success is True
        assert result.solve_error is None

    def test_format_detected(self, result):
        assert result.source_format == "requirements"


class TestMixedAvailability:
    """Customer with a mix of conda-available and pip-only packages.

    The tool should identify which are unavailable and still solve
    the available ones.
    """

    @pytest.fixture()
    def result(self):
        content = (FIXTURES / "mixed_availability.txt").read_text()
        return migrate(content, channels=["conda-forge"], platform="linux-64")

    def test_standard_packages_available(self, result):
        by_pip = {p.pip_name: p for p in result.packages}
        assert by_pip["numpy"].status == "available"
        assert by_pip["pandas"].status == "available"
        assert by_pip["requests"].status == "available"

    def test_pip_only_packages_flagged(self, result):
        by_pip = {p.pip_name: p for p in result.packages}
        assert by_pip["outerbounds"].status == "unavailable"
        assert by_pip["metaflow-card-html"].status == "unavailable"

    def test_solves_with_available_packages_only(self, result):
        """Should solve successfully after excluding unavailable packages."""
        assert result.solve_success is True

    def test_reports_coverage(self, result):
        available = [p for p in result.packages if p.status == "available"]
        total = len(result.packages)
        assert len(available) < total, "Some packages should be unavailable"
        assert len(available) > 0, "Some packages should be available"


class TestPyprojectToml:
    """Customer migrating from a pyproject.toml-managed project."""

    @pytest.fixture()
    def result(self):
        content = (FIXTURES / "ml_project_pyproject.toml").read_text()
        return migrate(content, channels=["conda-forge"], platform="linux-64")

    def test_format_detected(self, result):
        assert result.source_format == "pyproject"

    def test_parses_all_dependencies(self, result):
        names = [p.pip_name for p in result.packages]
        assert "numpy" in names
        assert "pandas" in names
        assert "scikit-learn" in names

    def test_solves_or_identifies_unavailable(self, result):
        """Either everything solves, or unavailable packages are flagged."""
        if result.solve_success:
            return
        # If solve fails, every package should still have a status
        for pkg in result.packages:
            assert pkg.status in ("available", "unavailable")


class TestCrossPlatform:
    """Customer solving for a different platform than their workstation.

    Common scenario: developer on macOS generating an environment.yml
    for a linux-64 production server.
    """

    def test_solve_for_linux_from_any_host(self):
        content = "numpy>=1.24\npandas>=2.0\nrequests>=2.28\n"
        result = migrate(
            content, channels=["conda-forge"], platform="linux-64"
        )
        assert result.platform == "linux-64"
        assert result.solve_success is True

    def test_solve_for_osx_arm64(self):
        content = "numpy>=1.24\npandas>=2.0\n"
        result = migrate(
            content, channels=["conda-forge"], platform="osx-arm64"
        )
        assert result.platform == "osx-arm64"
        assert result.solve_success is True


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_empty_requirements(self):
        result = migrate("# just comments\n", channels=["conda-forge"])
        assert result.packages == []
        assert result.solve_success is True

    def test_all_unavailable(self):
        content = "totally-fake-package-xyz>=1.0\n"
        result = migrate(content, channels=["conda-forge"], platform="linux-64")
        assert result.packages[0].status == "unavailable"
        assert result.solve_success is False

    def test_version_constraints_preserved(self):
        content = "numpy>=1.24,<2.0\npandas==2.1.0\n"
        result = migrate(content, channels=["conda-forge"], platform="linux-64")
        by_pip = {p.pip_name: p for p in result.packages}
        # packaging normalizes specifier order; check both bounds present
        assert ">=1.24" in by_pip["numpy"].pip_version_spec
        assert "<2.0" in by_pip["numpy"].pip_version_spec
        assert by_pip["pandas"].pip_version_spec == "==2.1.0"


# === environment.yml output tests ===


class TestEnvironmentYml:
    """Tests for the rendered environment.yml output."""

    def test_mixed_produces_valid_yaml(self):
        """Mixed availability produces conda deps + pip section."""
        import yaml

        content = (FIXTURES / "mixed_availability.txt").read_text()
        result = migrate(content, channels=["conda-forge"], platform="linux-64")
        yml = result.environment_yml
        assert yml != ""
        parsed = yaml.safe_load(yml)
        assert parsed["name"] == "migrated"
        assert parsed["channels"] == ["conda-forge"]
        assert "dependencies" in parsed
        # pip section is a dict inside the dependencies list
        pip_entries = [d for d in parsed["dependencies"] if isinstance(d, dict)]
        assert len(pip_entries) == 1
        assert "pip" in pip_entries[0]

    def test_all_available_no_pip_section(self):
        """When everything is on conda, no pip section in output."""
        content = "numpy>=1.24\nrequests>=2.28\n"
        result = migrate(content, channels=["conda-forge"], platform="linux-64")
        yml = result.environment_yml
        assert "dependencies:" in yml
        assert "pip:" not in yml
        assert "pip\n" not in yml

    def test_all_unavailable_only_pip_section(self):
        """When nothing is on conda, output has only pip section."""
        content = "totally-fake-package-xyz>=1.0\n"
        result = migrate(content, channels=["conda-forge"], platform="linux-64")
        yml = result.environment_yml
        assert "  - pip\n" in yml
        assert "  - pip:" in yml
        assert "    - totally-fake-package-xyz>=1.0" in yml

    def test_empty_input_produces_valid_yaml(self):
        """Empty input produces valid YAML with empty dependencies."""
        import yaml

        result = migrate("# just comments\n", channels=["conda-forge"])
        parsed = yaml.safe_load(result.environment_yml)
        assert parsed["name"] == "migrated"
        assert parsed["dependencies"] == []

    def test_conda_packages_use_conda_name_with_space(self):
        """Conda section uses translated conda name with space before spec."""
        content = "numpy>=1.24\nPyYAML>=6.0\n"
        result = migrate(content, channels=["conda-forge"], platform="linux-64")
        yml = result.environment_yml
        # conda section has space between name and version spec
        assert "  - numpy >=1.24" in yml
        assert "  - pyyaml >=6.0" in yml

    def test_pip_packages_use_original_pip_name(self):
        """Pip section uses original pip name, not conda name."""
        content = (FIXTURES / "mixed_availability.txt").read_text()
        result = migrate(content, channels=["conda-forge"], platform="linux-64")
        yml = result.environment_yml
        assert "    - outerbounds>=0.3" in yml
        assert "    - metaflow-card-html>=1.0" in yml

    def test_channels_included(self):
        """Output includes channels section."""
        content = "numpy>=1.24\n"
        result = migrate(content, channels=["conda-forge"], platform="linux-64")
        yml = result.environment_yml
        assert "channels:" in yml
        assert "  - conda-forge" in yml

    def test_environment_yml_always_populated(self):
        """environment_yml is never empty string on a valid migration."""
        content = "numpy>=1.24\n"
        result = migrate(content, channels=["conda-forge"], platform="linux-64")
        assert result.environment_yml
        assert "name:" in result.environment_yml

    def test_python_included_when_in_input(self):
        """Python is in output only if the input specifies it."""
        content = "python>=3.10\nnumpy>=1.24\n"
        result = migrate(content, channels=["conda-forge"], platform="linux-64")
        assert "python" in result.environment_yml

    def test_python_not_injected_when_absent(self):
        """Python is NOT injected if the input doesn't specify it."""
        content = "numpy>=1.24\n"
        result = migrate(content, channels=["conda-forge"], platform="linux-64")
        # numpy should be there, python should not be forced
        assert "numpy" in result.environment_yml
        lines = result.environment_yml.splitlines()
        dep_lines = [l.strip() for l in lines if l.strip().startswith("- python")]
        assert dep_lines == []


# === cf-graph mapping tests ===


class TestNameMapping:
    """Tests for cf-graph-countyfair name mapping."""

    def test_known_divergent_names(self):
        """Packages with non-obvious conda names get mapped correctly."""
        from conda_presto.migrate.name_mapping import get_conda_name

        assert get_conda_name("opencv-python") == "opencv"
        assert get_conda_name("tables") == "pytables"
        assert get_conda_name("apache-airflow") == "airflow"

    def test_normalization_handles_simple_cases(self):
        """Lowercase + underscore→hyphen handles most packages."""
        from conda_presto.migrate.name_mapping import get_conda_name

        assert get_conda_name("PyYAML") == "pyyaml"
        assert get_conda_name("Pillow") == "pillow"
        assert get_conda_name("Flask") == "flask"

    def test_extras_stripped(self):
        """Extras notation is stripped before lookup."""
        from conda_presto.migrate.name_mapping import get_conda_name

        assert get_conda_name("requests[security]") == "requests"

    def test_unknown_package_falls_through(self):
        """Unknown packages return normalized name."""
        from conda_presto.migrate.name_mapping import get_conda_name

        assert get_conda_name("my-internal-tool") == "my-internal-tool"

    def test_static_fallback_when_cf_unavailable(self, monkeypatch):
        """Static mapping works even if cf-graph fetch fails."""
        import conda_presto.migrate.name_mapping as nm

        monkeypatch.setattr(nm, "_cf_mapping", {})
        monkeypatch.setattr(nm, "_cf_fetch_failed", True)
        assert nm.get_conda_name("sklearn") == "scikit-learn"
        assert nm.get_conda_name("bs4") == "beautifulsoup4"


# === CLI tests ===


class TestMigrateCLI:
    """Tests for ``conda presto migrate`` subcommand."""

    def _run_cli(self, *args):
        """Run the CLI and return (stdout, stderr, returncode)."""
        cmd = [
            sys.executable, "-m", "conda_presto.cli",
            "--override-channels", "-c", "conda-forge",
        ] + list(args)
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
        return result.stdout, result.stderr, result.returncode

    def test_migrate_default_output_is_yaml(self):
        req_file = FIXTURES / "mixed_availability.txt"
        stdout, stderr, rc = self._run_cli(
            "--migrate", "-f", str(req_file), "-p", "linux-64"
        )
        assert rc == 0
        assert "name: migrated" in stdout
        assert "channels:" in stdout
        assert "dependencies:" in stdout
        assert "- pip:" in stdout
        assert "outerbounds" in stdout
        # Summary goes to stderr
        assert "conda" in stderr
        assert "pip-only" in stderr or "pip fallback" in stderr

    def test_migrate_json_flag(self):
        req_file = FIXTURES / "mixed_availability.txt"
        stdout, _, rc = self._run_cli(
            "--migrate", "-f", str(req_file), "-p", "linux-64", "--json"
        )
        assert rc == 0
        data = json.loads(stdout)
        statuses = {p["pip_name"]: p["status"] for p in data[0]["packages"]}
        assert statuses["numpy"] == "available"
        assert statuses["outerbounds"] == "unavailable"
        assert data[0]["environment_yml"] is not None

    def test_migrate_platform_flag(self):
        req_file = FIXTURES / "data_science_requirements.txt"
        stdout, _, rc = self._run_cli(
            "--migrate", "-f", str(req_file), "-p", "osx-arm64", "--json"
        )
        assert rc == 0
        data = json.loads(stdout)
        assert data[0]["platform"] == "osx-arm64"

    def test_migrate_no_file(self):
        _, stderr, rc = self._run_cli("--migrate")
        assert rc == 1
        assert "Usage" in stderr or "-f" in stderr

    def test_existing_resolve_still_works(self):
        """Existing resolve behavior must not regress."""
        stdout, _, rc = self._run_cli("-p", "linux-64", "zlib")
        assert rc == 0
        data = json.loads(stdout)
        assert isinstance(data, list)
        assert data[0]["platform"] == "linux-64"
        names = [p["name"] for p in data[0]["packages"]]
        assert "zlib" in names


# === HTTP endpoint tests ===


class TestMigrateHTTP:
    """Tests for ``POST /migrate`` endpoint."""

    @pytest.fixture()
    def client(self):
        from litestar.testing import TestClient
        from conda_presto.app import app
        with TestClient(app=app) as c:
            yield c

    def test_post_migrate_json_body(self, client):
        resp = client.post("/migrate", json={
            "file": "numpy>=1.24\npandas>=2.0\n",
            "channels": ["conda-forge"],
            "platform": "linux-64",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["platform"] == "linux-64"
        assert data["solve_success"] is True
        assert len(data["packages"]) == 2

    def test_post_migrate_raw_text_body(self, client):
        resp = client.post(
            "/migrate",
            content="numpy>=1.24\nrequests>=2.28\n",
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["solve_success"] is True

    def test_post_migrate_channel_override(self, client):
        resp = client.post("/migrate", json={
            "file": "numpy>=1.24\n",
            "channels": ["conda-forge"],
            "platform": "linux-64",
        })
        assert resp.status_code == 200
        assert resp.json()["solve_success"] is True

    def test_post_migrate_platform_override(self, client):
        resp = client.post("/migrate", json={
            "file": "numpy>=1.24\npandas>=2.0\n",
            "channels": ["conda-forge"],
            "platform": "osx-arm64",
        })
        assert resp.status_code == 200
        assert resp.json()["platform"] == "osx-arm64"

    def test_post_migrate_invalid_input(self, client):
        resp = client.post(
            "/migrate",
            content="hello",
            headers={"Content-Type": "application/xml"},
        )
        assert resp.status_code == 400
        assert "Unsupported" in resp.json()["error"]

    def test_post_migrate_empty_body(self, client):
        resp = client.post("/migrate", json={})
        assert resp.status_code == 400

    def test_cli_and_api_identical_result(self, client):
        """CLI and API should produce the same MigrationResult."""
        content = "numpy>=1.24\npandas>=2.0\n"

        # API result
        resp = client.post("/migrate", json={
            "file": content,
            "channels": ["conda-forge"],
            "platform": "linux-64",
        })
        api_result = resp.json()

        # Library result (same as what CLI --json produces)
        lib_result = migrate(content, channels=["conda-forge"], platform="linux-64")

        assert api_result["platform"] == lib_result.platform
        assert api_result["solve_success"] == lib_result.solve_success
        assert len(api_result["packages"]) == len(lib_result.packages)
        for api_pkg, lib_pkg in zip(api_result["packages"], lib_result.packages):
            assert api_pkg["pip_name"] == lib_pkg.pip_name
            assert api_pkg["status"] == lib_pkg.status


# === Unavailability explanation tests ===


class TestUnavailabilityReasons:
    """Tests for classifying WHY a package is unavailable."""

    def test_version_not_available_reason(self):
        """Package exists but pinned version doesn't."""
        content = "numpy==0.0.1\n"
        result = migrate(content, channels=["conda-forge"], platform="linux-64")
        pkg = result.packages[0]
        assert pkg.status == "unavailable"
        assert pkg.reason == "version_not_available"

    def test_version_not_available_reports_alternatives(self):
        """Version gap includes what IS available."""
        content = "numpy==0.0.1\n"
        result = migrate(content, channels=["conda-forge"], platform="linux-64")
        pkg = result.packages[0]
        assert pkg.available_versions is not None
        assert len(pkg.available_versions) > 0

    def test_not_in_conda_reason(self):
        """Truly pip-only package doesn't exist on any channel."""
        content = "outerbounds>=0.3\n"
        result = migrate(content, channels=["conda-forge"], platform="linux-64")
        pkg = result.packages[0]
        assert pkg.status == "unavailable"
        assert pkg.reason == "not_in_conda"
        assert pkg.available_versions is None

    def test_wrong_arch_reason(self):
        """Package exists on other platforms but not target."""
        content = "cuda-toolkit>=12.0\n"
        result = migrate(content, channels=["conda-forge"], platform="osx-arm64")
        pkg = result.packages[0]
        assert pkg.status == "unavailable"
        assert pkg.reason == "wrong_arch"

    def test_available_packages_have_no_reason(self):
        """Available packages should not have a reason set."""
        content = "numpy>=1.24\nrequests>=2.28\n"
        result = migrate(content, channels=["conda-forge"], platform="linux-64")
        for pkg in result.packages:
            assert pkg.status == "available"
            assert pkg.reason is None
            assert pkg.available_versions is None
