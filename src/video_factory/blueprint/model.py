"""Deterministic Blueprint construction and cross-field validation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import re

from video_factory.config.canonical import canonical_json_bytes, canonical_sha256
from video_factory.domain import HashDigest, OpaqueId
from video_factory.json_boundary import JsonInputError, parse_json_bytes

from .contracts import (
    BlueprintContext,
    BlueprintField,
    BlueprintSourceBundle,
    BlueprintStatus,
    ChannelConstitution,
    ConceptConstitution,
    DirectorProvenance,
    EpisodeIntent,
    FieldOwnership,
    ProductionBlueprint,
)
from .reference_paths import ReferencePathError, reference_path_collision_key


FIELD_PATH = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)+$")
TOKEN = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ARTIFACT_VERSION = re.compile(r"^[a-z][a-z0-9-]*/[1-9][0-9]*\.[0-9]+$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMPLEXITIES = {"LIGHT", "PRODUCTION", "COMPLEX"}
ROOT_REQUIRED_FIELDS = {
    "identity.episode_id",
    "intent.narrative_goal",
    "audience_and_success.audience",
    "audience_and_success.success_criteria",
    "narrative.structure",
    "visual_language.style",
    "asset_and_reference_locks.references",
    "sound_design.mix",
    "edit_timeline.rhythm",
    "generation_strategy.capability",
    "delivery_and_distribution.targets",
    "continuity_state.summary",
    "risks_and_acceptance.criteria",
}
SHOT_REQUIRED_SUFFIXES = {
    "purpose",
    "timing",
    "state",
    "subjects",
    "environment",
    "camera.framing",
    "camera.lens",
    "camera.movement",
    "camera.focus",
    "motion.blocking",
    "motion.gaze",
    "motion.performance",
    "motion.physics",
    "lighting_and_color",
    "sound.ambience",
    "sound.foley",
    "sound.sync",
    "sound.mix",
    "transition",
    "generation.capability",
    "generation.prompt",
    "generation.references",
    "generation.candidate_policy",
    "generation.retry_policy",
    "generation.fallback",
    "continuity.state",
    "acceptance.criteria",
}


class BlueprintContractError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _token(value: str, label: str) -> str:
    if not TOKEN.fullmatch(value):
        raise BlueprintContractError("blueprint.token", f"invalid {label}")
    return value


def _version(value: str, label: str) -> str:
    if not VERSION.fullmatch(value):
        raise BlueprintContractError("blueprint.version", f"invalid {label}")
    return value


def _sha(value: str, label: str) -> HashDigest:
    if not SHA256.fullmatch(value):
        raise BlueprintContractError("blueprint.sha256", f"invalid {label}")
    return HashDigest(value)


def _canonical_value_text(value_json: str) -> str:
    try:
        parsed = parse_json_bytes(value_json.encode("utf-8"))
        canonical = canonical_json_bytes(parsed).decode("utf-8")
    except (UnicodeError, JsonInputError, ValueError, TypeError) as error:
        raise BlueprintContractError(
            "blueprint.field.value_json", "field value is not bounded canonical JSON"
        ) from error
    if canonical != value_json:
        raise BlueprintContractError(
            "blueprint.field.noncanonical", "field value JSON is not canonical"
        )
    return canonical


def blueprint_field(path: str, value: object) -> BlueprintField:
    """Create one immutable active field from an ordinary JSON value."""

    try:
        value_json = canonical_json_bytes(value).decode("utf-8")
    except (ValueError, TypeError, UnicodeError) as error:
        raise BlueprintContractError(
            "blueprint.field.unsupported", "field value is not canonical JSON"
        ) from error
    # Reparse to apply the bounded JSON depth/node/number contract.
    _canonical_value_text(value_json)
    return _validate_field(BlueprintField(path=path, value_json=value_json))


def _validate_field(field: BlueprintField) -> BlueprintField:
    if not isinstance(field, BlueprintField) or not FIELD_PATH.fullmatch(field.path):
        raise BlueprintContractError(
            "blueprint.field.path", "field path must be canonical dotted lowercase"
        )
    _canonical_value_text(field.value_json)
    return field


def _is_material_field_value(field: BlueprintField) -> bool:
    parsed = parse_json_bytes(field.value_json.encode("utf-8"))
    return not (
        parsed is None
        or parsed == ""
        or parsed == []
        or parsed == {}
    )


def _require_material_paths(
    fields: tuple[BlueprintField, ...],
    required: set[str],
    *,
    reason_code: str,
    label: str,
) -> None:
    by_path = {item.path: item for item in fields}
    missing = sorted(required - set(by_path))
    if missing:
        raise BlueprintContractError(reason_code, f"{label} lacks required fields: {missing}")
    empty = sorted(
        path for path in required if not _is_material_field_value(by_path[path])
    )
    if empty:
        raise BlueprintContractError(
            f"{reason_code}.empty",
            f"{label} required fields must contain material values: {empty}",
        )


def _validate_reference_locks(
    value: object, *, path: str
) -> tuple[tuple[str, str, str, str], ...]:
    if not isinstance(value, list) or not value:
        raise BlueprintContractError(
            "blueprint.coherent.reference_lock",
            f"{path} must be a non-empty reference-lock list",
        )
    validated: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "path",
            "sha256",
            "artifact_version",
        }:
            raise BlueprintContractError(
                "blueprint.coherent.reference_lock",
                f"{path} contains an invalid reference-lock object",
            )
        reference_path = item["path"]
        try:
            collision_key = reference_path_collision_key(reference_path)
        except ReferencePathError as error:
            raise BlueprintContractError(
                "blueprint.coherent.reference_lock",
                f"{path} contains an unsafe reference path",
            ) from error
        if not isinstance(item["sha256"], str) or not SHA256.fullmatch(
            item["sha256"]
        ):
            raise BlueprintContractError(
                "blueprint.coherent.reference_lock",
                f"{path} contains an invalid reference digest",
            )
        if not isinstance(item["artifact_version"], str) or not ARTIFACT_VERSION.fullmatch(
            item["artifact_version"]
        ):
            raise BlueprintContractError(
                "blueprint.coherent.reference_lock",
                f"{path} contains an invalid artifact version",
            )
        if collision_key in seen:
            raise BlueprintContractError(
                "blueprint.coherent.reference_lock_duplicate",
                f"{path} contains duplicate or colliding reference paths",
            )
        seen.add(collision_key)
        validated.append(
            (
                collision_key,
                reference_path,
                item["sha256"],
                item["artifact_version"],
            )
        )
    canonical = tuple(
        sorted(validated, key=lambda item: (item[0], item[1], item[2], item[3]))
    )
    if tuple(validated) != canonical:
        raise BlueprintContractError(
            "blueprint.coherent.reference_lock_order",
            f"{path} reference locks must use canonical path order",
        )
    return canonical


def _validate_coherent_detail_semantics(fields: tuple[BlueprintField, ...]) -> None:
    string_paths = {"identity.episode_id", "intent.narrative_goal"}
    list_paths = {
        "audience_and_success.success_criteria",
        "delivery_and_distribution.targets",
        "risks_and_acceptance.criteria",
    }
    reference_paths = {"asset_and_reference_locks.references"}
    string_suffixes = {"purpose", "generation.prompt"}
    list_suffixes = {"subjects", "acceptance.criteria"}
    reference_suffixes = {"generation.references"}
    reference_identities: dict[str, tuple[str, str, str]] = {}
    for field in fields:
        value = parse_json_bytes(field.value_json.encode("utf-8"))
        suffix = (
            ".".join(field.path.split(".")[2:])
            if field.path.startswith("shot_graph.")
            else ""
        )
        if field.path in reference_paths or suffix in reference_suffixes:
            locks = _validate_reference_locks(value, path=field.path)
            for collision_key, reference_path, digest, artifact_version in locks:
                identity = (reference_path, digest, artifact_version)
                previous = reference_identities.get(collision_key)
                if previous is not None and previous != identity:
                    raise BlueprintContractError(
                        "blueprint.coherent.reference_lock_conflict",
                        "one reference path cannot identify different immutable artifacts",
                    )
                reference_identities[collision_key] = identity
        elif field.path in string_paths or suffix in string_suffixes:
            if not isinstance(value, str) or not value.strip():
                raise BlueprintContractError(
                    "blueprint.coherent.field_type",
                    f"{field.path} must be a non-empty string",
                )
        elif field.path in list_paths or suffix in list_suffixes:
            if not isinstance(value, list) or not value:
                raise BlueprintContractError(
                    "blueprint.coherent.field_type",
                    f"{field.path} must be a non-empty list",
                )
        elif not isinstance(value, dict) or not value:
            raise BlueprintContractError(
                "blueprint.coherent.field_type",
                f"{field.path} must be a non-empty structured object",
            )


def normalize_fields(fields: Iterable[BlueprintField]) -> tuple[BlueprintField, ...]:
    values = tuple(_validate_field(item) for item in fields)
    paths = [item.path for item in values]
    if len(paths) != len(set(paths)):
        raise BlueprintContractError(
            "blueprint.field.duplicate", "active field paths must be unique"
        )
    expected = tuple(sorted(values, key=lambda item: item.path))
    if values != expected:
        raise BlueprintContractError(
            "blueprint.field.order", "active fields must use canonical path order"
        )
    return values


def field_value_sha256(field: BlueprintField) -> HashDigest:
    _validate_field(field)
    return HashDigest(hashlib.sha256(field.value_json.encode("utf-8")).hexdigest())


def blueprint_context_to_mapping(context: BlueprintContext) -> dict[str, str]:
    return {
        name: str(_sha(str(getattr(context, name)), name))
        for name in (
            "channel_constitution_sha256",
            "concept_constitution_sha256",
            "episode_intent_sha256",
            "policy_bundle_sha256",
            "rules_bundle_sha256",
            "effective_config_sha256",
            "current_manifest_sha256",
            "evidence_graph_sha256",
        )
    }


def blueprint_context_sha256(context: BlueprintContext) -> HashDigest:
    return canonical_sha256(blueprint_context_to_mapping(context))


def _field_mapping(field: BlueprintField) -> dict[str, str]:
    _validate_field(field)
    return {"path": field.path, "value_json": field.value_json}


def _fields_mapping(fields: tuple[BlueprintField, ...]) -> list[dict[str, str]]:
    return [_field_mapping(item) for item in normalize_fields(fields)]


def _identity(prefix: str, mapping: Mapping[str, object]) -> tuple[OpaqueId, HashDigest]:
    digest = canonical_sha256(mapping)
    return OpaqueId(f"{prefix}-{str(digest)[:20]}"), digest


def channel_constitution_to_mapping(value: ChannelConstitution) -> dict[str, object]:
    normalized = normalize_fields(value.fields)
    _require_material_paths(
        normalized,
        {"audience.profile", "success.criteria", "visual.principles", "distribution.constraints"},
        reason_code="blueprint.channel.required",
        label="channel constitution",
    )
    identity = {
        "channel_id": _token(str(value.channel_id), "channel_id"),
        "rules_version": _token(value.rules_version, "rules_version"),
        "fields": _fields_mapping(value.fields),
    }
    expected_id, expected_sha = _identity("channel-constitution", identity)
    if value.constitution_id != expected_id or value.constitution_sha256 != expected_sha:
        raise BlueprintContractError(
            "blueprint.channel.identity", "channel constitution identity mismatch"
        )
    return {
        "artifact_version": "channel-constitution/1.0",
        "constitution_id": str(value.constitution_id),
        "constitution_sha256": str(value.constitution_sha256),
        **identity,
    }


def build_channel_constitution(
    *, channel_id: str, rules_version: str, fields: Iterable[BlueprintField]
) -> ChannelConstitution:
    normalized = normalize_fields(sorted(fields, key=lambda item: item.path))
    _require_material_paths(
        normalized,
        {"audience.profile", "success.criteria", "visual.principles", "distribution.constraints"},
        reason_code="blueprint.channel.required",
        label="channel constitution",
    )
    identity = {
        "channel_id": _token(channel_id, "channel_id"),
        "rules_version": _token(rules_version, "rules_version"),
        "fields": _fields_mapping(normalized),
    }
    constitution_id, digest = _identity("channel-constitution", identity)
    return ChannelConstitution(
        constitution_id=constitution_id,
        constitution_sha256=digest,
        channel_id=OpaqueId(channel_id),
        rules_version=rules_version,
        fields=normalized,
    )


def concept_constitution_to_mapping(value: ConceptConstitution) -> dict[str, object]:
    normalized = normalize_fields(value.fields)
    _require_material_paths(
        normalized,
        {"premise.summary", "narrative.principles", "visual.direction", "constraints.production"},
        reason_code="blueprint.concept.required",
        label="concept constitution",
    )
    identity = {
        "concept_id": _token(str(value.concept_id), "concept_id"),
        "channel_constitution_sha256": str(
            _sha(str(value.channel_constitution_sha256), "channel constitution")
        ),
        "rules_version": _token(value.rules_version, "rules_version"),
        "fields": _fields_mapping(value.fields),
    }
    _sha(identity["channel_constitution_sha256"], "channel_constitution_sha256")
    expected_id, expected_sha = _identity("concept-constitution", identity)
    if value.constitution_id != expected_id or value.constitution_sha256 != expected_sha:
        raise BlueprintContractError(
            "blueprint.concept.identity", "concept constitution identity mismatch"
        )
    return {
        "artifact_version": "concept-constitution/1.0",
        "constitution_id": str(value.constitution_id),
        "constitution_sha256": str(value.constitution_sha256),
        **identity,
    }


def build_concept_constitution(
    *,
    concept_id: str,
    channel_constitution_sha256: str,
    rules_version: str,
    fields: Iterable[BlueprintField],
) -> ConceptConstitution:
    normalized = normalize_fields(sorted(fields, key=lambda item: item.path))
    _require_material_paths(
        normalized,
        {"premise.summary", "narrative.principles", "visual.direction", "constraints.production"},
        reason_code="blueprint.concept.required",
        label="concept constitution",
    )
    identity = {
        "concept_id": _token(concept_id, "concept_id"),
        "channel_constitution_sha256": str(_sha(channel_constitution_sha256, "channel constitution")),
        "rules_version": _token(rules_version, "rules_version"),
        "fields": _fields_mapping(normalized),
    }
    constitution_id, digest = _identity("concept-constitution", identity)
    return ConceptConstitution(
        constitution_id=constitution_id,
        constitution_sha256=digest,
        concept_id=OpaqueId(concept_id),
        channel_constitution_sha256=HashDigest(channel_constitution_sha256),
        rules_version=rules_version,
        fields=normalized,
    )


def episode_intent_to_mapping(value: EpisodeIntent) -> dict[str, object]:
    normalized = normalize_fields(value.fields)
    _require_material_paths(
        normalized,
        {"goal.narrative", "audience.effect", "success.criteria", "duration.target"},
        reason_code="blueprint.intent.required",
        label="episode intent",
    )
    if value.needs_complexity not in COMPLEXITIES:
        raise BlueprintContractError(
            "blueprint.intent.complexity", "invalid needs complexity"
        )
    signals = tuple(value.activation_signals)
    if signals != tuple(sorted(set(signals))) or any(
        not TOKEN.fullmatch(item) for item in signals
    ):
        raise BlueprintContractError(
            "blueprint.intent.signals",
            "activation signals must be sorted unique tokens",
        )
    identity = {
        "episode_id": _token(str(value.episode_id), "episode_id"),
        "channel_constitution_sha256": str(
            _sha(str(value.channel_constitution_sha256), "channel constitution")
        ),
        "concept_constitution_sha256": str(
            _sha(str(value.concept_constitution_sha256), "concept constitution")
        ),
        "needs_complexity": value.needs_complexity,
        "activation_signals": list(signals),
        "fields": _fields_mapping(value.fields),
    }
    expected_id, expected_sha = _identity("episode-intent", identity)
    if value.intent_id != expected_id or value.intent_sha256 != expected_sha:
        raise BlueprintContractError(
            "blueprint.intent.identity", "episode intent identity mismatch"
        )
    return {
        "artifact_version": "episode-intent/1.0",
        "intent_id": str(value.intent_id),
        "intent_sha256": str(value.intent_sha256),
        **identity,
    }


def build_episode_intent(
    *,
    episode_id: str,
    channel_constitution_sha256: str,
    concept_constitution_sha256: str,
    needs_complexity: str,
    activation_signals: Iterable[str],
    fields: Iterable[BlueprintField],
) -> EpisodeIntent:
    normalized = normalize_fields(sorted(fields, key=lambda item: item.path))
    _require_material_paths(
        normalized,
        {"goal.narrative", "audience.effect", "success.criteria", "duration.target"},
        reason_code="blueprint.intent.required",
        label="episode intent",
    )
    if needs_complexity not in COMPLEXITIES:
        raise BlueprintContractError("blueprint.intent.complexity", "invalid needs complexity")
    signals = tuple(sorted(set(activation_signals)))
    if any(not TOKEN.fullmatch(item) for item in signals):
        raise BlueprintContractError(
            "blueprint.intent.signals", "activation signals must be sorted unique tokens"
        )
    identity = {
        "episode_id": _token(episode_id, "episode_id"),
        "channel_constitution_sha256": str(_sha(channel_constitution_sha256, "channel constitution")),
        "concept_constitution_sha256": str(_sha(concept_constitution_sha256, "concept constitution")),
        "needs_complexity": needs_complexity,
        "activation_signals": list(signals),
        "fields": _fields_mapping(normalized),
    }
    intent_id, digest = _identity("episode-intent", identity)
    return EpisodeIntent(
        intent_id=intent_id,
        intent_sha256=digest,
        episode_id=OpaqueId(episode_id),
        channel_constitution_sha256=HashDigest(channel_constitution_sha256),
        concept_constitution_sha256=HashDigest(concept_constitution_sha256),
        needs_complexity=needs_complexity,
        activation_signals=signals,
        fields=normalized,
    )


def _ownership_mapping(value: FieldOwnership) -> dict[str, object]:
    if not FIELD_PATH.fullmatch(value.field_path):
        raise BlueprintContractError("blueprint.ownership.path", "invalid ownership field path")
    owner = _token(str(value.owner_director_id), "owner director")
    verifiers = tuple(str(item) for item in value.verifier_director_ids)
    if not verifiers or verifiers != tuple(sorted(set(verifiers))) or owner in verifiers:
        raise BlueprintContractError(
            "blueprint.ownership.verifiers", "owner and distinct sorted verifiers are required"
        )
    for verifier in verifiers:
        _token(verifier, "verifier director")
    return {
        "field_path": value.field_path,
        "owner_director_id": owner,
        "verifier_director_ids": list(verifiers),
    }


def _provenance_mapping(value: DirectorProvenance) -> dict[str, str]:
    return {
        "director_id": _token(str(value.director_id), "director_id"),
        "director_version": _version(value.director_version, "director_version"),
        "charter_sha256": str(_sha(str(value.charter_sha256), "charter_sha256")),
        "assessment_sha256": str(_sha(str(value.assessment_sha256), "assessment_sha256")),
        "input_blueprint_sha256": str(_sha(str(value.input_blueprint_sha256), "input blueprint")),
        "blueprint_context_sha256": str(_sha(str(value.blueprint_context_sha256), "blueprint context")),
    }


def _validate_blueprint_detail(fields: tuple[BlueprintField, ...]) -> None:
    paths = {item.path for item in fields}
    if not ROOT_REQUIRED_FIELDS.issubset(paths):
        missing = sorted(ROOT_REQUIRED_FIELDS - paths)
        raise BlueprintContractError(
            "blueprint.required_field", f"required Blueprint fields missing: {missing}"
        )
    shot_ids = {
        parts[1]
        for path in paths
        if len((parts := path.split("."))) >= 3 and parts[0] == "shot_graph"
    }
    if not shot_ids:
        raise BlueprintContractError("blueprint.shot.missing", "at least one shot is required")
    for shot_id in shot_ids:
        missing = {
            suffix
            for suffix in SHOT_REQUIRED_SUFFIXES
            if f"shot_graph.{shot_id}.{suffix}" not in paths
        }
        if missing:
            raise BlueprintContractError(
                "blueprint.shot.required_field",
                f"shot {shot_id} lacks required fields: {sorted(missing)}",
            )


def build_blueprint_source_bundle(
    *,
    channel_constitution: ChannelConstitution,
    concept_constitution: ConceptConstitution,
    episode_intent: EpisodeIntent,
) -> BlueprintSourceBundle:
    channel_constitution_to_mapping(channel_constitution)
    concept_constitution_to_mapping(concept_constitution)
    episode_intent_to_mapping(episode_intent)
    if (
        concept_constitution.channel_constitution_sha256
        != channel_constitution.constitution_sha256
        or episode_intent.channel_constitution_sha256
        != channel_constitution.constitution_sha256
        or episode_intent.concept_constitution_sha256
        != concept_constitution.constitution_sha256
    ):
        raise BlueprintContractError(
            "blueprint.source_chain",
            "Channel, Concept, and EpisodeIntent artifacts do not form one exact chain",
        )
    return BlueprintSourceBundle(
        channel_constitution=channel_constitution,
        concept_constitution=concept_constitution,
        episode_intent=episode_intent,
    )


def _blueprint_identity_mapping(value: ProductionBlueprint) -> dict[str, object]:
    fields = normalize_fields(value.fields)
    _validate_blueprint_detail(fields)
    identity_episode = next(
        item for item in fields if item.path == "identity.episode_id"
    )
    if parse_json_bytes(identity_episode.value_json.encode("utf-8")) != str(
        value.episode_id
    ):
        raise BlueprintContractError(
            "blueprint.episode_identity",
            "identity.episode_id must exactly match the top-level episode_id",
        )
    if value.status is BlueprintStatus.COHERENT:
        empty = tuple(
            item.path for item in fields if not _is_material_field_value(item)
        )
        if empty:
            raise BlueprintContractError(
                "blueprint.coherent.empty_detail",
                f"coherent Blueprint fields must contain material values: {empty}",
            )
        _validate_coherent_detail_semantics(fields)
    ownership = tuple(value.ownership)
    ownership_paths = [item.field_path for item in ownership]
    field_paths = [item.path for item in fields]
    if ownership_paths != sorted(ownership_paths) or ownership_paths != field_paths:
        raise BlueprintContractError(
            "blueprint.ownership.coverage", "ownership must exactly cover active fields in order"
        )
    ownership_mapping = [_ownership_mapping(item) for item in ownership]
    provenance = tuple(value.director_provenance)
    provenance_mapping = [_provenance_mapping(item) for item in provenance]
    if provenance_mapping != sorted(provenance_mapping, key=lambda item: (item["director_id"], item["assessment_sha256"])):
        raise BlueprintContractError(
            "blueprint.provenance.order", "director provenance must use canonical order"
        )
    provenance_ids = tuple(item["director_id"] for item in provenance_mapping)
    if len(provenance_ids) != len(set(provenance_ids)):
        raise BlueprintContractError(
            "blueprint.provenance.duplicate", "director provenance must be unique per director"
        )
    blockers = tuple(value.unresolved_blockers)
    if blockers != tuple(sorted(set(blockers))) or any(not TOKEN.fullmatch(item) for item in blockers):
        raise BlueprintContractError(
            "blueprint.blocker.order", "blockers must be sorted unique reason codes"
        )
    if value.status is BlueprintStatus.COHERENT and blockers:
        raise BlueprintContractError("blueprint.status.blockers", "coherent Blueprint has blockers")
    if value.status is BlueprintStatus.COHERENT:
        required_directors = {
            str(item.owner_director_id) for item in ownership
        } | {
            str(director_id)
            for item in ownership
            for director_id in item.verifier_director_ids
        }
        if set(provenance_ids) != required_directors:
            raise BlueprintContractError(
                "blueprint.provenance.coverage",
                "coherent Blueprint provenance must exactly cover every active owner and verifier",
            )
    if value.status is BlueprintStatus.BLOCKED and not blockers:
        raise BlueprintContractError("blueprint.status.blockers", "blocked Blueprint needs blockers")
    if not isinstance(value.revision, int) or isinstance(value.revision, bool) or value.revision < 1:
        raise BlueprintContractError("blueprint.revision", "revision must be a positive integer")
    return {
        "episode_id": _token(str(value.episode_id), "episode_id"),
        "revision": value.revision,
        "status": value.status.value,
        "context": blueprint_context_to_mapping(value.context),
        "fields": _fields_mapping(fields),
        "ownership": ownership_mapping,
        "director_provenance": provenance_mapping,
        "unresolved_blockers": list(blockers),
    }


def production_blueprint_to_mapping(value: ProductionBlueprint) -> dict[str, object]:
    identity = _blueprint_identity_mapping(value)
    expected_id, expected_sha = _identity("production-blueprint", identity)
    if value.blueprint_id != expected_id or value.blueprint_sha256 != expected_sha:
        raise BlueprintContractError(
            "blueprint.identity", "production Blueprint identity mismatch"
        )
    return {
        "artifact_version": "production-blueprint/1.0",
        "blueprint_id": str(value.blueprint_id),
        "blueprint_sha256": str(value.blueprint_sha256),
        **identity,
    }


def production_blueprint_artifact_sha256(
    value: ProductionBlueprint,
) -> HashDigest:
    """Return the exact canonical serialized-byte digest for an artifact reference."""

    return HashDigest(
        hashlib.sha256(canonical_json_bytes(production_blueprint_to_mapping(value))).hexdigest()
    )


def validate_blueprint_source_bundle(
    blueprint: ProductionBlueprint, source_bundle: BlueprintSourceBundle
) -> None:
    production_blueprint_to_mapping(blueprint)
    expected = build_blueprint_source_bundle(
        channel_constitution=source_bundle.channel_constitution,
        concept_constitution=source_bundle.concept_constitution,
        episode_intent=source_bundle.episode_intent,
    )
    context = blueprint.context
    intent = expected.episode_intent
    if (
        blueprint.episode_id != intent.episode_id
        or context.channel_constitution_sha256
        != expected.channel_constitution.constitution_sha256
        or context.concept_constitution_sha256
        != expected.concept_constitution.constitution_sha256
        or context.episode_intent_sha256 != intent.intent_sha256
    ):
        raise BlueprintContractError(
            "blueprint.source_binding",
            "Blueprint is not bound to the exact Channel/Concept/EpisodeIntent source chain",
        )


def _assemble_production_blueprint(
    *,
    episode_id: str,
    revision: int,
    status: BlueprintStatus,
    context: BlueprintContext,
    fields: Iterable[BlueprintField],
    ownership: Iterable[FieldOwnership],
    director_provenance: Iterable[DirectorProvenance] = (),
    unresolved_blockers: Iterable[str] = (),
) -> ProductionBlueprint:
    canonical_fields = tuple(sorted(fields, key=lambda item: item.path))
    canonical_ownership = tuple(sorted(ownership, key=lambda item: item.field_path))
    canonical_provenance = tuple(
        sorted(
            director_provenance,
            key=lambda item: (str(item.director_id), str(item.assessment_sha256)),
        )
    )
    canonical_blockers = tuple(sorted(set(unresolved_blockers)))
    value = ProductionBlueprint(
        blueprint_id=OpaqueId("pending"),
        blueprint_sha256=HashDigest("0" * 64),
        episode_id=OpaqueId(episode_id),
        revision=revision,
        status=status,
        context=context,
        fields=canonical_fields,
        ownership=canonical_ownership,
        director_provenance=canonical_provenance,
        unresolved_blockers=canonical_blockers,
    )
    identity = _blueprint_identity_mapping(value)
    blueprint_id, digest = _identity("production-blueprint", identity)
    return ProductionBlueprint(
        blueprint_id=blueprint_id,
        blueprint_sha256=digest,
        episode_id=value.episode_id,
        revision=value.revision,
        status=value.status,
        context=value.context,
        fields=value.fields,
        ownership=value.ownership,
        director_provenance=value.director_provenance,
        unresolved_blockers=value.unresolved_blockers,
    )


def build_production_blueprint(
    *,
    episode_id: str,
    revision: int,
    status: BlueprintStatus,
    context: BlueprintContext,
    fields: Iterable[BlueprintField],
    ownership: Iterable[FieldOwnership],
    director_provenance: Iterable[DirectorProvenance] = (),
    unresolved_blockers: Iterable[str] = (),
) -> ProductionBlueprint:
    """Build an unevaluated draft or blocked Blueprint.

    ``coherent`` is a verified Director-synthesis promotion state and cannot be
    minted by the general-purpose builder.  Persisted coherent artifacts remain
    loadable for observation, while callers that rely on the promotion claim
    must use the Director Mesh verifier with the complete evidence bundle.
    """

    if status is BlueprintStatus.COHERENT:
        raise BlueprintContractError(
            "blueprint.coherent.promotion_required",
            "coherent Blueprints must be produced by verified Director synthesis",
        )
    return _assemble_production_blueprint(
        episode_id=episode_id,
        revision=revision,
        status=status,
        context=context,
        fields=fields,
        ownership=ownership,
        director_provenance=director_provenance,
        unresolved_blockers=unresolved_blockers,
    )


def _load_production_blueprint(
    *,
    episode_id: str,
    revision: int,
    status: BlueprintStatus,
    context: BlueprintContext,
    fields: Iterable[BlueprintField],
    ownership: Iterable[FieldOwnership],
    director_provenance: Iterable[DirectorProvenance],
    unresolved_blockers: Iterable[str],
) -> ProductionBlueprint:
    """Rehydrate a structurally valid artifact without asserting promotion evidence."""

    return _assemble_production_blueprint(
        episode_id=episode_id,
        revision=revision,
        status=status,
        context=context,
        fields=fields,
        ownership=ownership,
        director_provenance=director_provenance,
        unresolved_blockers=unresolved_blockers,
    )


def _build_synthesized_production_blueprint(
    *,
    base_blueprint: ProductionBlueprint,
    fields: Iterable[BlueprintField],
    director_provenance: Iterable[DirectorProvenance],
) -> ProductionBlueprint:
    """Internal constructor for an already revalidated Director synthesis."""

    production_blueprint_to_mapping(base_blueprint)
    return _assemble_production_blueprint(
        episode_id=str(base_blueprint.episode_id),
        revision=base_blueprint.revision + 1,
        status=BlueprintStatus.COHERENT,
        context=base_blueprint.context,
        fields=fields,
        ownership=base_blueprint.ownership,
        director_provenance=director_provenance,
        unresolved_blockers=(),
    )


def field_index(blueprint: ProductionBlueprint) -> dict[str, BlueprintField]:
    production_blueprint_to_mapping(blueprint)
    return {item.path: item for item in blueprint.fields}
