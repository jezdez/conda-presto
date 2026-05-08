"""Tests for conda_presto.lint environment file linter."""

from __future__ import annotations

import pytest

from conda_presto.lint import (
    RULES,
    Finding,
    LintResult,
    LintSummary,
    lint,
)

CLEAN_ENV = """\
name: test
channels:
  - conda-forge
dependencies:
  - numpy==1.26.4
  - python>=3.12
"""

NO_FINDINGS_CASES = [
    pytest.param("", id="empty-string"),
    pytest.param("name: test\n", id="name-only"),
    pytest.param(CLEAN_ENV, id="clean-environment-yml"),
]


@pytest.mark.parametrize("content", NO_FINDINGS_CASES)
def test_clean_file_produces_no_findings(content):
    result = lint(content)
    assert result.findings == []
    assert result.summary == LintSummary(errors=0, warnings=0, info=0)


@pytest.mark.parametrize(
    "spec, expected_fix",
    [
        pytest.param("numpy=1.26.4", "numpy==1.26.4", id="simple"),
        pytest.param("scipy=1.12", "scipy==1.12", id="short-version"),
    ],
)
def test_pin001_single_equals(spec, expected_fix):
    content = f"name: t\ndependencies:\n  - {spec}\n"
    result = lint(content)
    pin001 = [f for f in result.findings if f.code == "PIN001"]
    assert len(pin001) == 1
    assert pin001[0].severity == "warning"
    assert pin001[0].fix == expected_fix
    assert pin001[0].line == 3


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param("numpy==1.26.4", id="double-equals"),
        pytest.param("numpy>=1.26", id="gte"),
        pytest.param("numpy<2", id="lt"),
    ],
)
def test_pin001_not_triggered(spec):
    content = f"name: t\ndependencies:\n  - {spec}\n"
    result = lint(content)
    assert not [f for f in result.findings if f.code == "PIN001"]


def test_pin002_bare_package():
    content = "name: t\ndependencies:\n  - numpy\n  - scipy\n"
    result = lint(content)
    pin002 = [f for f in result.findings if f.code == "PIN002"]
    assert len(pin002) == 2
    names = {f.message for f in pin002}
    assert "No version constraint on 'numpy'" in names
    assert "No version constraint on 'scipy'" in names


def test_pin002_not_triggered_with_constraint():
    content = "name: t\ndependencies:\n  - numpy>=1.26\n"
    result = lint(content)
    assert not [f for f in result.findings if f.code == "PIN002"]


def test_pin002_skips_pip():
    content = "name: t\ndependencies:\n  - pip\n"
    result = lint(content)
    assert not [f for f in result.findings if f.code == "PIN002"]


def test_dup001_duplicate_package():
    content = "name: t\ndependencies:\n  - numpy>=1.26\n  - numpy>=1.25\n"
    result = lint(content)
    dup = [f for f in result.findings if f.code == "DUP001"]
    assert len(dup) == 1
    assert "2 times" in dup[0].message


def test_dup001_no_duplicates():
    content = "name: t\ndependencies:\n  - numpy>=1.26\n  - scipy>=1.12\n"
    result = lint(content)
    assert not [f for f in result.findings if f.code == "DUP001"]


def test_chn001_unsorted_channels():
    content = "name: t\nchannels:\n  - defaults\n  - conda-forge\n"
    result = lint(content)
    chn = [f for f in result.findings if f.code == "CHN001"]
    assert len(chn) == 1
    assert chn[0].severity == "info"


def test_chn001_sorted_channels():
    content = "name: t\nchannels:\n  - conda-forge\n  - defaults\n"
    result = lint(content)
    assert not [f for f in result.findings if f.code == "CHN001"]


def test_chn002_duplicate_channel():
    content = (
        "name: t\nchannels:\n  - conda-forge\n  - conda-forge\n"
        "dependencies:\n  - python>=3.12\n"
    )
    result = lint(content)
    dup = [f for f in result.findings if f.code == "CHN002"]
    assert len(dup) == 1
    assert dup[0].line is not None


def test_chn002_no_duplicates():
    content = "name: t\nchannels:\n  - conda-forge\n  - defaults\n"
    result = lint(content)
    assert not [f for f in result.findings if f.code == "CHN002"]


def test_ord001_unsorted_deps():
    content = "name: t\ndependencies:\n  - scipy>=1.12\n  - numpy>=1.26\n"
    result = lint(content)
    ord_findings = [f for f in result.findings if f.code == "ORD001"]
    assert len(ord_findings) == 1
    assert ord_findings[0].severity == "info"


def test_ord001_sorted_deps():
    content = "name: t\ndependencies:\n  - numpy>=1.26\n  - scipy>=1.12\n"
    result = lint(content)
    assert not [f for f in result.findings if f.code == "ORD001"]


def test_fmt001_mixed_indentation():
    content = "name: t\n  spaces: yes\n\ttabs: yes\n"
    result = lint(content)
    fmt = [f for f in result.findings if f.code == "FMT001"]
    assert len(fmt) == 1
    assert fmt[0].severity == "info"


