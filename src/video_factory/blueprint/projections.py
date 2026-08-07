"""Deterministic, read-only shadow projections from a ProductionBlueprint.

The envelope is deliberately a different artifact family from every legacy
view.  A projected payload can therefore be compared with legacy state, but
cannot be mistaken for an authoritative brief, storyboard, packet, edit, or
publish document.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
import hashlib
import re

from video_factory.config.canonical import canonical_json_bytes, canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    HashDigest,
    OpaqueId,
)

from .contracts import (
    BlueprintField,
    BlueprintProjection,
    BlueprintProjectionKind,
    ProductionBlueprint,
    ShadowComparison,
    ShadowFieldDifference,
    UnverifiedShadowObservation,
)
from .model import (
    BlueprintContractError,
    blueprint_context_sha256,
    field_value_sha256,
    normalize_fields,
    production_blueprint_to_mapping,
    production_blueprint_artifact_sha256,
)
from .reference_paths import ReferencePathError, require_canonical_reference_path


PROJECTION_COMPILER_ID = OpaqueId("blueprint-shadow-compiler")
PROJECTION_COMPILER_VERSION = "1.0"
COMPARATOR_ID = OpaqueId("blueprint-shadow-comparator")
COMPARATOR_VERSION = "1.0"
AUTHORITY_EFFECT = "none"
OBSERVATION_TRUST_STATE = "unverified"
COMPARISON_SEMANTICS = "diagnostic_only"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_VERSION = re.compile(r"^[a-z][a-z0-9-]*/[1-9][0-9]*\.[0-9]+$")
_OPAQUE_ID = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")

_VIEW_PREFIXES: dict[BlueprintProjectionKind, tuple[str, ...]] = {
    BlueprintProjectionKind.BRIEF: (
        "audience_and_success",
        "delivery_and_distribution",
        "identity",
        "intent",
        "visual_language",
    ),
    BlueprintProjectionKind.STORYBOARD: (
        "asset_and_reference_locks",
        "continuity_state",
        "identity",
        "intent",
        "narrative",
        "risks_and_acceptance",
        "shot_graph",
        "visual_language",
    ),
    BlueprintProjectionKind.GENERATION: (
        "asset_and_reference_locks",
        "generation_strategy",
        "identity",
        "shot_graph",
        "visual_language",
    ),
    BlueprintProjectionKind.EDIT: (
        "continuity_state",
        "edit_timeline",
        "identity",
        "narrative",
        "shot_graph",
    ),
    BlueprintProjectionKind.SOUND: (
        "identity",
        "shot_graph",
        "sound_design",
    ),
    BlueprintProjectionKind.PUBLISH: (
        "audience_and_success",
        "continuity_state",
        "delivery_and_distribution",
        "identity",
        "risks_and_acceptance",
    ),
}

_LEGACY_VERSIONS: dict[BlueprintProjectionKind, str] = {
    BlueprintProjectionKind.BRIEF: "brief/1.0",
    BlueprintProjectionKind.STORYBOARD: "storyboard/1.0",
    BlueprintProjectionKind.GENERATION: "generation-packet/2.1",
    BlueprintProjectionKind.EDIT: "edit-manifest/1.0",
    BlueprintProjectionKind.SOUND: "sound-manifest/1.0",
    BlueprintProjectionKind.PUBLISH: "publish-manifest/1.0",
}


def _reference_mapping(value: ArtifactReference) -> dict[str, str]:
    path = str(value.path)
    try:
        require_canonical_reference_path(path)
    except ReferencePathError as error:
        raise BlueprintContractError(
            "blueprint.reference.invalid", "invalid immutable artifact reference"
        ) from error
    if not _SHA256.fullmatch(str(value.sha256)) or not _ARTIFACT_VERSION.fullmatch(
        str(value.artifact_version)
    ):
        raise BlueprintContractError(
            "blueprint.reference.invalid", "invalid immutable artifact reference"
        )
    return {
        "path": str(value.path),
        "sha256": str(value.sha256),
        "artifact_version": str(value.artifact_version),
    }


def _field_mappings(fields: Iterable[BlueprintField]) -> list[dict[str, str]]:
    return [
        {"path": item.path, "value_json": item.value_json}
        for item in normalize_fields(fields)
    ]


def projection_compiler_sha256() -> HashDigest:
    """Return the digest of the complete versioned projection rules."""

    return canonical_sha256(
        {
            "compiler_id": str(PROJECTION_COMPILER_ID),
            "compiler_version": PROJECTION_COMPILER_VERSION,
            "projection_artifact_version": "blueprint-projection/1.0",
            "authority_effect": AUTHORITY_EFFECT,
            "read_only": True,
            "shadow_only": True,
            "views": {
                kind.value: {
                    "legacy_artifact_version": _LEGACY_VERSIONS[kind],
                    "prefixes": list(_VIEW_PREFIXES[kind]),
                }
                for kind in sorted(_VIEW_PREFIXES, key=lambda item: item.value)
            },
        }
    )


def _projection_identity_mapping(value: BlueprintProjection) -> dict[str, object]:
    if value.shadow_only is not True or value.read_only is not True:
        raise BlueprintContractError(
            "blueprint.projection.shadow", "projection must remain shadow-only/read-only"
        )
    if value.editable is not False or value.authority_effect != AUTHORITY_EFFECT:
        raise BlueprintContractError(
            "blueprint.projection.authority", "projection cannot grant authority or edits"
        )
    if value.compiler_id != PROJECTION_COMPILER_ID or (
        value.compiler_version != PROJECTION_COMPILER_VERSION
    ):
        raise BlueprintContractError(
            "blueprint.projection.compiler", "projection compiler identity is stale"
        )
    if value.compiler_sha256 != projection_compiler_sha256():
        raise BlueprintContractError(
            "blueprint.projection.compiler", "projection compiler digest is stale"
        )
    if value.legacy_artifact_version != _LEGACY_VERSIONS[value.view_kind]:
        raise BlueprintContractError(
            "blueprint.projection.legacy_version", "legacy view version mismatch"
        )
    if str(value.source_blueprint_ref.artifact_version) != "production-blueprint/1.0":
        raise BlueprintContractError(
            "blueprint.projection.source", "projection source must be a Blueprint reference"
        )
    if not _SHA256.fullmatch(str(value.source_blueprint_sha256)):
        raise BlueprintContractError(
            "blueprint.projection.source", "projection source logical digest is invalid"
        )
    if not _OPAQUE_ID.fullmatch(str(value.source_blueprint_id)) or not _SHA256.fullmatch(
        str(value.blueprint_context_sha256)
    ):
        raise BlueprintContractError(
            "blueprint.projection.source",
            "projection source identity and context digest must be canonical",
        )
    _reference_mapping(value.source_blueprint_ref)
    fields = normalize_fields(value.fields)
    payload_sha256 = canonical_sha256({"fields": _field_mappings(fields)})
    if value.payload_sha256 != payload_sha256:
        raise BlueprintContractError(
            "blueprint.projection.payload", "projection payload digest mismatch"
        )
    return {
        "source_blueprint_id": str(value.source_blueprint_id),
        "source_blueprint_sha256": str(value.source_blueprint_sha256),
        "source_blueprint_ref": _reference_mapping(value.source_blueprint_ref),
        "blueprint_context_sha256": str(value.blueprint_context_sha256),
        "compiler_id": str(value.compiler_id),
        "compiler_version": value.compiler_version,
        "compiler_sha256": str(value.compiler_sha256),
        "view_kind": value.view_kind.value,
        "legacy_artifact_version": value.legacy_artifact_version,
        "payload_sha256": str(value.payload_sha256),
        "shadow_only": value.shadow_only,
        "authority_effect": value.authority_effect,
        "editable": value.editable,
        "read_only": value.read_only,
        "fields": _field_mappings(fields),
    }


def blueprint_projection_to_mapping(value: BlueprintProjection) -> dict[str, object]:
    identity = _projection_identity_mapping(value)
    digest = canonical_sha256(identity)
    expected_id = OpaqueId(f"blueprint-projection-{str(digest)[:20]}")
    if value.projection_id != expected_id or value.projection_sha256 != digest:
        raise BlueprintContractError(
            "blueprint.projection.identity", "projection identity mismatch"
        )
    return {
        "artifact_version": "blueprint-projection/1.0",
        "projection_id": str(value.projection_id),
        "projection_sha256": str(value.projection_sha256),
        **identity,
    }


def blueprint_projection_artifact_sha256(
    value: BlueprintProjection,
) -> HashDigest:
    """Return the exact canonical serialized-byte digest for a projection reference."""

    return HashDigest(
        hashlib.sha256(canonical_json_bytes(blueprint_projection_to_mapping(value))).hexdigest()
    )


def project_blueprint(
    blueprint: ProductionBlueprint,
    *,
    source_blueprint_ref: ArtifactReference,
    view_kind: BlueprintProjectionKind,
) -> BlueprintProjection:
    """Compile one non-authoritative legacy-shaped view envelope."""

    production_blueprint_to_mapping(blueprint)
    if source_blueprint_ref.sha256 != production_blueprint_artifact_sha256(blueprint):
        raise BlueprintContractError(
            "blueprint.projection.source", "source reference is not the current Blueprint"
        )
    prefixes = _VIEW_PREFIXES[view_kind]
    fields = tuple(
        item
        for item in blueprint.fields
        if any(
            item.path == prefix or item.path.startswith(prefix + ".")
            for prefix in prefixes
        )
    )
    if not fields:
        raise BlueprintContractError(
            "blueprint.projection.empty", "projection rules selected no fields"
        )
    payload_sha256 = canonical_sha256({"fields": _field_mappings(fields)})
    provisional = BlueprintProjection(
        projection_id=OpaqueId("pending"),
        projection_sha256=HashDigest("0" * 64),
        source_blueprint_id=blueprint.blueprint_id,
        source_blueprint_sha256=blueprint.blueprint_sha256,
        source_blueprint_ref=source_blueprint_ref,
        blueprint_context_sha256=blueprint_context_sha256(blueprint.context),
        compiler_id=PROJECTION_COMPILER_ID,
        compiler_version=PROJECTION_COMPILER_VERSION,
        compiler_sha256=projection_compiler_sha256(),
        view_kind=view_kind,
        legacy_artifact_version=_LEGACY_VERSIONS[view_kind],
        payload_sha256=payload_sha256,
        shadow_only=True,
        authority_effect=AUTHORITY_EFFECT,
        editable=False,
        read_only=True,
        fields=fields,
    )
    identity = _projection_identity_mapping(provisional)
    digest = canonical_sha256(identity)
    return replace(
        provisional,
        projection_id=OpaqueId(f"blueprint-projection-{str(digest)[:20]}"),
        projection_sha256=digest,
    )


def project_all_blueprint_views(
    blueprint: ProductionBlueprint, *, source_blueprint_ref: ArtifactReference
) -> tuple[BlueprintProjection, ...]:
    return tuple(
        project_blueprint(
            blueprint,
            source_blueprint_ref=source_blueprint_ref,
            view_kind=kind,
        )
        for kind in sorted(BlueprintProjectionKind, key=lambda item: item.value)
    )


def validate_projection_current(
    projection: BlueprintProjection,
    *,
    current_blueprint: ProductionBlueprint,
    current_blueprint_ref: ArtifactReference,
) -> None:
    expected = project_blueprint(
        current_blueprint,
        source_blueprint_ref=current_blueprint_ref,
        view_kind=projection.view_kind,
    )
    if projection != expected:
        raise BlueprintContractError(
            "blueprint.projection.stale", "projection does not match current Blueprint/rules"
        )


def _comparator_sha256() -> HashDigest:
    return canonical_sha256(
        {
            "comparator_id": str(COMPARATOR_ID),
            "comparator_version": COMPARATOR_VERSION,
            "comparison_semantics": COMPARISON_SEMANTICS,
            "field_path": "exact",
            "value_json": "canonical-json-exact",
        }
    )


def _unverified_observation_identity_mapping(
    value: UnverifiedShadowObservation,
) -> dict[str, object]:
    _reference_mapping(value.legacy_artifact_ref)
    fields = normalize_fields(value.fields)
    observed_sha = canonical_sha256({"fields": _field_mappings(fields)})
    if (
        value.legacy_document_sha256 != value.legacy_artifact_ref.sha256
        or value.trust_state != OBSERVATION_TRUST_STATE
        or value.diagnostic_only is not True
        or value.authority_effect != AUTHORITY_EFFECT
        or value.observed_view_sha256 != observed_sha
    ):
        raise BlueprintContractError(
            "blueprint.observation.binding",
            "unverified observation is not bound to its source bytes and asserted fields",
        )
    return {
        "legacy_artifact_ref": _reference_mapping(value.legacy_artifact_ref),
        "legacy_document_sha256": str(value.legacy_document_sha256),
        "trust_state": value.trust_state,
        "diagnostic_only": value.diagnostic_only,
        "authority_effect": value.authority_effect,
        "observed_view_sha256": str(value.observed_view_sha256),
        "fields": _field_mappings(fields),
    }


def unverified_shadow_observation_to_mapping(
    value: UnverifiedShadowObservation,
) -> dict[str, object]:
    identity = _unverified_observation_identity_mapping(value)
    digest = canonical_sha256(identity)
    if value.observation_sha256 != digest or value.observation_id != OpaqueId(
        f"unverified-shadow-observation-{str(digest)[:20]}"
    ):
        raise BlueprintContractError(
            "blueprint.observation.identity", "shadow observation identity mismatch"
        )
    return {
        "observation_version": "blueprint-unverified-shadow-observation/1.0",
        "observation_id": str(value.observation_id),
        "observation_sha256": str(value.observation_sha256),
        **identity,
    }


def record_unverified_shadow_observation(
    *,
    legacy_artifact_ref: ArtifactReference,
    legacy_document_bytes: bytes,
    observed_fields: Iterable[BlueprintField],
) -> UnverifiedShadowObservation:
    """Bind caller-asserted fields to exact legacy bytes for diagnostics only.

    W03 has no versioned semantic legacy-to-Blueprint normalizer.  Consequently
    this record deliberately makes no normalization or parity claim and cannot
    be used as cutover, readiness, or execution evidence.
    """

    _reference_mapping(legacy_artifact_ref)
    document_sha = HashDigest(hashlib.sha256(legacy_document_bytes).hexdigest())
    if document_sha != legacy_artifact_ref.sha256:
        raise BlueprintContractError(
            "blueprint.observation.input_digest",
            "legacy document bytes do not match the immutable artifact reference",
        )
    fields = normalize_fields(sorted(observed_fields, key=lambda item: item.path))
    provisional = UnverifiedShadowObservation(
        observation_id=OpaqueId("pending"),
        observation_sha256=HashDigest("0" * 64),
        legacy_artifact_ref=legacy_artifact_ref,
        legacy_document_sha256=document_sha,
        trust_state=OBSERVATION_TRUST_STATE,
        diagnostic_only=True,
        authority_effect=AUTHORITY_EFFECT,
        observed_view_sha256=canonical_sha256(
            {"fields": _field_mappings(fields)}
        ),
        fields=fields,
    )
    identity = _unverified_observation_identity_mapping(provisional)
    digest = canonical_sha256(identity)
    return replace(
        provisional,
        observation_id=OpaqueId(
            f"unverified-shadow-observation-{str(digest)[:20]}"
        ),
        observation_sha256=digest,
    )


def _comparison_identity_mapping(value: ShadowComparison) -> dict[str, object]:
    differences = tuple(value.differences)
    if differences != tuple(sorted(differences, key=lambda item: item.field_path)):
        raise BlueprintContractError(
            "blueprint.comparison.order", "comparison differences are not canonical"
        )
    if len({item.field_path for item in differences}) != len(differences):
        raise BlueprintContractError(
            "blueprint.comparison.duplicate", "comparison field paths must be unique"
        )
    if value.authority_effect != AUTHORITY_EFFECT:
        raise BlueprintContractError(
            "blueprint.comparison.authority", "comparison cannot grant authority"
        )
    if (
        value.comparator_id != COMPARATOR_ID
        or value.comparator_version != COMPARATOR_VERSION
        or value.comparator_sha256 != _comparator_sha256()
        or value.comparison_semantics != COMPARISON_SEMANTICS
        or not _SHA256.fullmatch(str(value.observation_sha256))
        or not _SHA256.fullmatch(str(value.observed_view_sha256))
        or not _SHA256.fullmatch(str(value.projection_sha256))
    ):
        raise BlueprintContractError(
            "blueprint.comparison.rules",
            "comparison must remain diagnostic and bind one unverified observation",
        )
    return {
        "legacy_artifact_ref": _reference_mapping(value.legacy_artifact_ref),
        "projection_ref": _reference_mapping(value.projection_ref),
        "projection_sha256": str(value.projection_sha256),
        "observation_sha256": str(value.observation_sha256),
        "observed_view_sha256": str(value.observed_view_sha256),
        "comparator_id": str(value.comparator_id),
        "comparator_version": value.comparator_version,
        "comparator_sha256": str(value.comparator_sha256),
        "comparison_semantics": value.comparison_semantics,
        "authority_effect": value.authority_effect,
        "differences": [
            {
                "field_path": item.field_path,
                "projected_value_sha256": (
                    str(item.projected_value_sha256)
                    if item.projected_value_sha256 is not None
                    else None
                ),
                "observed_value_sha256": (
                    str(item.observed_value_sha256)
                    if item.observed_value_sha256 is not None
                    else None
                ),
            }
            for item in differences
        ],
    }


def shadow_comparison_to_mapping(value: ShadowComparison) -> dict[str, object]:
    identity = _comparison_identity_mapping(value)
    digest = canonical_sha256(identity)
    expected_id = OpaqueId(f"shadow-comparison-{str(digest)[:20]}")
    if value.comparison_id != expected_id or value.comparison_sha256 != digest:
        raise BlueprintContractError(
            "blueprint.comparison.identity", "shadow comparison identity mismatch"
        )
    return {
        "comparison_version": "blueprint-shadow-comparison/1.0",
        "comparison_id": str(value.comparison_id),
        "comparison_sha256": str(value.comparison_sha256),
        **identity,
    }


def compare_shadow_projection(
    projection: BlueprintProjection,
    *,
    projection_ref: ArtifactReference,
    observation: UnverifiedShadowObservation,
) -> ShadowComparison:
    blueprint_projection_to_mapping(projection)
    if projection_ref.sha256 != blueprint_projection_artifact_sha256(projection) or str(
        projection_ref.artifact_version
    ) != "blueprint-projection/1.0":
        raise BlueprintContractError(
            "blueprint.comparison.projection", "comparison projection reference mismatch"
        )
    unverified_shadow_observation_to_mapping(observation)
    legacy_artifact_ref = observation.legacy_artifact_ref
    if str(legacy_artifact_ref.artifact_version) != projection.legacy_artifact_version:
        raise BlueprintContractError(
            "blueprint.comparison.legacy", "legacy reference version mismatch"
        )
    observed = observation.fields
    projected_by_path = {item.path: item for item in projection.fields}
    observed_by_path = {item.path: item for item in observed}
    differences: list[ShadowFieldDifference] = []
    for path in sorted(set(projected_by_path) | set(observed_by_path)):
        projected = projected_by_path.get(path)
        actual = observed_by_path.get(path)
        projected_sha = field_value_sha256(projected) if projected is not None else None
        actual_sha = field_value_sha256(actual) if actual is not None else None
        if projected_sha != actual_sha:
            differences.append(
                ShadowFieldDifference(
                    field_path=path,
                    projected_value_sha256=projected_sha,
                    observed_value_sha256=actual_sha,
                )
            )
    comparator_sha = _comparator_sha256()
    provisional = ShadowComparison(
        comparison_id=OpaqueId("pending"),
        comparison_sha256=HashDigest("0" * 64),
        legacy_artifact_ref=legacy_artifact_ref,
        projection_ref=projection_ref,
        projection_sha256=projection.projection_sha256,
        observation_sha256=observation.observation_sha256,
        observed_view_sha256=observation.observed_view_sha256,
        comparator_id=COMPARATOR_ID,
        comparator_version=COMPARATOR_VERSION,
        comparator_sha256=comparator_sha,
        comparison_semantics=COMPARISON_SEMANTICS,
        authority_effect=AUTHORITY_EFFECT,
        differences=tuple(differences),
    )
    identity = _comparison_identity_mapping(provisional)
    digest = canonical_sha256(identity)
    return replace(
        provisional,
        comparison_id=OpaqueId(f"shadow-comparison-{str(digest)[:20]}"),
        comparison_sha256=digest,
    )


__all__ = [
    "AUTHORITY_EFFECT",
    "COMPARATOR_ID",
    "COMPARATOR_VERSION",
    "PROJECTION_COMPILER_ID",
    "PROJECTION_COMPILER_VERSION",
    "blueprint_projection_to_mapping",
    "blueprint_projection_artifact_sha256",
    "record_unverified_shadow_observation",
    "compare_shadow_projection",
    "project_all_blueprint_views",
    "project_blueprint",
    "projection_compiler_sha256",
    "shadow_comparison_to_mapping",
    "unverified_shadow_observation_to_mapping",
    "validate_projection_current",
]
