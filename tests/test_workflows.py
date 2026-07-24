"""Tests for release workflow security boundaries."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def test_release_dispatches_docker_from_final_tag():
    text = (WORKFLOWS / "release.yml").read_text()
    publish_job = text.split("  publish-github-release:\n", 1)[1]

    assert '- "v[0-9]+.[0-9]+.[0-9]+"' in text
    assert "environment: pypi" in text
    assert "actions: write" in publish_job
    assert 'gh release edit "$RELEASE_TAG"' in publish_job
    assert "gh workflow run docker.yml" in publish_job
    assert '--ref "$RELEASE_TAG"' in publish_job


def test_release_build_has_no_oidc_permissions():
    text = (WORKFLOWS / "release.yml").read_text()
    build_job = text.split("  build:\n", 1)[1].split("  attest:\n", 1)[0]
    attest_job = text.split("  attest:\n", 1)[1].split("  create-draft-release:\n", 1)[
        0
    ]

    assert "id-token: write" not in build_job
    assert "actions/attest@" not in build_job
    assert "python -m build --no-isolation" in build_job
    assert "id-token: write" in attest_job
    assert "actions/attest@" in attest_job


def test_docker_publish_uses_dispatched_tag():
    text = (WORKFLOWS / "docker.yml").read_text()
    publish_job = text.split("  build-and-push:\n", 1)[1].split(
        "  attest-images:\n", 1
    )[0]

    assert "workflow_dispatch:" in text
    assert (
        "RELEASE_TAG: ${{ github.event_name == 'pull_request' && '0.0.0' || "
        "github.ref_name }}"
    ) in text
    assert "needs: [smoke-server, smoke-cli, scan-arm64]" in publish_job
    assert "provenance: mode=max" in publish_job
    assert "sbom: true" in publish_job
    assert "environment: ghcr" in publish_job
    assert "type=sha,prefix=,suffix=${{ matrix.suffix }}" in publish_job
    assert "SOURCE_COMMIT: ${{ github.sha }}" in publish_job


@pytest.mark.parametrize(
    ("job", "next_job"),
    [
        pytest.param("smoke-server", "smoke-space", id="server-amd64"),
        pytest.param("smoke-space", "smoke-cli", id="space-amd64"),
        pytest.param("smoke-cli", "scan-arm64", id="cli-amd64"),
        pytest.param("scan-arm64", "metadata-smoke", id="published-arm64"),
    ],
)
def test_docker_builds_record_and_block_vulnerabilities(job, next_job):
    text = (WORKFLOWS / "docker.yml").read_text()
    scan_job = text.split(f"  {job}:\n", 1)[1].split(f"  {next_job}:\n", 1)[0]
    trivy = "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25"

    assert scan_job.count(trivy) == 2
    assert scan_job.count("scanners: vuln") == 2
    assert scan_job.count('exit-code: "0"') == 1
    assert scan_job.count("format: sarif") == 1
    assert scan_job.count('exit-code: "1"') == 1
    assert scan_job.count("ignore-unfixed: true") == 1
    assert scan_job.count("severity: HIGH,CRITICAL") == 1
    assert scan_job.count("trivyignores: .trivyignore.yaml") == 1


def test_arm64_scan_builds_server_and_cli_images():
    text = (WORKFLOWS / "docker.yml").read_text()
    arm_job = text.split("  scan-arm64:\n", 1)[1].split("  metadata-smoke:\n", 1)[0]

    assert "platforms: linux/arm64" in arm_job
    assert "target: server" in arm_job
    assert "target: cli" in arm_job
    assert (
        "docker/setup-qemu-action@96fe6ef7f33517b61c61be40b68a1882f3264fb8" in arm_job
    )


@pytest.mark.parametrize(
    ("advisory", "statement", "expiry"),
    [
        pytest.param(
            "GHSA-36hh-v3qg-5jq4",
            "PyList iterator nth or nth_back",
            "2026-09-30",
            id="pyo3-iterator",
        ),
        pytest.param(
            "GHSA-4w2j-m93h-cj5j",
            "SubdirData and passes local SparseRepoData to "
            "rattler.solve_with_sparse_repodata",
            "2026-08-31",
            id="quinn-receive-stream",
        ),
    ],
)
def test_trivy_exceptions_are_narrow_and_expire(advisory, statement, expiry):
    ignore = (ROOT / ".trivyignore.yaml").read_text()
    exception = ignore.split(f"- id: {advisory}", 1)[1].split("\n  - id:", 1)[0]

    assert ".pixi/envs/prod/" in exception
    assert ".pixi/envs/cli/" in exception
    assert statement in exception
    assert f"expired_at: {expiry}" in exception


def test_cli_image_is_scanned_before_publish():
    text = (WORKFLOWS / "docker.yml").read_text()
    smoke_job = text.split("  smoke-cli:\n", 1)[1].split("  scan-arm64:\n", 1)[0]

    assert "target: cli" in smoke_job
    assert "image-ref: conda-presto:cli-smoke" in smoke_job
    assert "docker run --rm conda-presto:cli-smoke --help" in smoke_job


def test_docker_publish_refuses_existing_immutable_tags():
    text = (WORKFLOWS / "docker.yml").read_text()
    publish_job = text.split("  build-and-push:\n", 1)[1].split(
        "  attest-images:\n", 1
    )[0]

    assert '"$IMAGE:$version$SUFFIX"' in publish_job
    assert '"$IMAGE:$short_commit$SUFFIX"' in publish_job
    assert "Immutable image tag already exists" in publish_job
    assert "subject-version:" not in text


def test_lockfile_update_tool_has_no_write_token():
    text = (WORKFLOWS / "update-lockfile.yml").read_text()
    update_job = text.split("  update:\n", 1)[1].split("  create-pull-request:\n", 1)[0]
    create_job = text.split("  create-pull-request:\n", 1)[1]

    assert "pixi-diff-to-markdown" not in update_job
    assert "pixi update --no-install" in update_job
    assert "contents: write" not in update_job
    assert "GH_TOKEN:" not in update_job
    assert "contents: write" in create_job
    assert "GH_TOKEN:" in create_job


def test_codeql_actions_use_commit_shas():
    text = (WORKFLOWS / "codeql.yml").read_text()
    revisions = re.findall(r"uses: github/codeql-action/(?:init|analyze)@([^ ]+)", text)

    assert len(revisions) == 2
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in revisions)
    assert "name: Analyze ${{ matrix.name }}" in text
    assert "- language: python" in text
    assert "name: Python" in text
    assert "- language: actions" in text
    assert "name: GitHub Actions" in text
    assert "languages: ${{ matrix.language }}" in text


@pytest.mark.parametrize(
    "path",
    [
        "action.yml",
        ".github/workflows/ci.yml",
        ".github/workflows/docs.yml",
        ".github/workflows/release.yml",
        ".github/workflows/solver-smoke.yml",
        ".github/workflows/update-lockfile.yml",
    ],
)
def test_setup_pixi_uses_pinned_version(path):
    text = (ROOT / path).read_text()
    setup_count = text.count("prefix-dev/setup-pixi@")

    assert setup_count > 0
    assert text.count("pixi-version: v0.70.1") == setup_count


def test_dev_server_disables_uvicorn_access_log():
    text = (ROOT / "pyproject.toml").read_text()

    assert (
        'serve = { cmd = "uvicorn conda_presto.app:app --reload --no-access-log"'
        in text
    )