def test_fmt001_consistent_indentation():
    content = "name: t\n  all: spaces\n  here: too\n"
    result = lint(content)
    assert not [f for f in result.findings if f.code == "FMT001"]


def test_fmt002_trailing_whitespace():
    content = "name: t  \ndependencies:\n  - numpy>=1.26\n"
    result = lint(content)
    fmt = [f for f in result.findings if f.code == "FMT002"]
    assert len(fmt) == 1
    assert fmt[0].line == 1


def test_fmt002_no_trailing_whitespace():
    content = "name: t\ndependencies:\n  - numpy>=1.26\n"
    result = lint(content)
    assert not [f for f in result.findings if f.code == "FMT002"]


def test_env001_missing_name():
    content = "dependencies:\n  - numpy>=1.26\n"
    result = lint(content)
    env = [f for f in result.findings if f.code == "ENV001"]
    assert len(env) == 1
    assert env[0].severity == "error"


def test_env001_has_name():
    content = "name: test\ndependencies:\n  - numpy>=1.26\n"
    result = lint(content)
    assert not [f for f in result.findings if f.code == "ENV001"]


def test_env002_prefix_field():
    content = "name: t\nprefix: /home/user/envs/test\ndependencies:\n  - numpy>=1.26\n"
    result = lint(content)
    env = [f for f in result.findings if f.code == "ENV002"]
    assert len(env) == 1
    assert env[0].severity == "warning"
    assert env[0].line == 2


def test_env002_no_prefix():
    content = "name: t\ndependencies:\n  - numpy>=1.26\n"
    result = lint(content)
    assert not [f for f in result.findings if f.code == "ENV002"]


@pytest.mark.parametrize(
    "ignored, expected_absent",
    [
        pytest.param({"PIN002"}, {"PIN002"}, id="single-rule"),
        pytest.param({"PIN002", "ORD001"}, {"PIN002", "ORD001"}, id="multiple-rules"),
    ],
)
def test_ignore_filtering(ignored, expected_absent):
    content = "name: t\ndependencies:\n  - scipy\n  - numpy\n"
    result = lint(content, ignore=ignored)
    found_codes = {f.code for f in result.findings}
    assert not found_codes & expected_absent


@pytest.mark.parametrize(
    "min_severity, excluded_severities",
    [
        pytest.param("warning", {"info"}, id="min-warning"),
        pytest.param("error", {"info", "warning"}, id="min-error"),
        pytest.param("info", set(), id="min-info-keeps-all"),
    ],
)
def test_severity_filtering(min_severity, excluded_severities):
    content = "dependencies:\n  - scipy\n  - numpy\nprefix: /tmp/env\n"
    result = lint(content, min_severity=min_severity)
    found_severities = {f.severity for f in result.findings}
    assert not found_severities & excluded_severities


def test_lint_summary_counts():
    content = "dependencies:\n  - scipy\n  - numpy\nprefix: /tmp/env\n"
    result = lint(content)
    assert result.summary.errors >= 1
    assert result.summary.warnings >= 1


def test_findings_sorted_by_severity():
    content = "dependencies:\n  - scipy\n  - numpy\nprefix: /tmp/env\n"
    result = lint(content)
    severity_order = {"error": 2, "warning": 1, "info": 0}
    severities = [severity_order[f.severity] for f in result.findings]
    assert severities == sorted(severities, reverse=True)


def test_lint_returns_lint_result_type():
    result = lint("")
    assert isinstance(result, LintResult)
    assert isinstance(result.summary, LintSummary)


def test_all_rules_registered():
    expected = {
        "PIN001",
        "PIN002",
        "DUP001",
        "CHN001",
        "CHN002",
        "ORD001",
        "FMT001",
        "FMT002",
        "ENV001",
        "ENV002",
    }
    assert set(RULES.keys()) == expected


def test_finding_struct_fields():
    f = Finding(code="X", severity="info", line=1, message="test")
    assert f.fix is None
    f2 = Finding(code="X", severity="info", line=1, message="test", fix="y")
    assert f2.fix == "y"


def test_lint_with_filename_kwarg():
    result = lint("name: t\n", filename="environment.yml")
    assert result.findings == []


def test_pin001_inside_pip_block_not_flagged():
    content = (
        "name: t\ndependencies:\n  - pip:\n    - mypackage=1.0\n  - python>=3.12\n"
    )
    result = lint(content)
    pin001 = [f for f in result.findings if f.code == "PIN001"]
    assert not pin001


def test_deps_block_ends_at_next_toplevel_key():
    content = "name: t\ndependencies:\n  - numpy>=1.26\nchannels:\n  - conda-forge\n"
    result = lint(content)
    deps_findings = [
        f for f in result.findings if f.code in ("PIN001", "PIN002", "DUP001")
    ]
    assert not deps_findings
