"""Versioned Director registry, activation, and field coverage."""

from __future__ import annotations

from collections.abc import Iterable
import re

from video_factory.blueprint import contracts as blueprint_contracts
from video_factory.blueprint.model import episode_intent_to_mapping, normalize_fields
from video_factory.config.canonical import canonical_sha256
from video_factory.domain import HashDigest, OpaqueId

from .contracts import (
    DirectorActivation,
    DirectorCharter,
    DirectorKind,
    EpisodeNeedsProfile,
    NeedsComplexity,
)


DIRECTOR_REGISTRY_VERSION = "director-registry/1.0"
ACTIVATION_POLICY_VERSION = "director-activation-policy/1.0"
MAX_CONFLICT_ROUNDS = 2
SCOPE_PATTERN = re.compile(
    r"^(?:\*|[a-z][a-z0-9_-]*)(?:\.(?:\*|[a-z0-9][a-z0-9_-]*))*$"
)
TOKEN = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DirectorRegistryError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def scope_matches(pattern: str, field_path: str) -> bool:
    if not SCOPE_PATTERN.fullmatch(pattern):
        raise DirectorRegistryError("director.scope.pattern", "invalid scope pattern")
    pattern_parts = pattern.split(".")
    field_parts = field_path.split(".")
    if len(pattern_parts) > len(field_parts):
        return False
    return all(
        expected == "*" or expected == observed
        for expected, observed in zip(pattern_parts, field_parts, strict=False)
    )


def _charter_identity_mapping(charter: DirectorCharter) -> dict[str, object]:
    if not TOKEN.fullmatch(str(charter.director_id)) or not VERSION.fullmatch(
        charter.director_version
    ):
        raise DirectorRegistryError(
            "director.charter.identity", "director identity/version is invalid"
        )
    if not VERSION.fullmatch(charter.rules_version):
        raise DirectorRegistryError(
            "director.charter.rules", "director rules version is invalid"
        )
    for group in (
        charter.owned_patterns,
        charter.verified_patterns,
        charter.veto_patterns,
    ):
        if group != tuple(sorted(set(group))) or any(
            not SCOPE_PATTERN.fullmatch(item) for item in group
        ):
            raise DirectorRegistryError(
                "director.charter.scope", "charter scopes must be sorted unique patterns"
            )
    signals = charter.activation_signals
    if signals != tuple(sorted(set(signals))) or any(
        not TOKEN.fullmatch(item) for item in signals
    ):
        raise DirectorRegistryError(
            "director.charter.signals", "activation signals must be sorted unique"
        )
    if charter.kind is DirectorKind.CORE and signals:
        raise DirectorRegistryError(
            "director.charter.core_signals", "core director cannot be conditional"
        )
    if charter.kind is DirectorKind.CONDITIONAL and not signals:
        raise DirectorRegistryError(
            "director.charter.conditional_signals", "conditional director needs signals"
        )
    if not isinstance(charter.conflict_priority, int) or isinstance(
        charter.conflict_priority, bool
    ) or not 0 <= charter.conflict_priority <= 100:
        raise DirectorRegistryError(
            "director.charter.priority", "conflict priority must be 0..100"
        )
    return {
        "director_id": str(charter.director_id),
        "director_version": charter.director_version,
        "kind": charter.kind.value,
        "owned_patterns": list(charter.owned_patterns),
        "verified_patterns": list(charter.verified_patterns),
        "activation_signals": list(charter.activation_signals),
        "veto_patterns": list(charter.veto_patterns),
        "conflict_priority": charter.conflict_priority,
        "rules_version": charter.rules_version,
    }


def director_charter_to_mapping(charter: DirectorCharter) -> dict[str, object]:
    identity = _charter_identity_mapping(charter)
    digest = canonical_sha256(identity)
    expected_id = OpaqueId(f"director-charter-{str(digest)[:20]}")
    if charter.charter_id != expected_id or charter.charter_sha256 != digest:
        raise DirectorRegistryError(
            "director.charter.identity", "director charter identity mismatch"
        )
    return {
        "artifact_version": "director-charter/1.0",
        "charter_id": str(charter.charter_id),
        "charter_sha256": str(charter.charter_sha256),
        **identity,
    }


