"""Target-owned adapter from episode observations to declarative gate facts.

The adapter shares low-level validation primitives with the legacy planner but
never calls ``plan_next_step`` and never accepts an expected action.  This
keeps dual-run characterization honest: both evaluators consume the same
observation and independently derive their result.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from video_factory.approvals import GateContext
from video_factory.domain import OpaqueId
from video_factory.mutation import (
    WorkspaceObservation,
    WorkspaceRevision,
    workspace_trust_blockers,
)
from video_factory.policy import (
    STAGE_FINAL_VIDEO,
    STAGE_GENERATION_PLAN,
    STAGE_STORYBOARD,
    ApprovalKind,
    ReviewMode,
    resolve_workflow_policy,
)
from video_factory.workflow import (
    GateResult,
    GateStatus,
    MaterialContextSeed,
    WorkflowContractError,
    build_gate_result,
    default_workflow_definition,
    gate_consumed_context_sha256,
)

from .artifact_graph import ArtifactSnapshot
from .contracts import WorkflowMode
from .orchestration import (
    EpisodeStateObservation,
    PipelineKind,
    _approval_blockers,
    _continuity_qc_blockers,
    _edit_manifest_blockers,
    _feasibility_blockers,
    _final_delivery_blockers,
    _packet_contract_blockers,
    _ranking_blockers,
    _review_blockers,
    _rough_cut_blockers,
    _shot_qc_blockers,
)


def _evidence(*values: object) -> tuple[str, ...]:
    digests: set[str] = set()

    def collect(value: object) -> None:
        if isinstance(value, ArtifactSnapshot):
            digests.add(str(value.sha256))
        elif isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            for item in value:
                collect(item)

    for value in values:
        collect(value)
    return tuple(sorted(digests))


def build_declarative_gate_results(
    observation: EpisodeStateObservation,
    workflow_mode: str | WorkflowMode,
    material_context: MaterialContextSeed,
    *,
    current_context: GateContext | None = None,
    evaluated_at: datetime | None = None,
    workspace_observation: WorkspaceObservation | None = None,
    expected_workspace_id: str | None = None,
    expected_workspace_revision_id: str | None = None,
    expected_workspace_revision: WorkspaceRevision | None = None,
    expected_workspace_revision_sha256: str | None = None,
) -> tuple[GateResult, ...]:
    """Derive all target-DAG gate results from one validated observation."""

    definition = default_workflow_definition()
    policy = resolve_workflow_policy(workflow_mode)
    if current_context is not None:
        shared_fields = (
            "workflow_definition_sha256",
            "policy_bundle_sha256",
            "rules_bundle_sha256",
            "effective_config_sha256",
            "current_manifest_sha256",
            "evidence_graph_sha256",
        )
        if any(
            getattr(current_context, field) != getattr(material_context, field)
            for field in shared_fields
        ):
            raise WorkflowContractError(
                "workflow.adapter.context_mismatch",
                "approval/workspace context differs from the evaluation material context",
            )
    gate_by_claim = {
        str(claim.claim_id): next(
            gate for gate in definition.gates if gate.gate_id == claim.gate_id
        )
        for claim in definition.claims
    }
    results: dict[str, GateResult] = {}

    def record(
        claim_id: str,
        blockers: Sequence[str],
        *,
        reason_code: str,
        evidence: Sequence[str] = (),
    ) -> None:
        gate = gate_by_claim[claim_id]
        blocked = tuple(dict.fromkeys(str(value) for value in blockers))
        results[claim_id] = build_gate_result(
            str(gate.gate_id),
            GateStatus.BLOCKED if blocked else GateStatus.PASS,
            consumed_context_sha256=str(
                gate_consumed_context_sha256(gate, material_context)
            ),
            reason_codes=(reason_code,) if blocked else (),
            messages=("; ".join(blocked),) if blocked else (),
            evidence_sha256s=evidence,
        )

    record(
        "artifact_graph_valid",
        observation.findings,
        reason_code="workflow.artifact_graph_valid.blocked",
        # Structural findings, rather than every artifact byte, are the
        # material input for this root claim.  Each downstream gate binds its
        # own exact artifact evidence, so a late-artifact change must not
        # invalidate the entire DAG through this root.
        evidence=(),
    )

    brief = observation.graph.one(PipelineKind.BRIEF.value)
    record(
        "brief_present",
        () if brief is not None else ("brief missing",),
        reason_code="workflow.brief_present.blocked",
        evidence=_evidence(brief),
    )
    storyboard = observation.graph.one(PipelineKind.STORYBOARD.value)
    record(
        "storyboard_present",
        () if storyboard is not None else ("storyboard missing",),
        reason_code="workflow.storyboard_present.blocked",
        evidence=_evidence(storyboard),
    )

    storyboard_reviews: tuple[ArtifactSnapshot, ...] = ()
    if storyboard is None:
        storyboard_review_blockers = ("storyboard unavailable",)
    else:
        storyboard_review_blockers, storyboard_reviews = _review_blockers(
            observation,
            subject=storyboard,
            review_family=PipelineKind.STORYBOARD_REVIEW.value,
            mode=policy.review_policy_by_stage.get(
                STAGE_STORYBOARD, ReviewMode.NONE
            ),
        )
    record(
        "storyboard_review_pass",
        storyboard_review_blockers,
        reason_code="workflow.review.blocked",
        evidence=_evidence(storyboard, storyboard_reviews),
    )

    storyboard_approval = None
    if not policy.hash_binding_required:
        storyboard_approval_blockers: tuple[str, ...] = ()
    elif storyboard is None:
        storyboard_approval_blockers = ("storyboard unavailable",)
    else:
        storyboard_approval_blockers, storyboard_approval = _approval_blockers(
            observation,
            family=PipelineKind.STORYBOARD_APPROVAL.value,
            expected_version="storyboard-approval/2.0",
            expected_capability="storyboard_approval",
            required_artifacts=(storyboard, *storyboard_reviews),
            current_context=current_context,
            evaluated_at=evaluated_at,
            enforce_current_context=True,
        )
    record(
        "storyboard_approval_current",
        storyboard_approval_blockers,
        reason_code="workflow.approval.blocked",
        evidence=_evidence(storyboard, storyboard_reviews, storyboard_approval),
    )

    packet = observation.graph.one(PipelineKind.GENERATION_PACKET.value)
    record(
        "packet_present",
        () if packet is not None else ("generation-packet missing",),
        reason_code="workflow.packet_present.blocked",
        evidence=_evidence(packet),
    )
    packet_contract_blockers = (
        _packet_contract_blockers(
            observation, packet=packet, storyboard=storyboard
        )
        if packet is not None and storyboard is not None
        else ("packet inputs unavailable",)
    )
    record(
        "packet_contract_valid",
        packet_contract_blockers,
        reason_code="workflow.packet_contract_valid.blocked",
        evidence=_evidence(packet, storyboard),
    )

    packet_reviews: tuple[ArtifactSnapshot, ...] = ()
    if packet is None:
        packet_review_blockers = ("packet unavailable",)
    else:
        packet_review_blockers, packet_reviews = _review_blockers(
            observation,
            subject=packet,
            review_family=PipelineKind.PACKET_REVIEW.value,
            mode=policy.review_policy_by_stage.get(
                STAGE_GENERATION_PLAN, ReviewMode.NONE
            ),
        )
    record(
        "packet_review_pass",
        packet_review_blockers,
        reason_code="workflow.review.blocked",
        evidence=_evidence(packet, packet_reviews),
    )

    feasibility = None
    if packet is None or storyboard is None:
        feasibility_blockers = ("feasibility inputs unavailable",)
    else:
        feasibility_blockers, feasibility = _feasibility_blockers(
            observation, packet=packet, storyboard=storyboard
        )
    record(
        "feasibility_pass",
        feasibility_blockers,
        reason_code="workflow.feasibility_pass.blocked",
        evidence=_evidence(packet, storyboard, feasibility),
    )

    generation_approval = None
    if ApprovalKind.GENERATION_APPROVAL not in policy.required_approval_kinds:
        generation_approval_blockers: tuple[str, ...] = ()
    elif packet is None:
        generation_approval_blockers = ("packet unavailable",)
    else:
        required = [packet, *packet_reviews]
        if feasibility is not None:
            required.append(feasibility)
        generation_approval_blockers, generation_approval = _approval_blockers(
            observation,
            family=PipelineKind.PACKET_APPROVAL.value,
            expected_version="packet-approval/2.0",
            expected_capability="generation_approval",
            required_artifacts=required,
            current_context=current_context,
            evaluated_at=evaluated_at,
            enforce_current_context=True,
        )
    record(
        "generation_approval_current",
        generation_approval_blockers,
        reason_code="workflow.approval.blocked",
        evidence=_evidence(packet, packet_reviews, feasibility, generation_approval),
    )

    rapid = policy.mode is WorkflowMode.RAPID
    record(
        "generation_mode_permits_execution",
        ("rapid mode cannot authorize generation",) if rapid else (),
        reason_code="workflow.mode.preview_only",
        evidence=(),
    )

    workspace_blockers = (
        ()
        if rapid
        else workspace_trust_blockers(
            workspace_observation,
            expected_workspace_id=(
                OpaqueId(expected_workspace_id)
                if expected_workspace_id is not None
                else None
            ),
            expected_revision_id=expected_workspace_revision_id,
            expected_manifest_sha256=(
                material_context.current_manifest_sha256
            ),
            expected_revision=expected_workspace_revision,
            expected_revision_sha256=expected_workspace_revision_sha256,
        )
    )
    record(
        "workspace_trusted",
        workspace_blockers,
        reason_code="workflow.workspace_trusted.blocked",
    )

    shot_qcs = observation.graph.family(PipelineKind.SHOT_QC.value)
    shot_blockers = (
        _shot_qc_blockers(observation, packet)
        if packet is not None
        else ("packet unavailable",)
    )
    record(
        "shot_qc_pass",
        shot_blockers,
        reason_code=(
            "workflow.shot_qc.missing"
            if not shot_qcs
            else "workflow.shot_qc.failed"
        ),
        evidence=_evidence(shot_qcs),
    )

    continuity = observation.graph.family(PipelineKind.CONTINUITY_QC.value)
    continuity_blockers = (
        _continuity_qc_blockers(observation, packet)
        if packet is not None
        else ("packet unavailable",)
    )
    record(
        "continuity_qc_pass",
        continuity_blockers,
        reason_code=(
            "workflow.continuity_qc.missing"
            if not continuity
            else "workflow.continuity_qc.failed"
        ),
        evidence=_evidence(continuity, shot_qcs),
    )

    ranking = observation.graph.one(PipelineKind.CANDIDATE_RANKING.value)
    ranking_blockers = (
        _ranking_blockers(observation, packet, ranking)
        if packet is not None
        else ("packet unavailable",)
    )
    record(
        "candidate_ranking_current",
        ranking_blockers,
        reason_code="workflow.candidate_ranking_current.blocked",
        evidence=_evidence(ranking),
    )

    edit_manifest = observation.graph.one(PipelineKind.EDIT_MANIFEST.value)
    edit_blockers = (
        _edit_manifest_blockers(packet, ranking, edit_manifest)
        if packet is not None and ranking is not None
        else ("edit inputs unavailable",)
    )
    record(
        "edit_manifest_current",
        edit_blockers,
        reason_code="workflow.edit_manifest_current.blocked",
        evidence=_evidence(edit_manifest, ranking),
    )

    rough_cut = observation.graph.one(PipelineKind.ROUGH_CUT_REPORT.value)
    rough_blockers = (
        _rough_cut_blockers(observation, ranking, edit_manifest, rough_cut)
        if ranking is not None and edit_manifest is not None
        else ("rough-cut inputs unavailable",)
    )
    record(
        "rough_cut_pass",
        rough_blockers,
        reason_code="workflow.rough_cut_pass.blocked",
        evidence=_evidence(rough_cut, edit_manifest),
    )

    delivery = observation.graph.one(PipelineKind.FINAL_DELIVERY.value)
    record(
        "final_delivery_present",
        () if delivery is not None else ("final-delivery missing",),
        reason_code="workflow.final_delivery.missing",
        evidence=_evidence(delivery),
    )
    delivery_blockers = (
        _final_delivery_blockers(
            observation,
            delivery=delivery,
            edit_manifest=edit_manifest,
            rough_cut=rough_cut,
        )
        if delivery is not None
        and edit_manifest is not None
        and rough_cut is not None
        else ("final-delivery inputs unavailable",)
    )
    record(
        "final_delivery_valid",
        delivery_blockers,
        reason_code="workflow.final_delivery_valid.blocked",
        evidence=_evidence(delivery, edit_manifest, rough_cut),
    )

    final_reviews: tuple[ArtifactSnapshot, ...] = ()
    if delivery is None:
        final_review_blockers = ("final-delivery unavailable",)
    else:
        final_review_blockers, final_reviews = _review_blockers(
            observation,
            subject=delivery,
            review_family=PipelineKind.FINAL_REVIEW.value,
            mode=policy.review_policy_by_stage.get(
                STAGE_FINAL_VIDEO, ReviewMode.NONE
            ),
        )
    record(
        "final_review_pass",
        final_review_blockers,
        reason_code="workflow.review.blocked",
        evidence=_evidence(delivery, final_reviews),
    )

    metadata = observation.graph.one(PipelineKind.PUBLISH_METADATA_DRAFT.value)
    record(
        "publish_metadata_present",
        () if metadata is not None else ("publish metadata missing",),
        reason_code="workflow.publish_metadata.missing",
        evidence=_evidence(metadata),
    )
    metadata_bound = (
        metadata is not None
        and brief is not None
        and observation.graph.matches(brief, metadata.document.get("source_brief_ref"))
    )
    record(
        "publish_metadata_bound",
        () if metadata_bound else ("publish metadata is not bound to the current brief",),
        reason_code="workflow.publish_metadata_bound.blocked",
        evidence=_evidence(metadata, brief),
    )

    publish_approval = None
    if ApprovalKind.PUBLISH_APPROVAL not in policy.required_approval_kinds:
        publish_blockers: tuple[str, ...] = ()
    elif delivery is None or metadata is None:
        publish_blockers = ("publish approval inputs unavailable",)
    else:
        publish_blockers, publish_approval = _approval_blockers(
            observation,
            family=PipelineKind.PUBLISH_APPROVAL.value,
            expected_version="publish-approval/1.0",
            expected_capability="publish_approval",
            required_artifacts=(delivery, *final_reviews, metadata),
            current_context=current_context,
            evaluated_at=evaluated_at,
            enforce_current_context=True,
        )
    record(
        "publish_approval_current",
        publish_blockers,
        reason_code="workflow.approval.blocked",
        evidence=_evidence(delivery, final_reviews, metadata, publish_approval),
    )
    record(
        "external_publish_complete",
        ("external publish requires a human publisher",),
        reason_code="workflow.external_publish_complete.pending_human",
        evidence=(),
    )

    return tuple(results[str(claim.claim_id)] for claim in definition.claims)
