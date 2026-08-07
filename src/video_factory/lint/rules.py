"""Injected lint rule data — channel owns content; core owns evaluation only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from video_factory.domain import HashDigest, OpaqueId, RelativeArtifactPath


@dataclass(frozen=True, slots=True)
class VerbatimRule:
    """Require that a document field matches expected text by byte digest (and text).

    Core never interprets what the text *means* (character bible, location, etc.).
    The channel supplies expected digests and optional full expected text.

    When ``source_path`` and ``source_sha256`` are set, the rule also detects
    source drift: the file at ``source_root / source_path`` must still hash to
    ``source_sha256``.
    """

    rule_id: OpaqueId
    target_field_pointer: str
    expected_text_sha256: HashDigest
    expected_text: str | None = None
    source_path: RelativeArtifactPath | None = None
    source_sha256: HashDigest | None = None


@dataclass(frozen=True, slots=True)
class SourceLockRule:
    """Require that a source file's current sha256 matches a locked digest.

    Generic form of channel ``source_lock_hash`` checks: when the canonical
    file changes, rules that were built against the previous bytes fail until
    the channel refreshes the lock digests.
    """

    rule_id: OpaqueId
    source_path: RelativeArtifactPath
    source_sha256: HashDigest


LintRule = VerbatimRule | SourceLockRule


@dataclass(frozen=True, slots=True)
class LintRuleSet:
    """Ordered collection of injected rules (document identity optional)."""

    rules: tuple[LintRule, ...]
    ruleset_id: OpaqueId | None = None

    def __iter__(self):
        return iter(self.rules)

    def __len__(self) -> int:
        return len(self.rules)


def rules_from_mapping(payload: Mapping[str, object]) -> LintRuleSet:
    """Build a rule set from a plain mapping (already-parsed JSON object)."""

    rules_raw = payload.get("rules")
    if not isinstance(rules_raw, Sequence) or isinstance(rules_raw, (str, bytes)):
        raise ValueError("lint rule file root must contain a 'rules' array")

    built: list[LintRule] = []
    for index, item in enumerate(rules_raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"rules[{index}] must be an object")
        kind = item.get("kind")
        if kind == "verbatim":
            built.append(_verbatim_from_mapping(item, index))
        elif kind == "source_lock":
            built.append(_source_lock_from_mapping(item, index))
        else:
            raise ValueError(
                f"rules[{index}].kind must be 'verbatim' or 'source_lock', got {kind!r}"
            )

    ruleset_id_raw = payload.get("ruleset_id")
    ruleset_id: OpaqueId | None
    if ruleset_id_raw is None:
        ruleset_id = None
    elif isinstance(ruleset_id_raw, str) and ruleset_id_raw.strip():
        ruleset_id = OpaqueId(ruleset_id_raw)
    else:
        raise ValueError("ruleset_id must be a non-empty string when present")

    return LintRuleSet(rules=tuple(built), ruleset_id=ruleset_id)


def _require_str(item: Mapping[str, object], key: str, index: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"rules[{index}].{key} must be a non-empty string")
    return value


def _optional_str(item: Mapping[str, object], key: str, index: int) -> str | None:
    if key not in item or item.get(key) is None:
        return None
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"rules[{index}].{key} must be a non-empty string when present")
    return value


def _sha256_field(item: Mapping[str, object], key: str, index: int) -> HashDigest:
    value = _require_str(item, key, index)
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value.lower()):
        raise ValueError(f"rules[{index}].{key} must be a 64-char hex sha256")
    return HashDigest(value.lower())


def _verbatim_from_mapping(item: Mapping[str, object], index: int) -> VerbatimRule:
    expected_text = item.get("expected_text")
    if expected_text is not None and not isinstance(expected_text, str):
        raise ValueError(f"rules[{index}].expected_text must be a string when present")
    source_path = _optional_str(item, "source_path", index)
    source_sha = item.get("source_sha256")
    source_sha256: HashDigest | None
    if source_path is None and source_sha is None:
        source_sha256 = None
    elif source_path is not None and source_sha is not None:
        source_sha256 = _sha256_field(item, "source_sha256", index)
    else:
        raise ValueError(
            f"rules[{index}] source_path and source_sha256 must both be set or both omitted"
        )
    return VerbatimRule(
        rule_id=OpaqueId(_require_str(item, "rule_id", index)),
        target_field_pointer=_require_str(item, "target_field_pointer", index),
        expected_text_sha256=_sha256_field(item, "expected_text_sha256", index),
        expected_text=expected_text if isinstance(expected_text, str) else None,
        source_path=RelativeArtifactPath(source_path) if source_path else None,
        source_sha256=source_sha256,
    )


def _source_lock_from_mapping(item: Mapping[str, object], index: int) -> SourceLockRule:
    return SourceLockRule(
        rule_id=OpaqueId(_require_str(item, "rule_id", index)),
        source_path=RelativeArtifactPath(_require_str(item, "source_path", index)),
        source_sha256=_sha256_field(item, "source_sha256", index),
    )