def build_director_charter(
    *,
    director_id: str,
    kind: DirectorKind,
    owned_patterns: Iterable[str],
    verified_patterns: Iterable[str],
    activation_signals: Iterable[str] = (),
    veto_patterns: Iterable[str] = (),
    conflict_priority: int,
    director_version: str = "1.0",
    rules_version: str = "director-charter-rules/1.0",
) -> DirectorCharter:
    value = DirectorCharter(
        charter_id=OpaqueId("pending"),
        charter_sha256=HashDigest("0" * 64),
        director_id=OpaqueId(director_id),
        director_version=director_version,
        kind=kind,
        owned_patterns=tuple(sorted(set(owned_patterns))),
        verified_patterns=tuple(sorted(set(verified_patterns))),
        activation_signals=tuple(sorted(set(activation_signals))),
        veto_patterns=tuple(sorted(set(veto_patterns))),
        conflict_priority=conflict_priority,
        rules_version=rules_version,
    )
    identity = _charter_identity_mapping(value)
    digest = canonical_sha256(identity)
    return DirectorCharter(
        charter_id=OpaqueId(f"director-charter-{str(digest)[:20]}"),
        charter_sha256=digest,
        director_id=value.director_id,
        director_version=value.director_version,
        kind=value.kind,
        owned_patterns=value.owned_patterns,
        verified_patterns=value.verified_patterns,
        activation_signals=value.activation_signals,
        veto_patterns=value.veto_patterns,
        conflict_priority=value.conflict_priority,
        rules_version=value.rules_version,
    )


def default_director_charters() -> tuple[DirectorCharter, ...]:
    """Return the target-owned 12-core/6-conditional registry."""

    core = (
        ("showrunner", ("identity", "intent"), ("*",), ("intent",), 90),
        ("narrative-director", ("narrative", "shot_graph.*.purpose"), ("intent", "narrative"), (), 60),
        ("staging-director", ("shot_graph.*.environment", "shot_graph.*.state", "shot_graph.*.subjects"), ("shot_graph.*.motion",), (), 55),
        ("art-director", ("asset_and_reference_locks", "shot_graph.*.lighting_and_color", "visual_language"), ("shot_graph.*.camera",), (), 55),
        ("cinematography-director", ("shot_graph.*.camera",), ("shot_graph.*.lighting_and_color", "shot_graph.*.motion"), (), 60),
        ("motion-performance-director", ("shot_graph.*.motion",), ("shot_graph.*.subjects",), (), 60),
        ("generative-technical-director", ("generation_strategy", "shot_graph.*.generation"), ("asset_and_reference_locks",), ("generation_strategy", "shot_graph.*.generation"), 80),
        ("editing-rhythm-director", ("edit_timeline", "shot_graph.*.timing", "shot_graph.*.transition"), ("narrative",), (), 50),
        ("sound-director", ("shot_graph.*.sound", "sound_design"), ("shot_graph.*.timing",), (), 50),
        ("continuity-director", ("continuity_state", "shot_graph.*.continuity"), ("shot_graph.*.state",), ("continuity_state", "shot_graph.*.continuity"), 85),
        ("quality-director", ("risks_and_acceptance", "shot_graph.*.acceptance"), ("*",), ("risks_and_acceptance", "shot_graph.*.acceptance"), 95),
        ("audience-distribution-director", ("audience_and_success", "delivery_and_distribution"), ("intent", "narrative"), (), 55),
    )
    conditional = (
        ("fact-research-director", "factual_claims_present", "factual"),
        ("rights-brand-safety-director", "rights_or_policy_risk", "rights"),
        ("vfx-compositing-director", "vfx_required", "vfx"),
        ("dialogue-voice-director", "dialogue_or_voice_present", "dialogue"),
        ("localization-accessibility-director", "localization_or_accessibility", "localization"),
        ("live-production-director", "live_production_required", "live_production"),
    )
    values = [
        build_director_charter(
            director_id=director_id,
            kind=DirectorKind.CORE,
            owned_patterns=owned,
            verified_patterns=verified,
            veto_patterns=veto,
            conflict_priority=priority,
        )
        for director_id, owned, verified, veto, priority in core
    ]
    values.extend(
        build_director_charter(
            director_id=director_id,
            kind=DirectorKind.CONDITIONAL,
            owned_patterns=(scope,),
            verified_patterns=(scope,),
            activation_signals=(signal,),
            veto_patterns=(scope,),
            conflict_priority=92,
        )
        for director_id, signal, scope in conditional
    )
    return tuple(sorted(values, key=lambda item: str(item.director_id)))


