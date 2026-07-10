"""No-solver validation for conda-presto inputs."""

from __future__ import annotations

import msgspec
from conda.models.channel import Channel
from conda.models.match_spec import MatchSpec


class Finding(msgspec.Struct, omit_defaults=True):
    """One deterministic preflight finding."""

    code: str
    severity: str
    message: str
    spec: str | None = None
    line: int | None = None


class FindingSummary(msgspec.Struct):
    """Counts for each preflight finding severity."""

    errors: int
    warnings: int
    info: int


class PreflightResult(msgspec.Struct):
    """Validation findings for a resolve request or environment file."""

    ok: bool
    findings: list[Finding]
    summary: FindingSummary

    @classmethod
    def from_values(
        cls,
        specs: list[str],
        channels: list[str],
        content: str | None = None,
        parse_error: str | None = None,
    ) -> PreflightResult:
        """Run local deterministic checks without a solver or network access."""
        findings: list[Finding] = []
        if content is not None:
            for line_number, line in enumerate(content.splitlines(), start=1):
                indentation = line[: len(line) - len(line.lstrip(" \t"))]
                if " " in indentation and "\t" in indentation:
                    findings.append(
                        Finding(
                            code="FMT001",
                            severity="info",
                            message="tabs and spaces are mixed in indentation",
                            line=line_number,
                        )
                    )
                if line.rstrip() != line:
                    findings.append(
                        Finding(
                            code="FMT002",
                            severity="info",
                            message="trailing whitespace",
                            line=line_number,
                        )
                    )
                if line.lstrip().startswith("prefix:"):
                    findings.append(
                        Finding(
                            code="ENV002",
                            severity="warning",
                            message="prefix is not portable in an environment file",
                            line=line_number,
                        )
                    )

        if parse_error is not None:
            findings.append(
                Finding(
                    code="ENV001",
                    severity="error",
                    message=parse_error,
                )
            )
        if not specs and content is None:
            findings.append(
                Finding(
                    code="ENV001",
                    severity="error",
                    message="Provide specs or file content",
                )
            )

        canonical_specs: set[str] = set()
        for value in specs:
            try:
                spec = MatchSpec(value)
            except Exception as exc:
                findings.append(
                    Finding(
                        code="SPC001",
                        severity="error",
                        message=str(exc),
                        spec=value,
                    )
                )
                continue

            canonical = str(spec)
            if canonical in canonical_specs:
                findings.append(
                    Finding(
                        code="DUP001",
                        severity="warning",
                        message="duplicate conda package spec",
                        spec=value,
                    )
                )
            canonical_specs.add(canonical)

            version = spec.get("version")
            if version is None:
                findings.append(
                    Finding(
                        code="PIN002",
                        severity="info",
                        message="package has no version or range",
                        spec=value,
                    )
                )
            elif version.endswith(".*"):
                findings.append(
                    Finding(
                        code="PIN001",
                        severity="warning",
                        message=(
                            "fuzzy equality may be unintended; use == for an exact pin"
                        ),
                        spec=value,
                    )
                )
            if spec.get("build") is not None:
                findings.append(
                    Finding(
                        code="PIN003",
                        severity="info",
                        message="build pin may reduce portability",
                        spec=value,
                    )
                )

        canonical_channels: set[str] = set()
        for value in channels:
            try:
                canonical = Channel(value).canonical_name
            except Exception:
                continue
            if canonical in canonical_channels:
                findings.append(
                    Finding(
                        code="CHN002",
                        severity="warning",
                        message="duplicate channel",
                        spec=value,
                    )
                )
            canonical_channels.add(canonical)

        summary = FindingSummary(
            errors=sum(finding.severity == "error" for finding in findings),
            warnings=sum(finding.severity == "warning" for finding in findings),
            info=sum(finding.severity == "info" for finding in findings),
        )
        return cls(
            ok=summary.errors == 0,
            findings=findings,
            summary=summary,
        )
