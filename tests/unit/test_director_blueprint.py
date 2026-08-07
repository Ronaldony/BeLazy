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
    build_channel_constitution,
    build_concept_constitution,
    build_episode_intent,
    build_production_blueprint,
    compare_shadow_projection,
    field_index,
    field_value_sha256,
    project_all_blueprint_views,
    production_blueprint_to_mapping,
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
    director_artifact_from_bytes,
    director_artifact_from_mapping,
    director_artifact_to_bytes,
    director_artifact_to_mapping,
    director_registry_sha256,
    plan_director_tasks,
    synthesize_director_assessments,
    validate_director_assessment,
)
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
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
    fields = [
        blueprint_field(path, {"source": path})
        for path in ROOT_REQUIRED_FIELDS
    ]
    fields.extend(
        blueprint_field(f"shot_graph.s01.{suffix}", {"source": suffix})
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
        status=BlueprintStatus.COHERENT,
        context=context,
        fields=reversed(ordered_fields),
        ownership=reversed(ownership),
    )
    return channel, concept, intent, charters, activation, blueprint


def _tasks(blueprint, charters, activation):
    blueprint_ref = ArtifactReference(
        path=RelativeArtifactPath("artifacts/production-blueprint.json"),
        sha256=blueprint.blueprint_sha256,
        artifact_version=ArtifactVersion("production-blueprint/1.0"),
    )
    return plan_director_tasks(
        blueprint=blueprint,
        charters=charters,
        activation=activation,
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
        evidence_refs=(_reference(),),
        model_id="model-fake",
        prompt_charter_version="director-prompt.1",
        request_sha256=_hash("6"),
        response_sha256=_hash("7"),
        execution_receipt=_reference(
            "director-call", "external-call-reservation/1.0"
        ),
    )


def _all_assessments(blueprint, charters, activation, tasks):
    by_id = {str(item.director_id): item for item in charters}
    return tuple(
        _assessment(task, by_id[str(task.director_id)], blueprint, activation)
        for task in tasks
    )


def test_detailed_blueprint_is_deterministic_and_fully_owned() -> None:
    channel, concept, intent, charters, activation, blueprint = _blueprint_fixture()
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
        status=BlueprintStatus.COHERENT,
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


def test_blueprint_rejects_missing_detail_and_owner_verifier_gaps() -> None:
    _, _, _, _, _, blueprint = _blueprint_fixture()
    with pytest.raises(BlueprintContractError, match="required Blueprint fields"):
        build_production_blueprint(
            episode_id="episode-demo",
            revision=1,
            status=BlueprintStatus.COHERENT,
            context=blueprint.context,
            fields=blueprint.fields[:-1],
            ownership=blueprint.ownership[:-1],
        )
    broken = replace(
        blueprint.ownership[0],
        verifier_director_ids=(blueprint.ownership[0].owner_director_id,),
    )
    with pytest.raises(BlueprintContractError, match="distinct"):
        build_production_blueprint(
            episode_id="episode-demo",
            revision=1,
            status=BlueprintStatus.COHERENT,
            context=blueprint.context,
            fields=blueprint.fields,
            ownership=(broken, *blueprint.ownership[1:]),
        )