def director_registry_sha256(charters: Iterable[DirectorCharter]) -> HashDigest:
    values = tuple(charters)
    ids = [str(item.director_id) for item in values]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise DirectorRegistryError(
            "director.registry.order", "director registry must be sorted and unique"
        )
    return canonical_sha256(
        {
            "registry_version": DIRECTOR_REGISTRY_VERSION,
            "charters": [director_charter_to_mapping(item) for item in values],
        }
    )


def activation_policy_sha256(charters: Iterable[DirectorCharter]) -> HashDigest:
    values = tuple(charters)
    return canonical_sha256(
        {
            "activation_policy_version": ACTIVATION_POLICY_VERSION,
            "sequential_stages_forbidden": True,
            "maximum_conflict_rounds": MAX_CONFLICT_ROUNDS,
            "conditional": [
                {
                    "director_id": str(item.director_id),
                    "signals": list(item.activation_signals),
                }
                for item in values
                if item.kind is DirectorKind.CONDITIONAL
            ],
        }
    )


def director_activation_to_mapping(value: DirectorActivation) -> dict[str, object]:
    if any(
        not SHA256.fullmatch(str(item))
        for item in (
            value.registry_sha256,
            value.activation_policy_sha256,
            value.episode_intent_sha256,
        )
    ):
        raise DirectorRegistryError(
            "director.activation.digest", "activation contains an invalid digest"
        )
    if value.activation_signals != tuple(sorted(set(value.activation_signals))) or any(
        not TOKEN.fullmatch(item) for item in value.activation_signals
    ):
        raise DirectorRegistryError(
            "director.activation.signals", "activation signals are not canonical"
        )
    if value.active_director_ids != tuple(
        sorted(set(value.active_director_ids), key=str)
    ):
        raise DirectorRegistryError(
            "director.activation.directors", "active director IDs are not canonical"
        )
    identity = {
        "registry_sha256": str(value.registry_sha256),
        "activation_policy_sha256": str(value.activation_policy_sha256),
        "episode_intent_sha256": str(value.episode_intent_sha256),
        "complexity": value.complexity.value,
        "activation_signals": list(value.activation_signals),
        "active_director_ids": [str(item) for item in value.active_director_ids],
    }
    digest = canonical_sha256(identity)
    if value.activation_sha256 != digest or value.activation_id != OpaqueId(
        f"director-activation-{str(digest)[:20]}"
    ):
        raise DirectorRegistryError(
            "director.activation.identity", "director activation identity mismatch"
        )
    return identity


def activate_directors(
    *,
    episode_intent: blueprint_contracts.EpisodeIntent,
    profile: EpisodeNeedsProfile,
    charters: Iterable[DirectorCharter],
) -> DirectorActivation:
    episode_intent_to_mapping(episode_intent)
    episode_intent_sha256 = episode_intent.intent_sha256
    values = tuple(charters)
    registry_digest = director_registry_sha256(values)
    policy_digest = activation_policy_sha256(values)
    signals = tuple(profile.activation_signals)
    if signals != tuple(sorted(set(signals))):
        raise DirectorRegistryError(
            "director.activation.signals", "profile signals must be sorted unique"
        )
    registered_signals = {
        signal
        for item in values
        if item.kind is DirectorKind.CONDITIONAL
        for signal in item.activation_signals
    }
    unknown_signals = set(signals) - registered_signals
    if unknown_signals:
        raise DirectorRegistryError(
            "director.activation.signal_unknown",
            f"activation signals are not registered: {sorted(unknown_signals)}",
        )
    if profile.complexity.value != episode_intent.needs_complexity or signals != (
        episode_intent.activation_signals
    ):
        raise DirectorRegistryError(
            "director.activation.intent_mismatch",
            "needs profile must exactly match the bound EpisodeIntent",
        )
    active = tuple(
        OpaqueId(str(item.director_id))
        for item in values
        if item.kind is DirectorKind.CORE
        or bool(set(item.activation_signals).intersection(signals))
    )
    if active != tuple(sorted(active, key=str)):
        raise DirectorRegistryError(
            "director.activation.order", "registry activation order is not canonical"
        )
    identity = {
        "registry_sha256": str(registry_digest),
        "activation_policy_sha256": str(policy_digest),
        "episode_intent_sha256": str(episode_intent_sha256),
        "complexity": profile.complexity.value,
        "activation_signals": list(signals),
        "active_director_ids": [str(item) for item in active],
    }
    digest = canonical_sha256(identity)
    return DirectorActivation(
        activation_id=OpaqueId(f"director-activation-{str(digest)[:20]}"),
        activation_sha256=digest,
        registry_sha256=registry_digest,
        activation_policy_sha256=policy_digest,
        episode_intent_sha256=episode_intent_sha256,
        complexity=profile.complexity,
        activation_signals=signals,
        active_director_ids=active,
    )


