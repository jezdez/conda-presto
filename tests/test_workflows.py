"""Tests for release workflow security boundaries."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
REQUIRED_CHECKS = (
    "Lint",
    "Test",
    "Broker lifecycle",
    "Analyze Python",
    "Analyze GitHub Actions",
)


@pytest.mark.parametrize("workflow", ["release.yml", "docker.yml"])
def test_publish_workflows_use_release_tag_verifier(workflow):
    text = (WORKFLOWS / workflow).read_text()

    assert "checks: read" in text
    assert "run: bash .github/scripts/verify-release-tag" in text


def test_release_tag_verifier_requires_a_signed_checked_commit_on_main():
    text = (ROOT / ".github" / "scripts" / "verify-release-tag").read_text()

    assert "GitHub could not verify the release tag signature" in text
    assert 'git merge-base --is-ancestor "${tagged_commit}" origin/main' in text
    assert 'git cat-file -t "refs/tags/${RELEASE_TAG}"' in text
    assert 'echo "commit=${tagged_commit}"' in text
    assert '.tag == $tag' in text
    assert "check-runs?filter=latest&per_page=100" in text
    assert '.app.slug == "github-actions"' in text
    assert "all($matches[]" in text
    assert '.conclusion == "success"' in text
    for check_name in REQUIRED_CHECKS:
        assert f'"{check_name}"' in text


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


def test_docker_publish_requires_release_verification():
    text = (WORKFLOWS / "docker.yml").read_text()
    verify_job = text.split("  verify-release:\n", 1)[1].split(
        "  smoke-server:\n", 1
    )[0]
    publish_job = text.split("  build-and-push:\n", 1)[1].split(
        "  attest-images:\n", 1
    )[0]

    assert (
        "ref: ${{ github.event_name == 'pull_request' && github.ref || "
        "github.event.repository.default_branch }}"
    ) in verify_job
    assert "needs: [smoke-server, smoke-cli, scan-arm64, verify-release]" in publish_job
    assert "Docker images require a published final GitHub release" in text
    assert "provenance: mode=max" in publish_job
    assert "sbom: true" in publish_job
    assert "environment: ghcr" in publish_job
    assert "needs.verify-release.outputs.short_commit" in publish_job
    assert text.count("needs.verify-release.outputs.commit") == 4
    assert "type=sha" not in publish_job
    assert text.count("needs: [verify-release]") == 4


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


def test_trivy_exception_is_narrow_and_expires():
    ignore = (ROOT / ".trivyignore.yaml").read_text()
    assert "GHSA-36hh-v3qg-5jq4" in ignore
    assert "rattler/rattler.abi3.so" in ignore
    assert ".pixi/envs/cli/" in ignore
    assert "PyList iterator nth or nth_back" in ignore
    assert "expired_at: 2026-09-30" in ignore


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
    assert '"$IMAGE:$SHORT_COMMIT$SUFFIX"' in publish_job
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


def test_workflows_install_a_digest_verified_pixi():
    installer = (ROOT / ".github" / "scripts" / "install-pixi").read_text()

    assert "version=v0.70.1" in installer
    assert (
        "https://github.com/prefix-dev/pixi/releases/download/${version}/${asset}"
        in installer
    )
    assert "--proto '=https'" in installer
    assert "--proto-redir '=https'" in installer
    assert '.createHash("sha256")' in installer
    assert '"${actual_digest}" != "${expected_digest}"' in installer
    assert len(re.findall(r"expected_digest=[0-9a-f]{64}", installer)) == 6

    paths = [ROOT / "action.yml", *WORKFLOWS.glob("*.yml")]

    for path in paths:
        text = path.read_text()
        assert "pixi-version:" not in text
        if setup_count := text.count("prefix-dev/setup-pixi@"):
            assert text.count("run: bash .github/scripts/install-pixi") == setup_count


def test_dev_server_disables_uvicorn_access_log():
    text = (ROOT / "pyproject.toml").read_text()

    assert (
        'serve = { cmd = "uvicorn conda_presto.app:app --reload --no-access-log"'
        in text
    )