def test_registry_rejects_missing_and_ambiguous_field_owners() -> None:
    _, _, intent, charters, _, blueprint = _blueprint_fixture()
    profile = EpisodeNeedsProfile(
        complexity=NeedsComplexity.PRODUCTION, activation_signals=()
    )
    without_showrunner = tuple(
        item for item in charters if str(item.director_id) != "showrunner"
    )
    missing_activation = activate_directors(
        episode_intent=intent,
        profile=profile,
        charters=without_showrunner,
    )
    with pytest.raises(DirectorRegistryError, match="no owner"):
        build_field_ownership(
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
    ambiguous_activation = activate_directors(
        episode_intent=intent,
        profile=profile,
        charters=ambiguous_charters,
    )
    with pytest.raises(DirectorRegistryError, match="ambiguous owner"):
        build_field_ownership(
            blueprint.fields, ambiguous_charters, ambiguous_activation
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
    _, _, _, charters, activation, blueprint = _blueprint_fixture(signals=(signal,))
    assert len(activation.active_director_ids) == 13
    conditional_id = next(
        item.director_id for item in charters if signal in item.activation_signals
    )
    assert any(
        item.owner_director_id == conditional_id for item in blueprint.ownership
    )


def test_task_mesh_is_logically_parallel_and_context_bound() -> None:
    _, _, _, charters, activation, blueprint = _blueprint_fixture()
    tasks = _tasks(blueprint, charters, activation)
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
            input_refs=(_reference("wrong", "production-blueprint/1.0"),),
        )


def test_activation_must_match_intent_and_blueprint_context() -> None:
    channel, concept, intent, charters, _, blueprint = _blueprint_fixture()
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
    with pytest.raises(DirectorMeshError, match="Blueprint EpisodeIntent"):
        plan_director_tasks(
            blueprint=blueprint,
            charters=charters,
            activation=other_activation,
            input_refs=(
                ArtifactReference(
                    path=RelativeArtifactPath("artifacts/production-blueprint.json"),
                    sha256=blueprint.blueprint_sha256,
                    artifact_version=ArtifactVersion("production-blueprint/1.0"),
                ),
            ),
        )


def test_assessment_binds_task_blueprint_context_model_and_receipt() -> None:
    _, _, _, charters, activation, blueprint = _blueprint_fixture()
    task = _tasks(blueprint, charters, activation)[0]
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
    _, _, _, charters, activation, blueprint = _blueprint_fixture()
    tasks = _tasks(blueprint, charters, activation)
    assessments = _all_assessments(blueprint, charters, activation, tasks)
    first = synthesize_director_assessments(
        blueprint=blueprint,
        charters=charters,
        activation=activation,
        tasks=reversed(tasks),
        assessments=reversed(assessments),
    )
    second = synthesize_director_assessments(
        blueprint=blueprint,
        charters=charters,
        activation=activation,
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
    with pytest.raises(DirectorMeshError, match="maximum conflict rounds"):
        synthesize_director_assessments(
            blueprint=blueprint,
            charters=charters,
            activation=activation,
            tasks=tasks,
            assessments=assessments,
            rounds_used=MAX_CONFLICT_ROUNDS + 1,
        )


def test_unresolved_base_blueprint_blocker_cannot_be_laundered_by_passes() -> None:
    _, _, _, charters, activation, blueprint = _blueprint_fixture()
    blocked = build_production_blueprint(
        episode_id=str(blueprint.episode_id),
        revision=blueprint.revision,
        status=BlueprintStatus.BLOCKED,
        context=blueprint.context,
        fields=blueprint.fields,
        ownership=blueprint.ownership,
        unresolved_blockers=("blueprint.base.unresolved",),
    )
    tasks = _tasks(blocked, charters, activation)
    assessments = _all_assessments(blocked, charters, activation, tasks)
    outcome = synthesize_director_assessments(
        blueprint=blocked,
        charters=charters,
        activation=activation,
        tasks=tasks,
        assessments=assessments,
    )
    assert outcome.blueprint is None
    assert outcome.synthesis.unresolved_blockers == ("blueprint.base.unresolved",)


def test_director_runtime_is_an_injected_fake_only() -> None:
    _, _, _, charters, activation, blueprint = _blueprint_fixture()
    task = _tasks(blueprint, charters, activation)[0]
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
    _, _, _, charters, activation, blueprint = _blueprint_fixture()
    tasks = _tasks(blueprint, charters, activation)
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
            replacement_value_json=blueprint_field(target, "owner").value_json,
            reason_code="director.patch.owner",
            hard_constraint=False,
            evidence_refs=evidence,
        ),
        str(verifier_task.director_id): PatchProposal(
            field_path=target,
            proposal_kind=ProposalKind.VERIFIER,
            expected_value_sha256=field_value_sha256(current),
            replacement_value_json=blueprint_field(target, "verifier").value_json,
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
    assert field_index(outcome.blueprint)[target].value_json == '"owner"'
    assert director_artifact_from_bytes(director_artifact_to_bytes(conflict)) == conflict


@pytest.mark.parametrize("mode", ["blocked", "low-confidence"])
def test_blocker_and_low_confidence_fail_closed(mode: str) -> None:
    _, _, _, charters, activation, blueprint = _blueprint_fixture()
    tasks = _tasks(blueprint, charters, activation)
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
        tasks=tasks,
        assessments=assessments,
    )
    assert outcome.blueprint is None
    assert outcome.synthesis.status is SynthesisStatus.BLOCKED
    assert outcome.synthesis.unresolved_blockers


def test_projection_envelope_is_deterministic_read_only_and_non_authoritative() -> None:
    _, _, _, _, _, blueprint = _blueprint_fixture()
    source_ref = ArtifactReference(
        path=RelativeArtifactPath("artifacts/production-blueprint.json"),
        sha256=blueprint.blueprint_sha256,
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
                sha256=changed.blueprint_sha256,
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


def test_shadow_comparison_is_observational_and_projection_cannot_be_current() -> None:
    _, _, _, _, _, blueprint = _blueprint_fixture()
    source_ref = ArtifactReference(
        path=RelativeArtifactPath("artifacts/production-blueprint.json"),
        sha256=blueprint.blueprint_sha256,
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
        sha256=projection.projection_sha256,
        artifact_version=ArtifactVersion("blueprint-projection/1.0"),
    )
    legacy_ref = _reference("legacy-brief", projection.legacy_artifact_version)
    comparison = compare_shadow_projection(
        projection,
        projection_ref=projection_ref,
        legacy_artifact_ref=legacy_ref,
        observed_fields=reversed(projection.fields),
    )
    assert comparison.matches is True
    assert comparison.authority_effect == "none"
    changed_fields = (
        blueprint_field(projection.fields[0].path, "changed"),
        *projection.fields[1:],
    )
    changed = compare_shadow_projection(
        projection,
        projection_ref=projection_ref,
        legacy_artifact_ref=legacy_ref,
        observed_fields=changed_fields,
    )
    assert changed.matches is False
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


def test_unsafe_reference_paths_fail_before_task_or_projection_identity() -> None:
    _, _, _, charters, activation, blueprint = _blueprint_fixture()
    unsafe = ArtifactReference(
        path=RelativeArtifactPath("../outside.json"),
        sha256=blueprint.blueprint_sha256,
        artifact_version=ArtifactVersion("production-blueprint/1.0"),
    )
    with pytest.raises(DirectorMeshError, match="invalid artifact reference"):
        plan_director_tasks(
            blueprint=blueprint,
            charters=charters,
            activation=activation,
            input_refs=(unsafe,),
        )
    with pytest.raises(BlueprintContractError, match="invalid immutable"):
        project_all_blueprint_views(blueprint, source_blueprint_ref=unsafe)


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
    assert len(registry.all_schemas()) == 61
    assert len(registry.list_versions()) == 57
    assert blueprint_context_sha256(_blueprint_fixture()[-1].context) == canonical_sha256(
        production_blueprint_to_mapping(_blueprint_fixture()[-1])["context"]
    )
