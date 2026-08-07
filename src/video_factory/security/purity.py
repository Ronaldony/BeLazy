"""In-process repository purity scanner (read-only observation).

Scans a repository tree for concrete workspace / product identity strings.
Used by the doctor command and by ``tools/check_core_purity.py`` (thin CLI
wrapper). This module never writes files and never launches child processes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Pattern, Sequence

from video_factory.domain import RelativeArtifactPath

from .contracts import PurityFinding


SKIPPED_DIRECTORIES = frozenset(
    {".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__", ".venv"}
)

# Repository-engineering controls and immutable provenance are not product
# inputs.  They necessarily describe the local migration environment and the
# agent runtime, so applying channel/product-neutrality rules to them produces
# false positives.  Keep this allowlist narrow: product architecture,
# governance, source code, tests, and ordinary documentation remain scanned.
NON_PRODUCT_CONTROL_DIRECTORIES = frozenset(
    {".agent", ".agents", ".be-lazy", "." + "co" + "dex", "waves"}
)
NON_PRODUCT_CONTROL_PREFIXES = (
    ("docs", "program"),
    ("docs", "provenance"),
    ("reports", "autopilot"),
)
NON_PRODUCT_CONTROL_FILES = frozenset(
    {
        "AGENTS.md",
        "AUTONOMOUS_DECISION_DEFAULTS.yaml",
        "AUTOPILOT_ARCHITECTURE_KO.md",
        "AUTOPILOT_PROGRAM.yaml",
        "COMPLETION_CONTRACT.md",
        "docs/governance/source-baseline-policy-v1.yaml",
    }
)


def _joined(*parts: str) -> str:
    return "".join(parts)


@dataclass(frozen=True, slots=True)
class ScanRule:
    rule_id: str
    pattern: Pattern[str]


@dataclass(frozen=True, slots=True)
class Violation:
    """CLI-oriented finding (string path) used by the purity gate tool."""

    rule_id: str
    relative_path: str
    line_number: int | None


def _literal_rule(rule_id: str, *parts: str) -> ScanRule:
    return ScanRule(rule_id, re.compile(re.escape(_joined(*parts)), re.IGNORECASE))


def content_rules() -> tuple[ScanRule, ...]:
    episode_prefix = _joined("EP", "90")
    windows_user_prefix = _joined("C", ":", "\\", "Users")
    personal_marker = _joined("wo", "tmd")
    return (
        _literal_rule("channel_identity", "BOSS_", "KIMU"),
        _literal_rule("channel_localized", "보", "스", "킴"),
        _literal_rule("channel_name_variant", "Boss", "Kimu"),
        _literal_rule("creative_pillar_a", "Tiny", " Employees"),
        _literal_rule("creative_pillar_b", "Impossible", " Product", " Tests"),
        _literal_rule("creative_pillar_c", "Object", " Bureaucracy"),
        _literal_rule("concrete_media_adapter_a", "Gr", "ok"),
        _literal_rule("concrete_media_adapter_b", "Kl", "ing"),
        _literal_rule("concrete_task_adapter_a", "Cla", "ude"),
        _literal_rule("concrete_task_adapter_b", "Cod", "ex"),
        ScanRule("pilot_episode", re.compile(rf"\b{episode_prefix}[0-9A-Za-z]\b", re.IGNORECASE)),
        _literal_rule("pilot_stage_a", "p2", "-pilot"),
        _literal_rule("pilot_stage_b", "p3", "-"),
        ScanRule("pilot_stage_family", re.compile(r"\bhm[345]\b", re.IGNORECASE)),
        ScanRule("media_shape", re.compile(r"\b9\s*:\s*16\b", re.IGNORECASE)),
        ScanRule("duration_range", re.compile(r"\b30\s*[~～-]\s*40\b", re.IGNORECASE)),
        _literal_rule("publishing_platform", "You", "Tube"),
        ScanRule("windows_absolute_path", re.compile(r"\b[A-Za-z]:[\\/]")),
        ScanRule("local_user_root", re.compile(re.escape(windows_user_prefix), re.IGNORECASE)),
        ScanRule("personal_user_marker", re.compile(re.escape(personal_marker), re.IGNORECASE)),
        ScanRule(
            "email_address",
            re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
        ),
    )


def opaque_id_branch_rule() -> ScanRule:
    control = r"\b(?:if|elif|case|match)\b"
    identity_names = "|".join(
        (
            _joined("chan", "nel"),
            _joined("prov", "ider"),
            _joined("exec", "utor"),
            _joined("adapt", "er"),
        )
    )
    identity = rf"\b(?:{identity_names})_id\b"
    comparison = r"(?:==|!=|\bin\b)"
    return ScanRule(
        "opaque_id_branch",
        re.compile(control + rf"[^\n]{{0,120}}{identity}\s*{comparison}", re.IGNORECASE),
    )


def _is_non_product_control_path(relative: Path) -> bool:
    relative_text = relative.as_posix()
    if relative_text in NON_PRODUCT_CONTROL_FILES:
        return True
    if relative.parts and relative.parts[0] in NON_PRODUCT_CONTROL_DIRECTORIES:
        return True
    return any(
        relative.parts[: len(prefix)] == prefix
        for prefix in NON_PRODUCT_CONTROL_PREFIXES
    )


def _iter_repository_files(root: Path):
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in SKIPPED_DIRECTORIES for part in relative.parts):
            continue
        if _is_non_product_control_path(relative):
            continue
        if path.is_symlink():
            yield path, relative, None
        elif path.is_file():
            yield path, relative, path.read_bytes()


def scan_repository(root: Path) -> tuple[list[Violation], int]:
    """Scan *root* for purity violations. Read-only; returns findings + file count."""

    rules = content_rules()
    branch_rule = opaque_id_branch_rule()
    violations: list[Violation] = []
    scanned_files = 0

    for path, relative, payload in _iter_repository_files(root):
        relative_text = relative.as_posix()
        if payload is None:
            violations.append(Violation("symlink_not_allowed", relative_text, None))
            continue

        scanned_files += 1
        for rule in rules:
            if rule.pattern.search(relative_text):
                violations.append(Violation(rule.rule_id, relative_text, None))

        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue

        for rule in (*rules, branch_rule):
            for match in rule.pattern.finditer(text):
                line_number = text.count("\n", 0, match.start()) + 1
                violations.append(Violation(rule.rule_id, relative_text, line_number))

    return violations, scanned_files


def violations_to_findings(violations: Sequence[Violation]) -> tuple[PurityFinding, ...]:
    """Map CLI violations to the public :class:`PurityFinding` contract type."""

    return tuple(
        PurityFinding(
            rule_id=item.rule_id,
            relative_path=RelativeArtifactPath(item.relative_path),
            line_number=item.line_number,
        )
        for item in violations
    )


def format_purity_report(
    violations: Sequence[Violation],
    scanned_files: int,
) -> tuple[int, str, str]:
    """Return ``(exit_code, compact_summary_line, full_text)`` matching the CLI gate format."""

    if violations:
        lines: list[str] = []
        for violation in violations:
            location = violation.relative_path
            if violation.line_number is not None:
                location += f":{violation.line_number}"
            lines.append(f"FAIL {violation.rule_id} {location}")
        summary = f"core_purity=FAIL scanned_files={scanned_files} violations={len(violations)}"
        lines.append(summary)
        full = "\n".join(lines)
        return 1, summary, full

    summary = f"core_purity=PASS scanned_files={scanned_files} violations=0"
    return 0, summary, summary


class RepositoryPurityScanner:
    """:class:`~video_factory.security.contracts.CorePurityScanner` implementation."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def scan(self) -> Sequence[PurityFinding]:
        violations, _scanned = scan_repository(self._root)
        return violations_to_findings(violations)
