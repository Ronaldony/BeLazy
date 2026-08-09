"""Target-owned adapter from episode observations to declarative gate facts.

The adapter shares low-level validation primitives with the legacy planner but
never calls ``plan_next_step`` and never accepts an expected action.  This
keeps dual-run characterization honest: both evaluators consume the same
observation and independently derive their result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from video_factory.approvals import GateContext, gate_context_to_mapping
from video_factory.config import canonical_sha256
from video_factory.domain import HashDigest, OpaqueId
from video_factory.mutation import (
    WorkspaceObservation,
    WorkspaceRevision,
    workspace_observation_mapping,
    workspace_observation_sha256,
    workspace_revision_to_mapping,
    workspace_trust_blockers,
)
from video_factory.policy import (
    STAGE_FINAL_VIDEO,
    STAGE_GENERATION_PLAN,
    STAGE_STORYBOARD,
    ApprovalKind,
    ReviewMode,
    WorkflowPolicy,
    resolve_workflow_policy,
)
from video_factory.workflow import (
    GateResult,
    GateStatus,
    MaterialContextSeed,
    WorkflowContractError,
    WorkflowEvaluation,
    build_gate_result,
    default_workflow_definition,
    evaluate_workflow,
    gate_consumed_context_sha256,
    validate_gate_result,
    validate_workflow_evaluation,
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


class _DeclarativeGateRunSeal:
    """Bind one ephemeral cache seal to exactly one builder-created run."""

    __slots__ = ("owner",)

    def __init__(self) -> None:
        self.owner: DeclarativeGateRun | None = None

    def bind(self, run: DeclarativeGateRun) -> None:
        if self.owner is not None:
            raise WorkflowContractError(
                "workflow.adapter.construction",
                "declarative gate run seal is already bound",
            )
        self.owner = run

    def is_bound_to(self, run: DeclarativeGateRun) -> bool:
        return self.owner is run

    def __copy__(self) -> _DeclarativeGateRunSeal:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> _DeclarativeGateRunSeal:
        return self


@dataclass(frozen=True, slots=True)
class DeclarativeGateRun:
    """Ephemeral, non-authorizing adapter cache for dependency-scoped reruns."""

    workflow_definition_sha256: HashDigest
    gate_results: tuple[GateResult, ...]
    input_sha256s: tuple[tuple[str, HashDigest], ...]
    evaluated_claim_ids: tuple[str, ...]
    reused_claim_ids: tuple[str, ...]
    _construction_seal: _DeclarativeGateRunSeal = field(
        init=False,
        repr=False,
        compare=False,
        default_factory=_DeclarativeGateRunSeal,
    )
    authority_effect: str = "none"


_GATE_ARTIFACT_FAMILIES: Mapping[str, tuple[str, ...]] = {
    "brief_present": (PipelineKind.BRIEF.value,),
    "storyboard_present": (PipelineKind.STORYBOARD.value,),
    "storyboard_review_pass": (
        PipelineKind.STORYBOARD.value,
        PipelineKind.STORYBOARD_REVIEW.value,
    ),
    "storyboard_approval_current": (
        PipelineKind.STORYBOARD.value,
        PipelineKind.STORYBOARD_REVIEW.value,
        PipelineKind.STORYBOARD_APPROVAL.value,
    ),
    "packet_present": (PipelineKind.GENERATION_PACKET.value,),
    "packet_contract_valid": (
        PipelineKind.GENERATION_PACKET.value,
        PipelineKind.STORYBOARD.value,
    ),
    "packet_review_pass": (
        PipelineKind.GENERATION_PACKET.value,
        PipelineKind.PACKET_REVIEW.value,
    ),
    "feasibility_pass": (
        PipelineKind.GENERATION_PACKET.value,
        PipelineKind.STORYBOARD.value,
        PipelineKind.FEASIBILITY_REVIEW.value,
    ),
    "generation_approval_current": (
        PipelineKind.GENERATION_PACKET.value,
        PipelineKind.PACKET_REVIEW.value,
        PipelineKind.FEASIBILITY_REVIEW.value,
        PipelineKind.PACKET_APPROVAL.value,
    ),
    "shot_qc_pass": (
        PipelineKind.GENERATION_PACKET.value,
        PipelineKind.SHOT_QC.value,
    ),
    "continuity_qc_pass": (
        PipelineKind.GENERATION_PACKET.value,
        PipelineKind.SHOT_QC.value,
        PipelineKind.CONTINUITY_QC.value,
    ),
    "candidate_ranking_current": (
        PipelineKind.GENERATION_PACKET.value,
        PipelineKind.SHOT_QC.value,
        PipelineKind.CANDIDATE_RANKING.value,
    ),
    "edit_manifest_current": (
        PipelineKind.GENERATION_PACKET.value,
        PipelineKind.CANDIDATE_RANKING.value,
        PipelineKind.EDIT_MANIFEST.value,
    ),
    "rough_cut_pass": (
        PipelineKind.CANDIDATE_RANKING.value,
        PipelineKind.EDIT_MANIFEST.value,
        PipelineKind.ROUGH_CUT_REPORT.value,
    ),
    "final_delivery_present": (PipelineKind.FINAL_DELIVERY.value,),
    "final_delivery_valid": (
        PipelineKind.FINAL_DELIVERY.value,
        PipelineKind.EDIT_MANIFEST.value,
        PipelineKind.ROUGH_CUT_REPORT.value,
        PipelineKind.SHOT_QC.value,
    ),
    "final_review_pass": (
        PipelineKind.FINAL_DELIVERY.value,
        PipelineKind.FINAL_REVIEW.value,
    ),
    "publish_metadata_present": (PipelineKind.PUBLISH_METADATA_DRAFT.value,),
    "publish_metadata_bound": (
        PipelineKind.PUBLISH_METADATA_DRAFT.value,
        PipelineKind.BRIEF.value,
    ),
    "publish_approval_current": (
        PipelineKind.FINAL_DELIVERY.value,
        PipelineKind.FINAL_REVIEW.value,
        PipelineKind.PUBLISH_METADATA_DRAFT.value,
        PipelineKind.PUBLISH_APPROVAL.value,
    ),
}
_APPROVAL_CLAIMS = frozenset(
    {
        "storyboard_approval_current",
        "generation_approval_current",
        "publish_approval_current",
    }
)
_REVIEW_STAGE_BY_CLAIM: Mapping[str, str] = {
    "storyboard_review_pass": STAGE_STORYBOARD,
    "packet_review_pass": STAGE_GENERATION_PLAN,
    "final_review_pass": STAGE_FINAL_VIDEO,
}
_TARGET_PREFLIGHT_STAGES = frozenset(
    {STAGE_STORYBOARD, STAGE_GENERATION_PLAN}
)
_EVIDENCE_REVIEW_MODES = frozenset(
    {
        ReviewMode.PEER_AI,
        ReviewMode.HUMAN,
        ReviewMode.PEER_AI_AND_HUMAN,
        ReviewMode.TWO_INDEPENDENT_REVIEWERS,
    }
)


def _target_review_mode(policy: WorkflowPolicy, stage: str) -> ReviewMode:
    """Project routine design/preflight review onto non-human target gates."""

    configured = policy.review_policy_by_stage.get(stage, ReviewMode.NONE)
    if stage in _TARGET_PREFLIGHT_STAGES and policy.mode is not WorkflowMode.RAPID:
        return ReviewMode.PEER_AI
    return configured


def _approval_required(policy: WorkflowPolicy, claim_id: str) -> bool:
    """Return whether the target adapter actually evaluates human evidence."""

    if claim_id == "storyboard_approval_current":
        # The legacy identity remains for dual-run compatibility.  Routine
        # storyboard approval is removed from the target path; material
        # creative deviations are escalated by the authority classifier.
        return False
    if claim_id == "generation_approval_current":
        return ApprovalKind.GENERATION_APPROVAL in policy.required_approval_kinds
    if claim_id == "publish_approval_current":
        return ApprovalKind.PUBLISH_APPROVAL in policy.required_approval_kinds
    return False


def _snapshot_input(snapshot: ArtifactSnapshot) -> dict[str, object]:
    return {
        "path": str(snapshot.path),
        "sha256": str(snapshot.sha256),
        "artifact_version": snapshot.artifact_version,
        "is_current": snapshot.is_current,
    }


def _gate_input_sha256s(
    observation: EpisodeStateObservation,
    workflow_mode: str | WorkflowMode,
    material_context: MaterialContextSeed,
    *,
    current_context: GateContext | None,
    evaluated_at: datetime | None,
    workspace_observation: WorkspaceObservation | None,
    expected_workspace_id: str | None,
    expected_workspace_revision_id: str | None,
    expected_workspace_revision: WorkspaceRevision | None,
    expected_workspace_revision_sha256: str | None,
) -> tuple[tuple[str, HashDigest], ...]:
    definition = default_workflow_definition()
    policy = resolve_workflow_policy(workflow_mode)
    gate_by_claim = {
        str(claim.claim_id): next(
            gate for gate in definition.gates if gate.gate_id == claim.gate_id
        )
        for claim in definition.claims
    }
    values: list[tuple[str, HashDigest]] = []
    for claim in definition.claims:
        claim_id = str(claim.claim_id)
        families = _GATE_ARTIFACT_FAMILIES.get(claim_id, ())
        review_stage = _REVIEW_STAGE_BY_CLAIM.get(claim_id)
        review_mode = (
            _target_review_mode(policy, review_stage)
            if review_stage is not None
            else None
        )
        approval_required = (
            _approval_required(policy, claim_id)
            if claim_id in _APPROVAL_CLAIMS
            else False
        )
        if review_mode is not None and review_mode not in _EVIDENCE_REVIEW_MODES:
            # A bypassed/self-check review depends on subject presence only,
            # not on review artifacts that the evaluator never reads.
            families = families[:1]
        if claim_id in _APPROVAL_CLAIMS and not approval_required:
            families = ()
        payload: dict[str, object] = {
            "claim_id": claim_id,
            "gate_id": str(claim.gate_id),
            "consumed_context_sha256": str(
                gate_consumed_context_sha256(
                    gate_by_claim[claim_id], material_context
                )
            ),
            "artifact_inputs": [
                {
                    "family": family,
                    "snapshots": [
                        _snapshot_input(snapshot)
                        for snapshot in observation.graph.family(family)
                    ],
                }
                for family in families
            ],
        }
        if claim_id == "artifact_graph_valid":
            payload["findings"] = list(observation.findings)
        if review_mode is not None:
            payload["review_mode"] = review_mode.value
        if claim_id in _APPROVAL_CLAIMS:
            payload["approval_required"] = approval_required
        if approval_required:
            payload["current_context"] = (
                gate_context_to_mapping(current_context)
                if current_context is not None
                else None
            )
            payload["evaluated_at"] = (
                evaluated_at.isoformat() if evaluated_at is not None else None
            )
        if claim_id == "generation_mode_permits_execution":
            payload["preview_only"] = policy.mode is WorkflowMode.RAPID
        if claim_id == "workspace_trusted":
            workspace_required = policy.mode is not WorkflowMode.RAPID
            payload["workspace_required"] = workspace_required
        if claim_id == "workspace_trusted" and workspace_required:
            payload["workspace"] = {
                "observation": (
                    workspace_observation_mapping(workspace_observation)
                    if workspace_observation is not None
                    else None
                ),
                "expected_workspace_id": expected_workspace_id,
                "expected_workspace_revision_id": expected_workspace_revision_id,
                "expected_workspace_revision": (
                    workspace_revision_to_mapping(expected_workspace_revision)
                    if expected_workspace_revision is not None
                    else None
                ),
                "expected_workspace_revision_sha256": (
                    expected_workspace_revision_sha256
                ),
            }
        values.append((claim_id, canonical_sha256(payload)))
    return tuple(values)


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


def _build_declarative_gate_results(
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
    reused_results: Mapping[str, GateResult] | None = None,
) -> tuple[GateResult, ...]:
    """Derive all target-DAG gate results from one validated observation."""

    definition = default_workflow_definition()
    policy = resolve_workflow_policy(workflow_mode)
    current_approval_required = any(
        _approval_required(policy, claim_id) for claim_id in _APPROVAL_CLAIMS
    )
    if current_context is not None and current_approval_required:
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
    reusable = dict(reused_results or {})

    def reuse(claim_id: str) -> bool:
        previous = reusable.get(claim_id)
        if previous is None:
            return False
        validate_gate_result(previous)
        gate = gate_by_claim[claim_id]
        if (
            previous.gate_id != gate.gate_id
            or previous.consumed_context_sha256
            != gate_consumed_context_sha256(gate, material_context)
        ):
            raise WorkflowContractError(
                "workflow.adapter.reuse_rebound",
                "reused gate result is bound to another gate or material context",
            )
        results[claim_id] = previous
        return True

    def reused_family_evidence(
        claim_id: str, family: str
    ) -> tuple[ArtifactSnapshot, ...]:
        evidence = {
            str(value) for value in results[claim_id].evidence_sha256s
        }
        return tuple(
            snapshot
            for snapshot in observation.graph.family(family)
            if str(snapshot.sha256) in evidence
        )

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

    if not reuse("artifact_graph_valid"):
        record(
            "artifact_graph_valid",
            observation.findings,
            reason_code="workflow.artifact_graph_valid.blocked",
            # Structural findings, rather than every artifact byte, are the
            # material input for this root claim.  Each downstream gate binds
            # its own exact artifact evidence, so a late-artifact change must
            # not invalidate the entire DAG through this root.
            evidence=(),
        )

    brief = observation.graph.one(PipelineKind.BRIEF.value)
    if not reuse("brief_present"):
        record(
            "brief_present",
            () if brief is not None else ("brief missing",),
            reason_code="workflow.brief_present.blocked",
            evidence=_evidence(brief),
        )
    storyboard = observation.graph.one(PipelineKind.STORYBOARD.value)
    if not reuse("storyboard_present"):
        record(
            "storyboard_present",
            () if storyboard is not None else ("storyboard missing",),
            reason_code="workflow.storyboard_present.blocked",
            evidence=_evidence(storyboard),
        )

    storyboard_reviews: tuple[ArtifactSnapshot, ...] = ()
    if reuse("storyboard_review_pass"):
        storyboard_reviews = reused_family_evidence(
            "storyboard_review_pass", PipelineKind.STORYBOARD_REVIEW.value
        )
    else:
        if storyboard is None:
            storyboard_review_blockers = ("storyboard unavailable",)
        else:
            storyboard_review_blockers, storyboard_reviews = _review_blockers(
                observation,
                subject=storyboard,
                review_family=PipelineKind.STORYBOARD_REVIEW.value,
                mode=_target_review_mode(policy, STAGE_STORYBOARD),
            )
        record(
            "storyboard_review_pass",
            storyboard_review_blockers,
            reason_code="workflow.review.blocked",
            evidence=_evidence(storyboard, storyboard_reviews),
        )

    if not reuse("storyboard_approval_current"):
        record(
            "storyboard_approval_current",
            (),
            reason_code="workflow.approval.blocked",
            evidence=(),
        )

    packet = observation.graph.one(PipelineKind.GENERATION_PACKET.value)
    if not reuse("packet_present"):
        record(
            "packet_present",
            () if packet is not None else ("generation-packet missing",),
            reason_code="workflow.packet_present.blocked",
            evidence=_evidence(packet),
        )
    if not reuse("packet_contract_valid"):
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
    if reuse("packet_review_pass"):
        packet_reviews = reused_family_evidence(
            "packet_review_pass", PipelineKind.PACKET_REVIEW.value
        )
    else:
        if packet is None:
            packet_review_blockers = ("packet unavailable",)
        else:
            packet_review_blockers, packet_reviews = _review_blockers(
                observation,
                subject=packet,
                review_family=PipelineKind.PACKET_REVIEW.value,
                mode=_target_review_mode(policy, STAGE_GENERATION_PLAN),
            )
        record(
            "packet_review_pass",
            packet_review_blockers,
            reason_code="workflow.review.blocked",
            evidence=_evidence(packet, packet_reviews),
        )

    feasibility = observation.graph.one(PipelineKind.FEASIBILITY_REVIEW.value)
    if not reuse("feasibility_pass"):
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

    if not reuse("generation_approval_current"):
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
            evidence=(
                _evidence(
                    packet,
                    packet_reviews,
                    feasibility,
                    generation_approval,
                )
                if ApprovalKind.GENERATION_APPROVAL
                in policy.required_approval_kinds
                else ()
            ),
        )

    rapid = policy.mode is WorkflowMode.RAPID
    if not reuse("generation_mode_permits_execution"):
        record(
            "generation_mode_permits_execution",
            ("rapid mode cannot authorize generation",) if rapid else (),
            reason_code="workflow.mode.preview_only",
            evidence=(),
        )

    if not reuse("workspace_trusted"):
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
            evidence=(
                (
                    str(
                        workspace_observation_sha256(
                            workspace_observation
                        )
                    ),
                )
                if workspace_observation is not None
                else ()
            ),
        )

    shot_qcs = observation.graph.family(PipelineKind.SHOT_QC.value)
    if not reuse("shot_qc_pass"):
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
            evidence=_evidence(packet, shot_qcs),
        )

    continuity = observation.graph.family(PipelineKind.CONTINUITY_QC.value)
    if not reuse("continuity_qc_pass"):
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
            evidence=_evidence(packet, continuity, shot_qcs),
        )

    ranking = observation.graph.one(PipelineKind.CANDIDATE_RANKING.value)
    if not reuse("candidate_ranking_current"):
        ranking_blockers = (
            _ranking_blockers(observation, packet, ranking)
            if packet is not None
            else ("packet unavailable",)
        )
        record(
            "candidate_ranking_current",
            ranking_blockers,
            reason_code="workflow.candidate_ranking_current.blocked",
            evidence=_evidence(packet, shot_qcs, ranking),
        )

    edit_manifest = observation.graph.one(PipelineKind.EDIT_MANIFEST.value)
    if not reuse("edit_manifest_current"):
        edit_blockers = (
            _edit_manifest_blockers(packet, ranking, edit_manifest)
            if packet is not None and ranking is not None
            else ("edit inputs unavailable",)
        )
        record(
            "edit_manifest_current",
            edit_blockers,
            reason_code="workflow.edit_manifest_current.blocked",
            evidence=_evidence(packet, edit_manifest, ranking),
        )

    rough_cut = observation.graph.one(PipelineKind.ROUGH_CUT_REPORT.value)
    if not reuse("rough_cut_pass"):
        rough_blockers = (
            _rough_cut_blockers(observation, ranking, edit_manifest, rough_cut)
            if ranking is not None and edit_manifest is not None
            else ("rough-cut inputs unavailable",)
        )
        record(
            "rough_cut_pass",
            rough_blockers,
            reason_code="workflow.rough_cut_pass.blocked",
            evidence=_evidence(ranking, rough_cut, edit_manifest),
        )

    delivery = observation.graph.one(PipelineKind.FINAL_DELIVERY.value)
    if not reuse("final_delivery_present"):
        record(
            "final_delivery_present",
            () if delivery is not None else ("final-delivery missing",),
            reason_code="workflow.final_delivery.missing",
            evidence=_evidence(delivery),
        )
    if not reuse("final_delivery_valid"):
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
            evidence=_evidence(delivery, edit_manifest, rough_cut, shot_qcs),
        )

    final_reviews: tuple[ArtifactSnapshot, ...] = ()
    if reuse("final_review_pass"):
        final_reviews = reused_family_evidence(
            "final_review_pass", PipelineKind.FINAL_REVIEW.value
        )
    else:
        if delivery is None:
            final_review_blockers = ("final-delivery unavailable",)
        else:
            final_review_blockers, final_reviews = _review_blockers(
                observation,
                subject=delivery,
                review_family=PipelineKind.FINAL_REVIEW.value,
                mode=_target_review_mode(policy, STAGE_FINAL_VIDEO),
            )
        record(
            "final_review_pass",
            final_review_blockers,
            reason_code="workflow.review.blocked",
            evidence=_evidence(delivery, final_reviews),
        )

    metadata = observation.graph.one(PipelineKind.PUBLISH_METADATA_DRAFT.value)
    if not reuse("publish_metadata_present"):
        record(
            "publish_metadata_present",
            () if metadata is not None else ("publish metadata missing",),
            reason_code="workflow.publish_metadata.missing",
            evidence=_evidence(metadata),
        )
    if not reuse("publish_metadata_bound"):
        metadata_bound = (
            metadata is not None
            and brief is not None
            and observation.graph.matches(
                brief, metadata.document.get("source_brief_ref")
            )
        )
        record(
            "publish_metadata_bound",
            (
                ()
                if metadata_bound
                else ("publish metadata is not bound to the current brief",)
            ),
            reason_code="workflow.publish_metadata_bound.blocked",
            evidence=_evidence(metadata, brief),
        )

    if not reuse("publish_approval_current"):
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
            evidence=(
                _evidence(delivery, final_reviews, metadata, publish_approval)
                if ApprovalKind.PUBLISH_APPROVAL
                in policy.required_approval_kinds
                else ()
            ),
        )
    if not reuse("external_publish_complete"):
        record(
            "external_publish_complete",
            ("external publish requires a human publisher",),
            reason_code="workflow.external_publish_complete.pending_human",
            evidence=(),
        )

    return tuple(results[str(claim.claim_id)] for claim in definition.claims)


def _validate_declarative_gate_run(run: DeclarativeGateRun) -> None:
    definition = default_workflow_definition()
    claim_ids = tuple(str(claim.claim_id) for claim in definition.claims)
    if (
        run.authority_effect != "none"
        or not run._construction_seal.is_bound_to(run)
        or run.workflow_definition_sha256 != definition.definition_sha256
        or tuple(value for value, _ in run.input_sha256s) != claim_ids
        or len(run.gate_results) != len(claim_ids)
        or set(run.evaluated_claim_ids) & set(run.reused_claim_ids)
        or set(run.evaluated_claim_ids) | set(run.reused_claim_ids)
        != set(claim_ids)
        or run.evaluated_claim_ids
        != tuple(
            value for value in claim_ids if value in run.evaluated_claim_ids
        )
        or run.reused_claim_ids
        != tuple(value for value in claim_ids if value in run.reused_claim_ids)
    ):
        raise WorkflowContractError(
            "workflow.adapter.previous_run",
            "previous declarative gate run is not the exact target run shape",
        )
    for _, digest in run.input_sha256s:
        value = str(digest)
        if (
            len(value) != 64
            or value.lower() != value
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise WorkflowContractError(
                "workflow.adapter.previous_run",
                "previous declarative gate input digest is malformed",
            )
    for claim, result in zip(definition.claims, run.gate_results):
        validate_gate_result(result)
        if result.gate_id != claim.gate_id:
            raise WorkflowContractError(
                "workflow.adapter.previous_run",
                "previous gate result order does not match the target DAG",
            )


def build_declarative_gate_run(
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
    previous: DeclarativeGateRun | None = None,
) -> DeclarativeGateRun:
    """Run only gates whose exact declared adapter inputs changed."""

    definition = default_workflow_definition()
    inputs = _gate_input_sha256s(
        observation,
        workflow_mode,
        material_context,
        current_context=current_context,
        evaluated_at=evaluated_at,
        workspace_observation=workspace_observation,
        expected_workspace_id=expected_workspace_id,
        expected_workspace_revision_id=expected_workspace_revision_id,
        expected_workspace_revision=expected_workspace_revision,
        expected_workspace_revision_sha256=(
            expected_workspace_revision_sha256
        ),
    )
    reused: dict[str, GateResult] = {}
    if previous is not None:
        _validate_declarative_gate_run(previous)
        previous_inputs = dict(previous.input_sha256s)
        previous_results = {
            str(claim.claim_id): result
            for claim, result in zip(definition.claims, previous.gate_results)
        }
        reused = {
            claim_id: previous_results[claim_id]
            for claim_id, digest in inputs
            if previous_inputs[claim_id] == digest
        }
    gate_results = _build_declarative_gate_results(
        observation,
        workflow_mode,
        material_context,
        current_context=current_context,
        evaluated_at=evaluated_at,
        workspace_observation=workspace_observation,
        expected_workspace_id=expected_workspace_id,
        expected_workspace_revision_id=expected_workspace_revision_id,
        expected_workspace_revision=expected_workspace_revision,
        expected_workspace_revision_sha256=(
            expected_workspace_revision_sha256
        ),
        reused_results=reused,
    )
    claim_ids = tuple(str(claim.claim_id) for claim in definition.claims)
    run = DeclarativeGateRun(
        workflow_definition_sha256=definition.definition_sha256,
        gate_results=gate_results,
        input_sha256s=inputs,
        evaluated_claim_ids=tuple(
            claim_id for claim_id in claim_ids if claim_id not in reused
        ),
        reused_claim_ids=tuple(
            claim_id for claim_id in claim_ids if claim_id in reused
        ),
    )
    run._construction_seal.bind(run)
    _validate_declarative_gate_run(run)
    return run


def evaluate_declarative_gate_run(
    run: DeclarativeGateRun,
    material_context: MaterialContextSeed,
    *,
    previous: WorkflowEvaluation | None = None,
) -> WorkflowEvaluation:
    """Bind sealed adapter-input identities into incremental evaluation."""

    _validate_declarative_gate_run(run)
    definition = default_workflow_definition()
    if previous is None:
        if run.reused_claim_ids:
            raise WorkflowContractError(
                "workflow.adapter.previous_evaluation_missing",
                "a run with reused gates requires its exact previous evaluation",
            )
    else:
        validate_workflow_evaluation(previous)
        previous_inputs = {
            str(gate_id): str(digest)
            for gate_id, digest in previous.gate_input_sha256s
        }
        previous_results = {
            str(result.gate_id): result for result in previous.gate_results
        }
        current_inputs = dict(run.input_sha256s)
        expected_reused = tuple(
            str(claim.claim_id)
            for claim in definition.claims
            if previous_inputs.get(str(claim.gate_id))
            == str(current_inputs[str(claim.claim_id)])
        )
        if run.reused_claim_ids != expected_reused:
            raise WorkflowContractError(
                "workflow.adapter.reuse_rebound",
                "reused gates do not match unchanged predecessor inputs",
            )
        current_results = {
            str(result.gate_id): result for result in run.gate_results
        }
        for claim in definition.claims:
            if str(claim.claim_id) not in expected_reused:
                continue
            gate_id = str(claim.gate_id)
            if current_results.get(gate_id) != previous_results.get(gate_id):
                raise WorkflowContractError(
                    "workflow.adapter.reuse_result_rebound",
                    "reused gate result differs from the exact predecessor result",
                )
    return evaluate_workflow(
        definition,
        run.gate_results,
        material_context,
        previous=previous,
        gate_input_sha256s=tuple(
            (str(claim.gate_id), str(digest))
            for claim, (_, digest) in zip(
                definition.claims, run.input_sha256s
            )
        ),
    )


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
    """Build a clean full set of target-DAG gate results."""

    return build_declarative_gate_run(
        observation,
        workflow_mode,
        material_context,
        current_context=current_context,
        evaluated_at=evaluated_at,
        workspace_observation=workspace_observation,
        expected_workspace_id=expected_workspace_id,
        expected_workspace_revision_id=expected_workspace_revision_id,
        expected_workspace_revision=expected_workspace_revision,
        expected_workspace_revision_sha256=(
            expected_workspace_revision_sha256
        ),
    ).gate_results
