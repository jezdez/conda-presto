"""Pure-function environment file linter.

Validates ``environment.yml`` files against a set of built-in rules
covering pinning hygiene, channel configuration, formatting, and
structural requirements.  All checks are static: no network calls,
solver invocations, or conda imports.  Designed for sub-50 ms execution
on typical environment files.

The public entry point is :func:`lint`, which accepts raw file content
and returns a :class:`LintResult` containing individual :class:`Finding`
objects grouped by severity.  Rules can be selectively ignored via the
*ignore* parameter, and findings can be filtered by minimum severity
via *min_severity*.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable

import msgspec

SEVERITY_ORDER: dict[str, int] = {"info": 0, "warning": 1, "error": 2}

RuleFn = Callable[[list[str], list[str], list[str]], list["Finding"]]


class Finding(msgspec.Struct):
    """A single lint finding with a machine-readable code and optional fix."""

    code: str
    severity: str
    line: int | None
    message: str
    fix: str | None = None


class LintSummary(msgspec.Struct):
    """Aggregate counts by severity level."""

    errors: int
    warnings: int
    info: int


class LintResult(msgspec.Struct):
    """Complete lint output: individual findings plus a severity summary."""

    findings: list[Finding]
    summary: LintSummary


def _extract_block_items(lines: list[str], header: str) -> list[tuple[int, str]]:
    """Return ``(line_number, value)`` pairs for a YAML list block.

    Finds the first line matching *header* (e.g. ``dependencies:``) and
    collects subsequent indented ``- value`` lines until the indentation
    drops back.  Only collects items at the block's own indentation
    level, skipping deeper sub-blocks (e.g. ``pip:`` entries).
    Line numbers are 1-based.
    """
    items: list[tuple[int, str]] = []
    in_block = False
    block_indent: int | None = None
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped == header or stripped == f"{header} ":
            in_block = True
            continue
        if in_block:
            if not raw or raw[0] not in (" ", "\t"):
                break
            match = re.match(r"^(\s+)-\s+(.*)", raw)
            if match:
                indent = len(match.group(1))
                if block_indent is None:
                    block_indent = indent
                if indent == block_indent:
                    items.append((i + 1, match.group(2).strip()))
    return items


def _parse_deps(lines: list[str]) -> list[tuple[int, str]]:
    return _extract_block_items(lines, "dependencies:")


def _parse_channels(lines: list[str]) -> list[tuple[int, str]]:
    return _extract_block_items(lines, "channels:")


def _is_pip_block_item(dep: str) -> bool:
    return dep.startswith("pip:") or dep == "pip"


_SPEC_RE = re.compile(r"^([a-zA-Z0-9_][a-zA-Z0-9_.\-]*)(.*)$")


def _rule_pin001(
    lines: list[str], parsed_specs: list[str], parsed_channels: list[str]
) -> list[Finding]:
    """PIN001: single ``=`` instead of ``==`` in version pin."""
    findings: list[Finding] = []
    for lineno, dep in _parse_deps(lines):
        if _is_pip_block_item(dep):
            continue
        m = _SPEC_RE.match(dep)
        if not m:
            continue
        rest = m.group(2)
        if re.match(r"^=[^=<>!]", rest):
            name = m.group(1)
            version = rest[1:]
            findings.append(
                Finding(
                    code="PIN001",
                    severity="warning",
                    line=lineno,
                    message=f"Single '=' in spec '{dep}'; use '==' for exact pins",
                    fix=f"{name}=={version}",
                )
            )
    return findings


def _rule_pin002(
    lines: list[str], parsed_specs: list[str], parsed_channels: list[str]
) -> list[Finding]:
    """PIN002: spec with no version constraint."""
    findings: list[Finding] = []
    for lineno, dep in _parse_deps(lines):
        if _is_pip_block_item(dep):
            continue
        m = _SPEC_RE.match(dep)
        if not m:
            continue
        rest = m.group(2).strip()
        if not rest:
            findings.append(
                Finding(
                    code="PIN002",
                    severity="warning",
                    line=lineno,
                    message=f"No version constraint on '{dep}'",
                )
            )
    return findings


def _rule_dup001(
    lines: list[str], parsed_specs: list[str], parsed_channels: list[str]
) -> list[Finding]:
    """DUP001: duplicate package name in specs."""
    findings: list[Finding] = []
    names: list[str] = []
    line_map: dict[str, list[int]] = {}
    for lineno, dep in _parse_deps(lines):
        if _is_pip_block_item(dep):
            continue
        m = _SPEC_RE.match(dep)
        if not m:
            continue
        name = m.group(1).lower()
        names.append(name)
        line_map.setdefault(name, []).append(lineno)

    counts = Counter(names)
    for name, count in counts.items():
        if count > 1:
            first_line = line_map[name][0]
            findings.append(
                Finding(
                    code="DUP001",
                    severity="warning",
                    line=first_line,
                    message=(f"Package '{name}' appears {count} times in dependencies"),
                )
            )
    return findings


def _rule_chn001(
    lines: list[str], parsed_specs: list[str], parsed_channels: list[str]
) -> list[Finding]:
    """CHN001: channels not sorted alphabetically."""
    if len(parsed_channels) < 2:
        return []
    if parsed_channels != sorted(parsed_channels):
        return [
            Finding(
                code="CHN001",
                severity="info",
                line=None,
                message="Channels are not sorted alphabetically",
            )
        ]
    return []


def _rule_chn002(
    lines: list[str], parsed_specs: list[str], parsed_channels: list[str]
) -> list[Finding]:
    """CHN002: duplicate channel."""
    findings: list[Finding] = []
    seen: set[str] = set()
    for lineno, ch in _parse_channels(lines):
        if ch in seen:
            findings.append(
                Finding(
                    code="CHN002",
                    severity="warning",
                    line=lineno,
                    message=f"Duplicate channel '{ch}'",
                )
            )
        seen.add(ch)
    return findings


def _rule_ord001(
    lines: list[str], parsed_specs: list[str], parsed_channels: list[str]
) -> list[Finding]:
    """ORD001: dependencies not sorted alphabetically."""
    dep_names: list[str] = []
    for _lineno, dep in _parse_deps(lines):
        if _is_pip_block_item(dep):
            continue
        m = _SPEC_RE.match(dep)
        if m:
            dep_names.append(m.group(1).lower())

    if len(dep_names) < 2:
        return []
    if dep_names != sorted(dep_names):
        return [
            Finding(
                code="ORD001",
                severity="info",
                line=None,
                message="Dependencies are not sorted alphabetically",
            )
        ]
    return []


def _rule_fmt001(
    lines: list[str], parsed_specs: list[str], parsed_channels: list[str]
) -> list[Finding]:
    """FMT001: mixed tabs and spaces."""
    has_tabs = any("\t" in line for line in lines)
    has_spaces = any(line.startswith(" ") for line in lines)
    if has_tabs and has_spaces:
        return [
            Finding(
                code="FMT001",
                severity="info",
                line=None,
                message="Mixed indentation: tabs and spaces in the same file",
            )
        ]
    return []


def _rule_fmt002(
    lines: list[str], parsed_specs: list[str], parsed_channels: list[str]
) -> list[Finding]:
    """FMT002: trailing whitespace."""
    findings: list[Finding] = []
    for i, line in enumerate(lines):
        if line != line.rstrip():
            findings.append(
                Finding(
                    code="FMT002",
                    severity="info",
                    line=i + 1,
                    message="Trailing whitespace",
                )
            )
    return findings


def _rule_env001(
    lines: list[str], parsed_specs: list[str], parsed_channels: list[str]
) -> list[Finding]:
    """ENV001: dependencies block but no name field."""
    has_deps = any(line.strip().startswith("dependencies:") for line in lines)
    has_name = any(re.match(r"^name\s*:", line) for line in lines)
    if has_deps and not has_name:
        return [
            Finding(
                code="ENV001",
                severity="error",
                line=None,
                message="File has 'dependencies:' but no 'name:' field",
            )
        ]
    return []


def _rule_env002(
    lines: list[str], parsed_specs: list[str], parsed_channels: list[str]
) -> list[Finding]:
    """ENV002: non-portable prefix field."""
    for i, line in enumerate(lines):
        if re.match(r"^prefix\s*:", line):
            return [
                Finding(
                    code="ENV002",
                    severity="warning",
                    line=i + 1,
                    message="'prefix:' field is not portable across machines",
                )
            ]
    return []


RULES: dict[str, RuleFn] = {
    "PIN001": _rule_pin001,
    "PIN002": _rule_pin002,
    "DUP001": _rule_dup001,
    "CHN001": _rule_chn001,
    "CHN002": _rule_chn002,
    "ORD001": _rule_ord001,
    "FMT001": _rule_fmt001,
    "FMT002": _rule_fmt002,
    "ENV001": _rule_env001,
    "ENV002": _rule_env002,
}


def lint(
    content: str,
    filename: str | None = None,
    ignore: set[str] | None = None,
    min_severity: str | None = None,
) -> LintResult:
    """Lint an environment file and return structured findings.

    *content* is the raw file text.  *filename* is currently unused but
    reserved for future format-specific rules.  *ignore* is a set of
    rule codes to skip.  *min_severity* filters findings below the
    given threshold (``"info"``, ``"warning"``, or ``"error"``).
    """
    lines = content.splitlines()
    ignore = ignore or set()

    dep_items = _parse_deps(lines)
    channel_items = _parse_channels(lines)
    parsed_specs = [dep for _, dep in dep_items if not _is_pip_block_item(dep)]
    parsed_channels = [ch for _, ch in channel_items]

    findings: list[Finding] = []
    for code, rule_fn in RULES.items():
        if code in ignore:
            continue
        findings.extend(rule_fn(lines, parsed_specs, parsed_channels))

    if min_severity is not None:
        threshold = SEVERITY_ORDER.get(min_severity, 0)
        findings = [
            f for f in findings if SEVERITY_ORDER.get(f.severity, 0) >= threshold
        ]

    findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 0), reverse=True)

    summary = LintSummary(
        errors=sum(1 for f in findings if f.severity == "error"),
        warnings=sum(1 for f in findings if f.severity == "warning"),
        info=sum(1 for f in findings if f.severity == "info"),
    )
    return LintResult(findings=findings, summary=summary)
