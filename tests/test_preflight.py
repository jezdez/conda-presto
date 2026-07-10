"""Tests for no-solver preflight validation."""

from __future__ import annotations

import msgspec

from conda_presto.preflight import PreflightResult


def test_preflight_reports_spec_and_channel_findings():
    result = PreflightResult.from_values(
        ["numpy=1.26.4", "numpy=1.26.4", "python==3.13=cpython_0", "zlib"],
        ["conda-forge", "https://conda.anaconda.org/conda-forge"],
    )

    assert {finding.code for finding in result.findings} == {
        "PIN001",
        "PIN002",
        "PIN003",
        "DUP001",
        "CHN002",
    }
    assert result.ok
    assert result.summary.errors == 0


def test_preflight_reports_content_and_parse_errors():
    result = PreflightResult.from_values(
        [],
        [],
        "prefix: /tmp/demo  \n \tdependencies:\n",
        "input file is invalid",
    )

    assert {finding.code for finding in result.findings} == {
        "ENV001",
        "ENV002",
        "FMT001",
        "FMT002",
    }
    assert not result.ok


def test_preflight_requires_input():
    result = PreflightResult.from_values([], [])

    assert not result.ok
    assert result.findings[0].code == "ENV001"


def test_preflight_reports_invalid_matchspec_without_null_locations():
    result = PreflightResult.from_values(["numpy ["], [])
    finding = msgspec.json.decode(msgspec.json.encode(result))["findings"][0]

    assert finding["code"] == "SPC001"
    assert "line" not in finding
