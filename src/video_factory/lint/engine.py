"""Generic verbatim / source-lock lint evaluation (Finding reuse from qc)."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

from video_factory.domain import HashDigest, OpaqueId
from video_factory.qc import Finding, Measurement

from .rules import LintRule, LintRuleSet, SourceLockRule, VerbatimRule


class LintError(ValueError):
    """Raised when a rule or document cannot be evaluated without guessing."""


@dataclass(frozen=True, slots=True)
class LintReport:
    """Deterministic report for a document evaluated against injected rules."""

    findings: tuple[Finding, ...]
    document_field: str | None = None

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.findings)

    def failing(self) -> tuple[Finding, ...]:
        return tuple(item for item in self.findings if not item.passed)


def run_lint(
    document: Mapping[str, Any],
    rules: LintRuleSet | Sequence[LintRule],
    *,
    source_root: Path | str | None = None,
) -> LintReport:
    """Evaluate *document* against injected *rules*.

    Parameters
    ----------
    document:
        Parsed JSON-like mapping (e.g. a generation-packet body).
    rules:
        Channel-owned rule data. Core never embeds channel text.
    source_root:
        Root used to resolve ``source_path`` for source-lock and verbatim
        source-drift checks. Required when any rule references a source file.
    """

    rule_seq: Sequence[LintRule]
    if isinstance(rules, LintRuleSet):
        rule_seq = rules.rules
    else:
        rule_seq = rules

    root = Path(source_root) if source_root is not None else None
    findings: list[Finding] = []
    for rule in rule_seq:
        if isinstance(rule, VerbatimRule):
            findings.extend(_eval_verbatim(document, rule, root))
        elif isinstance(rule, SourceLockRule):
            findings.append(_eval_source_lock(rule, root))
        else:
            raise LintError(f"unsupported rule type: {type(rule).__name__}")
    return LintReport(findings=tuple(findings))


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 JSON Pointer against *document*."""

    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise LintError(f"JSON Pointer must start with '/': {pointer!r}")
    current: Any = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                raise LintError(f"JSON Pointer path not found: {pointer} (missing {token!r})")
            current = current[token]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            if not token.isdigit():
                raise LintError(f"JSON Pointer array index must be digits: {token!r} in {pointer}")
            index = int(token)
            if index >= len(current):
                raise LintError(f"JSON Pointer array index out of range: {pointer}")
            current = current[index]
        else:
            raise LintError(f"JSON Pointer cannot descend further at {token!r} in {pointer}")
    return current


def _text_sha256(text: str) -> HashDigest:
    return HashDigest(hashlib.sha256(text.encode("utf-8")).hexdigest())


def _file_sha256(path: Path) -> HashDigest:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return HashDigest(digest.hexdigest())


def _finding(
    rule_id: OpaqueId,
    *,
    passed: bool,
    measurement_id: str,
    value: str | int | bool,
    unit: str | None,
    message: str,
) -> Finding:
    return Finding(
        constraint_id=rule_id,
        passed=passed,
        observed=Measurement(
            measurement_id=OpaqueId(measurement_id),
            value=value if not isinstance(value, str) else value,
            unit=unit,
        ),
        message=message,
    )


def _eval_verbatim(
    document: Mapping[str, Any],
    rule: VerbatimRule,
    source_root: Path | None,
) -> list[Finding]:
    results: list[Finding] = []

    if rule.expected_text is not None:
        text_digest = _text_sha256(rule.expected_text)
        if text_digest != rule.expected_text_sha256:
            results.append(
                _finding(
                    rule.rule_id,
                    passed=False,
                    measurement_id="expected_text_sha256",
                    value=str(text_digest),
                    unit="sha256",
                    message=(
                        "rule.expected_text does not match rule.expected_text_sha256 "
                        f"(observed {text_digest})"
                    ),
                )
            )
            return results

    try:
        target = resolve_json_pointer(document, rule.target_field_pointer)
    except LintError as exc:
        results.append(
            _finding(
                rule.rule_id,
                passed=False,
                measurement_id="target_field",
                value=0,
                unit=None,
                message=str(exc),
            )
        )
        return results

    if not isinstance(target, str):
        results.append(
            _finding(
                rule.rule_id,
                passed=False,
                measurement_id="target_field_type",
                value=type(target).__name__,
                unit=None,
                message=(
                    f"target field {rule.target_field_pointer!r} must be a string, "
                    f"got {type(target).__name__}"
                ),
            )
        )
        return results

    observed = _text_sha256(target)
    byte_match = observed == rule.expected_text_sha256
    if rule.expected_text is not None:
        byte_match = byte_match and (target == rule.expected_text)

    results.append(
        _finding(
            rule.rule_id,
            passed=byte_match,
            measurement_id="target_text_sha256",
            value=str(observed),
            unit="sha256",
            message=(
                "target field matches expected text (byte / sha256)"
                if byte_match
                else (
                    f"target field {rule.target_field_pointer!r} does not match expected "
                    f"text (observed sha256={observed}, expected={rule.expected_text_sha256})"
                )
            ),
        )
    )

    if rule.source_path is not None and rule.source_sha256 is not None:
        results.append(_check_source_file(rule.rule_id, rule.source_path, rule.source_sha256, source_root))

    return results


def _eval_source_lock(rule: SourceLockRule, source_root: Path | None) -> Finding:
    return _check_source_file(rule.rule_id, rule.source_path, rule.source_sha256, source_root)


def _check_source_file(
    rule_id: OpaqueId,
    source_path: str,
    expected_sha256: HashDigest,
    source_root: Path | None,
) -> Finding:
    if source_root is None:
        return _finding(
            rule_id,
            passed=False,
            measurement_id="source_root",
            value=0,
            unit=None,
            message="source_root is required when a rule references source_path",
        )

    relative = Path(source_path)
    if relative.is_absolute() or ".." in relative.parts:
        return _finding(
            rule_id,
            passed=False,
            measurement_id="source_path",
            value=source_path,
            unit=None,
            message=f"source_path must be a relative path without '..': {source_path!r}",
        )

    full = (source_root / relative).resolve()
    try:
        full.relative_to(source_root.resolve())
    except ValueError:
        return _finding(
            rule_id,
            passed=False,
            measurement_id="source_path",
            value=source_path,
            unit=None,
            message=f"source_path escapes source_root: {source_path!r}",
        )

    if not full.is_file():
        return _finding(
            rule_id,
            passed=False,
            measurement_id="source_file",
            value=0,
            unit=None,
            message=f"source file not found: {source_path}",
        )

    try:
        observed = _file_sha256(full)
    except OSError as exc:
        return _finding(
            rule_id,
            passed=False,
            measurement_id="source_file",
            value=0,
            unit=None,
            message=f"cannot read source file {source_path}: {exc}",
        )

    passed = observed == expected_sha256
    return _finding(
        rule_id,
        passed=passed,
        measurement_id="source_sha256",
        value=str(observed),
        unit="sha256",
        message=(
            "source file sha256 matches lock"
            if passed
            else (
                f"source drift: {source_path} sha256={observed} "
                f"does not match locked {expected_sha256}"
            )
        ),
    )
