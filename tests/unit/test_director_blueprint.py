"""W03 ProductionBlueprint, Director Mesh, and shadow-projection acceptance."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from importlib.resources import files
from pathlib import Path

import pytest

from tools.build_director_resources import build_resources
from video_factory.artifacts import ArtifactSchemaRegistry, validate_artifact_mapping
from video_factory.blueprint import (
    ROOT_REQUIRED_FIELDS,
    SHOT_REQUIRED_SUFFIXES,
    BlueprintContext,
    BlueprintContractError,
    BlueprintProjectionKind,
    BlueprintStatus,
    blueprint_artifact_from_bytes,
    blueprint_artifact_from_mapping,
    blueprint_artifact_to_bytes,
    blueprint_artifact_to_mapping,
    blueprint_context_sha256,
    blueprint_field,
    blueprint_projection_to_mapping,
    blueprint_projection_artifact_sha256,
    build_channel_constitution,
    build_blueprint_source_bundle,
    build_concept_constitution,
    build_episode_intent,
    build_production_blueprint,
    compare_shadow_projection,
    field_index,
    field_value_sha256,
    project_all_blueprint_views,
    production_blueprint_to_mapping,
    production_blueprint_artifact_sha256,
    record_unverified_shadow_observation,
    shadow_comparison_to_mapping,
    validate_projection_current,
)
from video_factory.config.canonical import canonical_sha256
from video_factory.directors import (
    MAX_CONFLICT_ROUNDS,
    ConflictStatus,
    DirectorBlocker,
    DirectorKind,
    DirectorMeshError,
    DirectorRegistryError,
    DirectorRuntimePort,
    DirectorVerdict,
    EpisodeNeedsProfile,
    NeedsComplexity,
    PatchProposal,
    ProposalKind,
    SynthesisStatus,
    activate_directors,
    build_director_assessment,
    build_director_charter,
    build_field_ownership,
    default_director_charters,
    director_activation_to_mapping,
    director_artifact_from_bytes,
    director_artifact_from_mapping,
    director_artifact_to_bytes,
    director_artifact_to_mapping,
    director_registry_sha256,
    director_synthesis_to_mapping,
    director_task_plan_to_mapping,
    plan_director_tasks,
    synthesize_director_assessments,
    validate_director_assessment,
    verify_coherent_blueprint_promotion,
)
from video_factory.directors.registry import (
    _activate_directors_for_registry,
    _build_field_ownership_for_registry,
)
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.engine import build_artifact_graph, make_artifact_snapshot
from video_factory.json_boundary import parse_json_bytes, require_json_object


def _hash(character: str) -> HashDigest:
    return HashDigest(character * 64)


def _reference(
    name: str = "evidence", version: str = "analytics-record/1.0"
) -> ArtifactReference:
    return ArtifactReference(
        path=RelativeArtifactPath(f"artifacts/{name}.json"),
        sha256=HashDigest(hashlib.sha256(name.encode("utf-8")).hexdigest()),
        artifact_version=ArtifactVersion(version),
    )


def _reference_at(
    path: str,
    digest_character: str,
    version: str = "analytics-record/1.0",
) -> ArtifactReference:
    return ArtifactReference(
        path=RelativeArtifactPath(path),
        sha256=_hash(digest_character),
        artifact_version=ArtifactVersion(version),
    )


def _blueprint_fixture(*, signals: tuple[str, ...] = ()):
    channel = build_channel_constitution(
        channel_id="channel-demo",
        rules_version="channel-rules.1",
        fields=(
            blueprint_field("visual.principles", ["clear"]),
            blueprint_field("audience.profile", {"age": "adult"}),
            blueprint_field("distribution.constraints", ["platform-primary"]),
            blueprint_field("success.criteria", ["retention"]),
        ),
    )
    concept = build_concept_constitution(
        concept_id="concept-demo",
        channel_constitution_sha256=str(channel.constitution_sha256),
        rules_version="concept-rules.1",
        fields=(
            blueprint_field("visual.direction", "cinematic"),
            blueprint_field("premise.summary", "A precise demo"),
            blueprint_field("constraints.production", ["offline"]),
            blueprint_field("narrative.principles", ["cause-and-effect"]),
        ),
    )
    intent = build_episode_intent(
        episode_id="episode-demo",
        channel_constitution_sha256=str(channel.constitution_sha256),
        concept_constitution_sha256=str(concept.constitution_sha256),
        needs_complexity="PRODUCTION",
        activation_signals=reversed(signals),
        fields=(
            blueprint_field("success.criteria", ["complete"]),
            blueprint_field("goal.narrative", "Explain the system"),
            blueprint_field("duration.target", 90),
            blueprint_field("audience.effect", "confident"),
        ),
    )
    reference_lock = {
        "path": "artifacts/source.json",
        "sha256": "a" * 64,
        "artifact_version": "analytics-record/1.0",
    }

    def detail_value(path: str) -> object:
        suffix = (
            ".".join(path.split(".")[2:])
            if path.startswith("shot_graph.")
            else ""
        )
        if path == "identity.episode_id":
            return "episode-demo"
        if path == "intent.narrative_goal" or suffix in {
            "purpose",
            "generation.prompt",
        }:
            return f"configured:{path}"
        if path in {
            "audience_and_success.success_criteria",
            "delivery_and_distribution.targets",
            "risks_and_acceptance.criteria",
        } or suffix in {"subjects", "acceptance.criteria"}:
            return [f"configured:{path}"]
        if path == "asset_and_reference_locks.references" or suffix == "generation.references":
            return [reference_lock]
        return {"configured": path}

    fields = [blueprint_field(path, detail_value(path)) for path in ROOT_REQUIRED_FIELDS]
    fields.extend(
        blueprint_field(
            f"shot_graph.s01.{suffix}",
            detail_value(f"shot_graph.s01.{suffix}"),
        )
        for suffix in SHOT_REQUIRED_SUFFIXES
    )
    conditional_scopes = {
        "factual_claims_present": "factual.claims",
        "rights_or_policy_risk": "rights.review",
        "vfx_required": "vfx.compositing",
        "dialogue_or_voice_present": "dialogue.voice",
        "localization_or_accessibility": "localization.accessibility",
        "live_production_required": "live_production.cues",
    }
    fields.extend(
        blueprint_field(conditional_scopes[signal], {"active": True})
        for signal in signals
    )
    ordered_fields = tuple(sorted(fields, key=lambda item: item.path))
    charters = default_director_charters()
    activation = activate_directors(
        episode_intent=intent,
        profile=EpisodeNeedsProfile(
            complexity=NeedsComplexity.PRODUCTION,
            activation_signals=tuple(sorted(signals)),
        ),
        charters=charters,
    )
    source_bundle = build_blueprint_source_bundle(
        channel_constitution=channel,
        concept_constitution=concept,
        episode_intent=intent,
    )
    ownership = build_field_ownership(ordered_fields, charters, activation)
    context = BlueprintContext(
        channel_constitution_sha256=channel.constitution_sha256,
        concept_constitution_sha256=concept.constitution_sha256,
        episode_intent_sha256=intent.intent_sha256,
        policy_bundle_sha256=_hash("1"),
        rules_bundle_sha256=_hash("2"),
        effective_config_sha256=_hash("3"),
        current_manifest_sha256=_hash("4"),
        evidence_graph_sha256=_hash("5"),
    )
    blueprint = build_production_blueprint(
        episode_id="episode-demo",
        revision=1,
        status=BlueprintStatus.DRAFT,
        context=context,
        fields=reversed(ordered_fields),
        ownership=reversed(ownership),
    )
    return channel, concept, intent, source_bundle, charters, activation, blueprint


def _tasks(blueprint, charters, activation, source_bundle):
    blueprint_ref = ArtifactReference(
        path=RelativeArtifactPath("artifacts/production-blueprint.json"),
        sha256=production_blueprint_artifact_sha256(blueprint),
        artifact_version=ArtifactVersion("production-blueprint/1.0"),
    )
    return plan_director_tasks(
        blueprint=blueprint,
        charters=charters,
        activation=activation,
        source_bundle=source_bundle,
        input_refs=(blueprint_ref,),
    )


def _assessment(
    task,
    charter,
    blueprint,
    activation,
    *,
    verdict: DirectorVerdict = DirectorVerdict.PASS,
    patches: tuple[PatchProposal, ...] = (),
    blockers: tuple[DirectorBlocker, ...] = (),
    confidence: int = 9_000,
    evidence_refs: tuple[ArtifactReference, ...] | None = None,
    execution_receipt: ArtifactReference | None = None,
):
    return build_director_assessment(
        task=task,
        charter=charter,
        current_blueprint=blueprint,
        activation=activation,
        verdict=verdict,
        patches=patches,
        blockers=blockers,
        recommendations=(),
        confidence_basis_points=confidence,
        assumptions=(),
        evidence_refs=(evidence_refs if evidence_refs is not None else (_reference(),)),
        model_id="model-fake",
        prompt_charter_version="director-prompt.1",
        request_sha256=_hash("6"),
        response_sha256=_hash("7"),
        execution_receipt=(
            execution_receipt
            if execution_receipt is not None
            else _reference("director-call", "external-call-reservation/1.0")
        ),
    )


def _all_assessments(blueprint, charters, activation, tasks):
    by_id = {str(item.director_id): item for item in charters}
    return tuple(
        _assessment(task, by_id[str(task.director_id)], blueprint, activation)
        for task in tasks
    )


def test_detailed_blueprint_is_deterministic_and_fully_owned() -> None:
    channel, concept, intent, _, charters, activation, blueprint = _blueprint_fixture()
    assert len(blueprint.fields) == len(ROOT_REQUIRED_FIELDS) + len(
        SHOT_REQUIRED_SUFFIXES
    )
    assert tuple(item.field_path for item in blueprint.ownership) == tuple(
        item.path for item in blueprint.fields
    )
    assert all(item.verifier_director_ids for item in blueprint.ownership)
    assert all(
        item.owner_director_id not in item.verifier_director_ids
        for item in blueprint.ownership
    )
    rebuilt = build_production_blueprint(
        episode_id="episode-demo",
        revision=1,
        status=blueprint.status,
        context=blueprint.context,
        fields=reversed(blueprint.fields),
        ownership=reversed(blueprint.ownership),
    )
    assert rebuilt == blueprint
    for value in (channel, concept, intent, blueprint):
        mapping = blueprint_artifact_to_mapping(value)
        assert validate_artifact_mapping(mapping).ok
        assert blueprint_artifact_from_mapping(mapping) == value
        assert blueprint_artifact_from_bytes(blueprint_artifact_to_bytes(value)) == value
    assert production_blueprint_artifact_sha256(blueprint) == HashDigest(
        hashlib.sha256(blueprint_artifact_to_bytes(blueprint)).hexdigest()
    )
    empty_coherent_mapping = production_blueprint_to_mapping(blueprint)
    empty_coherent_mapping["status"] = "coherent"
    empty_coherent_mapping["director_provenance"] = []
    empty_coherent_mapping["fields"] = [
        {
            **item,
            "value_json": (
                '"episode-demo"' if item["path"] == "identity.episode_id" else "null"
            ),
        }
        for item in empty_coherent_mapping["fields"]
    ]
    assert validate_artifact_mapping(empty_coherent_mapping).ok is False


def test_public_blueprint_serializers_reject_self_rehashed_schema_invalid_values() -> None:
    channel, concept, intent, _, _, _, blueprint = _blueprint_fixture()

    channel_mapping = blueprint_artifact_to_mapping(channel)
    channel_mapping["channel_id"] = "bad id"
    channel_identity = {
        key: value
        for key, value in channel_mapping.items()
        if key not in {"artifact_version", "constitution_id", "constitution_sha256"}
    }
    channel_digest = canonical_sha256(channel_identity)
    rebound_channel = replace(
        channel,
        constitution_id=OpaqueId(f"channel-constitution-{str(channel_digest)[:20]}"),
        constitution_sha256=channel_digest,
        channel_id=OpaqueId("bad id"),
    )
    with pytest.raises(BlueprintContractError, match="invalid channel_id"):
        blueprint_artifact_to_mapping(rebound_channel)

    concept_mapping = blueprint_artifact_to_mapping(concept)
    concept_mapping["rules_version"] = ""
    concept_identity = {
        key: value
        for key, value in concept_mapping.items()
        if key not in {"artifact_version", "constitution_id", "constitution_sha256"}
    }
    concept_digest = canonical_sha256(concept_identity)
    rebound_concept = replace(
        concept,
        constitution_id=OpaqueId(f"concept-constitution-{str(concept_digest)[:20]}"),
        constitution_sha256=concept_digest,
        rules_version="",
    )
    with pytest.raises(BlueprintContractError, match="invalid rules_version"):
        blueprint_artifact_to_mapping(rebound_concept)

    intent_mapping = blueprint_artifact_to_mapping(intent)
    intent_mapping["activation_signals"] = ["z", "a"]
    intent_identity = {
        key: value
        for key, value in intent_mapping.items()
        if key not in {"artifact_version", "intent_id", "intent_sha256"}
    }
    intent_digest = canonical_sha256(intent_identity)
    rebound_intent = replace(
        intent,
        intent_id=OpaqueId(f"episode-intent-{str(intent_digest)[:20]}"),
        intent_sha256=intent_digest,
        activation_signals=("z", "a"),
    )
    with pytest.raises(BlueprintContractError, match="sorted unique"):
        blueprint_artifact_to_mapping(rebound_intent)

    source_ref = ArtifactReference(
        path=RelativeArtifactPath("artifacts/production-blueprint.json"),
        sha256=production_blueprint_artifact_sha256(blueprint),
        artifact_version=ArtifactVersion("production-blueprint/1.0"),
    )
    projection = project_all_blueprint_views(
        blueprint, source_blueprint_ref=source_ref
    )[0]
    projection_mapping = blueprint_projection_to_mapping(projection)
    projection_mapping["source_blueprint_id"] = "bad id"
    projection_identity = {
        key: value
        for key, value in projection_mapping.items()
        if key not in {"artifact_version", "projection_id", "projection_sha256"}
    }
    projection_digest = canonical_sha256(projection_identity)
    rebound_projection = replace(
        projection,
        projection_id=OpaqueId(f"blueprint-projection-{str(projection_digest)[:20]}"),
        projection_sha256=projection_digest,
        source_blueprint_id=OpaqueId("bad id"),
    )
    with pytest.raises(BlueprintContractError, match="source identity"):
        blueprint_projection_to_mapping(rebound_projection)


def test_blueprint_rejects_missing_detail_and_owner_verifier_gaps() -> None:
    _, _, _, _, _, _, blueprint = _blueprint_fixture()
    with pytest.raises(BlueprintContractError, match="verified Director synthesis"):
        build_production_blueprint(
            episode_id="episode-demo",
            revision=1,
            status=BlueprintStatus.COHERENT,
            context=blueprint.context,
            fields=blueprint.fields[:-1],
            ownership=blueprint.ownership[:-1],
        )
    with pytest.raises(BlueprintContractError, match="verified Director synthesis"):
        build_production_blueprint(
            episode_id="episode-demo",
            revision=1,
            status=BlueprintStatus.COHERENT,
            context=blueprint.context,
            fields=blueprint.fields,
            ownership=blueprint.ownership,
        )


def test_coherent_blueprint_requires_material_detail_and_exact_provenance() -> None:
    _, _, _, _, _, _, blueprint = _blueprint_fixture()
    empty_fields = tuple(
        blueprint_field(
            item.path,
            "episode-demo" if item.path == "identity.episode_id" else None,
        )
        for item in blueprint.fields
    )
    with pytest.raises(BlueprintContractError, match="verified Director synthesis"):
        build_production_blueprint(
            episode_id="episode-demo",
            revision=1,
            status=BlueprintStatus.COHERENT,
            context=blueprint.context,
            fields=empty_fields,
            ownership=blueprint.ownership,
        )
    with pytest.raises(BlueprintContractError, match="verified Director synthesis"):
        build_production_blueprint(
            episode_id="episode-demo",
            revision=1,
            status=BlueprintStatus.COHERENT,
            context=blueprint.context,
            fields=blueprint.fields,
            ownership=blueprint.ownership,
        )


@pytest.mark.parametrize(
    "field_path",
    (
        "asset_and_reference_locks.references",
        "shot_graph.s01.generation.references",
    ),
)
@pytest.mark.parametrize("variant", ("exact_duplicate", "digest", "version"))
def test_coherent_promotion_rejects_duplicate_reference_lock_identity(
    field_path: str, variant: str
) -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    original = {
        "path": "artifacts/same.json",
        "sha256": "a" * 64,
        "artifact_version": "analytics-record/1.0",
    }
    duplicate = dict(original)
    if variant == "digest":
        duplicate["sha256"] = "b" * 64
    elif variant == "version":
        duplicate["artifact_version"] = "brief/1.0"
    current = field_index(blueprint)
    current[field_path] = blueprint_field(field_path, [original, duplicate])
    candidate = build_production_blueprint(
        episode_id=str(blueprint.episode_id),
        revision=blueprint.revision,
        status=BlueprintStatus.DRAFT,
        context=blueprint.context,
        fields=tuple(current[path] for path in sorted(current)),
        ownership=blueprint.ownership,
    )
    tasks = _tasks(candidate, charters, activation, sources)
    assessments = _all_assessments(candidate, charters, activation, tasks)
    with pytest.raises(BlueprintContractError, match="duplicate or colliding"):
        synthesize_director_assessments(
            blueprint=candidate,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            tasks=tasks,
            assessments=assessments,
        )


def test_coherent_promotion_rejects_cross_field_reference_conflict_and_order() -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()

    def synthesize_with(changes: dict[str, object]) -> None:
        current = field_index(blueprint)
        for path, value in changes.items():
            current[path] = blueprint_field(path, value)
        candidate = build_production_blueprint(
            episode_id=str(blueprint.episode_id),
            revision=blueprint.revision,
            status=BlueprintStatus.DRAFT,
            context=blueprint.context,
            fields=tuple(current[path] for path in sorted(current)),
            ownership=blueprint.ownership,
        )
        tasks = _tasks(candidate, charters, activation, sources)
        assessments = _all_assessments(candidate, charters, activation, tasks)
        synthesize_director_assessments(
            blueprint=candidate,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            tasks=tasks,
            assessments=assessments,
        )

    common = {
        "path": "artifacts/shared.json",
        "artifact_version": "analytics-record/1.0",
    }
    with pytest.raises(BlueprintContractError, match="different immutable artifacts"):
        synthesize_with(
            {
                "asset_and_reference_locks.references": [
                    {**common, "sha256": "a" * 64}
                ],
                "shot_graph.s01.generation.references": [
                    {**common, "sha256": "b" * 64}
                ],
            }
        )
    with pytest.raises(BlueprintContractError, match="canonical path order"):
        synthesize_with(
            {
                "asset_and_reference_locks.references": [
                    {
                        "path": "artifacts/z.json",
                        "sha256": "a" * 64,
                        "artifact_version": "analytics-record/1.0",
                    },
                    {
                        "path": "artifacts/a.json",
                        "sha256": "b" * 64,
                        "artifact_version": "analytics-record/1.0",
                    },
                ]
            }
        )


@pytest.mark.parametrize(
    "alias_path",
    (
        "./artifacts/alias.json",
        "artifacts/./alias.json",
        "artifacts//alias.json",
        "artifacts/alias.json/",
        "artifacts/\x7falias.json",
        "artifacts/\x85alias.json",
    ),
)
def test_coherent_promotion_rejects_noncanonical_reference_path_aliases(
    alias_path: str,
) -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    current = field_index(blueprint)
    current["asset_and_reference_locks.references"] = blueprint_field(
        "asset_and_reference_locks.references",
        [
            {
                "path": "artifacts/alias.json",
                "sha256": "a" * 64,
                "artifact_version": "analytics-record/1.0",
            }
        ],
    )
    current["shot_graph.s01.generation.references"] = blueprint_field(
        "shot_graph.s01.generation.references",
        [
            {
                "path": alias_path,
                "sha256": "b" * 64,
                "artifact_version": "brief/1.0",
            }
        ],
    )
    candidate = build_production_blueprint(
        episode_id=str(blueprint.episode_id),
        revision=blueprint.revision,
        status=BlueprintStatus.DRAFT,
        context=blueprint.context,
        fields=tuple(current[path] for path in sorted(current)),
        ownership=blueprint.ownership,
    )
    tasks = _tasks(candidate, charters, activation, sources)
    assessments = _all_assessments(candidate, charters, activation, tasks)
    with pytest.raises(BlueprintContractError, match="unsafe reference path"):
        synthesize_director_assessments(
            blueprint=candidate,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            tasks=tasks,
            assessments=assessments,
        )


def test_registry_rejects_missing_and_ambiguous_field_owners() -> None:
    _, _, intent, _, charters, _, blueprint = _blueprint_fixture()
    profile = EpisodeNeedsProfile(
        complexity=NeedsComplexity.PRODUCTION, activation_signals=()
    )
    without_showrunner = tuple(
        item for item in charters if str(item.director_id) != "showrunner"
    )
    with pytest.raises(DirectorRegistryError, match="exact target-owned registry"):
        activate_directors(
            episode_intent=intent,
            profile=profile,
            charters=without_showrunner,
        )
    missing_activation = _activate_directors_for_registry(
        episode_intent=intent,
        profile=profile,
        charters=without_showrunner,
    )
    with pytest.raises(DirectorRegistryError, match="no owner"):
        _build_field_ownership_for_registry(
            blueprint.fields, without_showrunner, missing_activation
        )
    clone = build_director_charter(
        director_id="showrunner-clone",
        kind=DirectorKind.CORE,
        owned_patterns=("identity", "intent"),
        verified_patterns=("*",),
        conflict_priority=90,
    )
    ambiguous_charters = tuple(
        sorted((*charters, clone), key=lambda item: str(item.director_id))
    )
    ambiguous_activation = _activate_directors_for_registry(
        episode_intent=intent,
        profile=profile,
        charters=ambiguous_charters,
    )
    with pytest.raises(DirectorRegistryError, match="ambiguous owner"):
        _build_field_ownership_for_registry(
            blueprint.fields, ambiguous_charters, ambiguous_activation
        )


def test_production_mesh_rejects_self_consistent_reduced_registry() -> None:
    _, _, intent, sources, _, _, blueprint = _blueprint_fixture()
    custom_charters = tuple(
        sorted(
            (
                build_director_charter(
                    director_id="custom-owner",
                    kind=DirectorKind.CORE,
                    owned_patterns=("*",),
                    verified_patterns=(),
                    conflict_priority=90,
                ),
                build_director_charter(
                    director_id="custom-verifier",
                    kind=DirectorKind.CORE,
                    owned_patterns=(),
                    verified_patterns=("*",),
                    conflict_priority=80,
                ),
            ),
            key=lambda item: str(item.director_id),
        )
    )
    profile = EpisodeNeedsProfile(
        complexity=NeedsComplexity.PRODUCTION, activation_signals=()
    )
    custom_activation = _activate_directors_for_registry(
        episode_intent=intent,
        profile=profile,
        charters=custom_charters,
    )
    custom_ownership = _build_field_ownership_for_registry(
        blueprint.fields, custom_charters, custom_activation
    )
    custom_blueprint = build_production_blueprint(
        episode_id=str(blueprint.episode_id),
        revision=blueprint.revision,
        status=BlueprintStatus.DRAFT,
        context=blueprint.context,
        fields=blueprint.fields,
        ownership=custom_ownership,
    )
    with pytest.raises(DirectorRegistryError, match="exact target-owned registry"):
        activate_directors(
            episode_intent=intent,
            profile=profile,
            charters=custom_charters,
        )
    with pytest.raises(DirectorRegistryError, match="exact target-owned registry"):
        build_field_ownership(
            custom_blueprint.fields, custom_charters, custom_activation
        )
    custom_ref = ArtifactReference(
        path=RelativeArtifactPath("artifacts/custom-blueprint.json"),
        sha256=production_blueprint_artifact_sha256(custom_blueprint),
        artifact_version=ArtifactVersion("production-blueprint/1.0"),
    )
    with pytest.raises(DirectorRegistryError, match="exact target-owned registry"):
        plan_director_tasks(
            blueprint=custom_blueprint,
            charters=custom_charters,
            activation=custom_activation,
            source_bundle=sources,
            input_refs=(custom_ref,),
        )
    with pytest.raises(DirectorRegistryError, match="exact target-owned registry"):
        synthesize_director_assessments(
            blueprint=custom_blueprint,
            charters=custom_charters,
            activation=custom_activation,
            source_bundle=sources,
            tasks=(),
            assessments=(),
        )


@pytest.mark.parametrize(
    "signal",
    [
        "factual_claims_present",
        "rights_or_policy_risk",
        "vfx_required",
        "dialogue_or_voice_present",
        "localization_or_accessibility",
        "live_production_required",
    ],
)
def test_each_conditional_specialist_has_concrete_field_scope(signal: str) -> None:
    _, _, _, _, charters, activation, blueprint = _blueprint_fixture(signals=(signal,))
    assert len(activation.active_director_ids) == 13
    conditional_id = next(
        item.director_id for item in charters if signal in item.activation_signals
    )
    assert any(
        item.owner_director_id == conditional_id for item in blueprint.ownership
    )


def test_task_planning_rederives_conditional_activation_and_rejects_unknown_signals() -> None:
    signal = "rights_or_policy_risk"
    channel, concept, intent, sources, charters, activation, blueprint = (
        _blueprint_fixture(signals=(signal,))
    )
    specialist_id = next(
        item.director_id for item in charters if signal in item.activation_signals
    )
    reduced_ids = tuple(
        item for item in activation.active_director_ids if item != specialist_id
    )
    reduced_identity = director_activation_to_mapping(activation)
    reduced_identity["active_director_ids"] = [str(item) for item in reduced_ids]
    reduced_digest = canonical_sha256(reduced_identity)
    reduced = replace(
        activation,
        activation_id=type(activation.activation_id)(
            f"director-activation-{str(reduced_digest)[:20]}"
        ),
        activation_sha256=reduced_digest,
        active_director_ids=reduced_ids,
    )
    with pytest.raises(DirectorRegistryError, match="registry policy"):
        _tasks(blueprint, charters, reduced, sources)

    unknown_intent = build_episode_intent(
        episode_id="episode-demo",
        channel_constitution_sha256=str(channel.constitution_sha256),
        concept_constitution_sha256=str(concept.constitution_sha256),
        needs_complexity="PRODUCTION",
        activation_signals=("unregistered_specialist_signal",),
        fields=intent.fields,
    )
    with pytest.raises(DirectorRegistryError, match="not registered"):
        activate_directors(
            episode_intent=unknown_intent,
            profile=EpisodeNeedsProfile(
                complexity=NeedsComplexity.PRODUCTION,
                activation_signals=unknown_intent.activation_signals,
            ),
            charters=charters,
        )


def test_task_mesh_is_logically_parallel_and_context_bound() -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    tasks = _tasks(blueprint, charters, activation, sources)
    assert len(tasks) == 12
    assert len({task.base_blueprint_sha256 for task in tasks}) == 1
    assert len({task.blueprint_context_sha256 for task in tasks}) == 1
    assert len({task.registry_sha256 for task in tasks}) == 1
    assert tuple(str(item.director_id) for item in tasks) == tuple(
        sorted(str(item.director_id) for item in tasks)
    )
    assert all(not hasattr(item, "previous_task_id") for item in tasks)
    with pytest.raises(DirectorMeshError, match="exact base ProductionBlueprint"):
        plan_director_tasks(
            blueprint=blueprint,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            input_refs=(_reference("wrong", "production-blueprint/1.0"),),
        )
    base_ref = ArtifactReference(
        path=RelativeArtifactPath("artifacts/production-blueprint.json"),
        sha256=production_blueprint_artifact_sha256(blueprint),
        artifact_version=ArtifactVersion("production-blueprint/1.0"),
    )
    conflicting_ref = replace(base_ref, sha256=_hash("f"))
    with pytest.raises(DirectorMeshError, match="multiple identities"):
        plan_director_tasks(
            blueprint=blueprint,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            input_refs=(base_ref, conflicting_ref),
        )


def test_activation_must_match_intent_and_blueprint_context() -> None:
    channel, concept, intent, sources, charters, _, blueprint = _blueprint_fixture()
    with pytest.raises(DirectorRegistryError, match="exactly match"):
        activate_directors(
            episode_intent=intent,
            profile=EpisodeNeedsProfile(
                complexity=NeedsComplexity.PRODUCTION,
                activation_signals=("live_production_required",),
            ),
            charters=charters,
        )
    other_intent = build_episode_intent(
        episode_id="episode-other",
        channel_constitution_sha256=str(channel.constitution_sha256),
        concept_constitution_sha256=str(concept.constitution_sha256),
        needs_complexity="PRODUCTION",
        activation_signals=(),
        fields=intent.fields,
    )
    other_activation = activate_directors(
        episode_intent=other_intent,
        profile=EpisodeNeedsProfile(
            complexity=NeedsComplexity.PRODUCTION, activation_signals=()
        ),
        charters=charters,
    )
    with pytest.raises(DirectorRegistryError, match="current EpisodeIntent"):
        plan_director_tasks(
            blueprint=blueprint,
            charters=charters,
            activation=other_activation,
            source_bundle=sources,
            input_refs=(
                ArtifactReference(
                    path=RelativeArtifactPath("artifacts/production-blueprint.json"),
                    sha256=production_blueprint_artifact_sha256(blueprint),
                    artifact_version=ArtifactVersion("production-blueprint/1.0"),
                ),
            ),
        )
    other_sources = build_blueprint_source_bundle(
        channel_constitution=channel,
        concept_constitution=concept,
        episode_intent=other_intent,
    )
    with pytest.raises(BlueprintContractError, match="exact Channel/Concept/EpisodeIntent"):
        plan_director_tasks(
            blueprint=blueprint,
            charters=charters,
            activation=other_activation,
            source_bundle=other_sources,
            input_refs=(
                ArtifactReference(
                    path=RelativeArtifactPath("artifacts/production-blueprint.json"),
                    sha256=production_blueprint_artifact_sha256(blueprint),
                    artifact_version=ArtifactVersion("production-blueprint/1.0"),
                ),
            ),
        )


def test_assessment_binds_task_blueprint_context_model_and_receipt() -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    task = _tasks(blueprint, charters, activation, sources)[0]
    charter = {str(item.director_id): item for item in charters}[
        str(task.director_id)
    ]
    assessment = _assessment(task, charter, blueprint, activation)
    mapping = director_artifact_to_mapping(assessment)
    assert validate_artifact_mapping(mapping).ok
    assert director_artifact_from_mapping(mapping) == assessment
    assert director_artifact_from_bytes(director_artifact_to_bytes(assessment)) == assessment
    changed_context = replace(blueprint.context, current_manifest_sha256=_hash("8"))
    changed = build_production_blueprint(
        episode_id=str(blueprint.episode_id),
        revision=blueprint.revision,
        status=blueprint.status,
        context=changed_context,
        fields=blueprint.fields,
        ownership=blueprint.ownership,
    )
    with pytest.raises(DirectorMeshError, match="current task/base/context"):
        validate_director_assessment(
            assessment,
            task=task,
            charter=charter,
            current_blueprint=changed,
            activation=activation,
        )
    with pytest.raises(DirectorMeshError, match="identity mismatch"):
        director_artifact_to_mapping(
            replace(assessment, response_sha256=_hash("9"))
        )


def test_synthesis_is_deterministic_and_bounded_to_two_rounds() -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    tasks = _tasks(blueprint, charters, activation, sources)
    assessments = _all_assessments(blueprint, charters, activation, tasks)
    first = synthesize_director_assessments(
        blueprint=blueprint,
        charters=charters,
        activation=activation,
        source_bundle=sources,
        tasks=reversed(tasks),
        assessments=reversed(assessments),
    )
    second = synthesize_director_assessments(
        blueprint=blueprint,
        charters=charters,
        activation=activation,
        source_bundle=sources,
        tasks=tasks,
        assessments=assessments,
    )
    assert first == second
    assert first.blueprint is not None
    assert first.synthesis.status is SynthesisStatus.COHERENT
    assert first.blueprint.revision == 2
    assert director_artifact_from_bytes(
        director_artifact_to_bytes(first.synthesis)
    ) == first.synthesis
    assert first.synthesis.rounds_used == 1
    assert first.synthesis.previous_synthesis_sha256 is None
    verified = verify_coherent_blueprint_promotion(
        promoted_blueprint=first.blueprint,
        synthesis=first.synthesis,
        base_blueprint=blueprint,
        charters=charters,
        activation=activation,
        source_bundle=sources,
        tasks=tasks,
        assessments=assessments,
    )
    assert verified.blueprint == first.blueprint
    assert verified.synthesis == first.synthesis
    assert verified.authority_effect == "none"
    with pytest.raises(DirectorMeshError, match="unevaluated draft"):
        _tasks(first.blueprint, charters, activation, sources)
    with pytest.raises(DirectorMeshError, match="unevaluated draft"):
        synthesize_director_assessments(
            blueprint=first.blueprint,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            tasks=tasks,
            assessments=assessments,
        )
    with pytest.raises(DirectorMeshError, match="unevaluated draft"):
        verify_coherent_blueprint_promotion(
            promoted_blueprint=first.blueprint,
            synthesis=first.synthesis,
            base_blueprint=first.blueprint,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            tasks=tasks,
            assessments=assessments,
        )

    tampered_mapping = production_blueprint_to_mapping(first.blueprint)
    provenance = tampered_mapping["director_provenance"]
    assert isinstance(provenance, list)
    provenance[0]["assessment_sha256"] = "f" * 64
    identity = {
        key: value
        for key, value in tampered_mapping.items()
        if key not in {"artifact_version", "blueprint_id", "blueprint_sha256"}
    }
    rebound_digest = canonical_sha256(identity)
    tampered_mapping["blueprint_id"] = f"production-blueprint-{str(rebound_digest)[:20]}"
    tampered_mapping["blueprint_sha256"] = str(rebound_digest)
    tampered = blueprint_artifact_from_mapping(tampered_mapping)
    with pytest.raises(DirectorMeshError, match="complete evidence bundle"):
        verify_coherent_blueprint_promotion(
            promoted_blueprint=tampered,
            synthesis=first.synthesis,
            base_blueprint=blueprint,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            tasks=tasks,
            assessments=assessments,
        )
    with pytest.raises(DirectorMeshError, match="exactly one task and assessment"):
        verify_coherent_blueprint_promotion(
            promoted_blueprint=first.blueprint,
            synthesis=first.synthesis,
            base_blueprint=blueprint,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            tasks=tasks,
            assessments=assessments[:-1],
        )
    with pytest.raises(DirectorMeshError, match="assessment digests"):
        director_artifact_to_mapping(
            replace(first.synthesis, assessment_sha256s=(HashDigest("bad"),))
        )


def test_conflict_round_two_requires_exact_blocked_predecessor_and_stops() -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    tasks = _tasks(blueprint, charters, activation, sources)
    by_id = {str(item.director_id): item for item in charters}
    assessments = tuple(
        _assessment(
            task,
            by_id[str(task.director_id)],
            blueprint,
            activation,
            confidence=(5_999 if index == 0 else 9_000),
        )
        for index, task in enumerate(tasks)
    )
    first = synthesize_director_assessments(
        blueprint=blueprint,
        charters=charters,
        activation=activation,
        source_bundle=sources,
        tasks=tasks,
        assessments=assessments,
    )
    assert first.synthesis.status is SynthesisStatus.BLOCKED
    assert first.synthesis.rounds_used == 1
    second = synthesize_director_assessments(
        blueprint=blueprint,
        charters=charters,
        activation=activation,
        source_bundle=sources,
        tasks=tasks,
        assessments=assessments,
        previous_synthesis=first.synthesis,
        previous_tasks=tasks,
        previous_assessments=assessments,
    )
    assert second.synthesis.rounds_used == MAX_CONFLICT_ROUNDS
    assert second.synthesis.previous_synthesis_sha256 == first.synthesis.synthesis_sha256
    assert second.synthesis.conflict_session_id == first.synthesis.conflict_session_id
    with pytest.raises(DirectorMeshError, match="maximum conflict rounds"):
        synthesize_director_assessments(
            blueprint=blueprint,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            tasks=tasks,
            assessments=assessments,
            previous_synthesis=second.synthesis,
        )
    with pytest.raises(DirectorMeshError, match="identity mismatch"):
        synthesize_director_assessments(
            blueprint=blueprint,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            tasks=tasks,
            assessments=assessments,
            previous_synthesis=replace(
                first.synthesis,
                conflict_session_id=type(first.synthesis.conflict_session_id)(
                    "other-session"
                ),
            ),
            previous_tasks=tasks,
            previous_assessments=assessments,
        )


def test_round_two_recomputes_exact_blocked_predecessor_evidence() -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    tasks = _tasks(blueprint, charters, activation, sources)
    by_id = {str(item.director_id): item for item in charters}
    blocked_assessments = tuple(
        _assessment(
            task,
            by_id[str(task.director_id)],
            blueprint,
            activation,
            confidence=(5_999 if index == 0 else 9_000),
        )
        for index, task in enumerate(tasks)
    )
    first = synthesize_director_assessments(
        blueprint=blueprint,
        charters=charters,
        activation=activation,
        source_bundle=sources,
        tasks=tasks,
        assessments=blocked_assessments,
    )
    current_assessments = _all_assessments(blueprint, charters, activation, tasks)
    second = synthesize_director_assessments(
        blueprint=blueprint,
        charters=charters,
        activation=activation,
        source_bundle=sources,
        tasks=tasks,
        assessments=current_assessments,
        previous_synthesis=first.synthesis,
        previous_tasks=tasks,
        previous_assessments=blocked_assessments,
    )
    assert second.blueprint is not None
    verified = verify_coherent_blueprint_promotion(
        promoted_blueprint=second.blueprint,
        synthesis=second.synthesis,
        base_blueprint=blueprint,
        charters=charters,
        activation=activation,
        source_bundle=sources,
        tasks=tasks,
        assessments=current_assessments,
        previous_synthesis=first.synthesis,
        previous_tasks=tasks,
        previous_assessments=blocked_assessments,
    )
    assert verified.blueprint == second.blueprint

    altered_digests = tuple(
        sorted((HashDigest("f" * 64), *first.synthesis.assessment_sha256s[1:]), key=str)
    )
    tampered_identity = director_synthesis_to_mapping(first.synthesis)
    tampered_identity["assessment_sha256s"] = [str(item) for item in altered_digests]
    identity = {
        key: value
        for key, value in tampered_identity.items()
        if key not in {"artifact_version", "synthesis_id", "synthesis_sha256"}
    }
    digest = canonical_sha256(identity)
    tampered_previous = replace(
        first.synthesis,
        synthesis_id=OpaqueId(f"director-synthesis-{str(digest)[:20]}"),
        synthesis_sha256=digest,
        assessment_sha256s=altered_digests,
    )
    with pytest.raises(DirectorMeshError, match="complete round-one evidence"):
        synthesize_director_assessments(
            blueprint=blueprint,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            tasks=tasks,
            assessments=current_assessments,
            previous_synthesis=tampered_previous,
            previous_tasks=tasks,
            previous_assessments=blocked_assessments,
        )
    with pytest.raises(DirectorMeshError, match="complete round-one task"):
        synthesize_director_assessments(
            blueprint=blueprint,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            tasks=tasks,
            assessments=current_assessments,
            previous_synthesis=first.synthesis,
        )


def test_round_two_rejects_cross_round_reference_identity_conflicts() -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    tasks = _tasks(blueprint, charters, activation, sources)
    by_id = {str(item.director_id): item for item in charters}
    previous_reference = _reference_at("artifacts/round-evidence.json", "a")
    previous_assessments = tuple(
        _assessment(
            task,
            by_id[str(task.director_id)],
            blueprint,
            activation,
            confidence=(5_999 if index == 0 else 9_000),
            evidence_refs=(previous_reference,),
        )
        for index, task in enumerate(tasks)
    )
    first = synthesize_director_assessments(
        blueprint=blueprint,
        charters=charters,
        activation=activation,
        source_bundle=sources,
        tasks=tasks,
        assessments=previous_assessments,
    )
    matching_current = tuple(
        _assessment(
            task,
            by_id[str(task.director_id)],
            blueprint,
            activation,
            evidence_refs=(previous_reference,),
        )
        for task in tasks
    )
    second = synthesize_director_assessments(
        blueprint=blueprint,
        charters=charters,
        activation=activation,
        source_bundle=sources,
        tasks=tasks,
        assessments=matching_current,
        previous_synthesis=first.synthesis,
        previous_tasks=tasks,
        previous_assessments=previous_assessments,
    )
    assert second.blueprint is not None

    conflicting_reference = _reference_at(
        "ARTIFACTS/ROUND-EVIDENCE.JSON", "b"
    )
    conflicting_current = tuple(
        _assessment(
            task,
            by_id[str(task.director_id)],
            blueprint,
            activation,
            evidence_refs=(conflicting_reference,),
        )
        for task in tasks
    )
    with pytest.raises(DirectorMeshError, match="multiple identities"):
        synthesize_director_assessments(
            blueprint=blueprint,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            tasks=tasks,
            assessments=conflicting_current,
            previous_synthesis=first.synthesis,
            previous_tasks=tasks,
            previous_assessments=previous_assessments,
        )
    with pytest.raises(DirectorMeshError, match="multiple identities"):
        verify_coherent_blueprint_promotion(
            promoted_blueprint=second.blueprint,
            synthesis=second.synthesis,
            base_blueprint=blueprint,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            tasks=tasks,
            assessments=conflicting_current,
            previous_synthesis=first.synthesis,
            previous_tasks=tasks,
            previous_assessments=previous_assessments,
        )


def test_synthesis_revalidates_activation_registry_coverage_and_task_scope() -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    tasks = _tasks(blueprint, charters, activation, sources)
    assessments = _all_assessments(blueprint, charters, activation, tasks)

    retained_identity_tamper = replace(
        activation, active_director_ids=(activation.active_director_ids[0],)
    )
    with pytest.raises(DirectorRegistryError, match="identity mismatch"):
        synthesize_director_assessments(
            blueprint=blueprint,
            charters=charters,
            activation=retained_identity_tamper,
            source_bundle=sources,
            tasks=(tasks[0],),
            assessments=(assessments[0],),
        )

    rebound_identity = director_activation_to_mapping(activation)
    rebound_identity["active_director_ids"] = [
        str(activation.active_director_ids[0])
    ]
    rebound_digest = canonical_sha256(rebound_identity)
    rebound_activation = replace(
        activation,
        activation_id=type(activation.activation_id)(
            f"director-activation-{str(rebound_digest)[:20]}"
        ),
        activation_sha256=rebound_digest,
        active_director_ids=(activation.active_director_ids[0],),
    )
    with pytest.raises(DirectorRegistryError):
        synthesize_director_assessments(
            blueprint=blueprint,
            charters=charters,
            activation=rebound_activation,
            source_bundle=sources,
            tasks=(tasks[0],),
            assessments=(assessments[0],),
        )

    task_mapping = director_task_plan_to_mapping(tasks[0])
    task_identity = {
        key: value
        for key, value in task_mapping.items()
        if key not in {"task_id", "task_sha256"}
    }
    task_identity["owned_fields"] = list(tasks[0].owned_fields[1:])
    task_digest = canonical_sha256(task_identity)
    rebound_task = replace(
        tasks[0],
        task_id=type(tasks[0].task_id)(f"director-task-{str(task_digest)[:20]}"),
        task_sha256=task_digest,
        owned_fields=tasks[0].owned_fields[1:],
    )
    with pytest.raises(DirectorMeshError, match="registry-derived scope"):
        synthesize_director_assessments(
            blueprint=blueprint,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            tasks=(rebound_task, *tasks[1:]),
            assessments=assessments,
        )


def test_unresolved_base_blueprint_blocker_cannot_be_laundered_by_passes() -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    blocked = build_production_blueprint(
        episode_id=str(blueprint.episode_id),
        revision=blueprint.revision,
        status=BlueprintStatus.DRAFT,
        context=blueprint.context,
        fields=blueprint.fields,
        ownership=blueprint.ownership,
        unresolved_blockers=("blueprint.base.unresolved",),
    )
    tasks = _tasks(blocked, charters, activation, sources)
    assessments = _all_assessments(blocked, charters, activation, tasks)
    outcome = synthesize_director_assessments(
        blueprint=blocked,
        charters=charters,
        activation=activation,
        source_bundle=sources,
        tasks=tasks,
        assessments=assessments,
    )
    assert outcome.blueprint is None
    assert outcome.synthesis.unresolved_blockers == ("blueprint.base.unresolved",)


def test_director_runtime_is_an_injected_fake_only() -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    task = _tasks(blueprint, charters, activation, sources)[0]
    charter = {str(item.director_id): item for item in charters}[
        str(task.director_id)
    ]
    expected = _assessment(task, charter, blueprint, activation)

    class FakeRuntime:
        def __init__(self) -> None:
            self.calls: list[tuple[object, object]] = []

        def assess(self, current_task, current_blueprint):
            self.calls.append((current_task, current_blueprint))
            return expected

    runtime: DirectorRuntimePort = FakeRuntime()
    assert runtime.assess(task, blueprint) == expected
    assert runtime.calls == [(task, blueprint)]


def test_owner_wins_nonhard_conflict_and_conflict_is_auditable() -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    tasks = _tasks(blueprint, charters, activation, sources)
    charter_by_id = {str(item.director_id): item for item in charters}
    target = "narrative.structure"
    owner_id = next(
        str(item.owner_director_id)
        for item in blueprint.ownership
        if item.field_path == target
    )
    owner_task = next(item for item in tasks if str(item.director_id) == owner_id)
    verifier_task = next(
        item
        for item in tasks
        if item.director_id != owner_task.director_id and target in item.verified_fields
    )
    current = field_index(blueprint)[target]
    evidence = (_reference("patch"),)
    proposals = {
        str(owner_task.director_id): PatchProposal(
            field_path=target,
            proposal_kind=ProposalKind.OWNER,
            expected_value_sha256=field_value_sha256(current),
            replacement_value_json=blueprint_field(
                target, {"selection": "owner"}
            ).value_json,
            reason_code="director.patch.owner",
            hard_constraint=False,
            evidence_refs=evidence,
        ),
        str(verifier_task.director_id): PatchProposal(
            field_path=target,
            proposal_kind=ProposalKind.VERIFIER,
            expected_value_sha256=field_value_sha256(current),
            replacement_value_json=blueprint_field(
                target, {"selection": "verifier"}
            ).value_json,
            reason_code="director.patch.verifier",
            hard_constraint=False,
            evidence_refs=evidence,
        ),
    }
    assessments = []
    for task in tasks:
        proposal = proposals.get(str(task.director_id))
        assessments.append(
            _assessment(
                task,
                charter_by_id[str(task.director_id)],
                blueprint,
                activation,
                verdict=(DirectorVerdict.PATCH if proposal else DirectorVerdict.PASS),
                patches=((proposal,) if proposal else ()),
            )
        )
    outcome = synthesize_director_assessments(
        blueprint=blueprint,
        charters=charters,
        activation=activation,
        source_bundle=sources,
        tasks=tasks,
        assessments=assessments,
    )
    assert outcome.blueprint is not None
    assert len(outcome.conflicts) == 1
    conflict = outcome.conflicts[0]
    assert conflict.status is ConflictStatus.RESOLVED
    assert conflict.reason_code == "director.conflict.primary_owner"
    assert conflict.selected_assessment_id == next(
        item.assessment_id
        for item in assessments
        if item.director_id == owner_task.director_id
    )
    assert field_index(outcome.blueprint)[target].value_json == '{"selection":"owner"}'
    assert director_artifact_from_bytes(director_artifact_to_bytes(conflict)) == conflict
    with pytest.raises(DirectorMeshError, match="invalid base_blueprint_sha256"):
        director_artifact_to_mapping(
            replace(conflict, base_blueprint_sha256=HashDigest("bad"))
        )


@pytest.mark.parametrize("mode", ["blocked", "low-confidence"])
def test_blocker_and_low_confidence_fail_closed(mode: str) -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    tasks = _tasks(blueprint, charters, activation, sources)
    charter_by_id = {str(item.director_id): item for item in charters}
    assessments = []
    for index, task in enumerate(tasks):
        blockers = ()
        verdict = DirectorVerdict.PASS
        confidence = 9_000
        if index == 0 and mode == "blocked":
            field_path = (task.owned_fields or task.verified_fields)[0]
            blockers = (
                DirectorBlocker(
                    reason_code="director.blocked.test",
                    field_path=field_path,
                    hard=False,
                    evidence_refs=(_reference("blocker"),),
                ),
            )
            verdict = DirectorVerdict.BLOCKED
        if index == 0 and mode == "low-confidence":
            confidence = 5_999
        assessments.append(
            _assessment(
                task,
                charter_by_id[str(task.director_id)],
                blueprint,
                activation,
                verdict=verdict,
                blockers=blockers,
                confidence=confidence,
            )
        )
    outcome = synthesize_director_assessments(
        blueprint=blueprint,
        charters=charters,
        activation=activation,
        source_bundle=sources,
        tasks=tasks,
        assessments=assessments,
    )
    assert outcome.blueprint is None
    assert outcome.synthesis.status is SynthesisStatus.BLOCKED
    assert outcome.synthesis.unresolved_blockers


def test_projection_envelope_is_deterministic_read_only_and_non_authoritative() -> None:
    _, _, _, _, _, _, blueprint = _blueprint_fixture()
    source_ref = ArtifactReference(
        path=RelativeArtifactPath("artifacts/production-blueprint.json"),
        sha256=production_blueprint_artifact_sha256(blueprint),
        artifact_version=ArtifactVersion("production-blueprint/1.0"),
    )
    projections = project_all_blueprint_views(
        blueprint, source_blueprint_ref=source_ref
    )
    assert len(projections) == len(BlueprintProjectionKind) == 6
    assert projections == project_all_blueprint_views(
        blueprint, source_blueprint_ref=source_ref
    )
    for projection in projections:
        assert projection.shadow_only is True
        assert projection.read_only is True
        assert projection.editable is False
        assert projection.authority_effect == "none"
        mapping = blueprint_projection_to_mapping(projection)
        assert mapping["artifact_version"] == "blueprint-projection/1.0"
        assert mapping["artifact_version"] != projection.legacy_artifact_version
        assert validate_artifact_mapping(mapping).ok
        assert blueprint_artifact_from_mapping(mapping) == projection
        assert blueprint_projection_artifact_sha256(projection) == HashDigest(
            hashlib.sha256(blueprint_artifact_to_bytes(projection)).hexdigest()
        )
        validate_projection_current(
            projection,
            current_blueprint=blueprint,
            current_blueprint_ref=source_ref,
        )
    with pytest.raises(BlueprintContractError, match="authority or edits"):
        blueprint_projection_to_mapping(replace(projections[0], editable=True))
    changed = build_production_blueprint(
        episode_id=str(blueprint.episode_id),
        revision=2,
        status=blueprint.status,
        context=replace(blueprint.context, evidence_graph_sha256=_hash("a")),
        fields=blueprint.fields,
        ownership=blueprint.ownership,
    )
    with pytest.raises(BlueprintContractError, match="current Blueprint"):
        validate_projection_current(
            projections[0],
            current_blueprint=changed,
            current_blueprint_ref=ArtifactReference(
                path=source_ref.path,
                sha256=production_blueprint_artifact_sha256(changed),
                artifact_version=source_ref.artifact_version,
            ),
        )
    alias_ref = replace(source_ref, path=RelativeArtifactPath("artifacts/alias.json"))
    with pytest.raises(BlueprintContractError, match="does not match current"):
        validate_projection_current(
            projections[0],
            current_blueprint=blueprint,
            current_blueprint_ref=alias_ref,
        )


def test_shadow_comparison_is_unverified_diagnostic_and_cannot_be_current() -> None:
    _, _, _, _, _, _, blueprint = _blueprint_fixture()
    source_ref = ArtifactReference(
        path=RelativeArtifactPath("artifacts/production-blueprint.json"),
        sha256=production_blueprint_artifact_sha256(blueprint),
        artifact_version=ArtifactVersion("production-blueprint/1.0"),
    )
    projection = next(
        item
        for item in project_all_blueprint_views(
            blueprint, source_blueprint_ref=source_ref
        )
        if item.view_kind is BlueprintProjectionKind.BRIEF
    )
    projection_ref = ArtifactReference(
        path=RelativeArtifactPath("shadow/brief.json"),
        sha256=blueprint_projection_artifact_sha256(projection),
        artifact_version=ArtifactVersion("blueprint-projection/1.0"),
    )
    legacy_bytes = b'{"legacy":"brief"}'
    legacy_ref = ArtifactReference(
        path=RelativeArtifactPath("artifacts/legacy-brief.json"),
        sha256=HashDigest(hashlib.sha256(legacy_bytes).hexdigest()),
        artifact_version=ArtifactVersion(projection.legacy_artifact_version),
    )
    observation = record_unverified_shadow_observation(
        legacy_artifact_ref=legacy_ref,
        legacy_document_bytes=legacy_bytes,
        observed_fields=reversed(projection.fields),
    )
    comparison = compare_shadow_projection(
        projection,
        projection_ref=projection_ref,
        observation=observation,
    )
    assert comparison.diagnostic_equal is True
    assert comparison.comparison_semantics == "diagnostic_only"
    assert observation.trust_state == "unverified"
    assert observation.diagnostic_only is True
    assert comparison.authority_effect == "none"
    assert not hasattr(comparison, "matches")
    changed_fields = (
        blueprint_field(projection.fields[0].path, "changed"),
        *projection.fields[1:],
    )
    changed_observation = record_unverified_shadow_observation(
        legacy_artifact_ref=legacy_ref,
        legacy_document_bytes=legacy_bytes,
        observed_fields=changed_fields,
    )
    changed = compare_shadow_projection(
        projection,
        projection_ref=projection_ref,
        observation=changed_observation,
    )
    assert changed.diagnostic_equal is False
    assert changed_observation.observation_sha256 != observation.observation_sha256
    with pytest.raises(BlueprintContractError, match="do not match"):
        record_unverified_shadow_observation(
            legacy_artifact_ref=legacy_ref,
            legacy_document_bytes=b"different",
            observed_fields=projection.fields,
        )
    with pytest.raises(BlueprintContractError, match="remain diagnostic"):
        shadow_comparison_to_mapping(
            replace(comparison, comparator_version="2.0")
        )
    document = blueprint_projection_to_mapping(projection)
    graph = build_artifact_graph(
        [make_artifact_snapshot("shadow/brief.json", document, is_current=True)]
    )
    assert graph.ok is False
    assert "blueprint_projection_shadow_current_forbidden" in {
        finding.code for finding in graph.findings
    }
    historical = build_artifact_graph(
        [make_artifact_snapshot("shadow/brief.json", document, is_current=False)]
    )
    assert historical.ok is True
    assert historical.current_snapshots == ()


def test_target_owned_director_resources_match_code_projection(tmp_path: Path) -> None:
    charters, manifest_sha = build_resources(tmp_path)
    assert charters == 18
    packaged = files("video_factory.resources.directors")
    assert {
        item.name
        for item in packaged.iterdir()
        if item.is_file() and item.name.endswith(".json")
    } == {
        "director-registry.json",
        "director-activation-policy.json",
        "director-resource-manifest.json",
    }
    for name in (
        "director-registry.json",
        "director-activation-policy.json",
        "director-resource-manifest.json",
    ):
        assert packaged.joinpath(name).read_bytes() == (tmp_path / name).read_bytes()
    manifest_bytes = packaged.joinpath("director-resource-manifest.json").read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == manifest_sha
    manifest = require_json_object(parse_json_bytes(manifest_bytes))
    assert manifest["resource_count"] == 2
    registry = require_json_object(
        parse_json_bytes(packaged.joinpath("director-registry.json").read_bytes())
    )
    assert registry["registry_sha256"] == str(
        director_registry_sha256(default_director_charters())
    )
    for charter in default_director_charters():
        mapping = director_artifact_to_mapping(charter)
        assert validate_artifact_mapping(mapping).ok
        assert director_artifact_from_mapping(mapping) == charter


@pytest.mark.parametrize(
    "unsafe_path",
    (
        "../outside.json",
        "./artifacts/source.json",
        "artifacts/./source.json",
        "artifacts//source.json",
        "artifacts/source.json/",
        "artifacts/\x7fsource.json",
        "artifacts/\x85source.json",
    ),
)
def test_unsafe_reference_paths_fail_before_task_or_projection_identity(
    unsafe_path: str,
) -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    unsafe = ArtifactReference(
        path=RelativeArtifactPath(unsafe_path),
        sha256=production_blueprint_artifact_sha256(blueprint),
        artifact_version=ArtifactVersion("production-blueprint/1.0"),
    )
    with pytest.raises(DirectorMeshError, match="invalid artifact reference"):
        plan_director_tasks(
            blueprint=blueprint,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            input_refs=(unsafe,),
        )
    with pytest.raises(BlueprintContractError, match="invalid immutable"):
        project_all_blueprint_views(blueprint, source_blueprint_ref=unsafe)


def test_director_task_inputs_reject_cross_platform_path_alias_conflicts() -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    base_ref = ArtifactReference(
        path=RelativeArtifactPath("artifacts/production-blueprint.json"),
        sha256=production_blueprint_artifact_sha256(blueprint),
        artifact_version=ArtifactVersion("production-blueprint/1.0"),
    )
    case_alias = ArtifactReference(
        path=RelativeArtifactPath("ARTIFACTS/PRODUCTION-BLUEPRINT.JSON"),
        sha256=_hash("f"),
        artifact_version=ArtifactVersion("production-blueprint/1.0"),
    )
    with pytest.raises(DirectorMeshError, match="cross-platform artifact path"):
        plan_director_tasks(
            blueprint=blueprint,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            input_refs=(base_ref, case_alias),
        )


def test_assessment_rejects_reference_conflicts_across_nested_surfaces() -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    task = _tasks(blueprint, charters, activation, sources)[0]
    charter = {str(item.director_id): item for item in charters}[
        str(task.director_id)
    ]
    top_level = _reference_at("artifacts/evidence-bundle.json", "a")
    alias = _reference_at(
        "ARTIFACTS/EVIDENCE-BUNDLE.JSON",
        "b",
        "external-call-reservation/1.0",
    )

    with pytest.raises(DirectorMeshError, match="multiple identities"):
        _assessment(
            task,
            charter,
            blueprint,
            activation,
            evidence_refs=(top_level,),
            execution_receipt=alias,
        )

    field_path = task.owned_fields[0]
    current = field_index(blueprint)[field_path]
    patch = PatchProposal(
        field_path=field_path,
        proposal_kind=ProposalKind.OWNER,
        expected_value_sha256=field_value_sha256(current),
        replacement_value_json=blueprint_field(
            field_path, {"selection": "replacement"}
        ).value_json,
        reason_code="director.patch.reference_conflict",
        hard_constraint=False,
        evidence_refs=(alias,),
    )
    with pytest.raises(DirectorMeshError, match="multiple identities"):
        _assessment(
            task,
            charter,
            blueprint,
            activation,
            verdict=DirectorVerdict.PATCH,
            patches=(patch,),
            evidence_refs=(top_level,),
        )

    blocker = DirectorBlocker(
        reason_code="director.blocker.reference_conflict",
        field_path=field_path,
        hard=False,
        evidence_refs=(alias,),
    )
    with pytest.raises(DirectorMeshError, match="multiple identities"):
        _assessment(
            task,
            charter,
            blueprint,
            activation,
            verdict=DirectorVerdict.BLOCKED,
            blockers=(blocker,),
            evidence_refs=(top_level,),
        )


def test_synthesis_rejects_task_to_assessment_reference_conflict() -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    tasks = _tasks(blueprint, charters, activation, sources)
    charter_by_id = {str(item.director_id): item for item in charters}
    conflicting_receipt = _reference_at(
        "ARTIFACTS/PRODUCTION-BLUEPRINT.JSON",
        "c",
        "external-call-reservation/1.0",
    )
    assessments = tuple(
        _assessment(
            task,
            charter_by_id[str(task.director_id)],
            blueprint,
            activation,
            execution_receipt=(conflicting_receipt if index == 0 else None),
        )
        for index, task in enumerate(tasks)
    )
    with pytest.raises(DirectorMeshError, match="multiple identities"):
        synthesize_director_assessments(
            blueprint=blueprint,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            tasks=tasks,
            assessments=assessments,
        )


def test_synthesis_rejects_cross_director_reference_conflict() -> None:
    _, _, _, sources, charters, activation, blueprint = _blueprint_fixture()
    tasks = _tasks(blueprint, charters, activation, sources)
    charter_by_id = {str(item.director_id): item for item in charters}
    aliases = (
        _reference_at("artifacts/shared-evidence.json", "d"),
        _reference_at("ARTIFACTS/SHARED-EVIDENCE.JSON", "e"),
    )
    assessments = tuple(
        _assessment(
            task,
            charter_by_id[str(task.director_id)],
            blueprint,
            activation,
            evidence_refs=((aliases[index],) if index < len(aliases) else None),
        )
        for index, task in enumerate(tasks)
    )
    with pytest.raises(DirectorMeshError, match="multiple identities"):
        synthesize_director_assessments(
            blueprint=blueprint,
            charters=charters,
            activation=activation,
            source_bundle=sources,
            tasks=tasks,
            assessments=assessments,
        )


def test_w03_registered_schema_set_is_exact() -> None:
    registry = ArtifactSchemaRegistry()
    expected = {
        "channel-constitution/1.0",
        "concept-constitution/1.0",
        "episode-intent/1.0",
        "production-blueprint/1.0",
        "director-charter/1.0",
        "director-assessment/1.0",
        "blueprint-conflict/1.0",
        "director-synthesis/1.0",
        "blueprint-projection/1.0",
    }
    assert expected.issubset(registry.list_versions())
    assert len(registry.all_schemas()) == 80
    assert len(registry.list_versions()) == 75
    assert blueprint_context_sha256(_blueprint_fixture()[-1].context) == canonical_sha256(
        production_blueprint_to_mapping(_blueprint_fixture()[-1])["context"]
    )
