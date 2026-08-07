"""Strict JSON boundaries for registered Blueprint-plane artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from video_factory.artifacts import validate_artifact_mapping
from video_factory.config.canonical import canonical_json_bytes
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.json_boundary import (
    JsonInputError,
    parse_json_bytes,
    require_json_object,
    validate_json_mapping,
)

from .contracts import (
    BlueprintContext,
    BlueprintField,
    BlueprintProjection,
    BlueprintProjectionKind,
    BlueprintStatus,
    ChannelConstitution,
    ConceptConstitution,
    DirectorProvenance,
    EpisodeIntent,
    FieldOwnership,
    ProductionBlueprint,
)
from .model import (
    BlueprintContractError,
    _load_production_blueprint,
    build_channel_constitution,
    build_concept_constitution,
    build_episode_intent,
    channel_constitution_to_mapping,
    concept_constitution_to_mapping,
    episode_intent_to_mapping,
    production_blueprint_to_mapping,
)
from .projections import blueprint_projection_to_mapping


BlueprintArtifact = (
    ChannelConstitution
    | ConceptConstitution
    | EpisodeIntent
    | ProductionBlueprint
    | BlueprintProjection
)


def _schema_mapping(document: Mapping[str, object]) -> dict[str, object]:
    try:
        payload = dict(validate_json_mapping(document))
    except JsonInputError as error:
        raise BlueprintContractError(error.code.value, error.detail) from error
    result = validate_artifact_mapping(payload)
    if not result.ok:
        first = result.errors[0]
        raise BlueprintContractError(
            first.reason_code or f"blueprint.schema.{first.validator}", first.as_text()
        )
    return payload


def _fields(value: object) -> tuple[BlueprintField, ...]:
    return tuple(
        BlueprintField(path=str(item["path"]), value_json=str(item["value_json"]))
        for item in cast(list[dict[str, object]], value)
    )


def _reference(value: object) -> ArtifactReference:
    item = cast(dict[str, object], value)
    return ArtifactReference(
        path=RelativeArtifactPath(str(item["path"])),
        sha256=HashDigest(str(item["sha256"])),
        artifact_version=ArtifactVersion(str(item["artifact_version"])),
    )


def _context(value: object) -> BlueprintContext:
    item = cast(dict[str, object], value)
    return BlueprintContext(
        channel_constitution_sha256=HashDigest(
            str(item["channel_constitution_sha256"])
        ),
        concept_constitution_sha256=HashDigest(
            str(item["concept_constitution_sha256"])
        ),
        episode_intent_sha256=HashDigest(str(item["episode_intent_sha256"])),
        policy_bundle_sha256=HashDigest(str(item["policy_bundle_sha256"])),
        rules_bundle_sha256=HashDigest(str(item["rules_bundle_sha256"])),
        effective_config_sha256=HashDigest(str(item["effective_config_sha256"])),
        current_manifest_sha256=HashDigest(str(item["current_manifest_sha256"])),
        evidence_graph_sha256=HashDigest(str(item["evidence_graph_sha256"])),
    )


def _assert_exact(
    payload: Mapping[str, object], rendered: Mapping[str, object], label: str
) -> None:
    if dict(payload) != dict(rendered):
        raise BlueprintContractError(
            f"blueprint.{label}.canonical", f"{label} mapping is not canonical"
        )


def channel_constitution_from_mapping(
    document: Mapping[str, object],
) -> ChannelConstitution:
    payload = _schema_mapping(document)
    value = build_channel_constitution(
        channel_id=str(payload["channel_id"]),
        rules_version=str(payload["rules_version"]),
        fields=_fields(payload["fields"]),
    )
    _assert_exact(payload, channel_constitution_to_mapping(value), "channel")
    return value


def concept_constitution_from_mapping(
    document: Mapping[str, object],
) -> ConceptConstitution:
    payload = _schema_mapping(document)
    value = build_concept_constitution(
        concept_id=str(payload["concept_id"]),
        channel_constitution_sha256=str(payload["channel_constitution_sha256"]),
        rules_version=str(payload["rules_version"]),
        fields=_fields(payload["fields"]),
    )
    _assert_exact(payload, concept_constitution_to_mapping(value), "concept")
    return value


def episode_intent_from_mapping(document: Mapping[str, object]) -> EpisodeIntent:
    payload = _schema_mapping(document)
    value = build_episode_intent(
        episode_id=str(payload["episode_id"]),
        channel_constitution_sha256=str(payload["channel_constitution_sha256"]),
        concept_constitution_sha256=str(payload["concept_constitution_sha256"]),
        needs_complexity=str(payload["needs_complexity"]),
        activation_signals=cast(list[str], payload["activation_signals"]),
        fields=_fields(payload["fields"]),
    )
    _assert_exact(payload, episode_intent_to_mapping(value), "intent")
    return value


def production_blueprint_from_mapping(
    document: Mapping[str, object],
) -> ProductionBlueprint:
    payload = _schema_mapping(document)
    ownership = tuple(
        FieldOwnership(
            field_path=str(item["field_path"]),
            owner_director_id=OpaqueId(str(item["owner_director_id"])),
            verifier_director_ids=tuple(
                OpaqueId(str(value)) for value in item["verifier_director_ids"]
            ),
        )
        for item in cast(list[dict[str, object]], payload["ownership"])
    )
    provenance = tuple(
        DirectorProvenance(
            director_id=OpaqueId(str(item["director_id"])),
            director_version=str(item["director_version"]),
            charter_sha256=HashDigest(str(item["charter_sha256"])),
            assessment_sha256=HashDigest(str(item["assessment_sha256"])),
            input_blueprint_sha256=HashDigest(
                str(item["input_blueprint_sha256"])
            ),
            blueprint_context_sha256=HashDigest(
                str(item["blueprint_context_sha256"])
            ),
        )
        for item in cast(list[dict[str, object]], payload["director_provenance"])
    )
    value = _load_production_blueprint(
        episode_id=str(payload["episode_id"]),
        revision=int(payload["revision"]),
        status=BlueprintStatus(str(payload["status"])),
        context=_context(payload["context"]),
        fields=_fields(payload["fields"]),
        ownership=ownership,
        director_provenance=provenance,
        unresolved_blockers=cast(list[str], payload["unresolved_blockers"]),
    )
    _assert_exact(payload, production_blueprint_to_mapping(value), "production")
    return value


def blueprint_projection_from_mapping(
    document: Mapping[str, object],
) -> BlueprintProjection:
    payload = _schema_mapping(document)
    value = BlueprintProjection(
        projection_id=OpaqueId(str(payload["projection_id"])),
        projection_sha256=HashDigest(str(payload["projection_sha256"])),
        source_blueprint_id=OpaqueId(str(payload["source_blueprint_id"])),
        source_blueprint_sha256=HashDigest(str(payload["source_blueprint_sha256"])),
        source_blueprint_ref=_reference(payload["source_blueprint_ref"]),
        blueprint_context_sha256=HashDigest(
            str(payload["blueprint_context_sha256"])
        ),
        compiler_id=OpaqueId(str(payload["compiler_id"])),
        compiler_version=str(payload["compiler_version"]),
        compiler_sha256=HashDigest(str(payload["compiler_sha256"])),
        view_kind=BlueprintProjectionKind(str(payload["view_kind"])),
        legacy_artifact_version=str(payload["legacy_artifact_version"]),
        payload_sha256=HashDigest(str(payload["payload_sha256"])),
        shadow_only=bool(payload["shadow_only"]),
        authority_effect=str(payload["authority_effect"]),
        editable=bool(payload["editable"]),
        read_only=bool(payload["read_only"]),
        fields=_fields(payload["fields"]),
    )
    _assert_exact(payload, blueprint_projection_to_mapping(value), "projection")
    return value


def blueprint_artifact_from_mapping(
    document: Mapping[str, object],
) -> BlueprintArtifact:
    version = document.get("artifact_version")
    parsers = {
        "channel-constitution/1.0": channel_constitution_from_mapping,
        "concept-constitution/1.0": concept_constitution_from_mapping,
        "episode-intent/1.0": episode_intent_from_mapping,
        "production-blueprint/1.0": production_blueprint_from_mapping,
        "blueprint-projection/1.0": blueprint_projection_from_mapping,
    }
    parser = parsers.get(version) if isinstance(version, str) else None
    if parser is None:
        raise BlueprintContractError(
            "blueprint.artifact.version", "unsupported Blueprint artifact version"
        )
    return parser(document)


def blueprint_artifact_from_bytes(document: bytes) -> BlueprintArtifact:
    try:
        mapping = require_json_object(parse_json_bytes(document))
    except JsonInputError as error:
        raise BlueprintContractError(error.code.value, error.detail) from error
    return blueprint_artifact_from_mapping(mapping)


def blueprint_artifact_to_mapping(value: BlueprintArtifact) -> dict[str, object]:
    if isinstance(value, ChannelConstitution):
        return channel_constitution_to_mapping(value)
    if isinstance(value, ConceptConstitution):
        return concept_constitution_to_mapping(value)
    if isinstance(value, EpisodeIntent):
        return episode_intent_to_mapping(value)
    if isinstance(value, ProductionBlueprint):
        return production_blueprint_to_mapping(value)
    if isinstance(value, BlueprintProjection):
        return blueprint_projection_to_mapping(value)
    raise BlueprintContractError(
        "blueprint.artifact.type", "unsupported Blueprint artifact type"
    )


def blueprint_artifact_to_bytes(value: BlueprintArtifact) -> bytes:
    return canonical_json_bytes(blueprint_artifact_to_mapping(value))


__all__ = [
    "BlueprintArtifact",
    "blueprint_artifact_from_bytes",
    "blueprint_artifact_from_mapping",
    "blueprint_artifact_to_bytes",
    "blueprint_artifact_to_mapping",
    "blueprint_projection_from_mapping",
    "channel_constitution_from_mapping",
    "concept_constitution_from_mapping",
    "episode_intent_from_mapping",
    "production_blueprint_from_mapping",
]