def validate_director_activation(
    activation: DirectorActivation,
    *,
    episode_intent: blueprint_contracts.EpisodeIntent,
    charters: Iterable[DirectorCharter],
) -> None:
    director_activation_to_mapping(activation)
    expected = activate_directors(
        episode_intent=episode_intent,
        profile=EpisodeNeedsProfile(
            complexity=activation.complexity,
            activation_signals=activation.activation_signals,
        ),
        charters=tuple(charters),
    )
    if activation != expected:
        raise DirectorRegistryError(
            "director.activation.policy_mismatch",
            "activation does not match the current EpisodeIntent and registry policy",
        )


def build_field_ownership(
    fields: Iterable[blueprint_contracts.BlueprintField],
    charters: Iterable[DirectorCharter],
    activation: DirectorActivation,
) -> tuple[blueprint_contracts.FieldOwnership, ...]:
    normalized_fields = normalize_fields(fields)
    values = tuple(charters)
    if activation.registry_sha256 != director_registry_sha256(values):
        raise DirectorRegistryError(
            "director.coverage.registry", "activation registry digest is stale"
        )
    active_ids = {str(item) for item in activation.active_director_ids}
    active = tuple(item for item in values if str(item.director_id) in active_ids)
    ownership: list[blueprint_contracts.FieldOwnership] = []
    owned_counts = {str(item.director_id): 0 for item in active}
    for field in normalized_fields:
        candidates: list[tuple[int, DirectorCharter]] = []
        for charter in active:
            for pattern in charter.owned_patterns:
                if scope_matches(pattern, field.path):
                    specificity = sum(part != "*" for part in pattern.split("."))
                    candidates.append((specificity * 100 + len(pattern.split(".")), charter))
        if not candidates:
            raise DirectorRegistryError(
                "director.coverage.owner_missing", f"no owner for {field.path}"
            )
        best = max(score for score, _ in candidates)
        owners = {str(item.director_id): item for score, item in candidates if score == best}
        if len(owners) != 1:
            raise DirectorRegistryError(
                "director.coverage.owner_conflict", f"ambiguous owner for {field.path}"
            )
        owner = next(iter(owners.values()))
        owned_counts[str(owner.director_id)] += 1
        verifiers = tuple(
            sorted(
                {
                    OpaqueId(str(charter.director_id))
                    for charter in active
                    if charter.director_id != owner.director_id
                    and any(scope_matches(pattern, field.path) for pattern in charter.verified_patterns)
                },
                key=str,
            )
        )
        if not verifiers:
            raise DirectorRegistryError(
                "director.coverage.verifier_missing", f"no distinct verifier for {field.path}"
            )
        ownership.append(
            blueprint_contracts.FieldOwnership(
                field_path=field.path,
                owner_director_id=owner.director_id,
                verifier_director_ids=verifiers,
            )
        )
    missing_conditional = [
        director_id
        for director_id, count in owned_counts.items()
        if count == 0
        and next(item for item in active if str(item.director_id) == director_id).kind
        is DirectorKind.CONDITIONAL
    ]
    if missing_conditional:
        raise DirectorRegistryError(
            "director.activation.scope_missing",
            f"active conditional directors lack Blueprint fields: {sorted(missing_conditional)}",
        )
    return tuple(ownership)


def validate_field_ownership(
    blueprint: blueprint_contracts.ProductionBlueprint,
    charters: Iterable[DirectorCharter],
    activation: DirectorActivation,
) -> None:
    expected = build_field_ownership(blueprint.fields, charters, activation)
    if blueprint.ownership != expected:
        raise DirectorRegistryError(
            "director.coverage.mismatch", "Blueprint ownership does not match registry coverage"
        )
