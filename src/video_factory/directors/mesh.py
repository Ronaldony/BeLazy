"""Pure parallel task planning, assessment validation, and bounded synthesis."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
import re

from video_factory.blueprint.contracts import (
    BlueprintField,
    BlueprintStatus,
    BlueprintSourceBundle,
    DirectorProvenance,
    ProductionBlueprint,
)
from video_factory.blueprint.model import (
    BlueprintContractError,
    _build_synthesized_production_blueprint,
    blueprint_context_sha256,
    field_index,
    field_value_sha256,
    normalize_fields,
    production_blueprint_to_mapping,
    production_blueprint_artifact_sha256,
    validate_blueprint_source_bundle,
)
from video_factory.blueprint.reference_paths import (
    ReferencePathError,
    reference_path_collision_key,
    require_canonical_reference_path,
)
from video_factory.config.canonical import canonical_sha256
from video_factory.domain import ArtifactReference, HashDigest, OpaqueId

from .contracts import (
    ConflictResult,
    ConflictStatus,
    DirectorActivation,
    DirectorAssessment,
    DirectorBlocker,
    DirectorCharter,
    DirectorSynthesis,
    DirectorTaskPlan,
    DirectorVerdict,
    PatchProposal,
    ProposalKind,
    SynthesisOutcome,
    SynthesisStatus,
    VerifiedBlueprintPromotion,
)
from .registry import (
    MAX_CONFLICT_ROUNDS,
    activation_policy_sha256,
    director_charter_to_mapping,
    director_registry_sha256,
    scope_matches,
    validate_director_activation,
    validate_field_ownership,
)


SHA256 = re.compile(r"^[0-9a-f]{64}$")
REASON = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")
TOKEN = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ARTIFACT_VERSION = re.compile(r"^[a-z][a-z0-9-]*/[1-9][0-9]*\.[0-9]+$")
MIN_SYNTHESIS_CONFIDENCE_BPS = 6_000


class DirectorMeshError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _reference_mapping(reference: ArtifactReference) -> dict[str, str]:
    path = str(reference.path)
    try:
        require_canonical_reference_path(path)
    except ReferencePathError as error:
        raise DirectorMeshError(
            "director.reference.invalid", "invalid artifact reference"
        ) from error
    if not SHA256.fullmatch(str(reference.sha256)) or not ARTIFACT_VERSION.fullmatch(
        str(reference.artifact_version)
    ):
        raise DirectorMeshError("director.reference.invalid", "invalid artifact reference")
    return {
        "path": str(reference.path),
        "sha256": str(reference.sha256),
        "artifact_version": str(reference.artifact_version),
    }


def _normalize_references(
    references: Iterable[ArtifactReference], *, required: bool = False
) -> tuple[ArtifactReference, ...]:
    values = tuple(references)
    keyed = {
        (
            str(item.path),
            str(item.sha256),
            str(item.artifact_version),
        ): item
        for item in values
    }
    identities_by_path: dict[str, tuple[str, str, str]] = {}
    for item in values:
        _reference_mapping(item)
        path = str(item.path)
        collision_key = reference_path_collision_key(path)
        identity = (path, str(item.sha256), str(item.artifact_version))
        if collision_key in identities_by_path and identities_by_path[collision_key] != identity:
            raise DirectorMeshError(
                "director.reference.path_conflict",
                "one cross-platform artifact path cannot carry multiple identities",
            )
        identities_by_path[collision_key] = identity
    expected = tuple(keyed[key] for key in sorted(keyed))
    if values != expected or len(values) != len(keyed):
        raise DirectorMeshError(
            "director.reference.order", "artifact references must be sorted unique"
        )
    if required and not values:
        raise DirectorMeshError(
            "director.reference.missing", "at least one evidence reference is required"
        )
    return values


def _require_reference_identity_consistency(
    references: Iterable[ArtifactReference],
) -> None:
    """Reject conflicting identities across one complete evidence bundle."""

    identities_by_path: dict[str, tuple[str, str, str]] = {}
    for item in references:
        _reference_mapping(item)
        path = str(item.path)
        collision_key = reference_path_collision_key(path)
        identity = (path, str(item.sha256), str(item.artifact_version))
        existing = identities_by_path.get(collision_key)
        if existing is not None and existing != identity:
            raise DirectorMeshError(
                "director.reference.path_conflict",
                "one cross-platform artifact path cannot carry multiple identities",
            )
        identities_by_path[collision_key] = identity


def _canonical_references(
    references: Iterable[ArtifactReference], *, required: bool = False
) -> tuple[ArtifactReference, ...]:
    keyed = {
        (str(item.path), str(item.sha256), str(item.artifact_version)): item
        for item in references
    }
    values = tuple(keyed[key] for key in sorted(keyed))
    return _normalize_references(values, required=required)


def _assessment_references(
    assessment: DirectorAssessment,
) -> tuple[ArtifactReference, ...]:
    return (
        assessment.execution_receipt,
        *assessment.evidence_refs,
        *(
            reference
            for patch in assessment.patches
            for reference in patch.evidence_refs
        ),
        *(
            reference
            for blocker in assessment.blockers
            for reference in blocker.evidence_refs
        ),
    )


def _task_identity_mapping(task: DirectorTaskPlan) -> dict[str, object]:
    if not TOKEN.fullmatch(str(task.director_id)) or not VERSION.fullmatch(
        task.director_version
    ):
        raise DirectorMeshError("director.task.identity", "invalid director identity")
    for label in (
        "charter_sha256",
        "registry_sha256",
        "activation_sha256",
        "activation_policy_sha256",
        "episode_intent_sha256",
        "base_blueprint_sha256",
        "blueprint_context_sha256",
    ):
        if not SHA256.fullmatch(str(getattr(task, label))):
            raise DirectorMeshError("director.task.digest", f"invalid {label}")
    if task.owned_fields != tuple(sorted(set(task.owned_fields))):
        raise DirectorMeshError("director.task.owned_order", "owned fields not canonical")
    if task.verified_fields != tuple(sorted(set(task.verified_fields))):
        raise DirectorMeshError(
            "director.task.verified_order", "verified fields not canonical"
        )
    refs = _normalize_references(task.input_refs, required=True)
    return {
        "director_id": str(task.director_id),
        "director_version": task.director_version,
        "charter_sha256": str(task.charter_sha256),
        "registry_sha256": str(task.registry_sha256),
        "activation_id": str(task.activation_id),
        "activation_sha256": str(task.activation_sha256),
        "activation_policy_sha256": str(task.activation_policy_sha256),
        "episode_intent_sha256": str(task.episode_intent_sha256),
        "base_blueprint_sha256": str(task.base_blueprint_sha256),
        "blueprint_context_sha256": str(task.blueprint_context_sha256),
        "input_refs": [_reference_mapping(item) for item in refs],
        "owned_fields": list(task.owned_fields),
        "verified_fields": list(task.verified_fields),
    }


def director_task_plan_to_mapping(task: DirectorTaskPlan) -> dict[str, object]:
    identity = _task_identity_mapping(task)
    digest = canonical_sha256(identity)
    if task.task_sha256 != digest or task.task_id != OpaqueId(
        f"director-task-{str(digest)[:20]}"
    ):
        raise DirectorMeshError("director.task.identity", "task identity mismatch")
    return {
        "task_id": str(task.task_id),
        "task_sha256": str(task.task_sha256),
        **identity,
    }


def plan_director_tasks(
    *,
    blueprint: ProductionBlueprint,
    charters: Iterable[DirectorCharter],
    activation: DirectorActivation,
    source_bundle: BlueprintSourceBundle,
    input_refs: Iterable[ArtifactReference],
) -> tuple[DirectorTaskPlan, ...]:
    production_blueprint_to_mapping(blueprint)
    if blueprint.status is not BlueprintStatus.DRAFT:
        raise DirectorMeshError(
            "director.task.base_status",
            "Director planning requires an unevaluated draft Blueprint base",
        )
    validate_blueprint_source_bundle(blueprint, source_bundle)
    values = tuple(charters)
    validate_director_activation(
        activation,
        episode_intent=source_bundle.episode_intent,
        charters=values,
    )
    if activation.episode_intent_sha256 != blueprint.context.episode_intent_sha256:
        raise DirectorMeshError(
            "director.task.activation",
            "activation is not bound to the Blueprint EpisodeIntent",
        )
    validate_field_ownership(blueprint, values, activation)
    registry_digest = director_registry_sha256(values)
    if registry_digest != activation.registry_sha256:
        raise DirectorMeshError("director.task.registry", "activation registry is stale")
    refs = _canonical_references(input_refs, required=True)
    blueprint_artifact_sha256 = production_blueprint_artifact_sha256(blueprint)
    blueprint_refs = tuple(
        item.sha256 == blueprint_artifact_sha256
        and str(item.artifact_version) == "production-blueprint/1.0"
        for item in refs
    )
    if sum(blueprint_refs) != 1:
        raise DirectorMeshError(
            "director.task.blueprint_ref",
            "task inputs must contain the exact base ProductionBlueprint reference exactly once",
        )
    active = {str(item) for item in activation.active_director_ids}
    tasks: list[DirectorTaskPlan] = []
    for charter in values:
        director_id = str(charter.director_id)
        if director_id not in active:
            continue
        owned = tuple(
            item.field_path
            for item in blueprint.ownership
            if str(item.owner_director_id) == director_id
        )
        verified = tuple(
            item.field_path
            for item in blueprint.ownership
            if director_id in {str(value) for value in item.verifier_director_ids}
        )
        if not owned and not verified:
            raise DirectorMeshError(
                "director.task.empty_scope", f"active director has no scope: {director_id}"
            )
        provisional = DirectorTaskPlan(
            task_id=OpaqueId("pending"),
            task_sha256=HashDigest("0" * 64),
            director_id=charter.director_id,
            director_version=charter.director_version,
            charter_sha256=charter.charter_sha256,
            registry_sha256=registry_digest,
            activation_id=activation.activation_id,
            activation_sha256=activation.activation_sha256,
            activation_policy_sha256=activation.activation_policy_sha256,
            episode_intent_sha256=activation.episode_intent_sha256,
            base_blueprint_sha256=blueprint.blueprint_sha256,
            blueprint_context_sha256=blueprint_context_sha256(blueprint.context),
            input_refs=refs,
            owned_fields=tuple(sorted(owned)),
            verified_fields=tuple(sorted(verified)),
        )
        identity = _task_identity_mapping(provisional)
        digest = canonical_sha256(identity)
        tasks.append(
            replace(
                provisional,
                task_id=OpaqueId(f"director-task-{str(digest)[:20]}"),
                task_sha256=digest,
            )
        )
    return tuple(sorted(tasks, key=lambda item: str(item.director_id)))


def _proposal_mapping(proposal: PatchProposal) -> dict[str, object]:
    try:
        normalized = normalize_fields(
            (BlueprintField(proposal.field_path, proposal.replacement_value_json),)
        )[0]
    except BlueprintContractError as error:
        raise DirectorMeshError(error.reason_code, str(error)) from error
    if not SHA256.fullmatch(str(proposal.expected_value_sha256)):
        raise DirectorMeshError("director.patch.expected_digest", "invalid expected digest")
    if not REASON.fullmatch(proposal.reason_code):
        raise DirectorMeshError("director.patch.reason", "invalid patch reason code")
    evidence = _normalize_references(proposal.evidence_refs, required=True)
    return {
        "field_path": normalized.path,
        "proposal_kind": proposal.proposal_kind.value,
        "expected_value_sha256": str(proposal.expected_value_sha256),
        "replacement_value_json": normalized.value_json,
        "reason_code": proposal.reason_code,
        "hard_constraint": proposal.hard_constraint,
        "evidence_refs": [_reference_mapping(item) for item in evidence],
    }


def _blocker_mapping(blocker: DirectorBlocker) -> dict[str, object]:
    if not REASON.fullmatch(blocker.reason_code):
        raise DirectorMeshError("director.blocker.reason", "invalid blocker reason code")
    try:
        normalized = normalize_fields((BlueprintField(blocker.field_path, "null"),))[0]
    except BlueprintContractError as error:
        raise DirectorMeshError(error.reason_code, str(error)) from error
    evidence = _normalize_references(blocker.evidence_refs, required=True)
    return {
        "reason_code": blocker.reason_code,
        "field_path": normalized.path,
        "hard": blocker.hard,
        "evidence_refs": [_reference_mapping(item) for item in evidence],
    }


def _assessment_identity_mapping(value: DirectorAssessment) -> dict[str, object]:
    for label in ("task_id", "director_id", "activation_id"):
        if not TOKEN.fullmatch(str(getattr(value, label))):
            raise DirectorMeshError(
                "director.assessment.identity", f"invalid {label}"
            )
    if not VERSION.fullmatch(value.director_version):
        raise DirectorMeshError(
            "director.assessment.identity", "invalid director version"
        )
    for label in (
        "assessment_sha256",
        "task_plan_sha256",
        "charter_sha256",
        "registry_sha256",
        "activation_sha256",
        "activation_policy_sha256",
        "episode_intent_sha256",
        "base_blueprint_sha256",
        "blueprint_context_sha256",
        "request_sha256",
        "response_sha256",
    ):
        if not SHA256.fullmatch(str(getattr(value, label))):
            raise DirectorMeshError(
                "director.assessment.digest", f"invalid {label}"
            )
    if not TOKEN.fullmatch(str(value.model_id)) or not TOKEN.fullmatch(
        value.prompt_charter_version
    ):
        raise DirectorMeshError(
            "director.assessment.model_binding",
            "model and prompt charter identifiers must be canonical tokens",
        )
    if not SHA256.fullmatch(str(value.request_sha256)) or not SHA256.fullmatch(
        str(value.response_sha256)
    ):
        raise DirectorMeshError(
            "director.assessment.response_binding", "request/response digest is invalid"
        )
    patches = tuple(value.patches)
    patch_keys = [
        (item.field_path, item.proposal_kind.value, item.replacement_value_json)
        for item in patches
    ]
    if patch_keys != sorted(patch_keys) or len({item[0] for item in patch_keys}) != len(
        patch_keys
    ):
        raise DirectorMeshError(
            "director.assessment.patch_order", "patches must be ordered with unique fields"
        )
    blockers = tuple(value.blockers)
    blocker_keys = [(item.field_path, item.reason_code) for item in blockers]
    if blocker_keys != sorted(blocker_keys) or len(blocker_keys) != len(set(blocker_keys)):
        raise DirectorMeshError(
            "director.assessment.blocker_order", "blockers must be ordered and unique"
        )
    if value.recommendations != tuple(sorted(set(value.recommendations))):
        raise DirectorMeshError(
            "director.assessment.recommendation_order", "recommendations not canonical"
        )
    if value.assumptions != tuple(sorted(set(value.assumptions))):
        raise DirectorMeshError(
            "director.assessment.assumption_order", "assumptions not canonical"
        )
    if any(
        not isinstance(item, str) or not item or len(item) > 4096
        for item in (*value.recommendations, *value.assumptions)
    ):
        raise DirectorMeshError(
            "director.assessment.text", "assessment text must be non-empty and bounded"
        )
    if not isinstance(value.confidence_basis_points, int) or isinstance(
        value.confidence_basis_points, bool
    ) or not 0 <= value.confidence_basis_points <= 10_000:
        raise DirectorMeshError(
            "director.assessment.confidence", "confidence must be 0..10000"
        )
    evidence = _normalize_references(value.evidence_refs, required=True)
    patch_mappings = [_proposal_mapping(item) for item in patches]
    blocker_mappings = [_blocker_mapping(item) for item in blockers]
    receipt_mapping = _reference_mapping(value.execution_receipt)
    _require_reference_identity_consistency(_assessment_references(value))
    return {
        "task_id": str(value.task_id),
        "task_plan_sha256": str(value.task_plan_sha256),
        "director_id": str(value.director_id),
        "director_version": value.director_version,
        "charter_sha256": str(value.charter_sha256),
        "registry_sha256": str(value.registry_sha256),
        "activation_id": str(value.activation_id),
        "activation_sha256": str(value.activation_sha256),
        "activation_policy_sha256": str(value.activation_policy_sha256),
        "episode_intent_sha256": str(value.episode_intent_sha256),
        "base_blueprint_sha256": str(value.base_blueprint_sha256),
        "blueprint_context_sha256": str(value.blueprint_context_sha256),
        "model_id": str(value.model_id),
        "prompt_charter_version": value.prompt_charter_version,
        "request_sha256": str(value.request_sha256),
        "response_sha256": str(value.response_sha256),
        "execution_receipt": receipt_mapping,
        "verdict": value.verdict.value,
        "patches": patch_mappings,
        "blockers": blocker_mappings,
        "recommendations": list(value.recommendations),
        "confidence_basis_points": value.confidence_basis_points,
        "assumptions": list(value.assumptions),
        "evidence_refs": [_reference_mapping(item) for item in evidence],
    }


def director_assessment_to_mapping(value: DirectorAssessment) -> dict[str, object]:
    identity = _assessment_identity_mapping(value)
    digest = canonical_sha256(identity)
    if value.assessment_sha256 != digest or value.assessment_id != OpaqueId(
        f"director-assessment-{str(digest)[:20]}"
    ):
        raise DirectorMeshError(
            "director.assessment.identity", "assessment identity mismatch"
        )
    return {
        "artifact_version": "director-assessment/1.0",
        "assessment_id": str(value.assessment_id),
        "assessment_sha256": str(value.assessment_sha256),
        **identity,
    }


def validate_director_assessment(
    assessment: DirectorAssessment,
    *,
    task: DirectorTaskPlan,
    charter: DirectorCharter,
    current_blueprint: ProductionBlueprint,
    activation: DirectorActivation,
) -> None:
    director_task_plan_to_mapping(task)
    director_charter_to_mapping(charter)
    director_assessment_to_mapping(assessment)
    production_blueprint_to_mapping(current_blueprint)
    expected_context = blueprint_context_sha256(current_blueprint.context)
    if (
        assessment.task_id != task.task_id
        or assessment.task_plan_sha256 != task.task_sha256
        or assessment.director_id != task.director_id
        or assessment.director_version != task.director_version
        or assessment.charter_sha256 != task.charter_sha256
        or assessment.registry_sha256 != task.registry_sha256
        or assessment.activation_id != task.activation_id
        or assessment.activation_sha256 != task.activation_sha256
        or assessment.activation_policy_sha256 != task.activation_policy_sha256
        or assessment.episode_intent_sha256 != task.episode_intent_sha256
        or assessment.base_blueprint_sha256 != current_blueprint.blueprint_sha256
        or assessment.base_blueprint_sha256 != task.base_blueprint_sha256
        or assessment.blueprint_context_sha256 != expected_context
        or assessment.blueprint_context_sha256 != task.blueprint_context_sha256
        or task.registry_sha256 != activation.registry_sha256
        or task.activation_id != activation.activation_id
        or task.activation_sha256 != activation.activation_sha256
        or task.activation_policy_sha256 != activation.activation_policy_sha256
        or task.episode_intent_sha256 != activation.episode_intent_sha256
        or task.director_id not in activation.active_director_ids
        or charter.director_id != task.director_id
        or charter.director_version != task.director_version
        or charter.charter_sha256 != task.charter_sha256
    ):
        raise DirectorMeshError(
            "director.assessment.binding", "assessment is not bound to current task/base/context"
        )
    current_fields = field_index(current_blueprint)
    for proposal in assessment.patches:
        current = current_fields.get(proposal.field_path)
        if current is None or field_value_sha256(current) != proposal.expected_value_sha256:
            raise DirectorMeshError(
                "director.patch.stale", f"patch is stale for {proposal.field_path}"
            )
        if proposal.proposal_kind is ProposalKind.OWNER:
            if proposal.field_path not in task.owned_fields:
                raise DirectorMeshError(
                    "director.patch.unowned", "owner patch is outside charter ownership"
                )
        elif proposal.field_path not in task.verified_fields:
            raise DirectorMeshError(
                "director.patch.unverified", "verifier patch is outside verifier scope"
            )
        if proposal.hard_constraint and not any(
            scope_matches(pattern, proposal.field_path)
            for pattern in charter.veto_patterns
        ):
            raise DirectorMeshError(
                "director.patch.invalid_veto", "hard constraint is outside veto scope"
            )
    for blocker in assessment.blockers:
        if blocker.field_path not in {*task.owned_fields, *task.verified_fields}:
            raise DirectorMeshError(
                "director.blocker.scope", "blocker is outside task scope"
            )
        if blocker.hard and not any(
            scope_matches(pattern, blocker.field_path)
            for pattern in charter.veto_patterns
        ):
            raise DirectorMeshError(
                "director.blocker.invalid_veto", "hard blocker is outside veto scope"
            )
    if assessment.verdict is DirectorVerdict.PASS and (
        assessment.patches or assessment.blockers
    ):
        raise DirectorMeshError(
            "director.assessment.pass_shape", "PASS cannot carry patches or blockers"
        )
    if assessment.verdict is DirectorVerdict.PATCH and not assessment.patches:
        raise DirectorMeshError(
            "director.assessment.patch_shape", "PATCH verdict requires patches"
        )
    if assessment.verdict is DirectorVerdict.BLOCKED and not assessment.blockers:
        raise DirectorMeshError(
            "director.assessment.blocked_shape", "BLOCKED verdict requires blockers"
        )


def build_director_assessment(
    *,
    task: DirectorTaskPlan,
    charter: DirectorCharter,
    current_blueprint: ProductionBlueprint,
    activation: DirectorActivation,
    verdict: DirectorVerdict,
    patches: Iterable[PatchProposal],
    blockers: Iterable[DirectorBlocker],
    recommendations: Iterable[str],
    confidence_basis_points: int,
    assumptions: Iterable[str],
    evidence_refs: Iterable[ArtifactReference],
    model_id: str,
    prompt_charter_version: str,
    request_sha256: HashDigest,
    response_sha256: HashDigest,
    execution_receipt: ArtifactReference,
) -> DirectorAssessment:
    canonical_patches = tuple(
        sorted(
            (
                replace(
                    item,
                    evidence_refs=_canonical_references(
                        item.evidence_refs, required=True
                    ),
                )
                for item in patches
            ),
            key=lambda item: (
                item.field_path,
                item.proposal_kind.value,
                item.replacement_value_json,
            ),
        )
    )
    canonical_blockers = tuple(
        sorted(
            (
                replace(
                    item,
                    evidence_refs=_canonical_references(
                        item.evidence_refs, required=True
                    ),
                )
                for item in blockers
            ),
            key=lambda item: (item.field_path, item.reason_code),
        )
    )
    provisional = DirectorAssessment(
        assessment_id=OpaqueId("pending"),
        assessment_sha256=HashDigest("0" * 64),
        task_id=task.task_id,
        task_plan_sha256=task.task_sha256,
        director_id=task.director_id,
        director_version=task.director_version,
        charter_sha256=task.charter_sha256,
        registry_sha256=task.registry_sha256,
        activation_id=task.activation_id,
        activation_sha256=task.activation_sha256,
        activation_policy_sha256=task.activation_policy_sha256,
        episode_intent_sha256=task.episode_intent_sha256,
        base_blueprint_sha256=task.base_blueprint_sha256,
        blueprint_context_sha256=task.blueprint_context_sha256,
        model_id=OpaqueId(model_id),
        prompt_charter_version=prompt_charter_version,
        request_sha256=request_sha256,
        response_sha256=response_sha256,
        execution_receipt=execution_receipt,
        verdict=verdict,
        patches=canonical_patches,
        blockers=canonical_blockers,
        recommendations=tuple(sorted(set(recommendations))),
        confidence_basis_points=confidence_basis_points,
        assumptions=tuple(sorted(set(assumptions))),
        evidence_refs=_canonical_references(evidence_refs, required=True),
    )
    identity = _assessment_identity_mapping(provisional)
    digest = canonical_sha256(identity)
    value = replace(
        provisional,
        assessment_id=OpaqueId(f"director-assessment-{str(digest)[:20]}"),
        assessment_sha256=digest,
    )
    validate_director_assessment(
        value,
        task=task,
        charter=charter,
        current_blueprint=current_blueprint,
        activation=activation,
    )
    return value


def _conflict_identity_mapping(value: ConflictResult) -> dict[str, object]:
    proposals = tuple(str(item) for item in value.proposal_assessment_ids)
    rejected = tuple(str(item) for item in value.rejected_assessment_ids)
    for label in ("base_blueprint_sha256", "blueprint_context_sha256"):
        if not SHA256.fullmatch(str(getattr(value, label))):
            raise DirectorMeshError("director.conflict.digest", f"invalid {label}")
    if not TOKEN.fullmatch(str(value.conflict_session_id)):
        raise DirectorMeshError(
            "director.conflict.session", "invalid conflict session identity"
        )
    if value.previous_synthesis_sha256 is not None and not SHA256.fullmatch(
        str(value.previous_synthesis_sha256)
    ):
        raise DirectorMeshError(
            "director.conflict.predecessor", "invalid previous synthesis digest"
        )
    if (value.rounds_used == 1) != (value.previous_synthesis_sha256 is None):
        raise DirectorMeshError(
            "director.conflict.predecessor",
            "round one forbids and round two requires a previous synthesis",
        )
    if proposals != tuple(sorted(set(proposals))) or rejected != tuple(sorted(set(rejected))):
        raise DirectorMeshError("director.conflict.order", "conflict IDs not canonical")
    if len(proposals) < 2 or len(rejected) < 1 or any(
        not TOKEN.fullmatch(item) for item in (*proposals, *rejected)
    ):
        raise DirectorMeshError(
            "director.conflict.proposals", "conflict requires valid competing assessments"
        )
    if not REASON.fullmatch(value.reason_code):
        raise DirectorMeshError("director.conflict.reason", "invalid conflict reason code")
    try:
        normalize_fields((BlueprintField(value.field_path, "null"),))
    except BlueprintContractError as error:
        raise DirectorMeshError(error.reason_code, str(error)) from error
    if value.rounds_used < 1 or value.rounds_used > MAX_CONFLICT_ROUNDS:
        raise DirectorMeshError("director.conflict.rounds", "conflict rounds exceed policy")
    if value.status is ConflictStatus.RESOLVED and (
        value.selected_assessment_id is None
        or value.selected_replacement_sha256 is None
    ):
        raise DirectorMeshError("director.conflict.selection", "resolved conflict lacks selection")
    if value.status is ConflictStatus.BLOCKED and (
        value.selected_assessment_id is not None
        or value.selected_replacement_sha256 is not None
    ):
        raise DirectorMeshError("director.conflict.selection", "blocked conflict selects a patch")
    if value.selected_assessment_id is not None and str(
        value.selected_assessment_id
    ) not in proposals:
        raise DirectorMeshError("director.conflict.selection", "selection is not a proposal")
    if value.selected_replacement_sha256 is not None and not SHA256.fullmatch(
        str(value.selected_replacement_sha256)
    ):
        raise DirectorMeshError(
            "director.conflict.selection", "selected replacement digest is invalid"
        )
    expected_rejected = tuple(
        item
        for item in proposals
        if item != (
            str(value.selected_assessment_id)
            if value.selected_assessment_id is not None
            else None
        )
    )
    if rejected != expected_rejected:
        raise DirectorMeshError(
            "director.conflict.rejected", "rejected assessments do not match selection"
        )
    return {
        "base_blueprint_sha256": str(value.base_blueprint_sha256),
        "blueprint_context_sha256": str(value.blueprint_context_sha256),
        "conflict_session_id": str(value.conflict_session_id),
        "previous_synthesis_sha256": (
            str(value.previous_synthesis_sha256)
            if value.previous_synthesis_sha256 is not None
            else None
        ),
        "field_path": value.field_path,
        "proposal_assessment_ids": list(proposals),
        "status": value.status.value,
        "selected_assessment_id": (
            str(value.selected_assessment_id)
            if value.selected_assessment_id is not None
            else None
        ),
        "rejected_assessment_ids": list(rejected),
        "selected_replacement_sha256": (
            str(value.selected_replacement_sha256)
            if value.selected_replacement_sha256 is not None
            else None
        ),
        "reason_code": value.reason_code,
        "rounds_used": value.rounds_used,
        "evidence_refs": [
            _reference_mapping(item)
            for item in _normalize_references(value.evidence_refs)
        ],
    }


def conflict_result_to_mapping(value: ConflictResult) -> dict[str, object]:
    identity = _conflict_identity_mapping(value)
    digest = canonical_sha256(identity)
    if value.conflict_sha256 != digest or value.conflict_id != OpaqueId(
        f"blueprint-conflict-{str(digest)[:20]}"
    ):
        raise DirectorMeshError("director.conflict.identity", "conflict identity mismatch")
    return {
        "artifact_version": "blueprint-conflict/1.0",
        "conflict_id": str(value.conflict_id),
        "conflict_sha256": str(value.conflict_sha256),
        **identity,
    }


def _synthesis_identity_mapping(value: DirectorSynthesis) -> dict[str, object]:
    assessments = tuple(str(item) for item in value.assessment_sha256s)
    conflicts = tuple(str(item) for item in value.conflict_sha256s)
    for label in ("base_blueprint_sha256", "blueprint_context_sha256"):
        if not SHA256.fullmatch(str(getattr(value, label))):
            raise DirectorMeshError("director.synthesis.digest", f"invalid {label}")
    if not TOKEN.fullmatch(str(value.conflict_session_id)):
        raise DirectorMeshError(
            "director.synthesis.session", "invalid conflict session identity"
        )
    if value.previous_synthesis_sha256 is not None and not SHA256.fullmatch(
        str(value.previous_synthesis_sha256)
    ):
        raise DirectorMeshError(
            "director.synthesis.predecessor", "invalid previous synthesis digest"
        )
    if (value.rounds_used == 1) != (value.previous_synthesis_sha256 is None):
        raise DirectorMeshError(
            "director.synthesis.predecessor",
            "round one forbids and round two requires a previous synthesis",
        )
    if assessments != tuple(sorted(set(assessments))) or conflicts != tuple(sorted(set(conflicts))):
        raise DirectorMeshError("director.synthesis.order", "synthesis inputs not canonical")
    if not assessments or any(not SHA256.fullmatch(item) for item in assessments):
        raise DirectorMeshError(
            "director.synthesis.assessments", "synthesis requires valid assessment digests"
        )
    if any(not SHA256.fullmatch(item) for item in conflicts):
        raise DirectorMeshError(
            "director.synthesis.conflicts", "synthesis conflict digests are invalid"
        )
    blockers = tuple(value.unresolved_blockers)
    if blockers != tuple(sorted(set(blockers))) or any(
        not REASON.fullmatch(item) for item in blockers
    ):
        raise DirectorMeshError(
            "director.synthesis.blockers", "synthesis blockers are not canonical"
        )
    if value.rounds_used < 1 or value.rounds_used > MAX_CONFLICT_ROUNDS:
        raise DirectorMeshError("director.synthesis.rounds", "synthesis rounds exceed policy")
    if value.status is SynthesisStatus.COHERENT and (
        value.resulting_blueprint_sha256 is None or value.unresolved_blockers
    ):
        raise DirectorMeshError("director.synthesis.coherent_shape", "invalid coherent synthesis")
    if value.resulting_blueprint_sha256 is not None and not SHA256.fullmatch(
        str(value.resulting_blueprint_sha256)
    ):
        raise DirectorMeshError(
            "director.synthesis.result", "resulting Blueprint digest is invalid"
        )
    if value.status is SynthesisStatus.BLOCKED and (
        value.resulting_blueprint_sha256 is not None or not value.unresolved_blockers
    ):
        raise DirectorMeshError("director.synthesis.blocked_shape", "invalid blocked synthesis")
    return {
        "base_blueprint_sha256": str(value.base_blueprint_sha256),
        "blueprint_context_sha256": str(value.blueprint_context_sha256),
        "conflict_session_id": str(value.conflict_session_id),
        "previous_synthesis_sha256": (
            str(value.previous_synthesis_sha256)
            if value.previous_synthesis_sha256 is not None
            else None
        ),
        "assessment_sha256s": list(assessments),
        "conflict_sha256s": list(conflicts),
        "status": value.status.value,
        "resulting_blueprint_sha256": (
            str(value.resulting_blueprint_sha256)
            if value.resulting_blueprint_sha256 is not None
            else None
        ),
        "unresolved_blockers": list(blockers),
        "rounds_used": value.rounds_used,
    }


def director_synthesis_to_mapping(value: DirectorSynthesis) -> dict[str, object]:
    identity = _synthesis_identity_mapping(value)
    digest = canonical_sha256(identity)
    if value.synthesis_sha256 != digest or value.synthesis_id != OpaqueId(
        f"director-synthesis-{str(digest)[:20]}"
    ):
        raise DirectorMeshError("director.synthesis.identity", "synthesis identity mismatch")
    return {
        "artifact_version": "director-synthesis/1.0",
        "synthesis_id": str(value.synthesis_id),
        "synthesis_sha256": str(value.synthesis_sha256),
        **identity,
    }


def _new_conflict(
    *,
    blueprint: ProductionBlueprint,
    field_path: str,
    proposals: Sequence[tuple[DirectorAssessment, PatchProposal]],
    selected: tuple[DirectorAssessment, PatchProposal] | None,
    reason_code: str,
    rounds_used: int,
    conflict_session_id: OpaqueId,
    previous_synthesis_sha256: HashDigest | None,
) -> ConflictResult:
    proposal_ids = tuple(sorted((item[0].assessment_id for item in proposals), key=str))
    selected_id = selected[0].assessment_id if selected is not None else None
    rejected = tuple(item for item in proposal_ids if item != selected_id)
    replacement_digest = None
    if selected is not None:
        replacement_digest = field_value_sha256(
            BlueprintField(field_path, selected[1].replacement_value_json)
        )
    evidence = _canonical_references(
        reference
        for _, proposal in proposals
        for reference in proposal.evidence_refs
    )
    provisional = ConflictResult(
        conflict_id=OpaqueId("pending"),
        conflict_sha256=HashDigest("0" * 64),
        base_blueprint_sha256=blueprint.blueprint_sha256,
        blueprint_context_sha256=blueprint_context_sha256(blueprint.context),
        conflict_session_id=conflict_session_id,
        previous_synthesis_sha256=previous_synthesis_sha256,
        field_path=field_path,
        proposal_assessment_ids=proposal_ids,
        status=(ConflictStatus.RESOLVED if selected is not None else ConflictStatus.BLOCKED),
        selected_assessment_id=selected_id,
        rejected_assessment_ids=rejected,
        selected_replacement_sha256=replacement_digest,
        reason_code=reason_code,
        rounds_used=rounds_used,
        evidence_refs=evidence,
    )
    identity = _conflict_identity_mapping(provisional)
    digest = canonical_sha256(identity)
    return replace(
        provisional,
        conflict_id=OpaqueId(f"blueprint-conflict-{str(digest)[:20]}"),
        conflict_sha256=digest,
    )


def synthesize_director_assessments(
    *,
    blueprint: ProductionBlueprint,
    charters: Iterable[DirectorCharter],
    activation: DirectorActivation,
    source_bundle: BlueprintSourceBundle,
    tasks: Iterable[DirectorTaskPlan],
    assessments: Iterable[DirectorAssessment],
    previous_synthesis: DirectorSynthesis | None = None,
    previous_tasks: Iterable[DirectorTaskPlan] | None = None,
    previous_assessments: Iterable[DirectorAssessment] | None = None,
) -> SynthesisOutcome:
    production_blueprint_to_mapping(blueprint)
    if blueprint.status is not BlueprintStatus.DRAFT:
        raise DirectorMeshError(
            "director.synthesis.base_status",
            "Director synthesis requires an unevaluated draft Blueprint base",
        )
    validate_blueprint_source_bundle(blueprint, source_bundle)
    charter_values = tuple(charters)
    validate_director_activation(
        activation,
        episode_intent=source_bundle.episode_intent,
        charters=charter_values,
    )
    registry_digest = director_registry_sha256(charter_values)
    policy_digest = activation_policy_sha256(charter_values)
    if (
        activation.registry_sha256 != registry_digest
        or activation.activation_policy_sha256 != policy_digest
        or activation.episode_intent_sha256
        != blueprint.context.episode_intent_sha256
    ):
        raise DirectorMeshError(
            "director.synthesis.activation_binding",
            "activation is not bound to the current registry, policy, and Blueprint",
        )
    validate_field_ownership(blueprint, charter_values, activation)
    session_digest = canonical_sha256(
        {
            "base_blueprint_sha256": str(blueprint.blueprint_sha256),
            "blueprint_context_sha256": str(
                blueprint_context_sha256(blueprint.context)
            ),
            "activation_sha256": str(activation.activation_sha256),
        }
    )
    conflict_session_id = OpaqueId(
        f"director-conflict-session-{str(session_digest)[:20]}"
    )
    previous_synthesis_sha256: HashDigest | None = None
    rounds_used = 1
    if previous_synthesis is None and (
        previous_tasks is not None or previous_assessments is not None
    ):
        raise DirectorMeshError(
            "director.synthesis.predecessor_evidence",
            "round-one synthesis cannot carry unused predecessor evidence",
        )
    if previous_synthesis is not None:
        director_synthesis_to_mapping(previous_synthesis)
        if (
            previous_synthesis.conflict_session_id != conflict_session_id
            or previous_synthesis.base_blueprint_sha256 != blueprint.blueprint_sha256
            or previous_synthesis.blueprint_context_sha256
            != blueprint_context_sha256(blueprint.context)
            or previous_synthesis.status is not SynthesisStatus.BLOCKED
        ):
            raise DirectorMeshError(
                "director.synthesis.predecessor",
                "previous synthesis is not the blocked predecessor for this conflict session",
            )
        if previous_synthesis.rounds_used >= MAX_CONFLICT_ROUNDS:
            raise DirectorMeshError(
                "director.synthesis.round_limit",
                "synthesis exceeds maximum conflict rounds",
            )
        if previous_tasks is None or previous_assessments is None:
            raise DirectorMeshError(
                "director.synthesis.predecessor_evidence",
                "round two requires the complete round-one task and assessment evidence",
            )
        expected_previous = synthesize_director_assessments(
            blueprint=blueprint,
            charters=charter_values,
            activation=activation,
            source_bundle=source_bundle,
            tasks=tuple(previous_tasks),
            assessments=tuple(previous_assessments),
        )
        if (
            expected_previous.synthesis != previous_synthesis
            or expected_previous.synthesis.status is not SynthesisStatus.BLOCKED
        ):
            raise DirectorMeshError(
                "director.synthesis.predecessor_binding",
                "previous synthesis does not match the complete round-one evidence",
            )
        rounds_used = previous_synthesis.rounds_used + 1
        previous_synthesis_sha256 = previous_synthesis.synthesis_sha256
    charter_by_id = {str(item.director_id): item for item in charter_values}
    task_values = tuple(tasks)
    task_by_id = {str(item.director_id): item for item in task_values}
    assessment_values = tuple(assessments)
    assessment_by_id = {str(item.director_id): item for item in assessment_values}
    active_ids = tuple(str(item) for item in activation.active_director_ids)
    if (
        not active_ids
        or len(task_by_id) != len(task_values)
        or len(assessment_by_id) != len(assessment_values)
        or tuple(sorted(task_by_id)) != active_ids
        or tuple(sorted(assessment_by_id)) != active_ids
    ):
        raise DirectorMeshError(
            "director.synthesis.coverage", "exactly one task and assessment per active director is required"
        )
    common_input_refs = task_values[0].input_refs
    if any(item.input_refs != common_input_refs for item in task_values):
        raise DirectorMeshError(
            "director.synthesis.task_inputs",
            "all Director tasks must share the exact canonical input references",
        )
    expected_tasks = plan_director_tasks(
        blueprint=blueprint,
        charters=charter_values,
        activation=activation,
        source_bundle=source_bundle,
        input_refs=common_input_refs,
    )
    expected_task_by_id = {
        str(item.director_id): item for item in expected_tasks
    }
    for director_id in active_ids:
        director_task_plan_to_mapping(task_by_id[director_id])
        if task_by_id[director_id] != expected_task_by_id[director_id]:
            raise DirectorMeshError(
                "director.synthesis.task_scope",
                "Director task does not match the current registry-derived scope",
            )
    for director_id in active_ids:
        validate_director_assessment(
            assessment_by_id[director_id],
            task=task_by_id[director_id],
            charter=charter_by_id[director_id],
            current_blueprint=blueprint,
            activation=activation,
        )
    _require_reference_identity_consistency(
        (
            *common_input_refs,
            *(
                reference
                for assessment in assessment_values
                for reference in _assessment_references(assessment)
            ),
        )
    )

    blockers = set(blueprint.unresolved_blockers)
    blockers.update(
        item.reason_code
        for assessment in assessment_values
        for item in assessment.blockers
    )
    blockers.update(
        "director.confidence.low"
        for item in assessment_values
        if item.confidence_basis_points < MIN_SYNTHESIS_CONFIDENCE_BPS
    )
    blockers.update(
        "director.assessment.uncertain"
        for item in assessment_values
        if item.verdict is DirectorVerdict.UNCERTAIN
    )

    proposals_by_field: dict[str, list[tuple[DirectorAssessment, PatchProposal]]] = {}
    for assessment in assessment_values:
        for proposal in assessment.patches:
            proposals_by_field.setdefault(proposal.field_path, []).append(
                (assessment, proposal)
            )

    selected_by_field: dict[str, tuple[DirectorAssessment, PatchProposal]] = {}
    conflicts: list[ConflictResult] = []
    ownership = {item.field_path: str(item.owner_director_id) for item in blueprint.ownership}
    for field_path, raw in sorted(proposals_by_field.items()):
        proposals = tuple(sorted(raw, key=lambda item: str(item[0].assessment_id)))
        if len(proposals) == 1:
            selected_by_field[field_path] = proposals[0]
            continue
        replacements = {item[1].replacement_value_json for item in proposals}
        selected: tuple[DirectorAssessment, PatchProposal] | None = None
        reason = "director.conflict.identical_patch"
        if len(replacements) == 1:
            selected = next(
                (
                    item
                    for item in proposals
                    if str(item[0].director_id) == ownership[field_path]
                ),
                proposals[0],
            )
        else:
            hard = tuple(item for item in proposals if item[1].hard_constraint)
            hard_values = {item[1].replacement_value_json for item in hard}
            if len(hard_values) == 1 and hard:
                selected = hard[0]
                reason = "director.conflict.hard_constraint"
            elif len(hard_values) > 1:
                reason = "director.conflict.hard_constraint_disagreement"
            else:
                owner = tuple(
                    item
                    for item in proposals
                    if str(item[0].director_id) == ownership[field_path]
                )
                if len(owner) == 1:
                    selected = owner[0]
                    reason = "director.conflict.primary_owner"
                else:
                    priorities = {
                        charter_by_id[str(item[0].director_id)].conflict_priority
                        for item in proposals
                    }
                    highest = max(priorities)
                    winners = tuple(
                        item
                        for item in proposals
                        if charter_by_id[str(item[0].director_id)].conflict_priority
                        == highest
                    )
                    if len(winners) == 1:
                        selected = winners[0]
                        reason = "director.conflict.priority"
                    else:
                        reason = "director.conflict.equal_priority"
        conflict = _new_conflict(
            blueprint=blueprint,
            field_path=field_path,
            proposals=proposals,
            selected=selected,
            reason_code=reason,
            rounds_used=rounds_used,
            conflict_session_id=conflict_session_id,
            previous_synthesis_sha256=previous_synthesis_sha256,
        )
        conflicts.append(conflict)
        if selected is None:
            blockers.add(reason)
        else:
            selected_by_field[field_path] = selected

    result_blueprint: ProductionBlueprint | None = None
    if not blockers:
        current = field_index(blueprint)
        for field_path, (_, proposal) in selected_by_field.items():
            current[field_path] = BlueprintField(
                path=field_path,
                value_json=proposal.replacement_value_json,
            )
        provenance = tuple(
            sorted(
                (
                    DirectorProvenance(
                        director_id=item.director_id,
                        director_version=item.director_version,
                        charter_sha256=item.charter_sha256,
                        assessment_sha256=item.assessment_sha256,
                        input_blueprint_sha256=item.base_blueprint_sha256,
                        blueprint_context_sha256=item.blueprint_context_sha256,
                    )
                    for item in assessment_values
                ),
                key=lambda item: (str(item.director_id), str(item.assessment_sha256)),
            )
        )
        result_blueprint = _build_synthesized_production_blueprint(
            base_blueprint=blueprint,
            fields=tuple(current[path] for path in sorted(current)),
            director_provenance=provenance,
        )

    provisional = DirectorSynthesis(
        synthesis_id=OpaqueId("pending"),
        synthesis_sha256=HashDigest("0" * 64),
        base_blueprint_sha256=blueprint.blueprint_sha256,
        blueprint_context_sha256=blueprint_context_sha256(blueprint.context),
        conflict_session_id=conflict_session_id,
        previous_synthesis_sha256=previous_synthesis_sha256,
        assessment_sha256s=tuple(
            sorted((item.assessment_sha256 for item in assessment_values), key=str)
        ),
        conflict_sha256s=tuple(
            sorted((item.conflict_sha256 for item in conflicts), key=str)
        ),
        status=(
            SynthesisStatus.COHERENT
            if result_blueprint is not None
            else SynthesisStatus.BLOCKED
        ),
        resulting_blueprint_sha256=(
            result_blueprint.blueprint_sha256
            if result_blueprint is not None
            else None
        ),
        unresolved_blockers=tuple(sorted(blockers)),
        rounds_used=rounds_used,
    )
    identity = _synthesis_identity_mapping(provisional)
    digest = canonical_sha256(identity)
    synthesis = replace(
        provisional,
        synthesis_id=OpaqueId(f"director-synthesis-{str(digest)[:20]}"),
        synthesis_sha256=digest,
    )
    return SynthesisOutcome(
        synthesis=synthesis,
        blueprint=result_blueprint,
        conflicts=tuple(conflicts),
    )


def verify_coherent_blueprint_promotion(
    *,
    promoted_blueprint: ProductionBlueprint,
    synthesis: DirectorSynthesis,
    base_blueprint: ProductionBlueprint,
    charters: Iterable[DirectorCharter],
    activation: DirectorActivation,
    source_bundle: BlueprintSourceBundle,
    tasks: Iterable[DirectorTaskPlan],
    assessments: Iterable[DirectorAssessment],
    previous_synthesis: DirectorSynthesis | None = None,
    previous_tasks: Iterable[DirectorTaskPlan] | None = None,
    previous_assessments: Iterable[DirectorAssessment] | None = None,
) -> VerifiedBlueprintPromotion:
    """Recompute a promotion from the complete evidence bundle.

    Loading a coherent artifact only proves structural validity.  This verifier
    is the semantic boundary for callers that need to rely on its Director
    promotion claim.  It still grants no generation, mutation, dispatch, or
    publication authority.
    """

    production_blueprint_to_mapping(promoted_blueprint)
    expected = synthesize_director_assessments(
        blueprint=base_blueprint,
        charters=tuple(charters),
        activation=activation,
        source_bundle=source_bundle,
        tasks=tuple(tasks),
        assessments=tuple(assessments),
        previous_synthesis=previous_synthesis,
        previous_tasks=(tuple(previous_tasks) if previous_tasks is not None else None),
        previous_assessments=(
            tuple(previous_assessments)
            if previous_assessments is not None
            else None
        ),
    )
    if expected.blueprint is None or expected.synthesis.status is not SynthesisStatus.COHERENT:
        raise DirectorMeshError(
            "director.promotion.not_coherent",
            "the supplied evidence does not produce a coherent Blueprint",
        )
    if expected.synthesis != synthesis or expected.blueprint != promoted_blueprint:
        raise DirectorMeshError(
            "director.promotion.binding",
            "coherent Blueprint and synthesis do not match the complete evidence bundle",
        )
    return VerifiedBlueprintPromotion(
        blueprint=promoted_blueprint,
        synthesis=synthesis,
        activation_sha256=activation.activation_sha256,
        authority_effect="none",
    )
