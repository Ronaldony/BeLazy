"""Hash-bound episode observation and plan-only orchestration.

The caller injects file snapshots. Core validates their schemas and bindings,
but never discovers files, chooses a latest file, changes workflow state,
executes paid generation, or publishes content.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from video_factory.approvals import (
    ApprovalRequirementError,
    GateContext,
    gate_context_from_mapping,
    gate_context_sha256,
    gate_context_to_mapping,
)
from video_factory.artifacts import ArtifactSchemaRegistry
from video_factory.config import canonical_sha256
from video_factory.continuity import (
    ContinuityPlanError,
    rejudge_continuity_qc,
)
from video_factory.domain import ArtifactReference, OpaqueId
from video_factory.json_boundary import parse_rfc3339_datetime
from video_factory.policy import (
    STAGE_FINAL_VIDEO,
    STAGE_GENERATION_PLAN,
    STAGE_STORYBOARD,
    ApprovalKind,
    ReviewMode,
    WorkflowPolicy,
    WorkflowPolicyError,
    resolve_workflow_policy,
)

from .artifact_graph import (
    ArtifactGraph,
    ArtifactGraphError,
    ArtifactSnapshot,
    artifact_reference_to_mapping,
    build_artifact_graph,
)
from .contracts import WorkflowMode


class OrchestrationPlanError(ValueError):
    """Raised when observation or next-step planning cannot proceed."""


class PipelineKind(StrEnum):
    """Canonical production artifact families observed for planning."""

    BRIEF = "brief"
    STORYBOARD = "storyboard"
    STORYBOARD_REVIEW = "storyboard-review"
    STORYBOARD_APPROVAL = "storyboard-approval"
    GENERATION_PACKET = "generation-packet"
    PACKET_REVIEW = "packet-review"
    FEASIBILITY_REVIEW = "generation-feasibility-review"
    PACKET_APPROVAL = "packet-approval"
    SHOT_QC = "shot-qc"
    CONTINUITY_QC = "continuity-qc"
    CANDIDATE_RANKING = "candidate-ranking"
    EDIT_MANIFEST = "edit-manifest"
    ROUGH_CUT_REPORT = "rough-cut-report"
    FINAL_DELIVERY = "final-delivery"
    FINAL_REVIEW = "final-review"
    PUBLISH_METADATA_DRAFT = "publish-metadata-draft"
    PUBLISH_APPROVAL = "publish-approval"


_VERSION_TO_KIND: Mapping[str, PipelineKind] = {
    kind.value: kind for kind in PipelineKind
}


@dataclass(frozen=True, slots=True)
class ObservedArtifact:
    kind: PipelineKind
    artifact_version: str
    path: str
    sha256: str
    is_current: bool
    approved_by_human: bool | None
    verdict: str | None


@dataclass(frozen=True, slots=True)
class EpisodeStateObservation:
    """Read-only snapshot of a validated, caller-injected artifact graph."""

    present_kinds: frozenset[str]
    artifacts: tuple[ObservedArtifact, ...]
    approved_kinds: frozenset[str]
    unapproved_kinds: frozenset[str]
    episode_ids: tuple[str, ...]
    rules_versions: tuple[str, ...]
    findings: tuple[str, ...]
    graph: ArtifactGraph

    @property
    def episode_id(self) -> str | None:
        return self.graph.episode_id

    @property
    def rules_version(self) -> str | None:
        return self.graph.rules_version

    @property
    def valid(self) -> bool:
        return self.graph.ok


@dataclass(frozen=True, slots=True)
class GenerationReadinessPlan:
    """Proof-oriented result for rendering or executing a generation order."""

    ready: bool
    packet: ArtifactReference | None
    packet_content_sha256: str | None
    feasibility_review: ArtifactReference | None
    approval_evidence: ArtifactReference | None
    blockers: tuple[str, ...]
    gate_context_sha256: str | None = None

    @property
    def packet_sha256(self) -> str | None:
        return self.packet_content_sha256


@dataclass(frozen=True, slots=True)
class NextStepPlan:
    """Proposed next action; ``transition_applied`` is always false."""

    next_step_id: OpaqueId
    workflow_mode: WorkflowMode
    action_type: str
    instructions: str
    next_phase: str | None
    next_procedure: str | None
    next_actor_role: str | None
    completed_phase: str | None
    approval_required: bool
    auto_execution: bool
    prerequisites: tuple[str, ...]
    blockers: tuple[str, ...]
    target_paths: tuple[str, ...]
    prohibited_actions: tuple[str, ...]
    observation: EpisodeStateObservation
    gate_context_sha256: str | None = None
    transition_applied: bool = False


def _kind_from_version(artifact_version: object) -> PipelineKind | None:
    if not isinstance(artifact_version, str) or "/" not in artifact_version:
        return None
    return _VERSION_TO_KIND.get(artifact_version.split("/", 1)[0])


def observe_episode_state(
    snapshots: Sequence[ArtifactSnapshot | Mapping[str, object]],
    *,
    registry: ArtifactSchemaRegistry | None = None,
) -> EpisodeStateObservation:
    """Validate caller-observed snapshots and expose their current graph.

    A raw artifact document is intentionally not accepted. Each mapping must be
    a snapshot envelope containing ``path``, ``sha256``, and ``document``.
    """

    try:
        graph = build_artifact_graph(snapshots, registry=registry)
    except ArtifactGraphError as error:
        raise OrchestrationPlanError(str(error)) from error

    observed: list[ObservedArtifact] = []
    present: set[str] = set()
    approved: set[str] = set()
    unapproved: set[str] = set()

    for snapshot in graph.snapshots:
        kind = _kind_from_version(snapshot.artifact_version)
        if kind is None:
            continue
        if snapshot.is_current:
            present.add(kind.value)
        flag = snapshot.document.get("approved_by_human")
        approved_flag = flag if isinstance(flag, bool) else None
        verdict = snapshot.document.get("verdict")
        verdict_text = verdict if isinstance(verdict, str) else None
        if snapshot.is_current and kind in {
            PipelineKind.STORYBOARD_APPROVAL,
            PipelineKind.PACKET_APPROVAL,
            PipelineKind.PUBLISH_APPROVAL,
        }:
            granted = (
                approved_flag is True
                and snapshot.document.get("state") == "granted"
            )
            (approved if granted else unapproved).add(kind.value)
        observed.append(
            ObservedArtifact(
                kind=kind,
                artifact_version=snapshot.artifact_version,
                path=str(snapshot.path),
                sha256=str(snapshot.sha256),
                is_current=snapshot.is_current,
                approved_by_human=approved_flag,
                verdict=verdict_text,
            )
        )

    episode_ids = tuple(
        sorted(
            {
                item.episode_id
                for item in graph.current_snapshots
                if item.episode_id is not None
            }
        )
    )
    rules_versions = tuple(
        sorted(
            {
                item.rules_version
                for item in graph.current_snapshots
                if item.rules_version is not None
            }
        )
    )
    return EpisodeStateObservation(
        present_kinds=frozenset(present),
        artifacts=tuple(observed),
        approved_kinds=frozenset(approved),
        unapproved_kinds=frozenset(unapproved),
        episode_ids=episode_ids,
        rules_versions=rules_versions,
        findings=tuple(f"{item.code}: {item.message}" for item in graph.findings),
        graph=graph,
    )


def _required_review_count(mode: ReviewMode) -> int:
    if mode in {
        ReviewMode.PEER_AI_AND_HUMAN,
        ReviewMode.TWO_INDEPENDENT_REVIEWERS,
    }:
        return 2
    if mode in {ReviewMode.PEER_AI, ReviewMode.HUMAN}:
        return 1
    return 0


def _reviews_for(
    observation: EpisodeStateObservation,
    family: str,
    subject: ArtifactSnapshot,
) -> tuple[ArtifactSnapshot, ...]:
    return tuple(
        item
        for item in observation.graph.family(family)
        if observation.graph.matches(subject, item.document.get("subject"))
    )


def _review_blockers(
    observation: EpisodeStateObservation,
    *,
    subject: ArtifactSnapshot,
    review_family: str,
    mode: ReviewMode,
) -> tuple[tuple[str, ...], tuple[ArtifactSnapshot, ...]]:
    required = _required_review_count(mode)
    if required == 0:
        return (), ()

    matching = _reviews_for(observation, review_family, subject)
    blockers: list[str] = []
    passing: list[ArtifactSnapshot] = []
    expected_creator = subject.document.get("generated_by") or subject.document.get(
        "created_by"
    )
    for review in matching:
        creator = review.document.get("creator_role")
        reviewer = review.document.get("reviewer_role")
        verdict = review.document.get("verdict")
        if expected_creator is not None and creator != expected_creator:
            blockers.append(
                f"{review_family} creator_role is not the current subject creator"
            )
            continue
        if creator == reviewer:
            blockers.append(f"{review_family} creator and reviewer are identical")
            continue
        if verdict != "pass":
            blockers.append(f"{review_family} verdict is {verdict!r}, not 'pass'")
            continue
        passing.append(review)

    distinct_reviewers = {
        item.document.get("reviewer_role")
        for item in passing
        if isinstance(item.document.get("reviewer_role"), str)
    }
    if len(distinct_reviewers) < required:
        blockers.append(
            f"{review_family} requires {required} distinct current-bound passing "
            f"reviewer(s); found {len(distinct_reviewers)}"
        )
    return tuple(dict.fromkeys(blockers)), tuple(passing)


def _reference_identity(reference: ArtifactReference) -> tuple[str, str, str]:
    return (
        str(reference.path),
        str(reference.sha256),
        str(reference.artifact_version),
    )


def _approval_context_blockers(
    document: Mapping[str, object],
    *,
    family: str,
    current_context: GateContext | None,
    evaluated_at: datetime | None,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if current_context is None:
        return (f"{family} current gate context is missing",)
    try:
        current = gate_context_from_mapping(
            gate_context_to_mapping(current_context)
        )
    except ApprovalRequirementError:
        return (f"{family} current gate context is invalid",)

    raw_context = document.get("gate_context")
    if not isinstance(raw_context, Mapping):
        blockers.append(f"{family} bound gate context is missing")
    else:
        try:
            bound = gate_context_from_mapping(raw_context)
        except ApprovalRequirementError:
            blockers.append(f"{family} bound gate context is invalid")
        else:
            if bound != current:
                blockers.append(f"{family} is bound to another material context")
            if bound.effective_config_sha256 != current.effective_config_sha256:
                blockers.append(f"{family} nested effective config does not match")

    if document.get("effective_config_sha256") != str(
        current.effective_config_sha256
    ):
        blockers.append(f"{family} effective config does not match current context")

    if (
        evaluated_at is None
        or evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
    ):
        blockers.append(f"{family} evaluation time is missing or timezone-naive")
        return tuple(dict.fromkeys(blockers))

    approved_raw = document.get("approved_at")
    expires_raw = document.get("expires_at")
    if not isinstance(approved_raw, str) or not isinstance(expires_raw, str):
        blockers.append(f"{family} validity window is incomplete")
        return tuple(dict.fromkeys(blockers))
    try:
        approved = parse_rfc3339_datetime(approved_raw)
        expires = parse_rfc3339_datetime(expires_raw)
    except ValueError:
        blockers.append(f"{family} validity window is invalid")
        return tuple(dict.fromkeys(blockers))
    if approved >= expires:
        blockers.append(f"{family} expiry is not after approval time")
    if approved > evaluated_at:
        blockers.append(f"{family} approval is not yet valid")
    if evaluated_at >= expires:
        blockers.append(f"{family} approval has expired")
    return tuple(dict.fromkeys(blockers))


def _approval_blockers(
    observation: EpisodeStateObservation,
    *,
    family: str,
    expected_version: str,
    expected_capability: str,
    required_artifacts: Sequence[ArtifactSnapshot],
    current_context: GateContext | None,
    evaluated_at: datetime | None,
) -> tuple[tuple[str, ...], ArtifactSnapshot | None]:
    approvals = observation.graph.family(family)
    if len(approvals) != 1:
        return (
            (
                f"{family} requires exactly one current evidence artifact; "
                f"found {len(approvals)}",
            ),
            None,
        )
    approval = approvals[0]
    doc = approval.document
    blockers: list[str] = []
    if approval.artifact_version != expected_version:
        blockers.append(
            f"{family} must use {expected_version}; found {approval.artifact_version}"
        )
    if doc.get("approved_by_human") is not True or doc.get("state") != "granted":
        blockers.append(f"{family} is not granted human evidence")
    if doc.get("capability_id") != expected_capability:
        blockers.append(f"{family} capability_id does not match the gate")

    bound = doc.get("bound_artifacts")
    actual_items: list[tuple[str, str, str]] = []
    if isinstance(bound, Sequence) and not isinstance(
        bound, (str, bytes, bytearray)
    ):
        for item in bound:
            if isinstance(item, Mapping):
                identity = (
                    item.get("path"),
                    item.get("sha256"),
                    item.get("artifact_version"),
                )
                if all(isinstance(value, str) for value in identity):
                    actual_items.append(identity)  # type: ignore[arg-type]
    required = {_reference_identity(item.reference) for item in required_artifacts}
    actual = set(actual_items)
    if len(actual_items) != len(actual):
        blockers.append(f"{family} bound_artifacts contain duplicate identities")
    if actual != required:
        blockers.append(f"{family} bound_artifacts do not exactly match current inputs")
    blockers.extend(
        _approval_context_blockers(
            doc,
            family=family,
            current_context=current_context,
            evaluated_at=evaluated_at,
        )
    )
    return tuple(blockers), approval


_BASE_FEASIBILITY_KINDS = frozenset(
    {
        "capability_binding",
        "minimum_duration",
        "first_frame_aspect",
        "first_frame_before_state",
        "continuity_anchor",
        "unsupported_render_dependency",
    }
)
_FEASIBILITY_KINDS_BY_PACKET_VERSION: Mapping[str, frozenset[str]] = {
    "generation-packet/2.0": _BASE_FEASIBILITY_KINDS,
    "generation-packet/2.1": _BASE_FEASIBILITY_KINDS
    | {"first_frame_state_carryover"},
}


def _packet_shot_ids(packet: ArtifactSnapshot) -> tuple[str, ...]:
    raw = packet.document.get("shots")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    values: list[str] = []
    for item in raw:
        if isinstance(item, Mapping) and isinstance(item.get("shot_id"), str):
            values.append(item["shot_id"])
    return tuple(values)


def _feasibility_blockers(
    observation: EpisodeStateObservation,
    *,
    packet: ArtifactSnapshot,
    storyboard: ArtifactSnapshot,
) -> tuple[tuple[str, ...], ArtifactSnapshot | None]:
    reviews = observation.graph.family(PipelineKind.FEASIBILITY_REVIEW.value)
    if len(reviews) != 1:
        return (
            (
                "generation-feasibility-review requires exactly one current "
                f"artifact; found {len(reviews)}",
            ),
            None,
        )
    review = reviews[0]
    doc = review.document
    blockers: list[str] = []
    if not observation.graph.matches(packet, doc.get("subject")):
        blockers.append("feasibility review is not bound to the current packet")
    if not observation.graph.matches(storyboard, doc.get("storyboard_ref")):
        blockers.append("feasibility review is not bound to the current storyboard")
    if doc.get("creator_role") != packet.document.get("generated_by"):
        blockers.append("feasibility creator_role is not the packet creator")
    if doc.get("creator_role") == doc.get("reviewer_role"):
        blockers.append("feasibility creator and reviewer are identical")
    if doc.get("verdict") != "pass":
        blockers.append(f"feasibility verdict is {doc.get('verdict')!r}, not 'pass'")

    capability = doc.get("capability_profile")
    capability_id = (
        capability.get("capability_id") if isinstance(capability, Mapping) else None
    )
    shots = packet.document.get("shots")
    if isinstance(shots, Sequence) and not isinstance(
        shots, (str, bytes, bytearray)
    ):
        if any(
            not isinstance(item, Mapping)
            or item.get("capability_id") != capability_id
            for item in shots
        ):
            blockers.append(
                "feasibility capability profile does not cover every packet shot"
            )

    checks = doc.get("checks")
    observed_pairs: list[tuple[str, str]] = []
    all_pass = True
    if isinstance(checks, Sequence) and not isinstance(
        checks, (str, bytes, bytearray)
    ):
        for check in checks:
            if not isinstance(check, Mapping):
                continue
            shot_id = check.get("shot_id")
            kind = check.get("kind")
            if isinstance(shot_id, str) and isinstance(kind, str):
                observed_pairs.append((shot_id, kind))
            if check.get("status") != "pass":
                all_pass = False
    else:
        all_pass = False
    expected_kinds = _FEASIBILITY_KINDS_BY_PACKET_VERSION.get(
        packet.artifact_version, frozenset()
    )
    expected_pairs = {
        (shot_id, kind)
        for shot_id in _packet_shot_ids(packet)
        for kind in expected_kinds
    }
    if set(observed_pairs) != expected_pairs or len(observed_pairs) != len(
        expected_pairs
    ):
        blockers.append("feasibility checks are missing, duplicated, or unexpected")
    if not all_pass:
        blockers.append("every feasibility check must pass")
    return tuple(dict.fromkeys(blockers)), review


def _packet_contract_blockers(
    observation: EpisodeStateObservation,
    *,
    packet: ArtifactSnapshot,
    storyboard: ArtifactSnapshot,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if packet.artifact_version not in _FEASIBILITY_KINDS_BY_PACKET_VERSION:
        blockers.append(
            "generation requires generation-packet/2.0 or generation-packet/2.1"
        )
    if not observation.graph.matches(storyboard, packet.document.get("storyboard_ref")):
        blockers.append("generation packet is not bound to the current storyboard")
    shot_ids = _packet_shot_ids(packet)
    if not shot_ids or len(set(shot_ids)) != len(shot_ids):
        blockers.append("generation packet shot ids are empty or duplicated")
    storyboard_shots = storyboard.document.get("shots")
    storyboard_ids: set[str] = set()
    if isinstance(storyboard_shots, Sequence) and not isinstance(
        storyboard_shots, (str, bytes, bytearray)
    ):
        storyboard_ids = {
            item["shot_id"]
            for item in storyboard_shots
            if isinstance(item, Mapping) and isinstance(item.get("shot_id"), str)
        }
    packet_shots = packet.document.get("shots")
    source_ids: list[str] = []
    if isinstance(packet_shots, Sequence) and not isinstance(
        packet_shots, (str, bytes, bytearray)
    ):
        source_ids = [
            item["source_shot_id"]
            for item in packet_shots
            if isinstance(item, Mapping)
            and isinstance(item.get("source_shot_id"), str)
        ]
    if (
        len(source_ids) != len(shot_ids)
        or len(set(source_ids)) != len(source_ids)
        or not set(source_ids) <= storyboard_ids
    ):
        blockers.append(
            "packet source_shot_ids must uniquely reference current storyboard shots"
        )
    return tuple(blockers)


def build_generation_readiness(
    observation: EpisodeStateObservation,
    workflow_mode: str | WorkflowMode | None,
    *,
    current_context: GateContext | None = None,
    evaluated_at: datetime | None = None,
) -> GenerationReadinessPlan:
    """Evaluate all generation gates without executing or approving anything."""

    try:
        policy = resolve_workflow_policy(workflow_mode)
    except WorkflowPolicyError as error:
        raise OrchestrationPlanError(str(error)) from error

    blockers = list(observation.findings)
    storyboard = observation.graph.one(PipelineKind.STORYBOARD.value)
    packet = observation.graph.one(PipelineKind.GENERATION_PACKET.value)
    feasibility: ArtifactSnapshot | None = None
    approval: ArtifactSnapshot | None = None

    if storyboard is None:
        blockers.append("current storyboard is missing or ambiguous")
    if packet is None:
        blockers.append("current generation packet is missing or ambiguous")
    if storyboard is not None:
        review_mode = policy.review_policy_by_stage.get(
            STAGE_STORYBOARD, ReviewMode.NONE
        )
        review_blockers, storyboard_reviews = _review_blockers(
            observation,
            subject=storyboard,
            review_family=PipelineKind.STORYBOARD_REVIEW.value,
            mode=review_mode,
        )
        blockers.extend(review_blockers)
        if policy.hash_binding_required:
            approval_blockers, _ = _approval_blockers(
                observation,
                family=PipelineKind.STORYBOARD_APPROVAL.value,
                expected_version="storyboard-approval/2.0",
                expected_capability="storyboard_approval",
                required_artifacts=(storyboard, *storyboard_reviews),
                current_context=current_context,
                evaluated_at=evaluated_at,
            )
            blockers.extend(approval_blockers)
    if storyboard is not None and packet is not None:
        blockers.extend(
            _packet_contract_blockers(
                observation, packet=packet, storyboard=storyboard
            )
        )
        review_mode = policy.review_policy_by_stage.get(
            STAGE_GENERATION_PLAN, ReviewMode.NONE
        )
        review_blockers, packet_reviews = _review_blockers(
            observation,
            subject=packet,
            review_family=PipelineKind.PACKET_REVIEW.value,
            mode=review_mode,
        )
        blockers.extend(review_blockers)
        feasibility_blockers, feasibility = _feasibility_blockers(
            observation, packet=packet, storyboard=storyboard
        )
        blockers.extend(feasibility_blockers)
        if ApprovalKind.GENERATION_APPROVAL in policy.required_approval_kinds:
            required = [packet, *packet_reviews]
            if feasibility is not None:
                required.append(feasibility)
            approval_blockers, approval = _approval_blockers(
                observation,
                family=PipelineKind.PACKET_APPROVAL.value,
                expected_version="packet-approval/2.0",
                expected_capability="generation_approval",
                required_artifacts=required,
                current_context=current_context,
                evaluated_at=evaluated_at,
            )
            blockers.extend(approval_blockers)
    if policy.mode is WorkflowMode.RAPID:
        blockers.append("rapid mode is preview-only and cannot authorize generation")

    unique = tuple(dict.fromkeys(blockers))
    return GenerationReadinessPlan(
        ready=not unique,
        packet=packet.reference if packet is not None else None,
        packet_content_sha256=(
            str(canonical_sha256(dict(packet.document)))
            if packet is not None
            else None
        ),
        feasibility_review=(
            feasibility.reference if feasibility is not None else None
        ),
        approval_evidence=approval.reference if approval is not None else None,
        gate_context_sha256=(
            str(gate_context_sha256(current_context))
            if current_context is not None
            else None
        ),
        blockers=unique,
    )


def _plan_id(
    observation: EpisodeStateObservation,
    mode: WorkflowMode,
    action_type: str,
    blockers: Sequence[str],
    current_context: GateContext | None,
) -> OpaqueId:
    identity = {
        "mode": mode.value,
        "action_type": action_type,
        "blockers": list(blockers),
        "artifacts": [
            {
                "path": item.path,
                "sha256": item.sha256,
                "artifact_version": item.artifact_version,
                "is_current": item.is_current,
            }
            for item in observation.artifacts
        ],
    }
    if current_context is not None:
        identity["gate_context"] = gate_context_to_mapping(current_context)
    return OpaqueId(f"next-{str(canonical_sha256(identity))[:20]}")


def plan_next_step(
    observation: EpisodeStateObservation,
    workflow_mode: str | WorkflowMode | None,
    *,
    current_context: GateContext | None = None,
    evaluated_at: datetime | None = None,
) -> NextStepPlan:
    """Derive one safe next action from validated evidence."""

    try:
        policy = resolve_workflow_policy(workflow_mode)
    except WorkflowPolicyError as error:
        raise OrchestrationPlanError(str(error)) from error
    mode = policy.mode
    prohibited = (
        "auto_transition_workflow_state",
        "auto_approve",
        "paid_external_generation",
        "publish",
        "overwrite_generated_assets",
    )

    def make_plan(
        *,
        action_type: str,
        instructions: str,
        next_phase: str | None,
        next_procedure: str | None,
        next_actor_role: str | None,
        completed_phase: str | None,
        approval_required: bool,
        prerequisites: tuple[str, ...] = (),
        blockers: tuple[str, ...] = (),
        target_paths: tuple[str, ...] = (),
    ) -> NextStepPlan:
        return NextStepPlan(
            next_step_id=_plan_id(
                observation, mode, action_type, blockers, current_context
            ),
            workflow_mode=mode,
            action_type=action_type,
            instructions=instructions,
            next_phase=next_phase,
            next_procedure=next_procedure,
            next_actor_role=next_actor_role,
            completed_phase=completed_phase,
            approval_required=approval_required,
            auto_execution=False,
            prerequisites=prerequisites,
            blockers=blockers,
            target_paths=target_paths,
            prohibited_actions=prohibited,
            observation=observation,
            gate_context_sha256=(
                str(gate_context_sha256(current_context))
                if current_context is not None
                else None
            ),
            transition_applied=False,
        )

    if observation.findings:
        return make_plan(
            action_type="resolve_artifact_graph",
            instructions=(
                "Resolve schema, current-version, episode, rules-version, or role "
                "separation findings before planning any production action."
            ),
            next_phase=None,
            next_procedure="repair_artifact_graph",
            next_actor_role="artifact-owner",
            completed_phase=None,
            approval_required=False,
            blockers=observation.findings,
        )

    brief = observation.graph.one(PipelineKind.BRIEF.value)
    if brief is None:
        return make_plan(
            action_type="create_brief",
            instructions="Create the episode brief artifact.",
            next_phase="brief",
            next_procedure="write_brief",
            next_actor_role="creator",
            completed_phase=None,
            approval_required=False,
            blockers=("brief missing",),
        )

    storyboard = observation.graph.one(PipelineKind.STORYBOARD.value)
    if storyboard is None:
        return make_plan(
            action_type="create_storyboard",
            instructions="Create a storyboard from the current brief.",
            next_phase=STAGE_STORYBOARD,
            next_procedure="write_storyboard",
            next_actor_role="creator",
            completed_phase="brief",
            approval_required=False,
            prerequisites=("brief",),
            blockers=("storyboard missing",),
        )

    storyboard_mode = policy.review_policy_by_stage.get(
        STAGE_STORYBOARD, ReviewMode.NONE
    )
    storyboard_review_blockers, storyboard_reviews = _review_blockers(
        observation,
        subject=storyboard,
        review_family=PipelineKind.STORYBOARD_REVIEW.value,
        mode=storyboard_mode,
    )
    if storyboard_review_blockers:
        return make_plan(
            action_type="review_or_revise_storyboard",
            instructions=(
                "Obtain the required independent passing review(s), or revise "
                "the storyboard after a failed/uncertain review."
            ),
            next_phase=STAGE_STORYBOARD,
            next_procedure="review_storyboard",
            next_actor_role="reviewer",
            completed_phase=STAGE_STORYBOARD,
            approval_required=False,
            prerequisites=("storyboard",),
            blockers=storyboard_review_blockers,
        )

    if policy.hash_binding_required:
        approval_blockers, _ = _approval_blockers(
            observation,
            family=PipelineKind.STORYBOARD_APPROVAL.value,
            expected_version="storyboard-approval/2.0",
            expected_capability="storyboard_approval",
            required_artifacts=(storyboard, *storyboard_reviews),
            current_context=current_context,
            evaluated_at=evaluated_at,
        )
        if approval_blockers:
            return make_plan(
                action_type="approve_storyboard",
                instructions=(
                    "A human must supply storyboard approval evidence exactly "
                    "bound to the current storyboard and required reviews."
                ),
                next_phase=STAGE_STORYBOARD,
                next_procedure="human_approve_storyboard",
                next_actor_role="human-approver",
                completed_phase=STAGE_STORYBOARD,
                approval_required=True,
                prerequisites=("storyboard", "passing storyboard reviews"),
                blockers=approval_blockers,
            )

    packet = observation.graph.one(PipelineKind.GENERATION_PACKET.value)
    if packet is None:
        return make_plan(
            action_type="create_generation_packet",
            instructions=(
                "Create generation-packet/2.0 from the approved current storyboard."
            ),
            next_phase=STAGE_GENERATION_PLAN,
            next_procedure="write_generation_packet",
            next_actor_role="creator",
            completed_phase=STAGE_STORYBOARD,
            approval_required=False,
            prerequisites=("storyboard approval",),
            blockers=("generation-packet missing",),
        )

    packet_contract_blockers = _packet_contract_blockers(
        observation, packet=packet, storyboard=storyboard
    )
    if packet_contract_blockers:
        return make_plan(
            action_type="rebuild_generation_packet",
            instructions=(
                "Rebuild the packet under the strict v2 contract and bind it "
                "to the current storyboard."
            ),
            next_phase=STAGE_GENERATION_PLAN,
            next_procedure="write_generation_packet",
            next_actor_role="creator",
            completed_phase=STAGE_STORYBOARD,
            approval_required=False,
            blockers=packet_contract_blockers,
        )

    packet_mode = policy.review_policy_by_stage.get(
        STAGE_GENERATION_PLAN, ReviewMode.NONE
    )
    packet_review_blockers, packet_reviews = _review_blockers(
        observation,
        subject=packet,
        review_family=PipelineKind.PACKET_REVIEW.value,
        mode=packet_mode,
    )
    if packet_review_blockers:
        return make_plan(
            action_type="review_or_revise_generation_packet",
            instructions=(
                "Obtain the required current-bound passing packet reviews, or "
                "revise the packet after a failed/uncertain review."
            ),
            next_phase=STAGE_GENERATION_PLAN,
            next_procedure="review_generation_packet",
            next_actor_role="reviewer",
            completed_phase=STAGE_GENERATION_PLAN,
            approval_required=False,
            prerequisites=("generation-packet",),
            blockers=packet_review_blockers,
        )

    feasibility_blockers, feasibility = _feasibility_blockers(
        observation, packet=packet, storyboard=storyboard
    )
    if feasibility_blockers:
        return make_plan(
            action_type="review_generation_feasibility",
            instructions=(
                "Evaluate capability binding, minimum duration, first-frame "
                "aspect and before-state, continuity anchors, and unsupported "
                "render dependencies. Every shot-level check must pass."
            ),
            next_phase=STAGE_GENERATION_PLAN,
            next_procedure="review_generation_feasibility",
            next_actor_role="independent-reviewer",
            completed_phase=STAGE_GENERATION_PLAN,
            approval_required=False,
            prerequisites=("generation-packet", "storyboard"),
            blockers=feasibility_blockers,
        )

    if ApprovalKind.GENERATION_APPROVAL in policy.required_approval_kinds:
        assert feasibility is not None
        approval_blockers, _ = _approval_blockers(
            observation,
            family=PipelineKind.PACKET_APPROVAL.value,
            expected_version="packet-approval/2.0",
            expected_capability="generation_approval",
            required_artifacts=(packet, *packet_reviews, feasibility),
            current_context=current_context,
            evaluated_at=evaluated_at,
        )
        if approval_blockers:
            return make_plan(
                action_type="approve_generation",
                instructions=(
                    "A human must supply generation approval evidence exactly "
                    "bound to the packet, required reviews, and feasibility pass."
                ),
                next_phase=STAGE_GENERATION_PLAN,
                next_procedure="human_approve_generation",
                next_actor_role="human-approver",
                completed_phase=STAGE_GENERATION_PLAN,
                approval_required=True,
                prerequisites=("packet review pass", "feasibility pass"),
                blockers=approval_blockers,
            )

    if mode is WorkflowMode.RAPID:
        return make_plan(
            action_type="preview_complete",
            instructions=(
                "Rapid mode is preview-only. Choose Standard or Controlled and "
                "obtain its human evidence before any external generation."
            ),
            next_phase=None,
            next_procedure=None,
            next_actor_role="human-operator",
            completed_phase=STAGE_GENERATION_PLAN,
            approval_required=False,
            blockers=("rapid mode cannot authorize generation",),
        )

    qc_blockers = _shot_qc_blockers(observation, packet)
    if qc_blockers:
        any_qc = bool(observation.graph.family(PipelineKind.SHOT_QC.value))
        return make_plan(
            action_type=(
                "remediate_or_repeat_shot_qc" if any_qc else "run_external_generation"
            ),
            instructions=(
                "The human/channel may execute the approved external generation. "
                "Then provide exactly one current pass/warn QC record per packet "
                "shot. Core does not perform the generation."
            ),
            next_phase=STAGE_FINAL_VIDEO,
            next_procedure="generate_then_qc",
            next_actor_role="human-or-channel-tool",
            completed_phase=STAGE_GENERATION_PLAN,
            approval_required=False,
            prerequisites=("generation readiness",),
            blockers=qc_blockers,
        )

    continuity_blockers = _continuity_qc_blockers(observation, packet)
    if continuity_blockers:
        any_continuity_qc = bool(
            observation.graph.family(PipelineKind.CONTINUITY_QC.value)
        )
        return make_plan(
            action_type=(
                "remediate_continuity_qc"
                if any_continuity_qc
                else "run_continuity_qc"
            ),
            instructions=(
                "Provide one current continuity-qc report exactly hash-bound "
                "to every generated packet-shot output. Compare every adjacent "
                "shot from last to first samples on all three continuity axes."
            ),
            next_phase=STAGE_FINAL_VIDEO,
            next_procedure="review_cross_shot_continuity",
            next_actor_role="qc-tool-or-reviewer",
            completed_phase=STAGE_GENERATION_PLAN,
            approval_required=False,
            blockers=continuity_blockers,
        )

    ranking = observation.graph.one(PipelineKind.CANDIDATE_RANKING.value)
    ranking_blockers = _ranking_blockers(observation, packet, ranking)
    if ranking_blockers:
        return make_plan(
            action_type="rank_generation_candidates",
            instructions=(
                "Create a deterministic ranking bound to the current packet. "
                "Ranking remains advisory and must not auto-select a winner."
            ),
            next_phase=STAGE_FINAL_VIDEO,
            next_procedure="rank_candidates",
            next_actor_role="qc-tool",
            completed_phase=STAGE_GENERATION_PLAN,
            approval_required=False,
            blockers=ranking_blockers,
        )
    assert ranking is not None

    edit_manifest = observation.graph.one(PipelineKind.EDIT_MANIFEST.value)
    edit_blockers = _edit_manifest_blockers(
        packet,
        ranking,
        edit_manifest,
    )
    if edit_blockers:
        return make_plan(
            action_type="select_edit_inputs",
            instructions="A human must select one QC-accepted clip per packet shot.",
            next_phase=STAGE_FINAL_VIDEO,
            next_procedure="prepare_edit_manifest",
            next_actor_role="human-editor",
            completed_phase=STAGE_GENERATION_PLAN,
            approval_required=True,
            blockers=edit_blockers,
        )
    assert edit_manifest is not None

    rough_cut = observation.graph.one(PipelineKind.ROUGH_CUT_REPORT.value)
    rough_blockers = _rough_cut_blockers(
        observation,
        ranking,
        edit_manifest,
        rough_cut,
    )
    if rough_blockers:
        return make_plan(
            action_type="assemble_or_repair_rough_cut",
            instructions=(
                "Assemble the selected clips and produce a passing rough-cut "
                "report bound to the current ranking."
            ),
            next_phase=STAGE_FINAL_VIDEO,
            next_procedure="assemble_rough_cut",
            next_actor_role="editor",
            completed_phase=STAGE_GENERATION_PLAN,
            approval_required=False,
            blockers=rough_blockers,
        )
    assert rough_cut is not None

    delivery = observation.graph.one(PipelineKind.FINAL_DELIVERY.value)
    if delivery is None:
        return make_plan(
            action_type="prepare_final_delivery",
            instructions=(
                "Create a hash-bound final-delivery record with lineage and a "
                "pass/warn technical QC reference."
            ),
            next_phase=STAGE_FINAL_VIDEO,
            next_procedure="prepare_final_delivery",
            next_actor_role="editor",
            completed_phase=STAGE_FINAL_VIDEO,
            approval_required=False,
            blockers=("final-delivery missing",),
        )
    delivery_blockers = _final_delivery_blockers(
        observation,
        delivery=delivery,
        edit_manifest=edit_manifest,
        rough_cut=rough_cut,
    )
    if delivery_blockers:
        return make_plan(
            action_type="repair_final_delivery",
            instructions=(
                "Rebuild final-delivery with current edit/rough-cut lineage and "
                "a current pass/warn technical-QC reference."
            ),
            next_phase=STAGE_FINAL_VIDEO,
            next_procedure="prepare_final_delivery",
            next_actor_role="editor",
            completed_phase=STAGE_FINAL_VIDEO,
            approval_required=False,
            blockers=delivery_blockers,
        )

    final_mode = policy.review_policy_by_stage.get(
        STAGE_FINAL_VIDEO, ReviewMode.NONE
    )
    final_review_blockers, final_reviews = _review_blockers(
        observation,
        subject=delivery,
        review_family=PipelineKind.FINAL_REVIEW.value,
        mode=final_mode,
    )
    if final_review_blockers:
        return make_plan(
            action_type="review_or_revise_final_delivery",
            instructions=(
                "Obtain the required independent passing final-delivery reviews, "
                "or revise the delivery after a failed/uncertain review."
            ),
            next_phase=STAGE_FINAL_VIDEO,
            next_procedure="review_final_delivery",
            next_actor_role="reviewer",
            completed_phase=STAGE_FINAL_VIDEO,
            approval_required=False,
            blockers=final_review_blockers,
        )

    metadata = observation.graph.one(PipelineKind.PUBLISH_METADATA_DRAFT.value)
    if metadata is None:
        return make_plan(
            action_type="prepare_publish_metadata",
            instructions="Prepare human-reviewable publish metadata.",
            next_phase=STAGE_FINAL_VIDEO,
            next_procedure="draft_publish_metadata",
            next_actor_role="creator",
            completed_phase=STAGE_FINAL_VIDEO,
            approval_required=False,
            blockers=("publish-metadata-draft missing",),
        )
    if not observation.graph.matches(brief, metadata.document.get("source_brief_ref")):
        return make_plan(
            action_type="repair_publish_metadata",
            instructions="Bind publish metadata to the current brief.",
            next_phase=STAGE_FINAL_VIDEO,
            next_procedure="draft_publish_metadata",
            next_actor_role="creator",
            completed_phase=STAGE_FINAL_VIDEO,
            approval_required=False,
            blockers=("publish metadata is not bound to the current brief",),
        )

    if ApprovalKind.PUBLISH_APPROVAL in policy.required_approval_kinds:
        publish_blockers, _ = _approval_blockers(
            observation,
            family=PipelineKind.PUBLISH_APPROVAL.value,
            expected_version="publish-approval/1.0",
            expected_capability="publish_approval",
            required_artifacts=(delivery, *final_reviews, metadata),
            current_context=current_context,
            evaluated_at=evaluated_at,
        )
        if publish_blockers:
            return make_plan(
                action_type="approve_publish",
                instructions=(
                    "A human must supply publish approval exactly bound to the "
                    "final delivery, required reviews, and metadata draft."
                ),
                next_phase=STAGE_FINAL_VIDEO,
                next_procedure="human_approve_publish",
                next_actor_role="human-approver",
                completed_phase=STAGE_FINAL_VIDEO,
                approval_required=True,
                prerequisites=("final review pass", "publish metadata"),
                blockers=publish_blockers,
            )

    return make_plan(
        action_type="ready_for_human_publish",
        instructions=(
            "All core evidence gates pass. A human/channel may perform the "
            "external publish action; core still does not publish."
        ),
        next_phase=None,
        next_procedure="human_publish",
        next_actor_role="human-publisher",
        completed_phase=STAGE_FINAL_VIDEO,
        approval_required=False,
        prerequisites=tuple(sorted(observation.present_kinds)),
    )


def _shot_qc_blockers(
    observation: EpisodeStateObservation,
    packet: ArtifactSnapshot,
) -> tuple[str, ...]:
    qcs = observation.graph.family(PipelineKind.SHOT_QC.value)
    blockers: list[str] = []
    for shot_id in _packet_shot_ids(packet):
        matching = [item for item in qcs if item.document.get("shot_id") == shot_id]
        if len(matching) != 1:
            blockers.append(
                f"shot {shot_id} requires exactly one current QC record; "
                f"found {len(matching)}"
            )
            continue
        verdict = matching[0].document.get("verdict")
        if verdict not in {"pass", "warn"}:
            blockers.append(f"shot {shot_id} QC verdict is {verdict!r}")
    return tuple(blockers)


def _mapping_reference_identity(
    value: object,
) -> tuple[str, str, str] | None:
    if not isinstance(value, Mapping):
        return None
    identity = (
        value.get("path"),
        value.get("sha256"),
        value.get("artifact_version"),
    )
    if not all(isinstance(item, str) for item in identity):
        return None
    return identity  # type: ignore[return-value]


def _continuity_qc_blockers(
    observation: EpisodeStateObservation,
    packet: ArtifactSnapshot,
) -> tuple[str, ...]:
    """Require current, generated-media-bound cross-shot continuity evidence."""

    shot_ids = _packet_shot_ids(packet)
    if len(shot_ids) < 2:
        return ()

    reports = observation.graph.family(PipelineKind.CONTINUITY_QC.value)
    if len(reports) != 1:
        return (
            "continuity-qc requires exactly one current artifact for a "
            f"multi-shot packet; found {len(reports)}",
        )

    report = reports[0]
    document = report.document
    blockers: list[str] = []
    if document.get("verdict") not in {"pass", "warn"}:
        blockers.append(
            f"continuity-qc verdict is {document.get('verdict')!r}, not pass/warn"
        )
    try:
        recomputed = rejudge_continuity_qc(document)
    except ContinuityPlanError as error:
        blockers.append(f"continuity-qc cannot be recomputed: {error}")
        recomputed = None
    if (
        recomputed is not None
        and document.get("verdict") != recomputed.overall.value
    ):
        blockers.append(
            "continuity-qc stored verdict differs from its serialized evidence"
        )

    shot_qcs = observation.graph.family(PipelineKind.SHOT_QC.value)
    expected_subjects: set[tuple[str, str, str, str]] = set()
    for shot_id in shot_ids:
        matching = [
            item for item in shot_qcs if item.document.get("shot_id") == shot_id
        ]
        if len(matching) != 1:
            blockers.append(
                f"continuity-qc cannot bind shot {shot_id}: current shot QC "
                f"count is {len(matching)}"
            )
            continue
        identity = _mapping_reference_identity(
            matching[0].document.get("subject")
        )
        if identity is None:
            blockers.append(
                f"continuity-qc cannot bind shot {shot_id}: shot-QC subject "
                "is not a bound artifact"
            )
            continue
        expected_subjects.add((shot_id, *identity))

    raw_subjects = document.get("subjects")
    actual_subjects: list[tuple[str, str, str, str]] = []
    if isinstance(raw_subjects, Sequence) and not isinstance(
        raw_subjects, (str, bytes, bytearray)
    ):
        for subject in raw_subjects:
            if not isinstance(subject, Mapping):
                continue
            shot_id = subject.get("shot_id")
            identity = _mapping_reference_identity(subject.get("artifact"))
            if isinstance(shot_id, str) and identity is not None:
                actual_subjects.append((shot_id, *identity))
    if (
        set(actual_subjects) != expected_subjects
        or len(actual_subjects) != len(expected_subjects)
    ):
        blockers.append(
            "continuity-qc subjects must exactly hash-bind every current "
            "packet-shot output"
        )

    observations: dict[str, Mapping[str, object]] = {}
    duplicate_observation = False
    raw_observations = document.get("observations")
    if isinstance(raw_observations, Sequence) and not isinstance(
        raw_observations, (str, bytes, bytearray)
    ):
        for item in raw_observations:
            if not isinstance(item, Mapping):
                continue
            observation_id = item.get("observation_id")
            if not isinstance(observation_id, str):
                continue
            if observation_id in observations:
                duplicate_observation = True
            observations[observation_id] = item
    if duplicate_observation:
        blockers.append("continuity-qc observation ids must be unique")

    covered: set[tuple[str, str, str, str, str]] = set()
    covered_elements: set[tuple[str, str, str, str, str, str]] = set()
    comparison_ids: list[str] = []
    raw_comparisons = document.get("comparisons")
    if isinstance(raw_comparisons, Sequence) and not isinstance(
        raw_comparisons, (str, bytes, bytearray)
    ):
        for comparison in raw_comparisons:
            if not isinstance(comparison, Mapping):
                continue
            comparison_id = comparison.get("comparison_id")
            if isinstance(comparison_id, str):
                comparison_ids.append(comparison_id)
            left = observations.get(str(comparison.get("from_observation_id")))
            right = observations.get(str(comparison.get("to_observation_id")))
            if left is None or right is None:
                continue
            axis = comparison.get("axis")
            if (
                left.get("axis") == axis
                and right.get("axis") == axis
                and left.get("element_id") == comparison.get("element_id")
                and right.get("element_id") == comparison.get("element_id")
            ):
                coverage = (
                    str(left.get("shot_id")),
                    str(right.get("shot_id")),
                    str(axis),
                    str(left.get("sample_point")),
                    str(right.get("sample_point")),
                )
                covered.add(coverage)
                covered_elements.add(
                    (*coverage, str(comparison.get("element_id")))
                )
    if len(comparison_ids) != len(set(comparison_ids)):
        blockers.append("continuity-qc comparison ids must be unique")

    required_coverage = {
        (from_shot, to_shot, axis, "last", "first")
        for from_shot, to_shot in zip(shot_ids, shot_ids[1:])
        for axis in ("relative_scale", "orientation_shape", "presence")
    }
    if not required_coverage <= covered:
        blockers.append(
            "continuity-qc must cover every adjacent shot pair on relative "
            "scale, orientation/shape, and presence from last to first samples"
        )

    required_carried_coverage: set[
        tuple[str, str, str, str, str, str]
    ] = set()
    raw_packet_shots = packet.document.get("shots")
    if isinstance(raw_packet_shots, Sequence) and not isinstance(
        raw_packet_shots, (str, bytes, bytearray)
    ):
        for index in range(1, len(raw_packet_shots)):
            previous_shot_id = shot_ids[index - 1]
            shot_id = shot_ids[index]
            raw_shot = raw_packet_shots[index]
            if not isinstance(raw_shot, Mapping):
                continue
            continuity = raw_shot.get("continuity")
            carried = (
                continuity.get("carried_elements")
                if isinstance(continuity, Mapping)
                else None
            )
            if not isinstance(carried, Sequence) or isinstance(
                carried, (str, bytes, bytearray)
            ):
                continue
            for entry in carried:
                if (
                    not isinstance(entry, Mapping)
                    or entry.get("from_shot_id") != previous_shot_id
                    or not isinstance(entry.get("element_id"), str)
                ):
                    continue
                for axis in (
                    "relative_scale",
                    "orientation_shape",
                    "presence",
                ):
                    required_carried_coverage.add(
                        (
                            previous_shot_id,
                            shot_id,
                            axis,
                            "last",
                            "first",
                            entry["element_id"],
                        )
                    )
    if not required_carried_coverage <= covered_elements:
        blockers.append(
            "continuity-qc must cover every declared carried element on all "
            "three axes for its adjacent shot transition"
        )

    judgment_ids: list[str] = []
    raw_judgments = document.get("judgments")
    if isinstance(raw_judgments, Sequence) and not isinstance(
        raw_judgments, (str, bytes, bytearray)
    ):
        judgment_ids = [
            str(item.get("comparison_id"))
            for item in raw_judgments
            if isinstance(item, Mapping)
            and isinstance(item.get("comparison_id"), str)
        ]
    if (
        set(judgment_ids) != set(comparison_ids)
        or len(judgment_ids) != len(comparison_ids)
    ):
        blockers.append(
            "continuity-qc judgments must cover every comparison exactly once"
        )
    elif recomputed is not None:
        stored_statuses = {
            str(item.get("comparison_id")): item.get("status")
            for item in raw_judgments
            if isinstance(item, Mapping)
            and isinstance(item.get("comparison_id"), str)
        }
        recomputed_statuses = {
            str(item.comparison_id): item.status.value
            for item in recomputed.judgments
        }
        if stored_statuses != recomputed_statuses:
            blockers.append(
                "continuity-qc stored judgments differ from serialized evidence"
            )

    return tuple(dict.fromkeys(blockers))


def _ranking_blockers(
    observation: EpisodeStateObservation,
    packet: ArtifactSnapshot,
    ranking: ArtifactSnapshot | None,
) -> tuple[str, ...]:
    if ranking is None:
        return ("candidate-ranking missing",)
    blockers: list[str] = []
    if not observation.graph.matches(packet, ranking.document.get("packet_ref")):
        blockers.append("candidate-ranking is not bound to the current packet")
    shots = ranking.document.get("shots")
    ranked_ids: list[str] = []
    current_qc = {
        (str(item.path), str(item.sha256))
        for item in observation.graph.family(PipelineKind.SHOT_QC.value)
    }
    stale_qc_reference = False
    if isinstance(shots, Sequence) and not isinstance(
        shots, (str, bytes, bytearray)
    ):
        for item in shots:
            if isinstance(item, Mapping) and isinstance(item.get("shot_id"), str):
                ranked_ids.append(item["shot_id"])
                candidates = item.get("candidates")
                if isinstance(candidates, Sequence) and not isinstance(
                    candidates, (str, bytes, bytearray)
                ):
                    for candidate in candidates:
                        if not isinstance(candidate, Mapping):
                            continue
                        qc_ref = candidate.get("qc_ref")
                        if not isinstance(qc_ref, Mapping) or (
                            qc_ref.get("path"),
                            qc_ref.get("sha256"),
                        ) not in current_qc:
                            stale_qc_reference = True
    if set(ranked_ids) != set(_packet_shot_ids(packet)) or len(ranked_ids) != len(
        _packet_shot_ids(packet)
    ):
        blockers.append("candidate-ranking does not cover packet shots exactly once")
    if stale_qc_reference:
        blockers.append(
            "candidate-ranking contains a QC reference that is not current"
        )
    return tuple(blockers)


def _edit_manifest_blockers(
    packet: ArtifactSnapshot,
    ranking: ArtifactSnapshot,
    manifest: ArtifactSnapshot | None,
) -> tuple[str, ...]:
    if manifest is None:
        return ("edit-manifest missing",)
    blockers: list[str] = []
    if manifest.document.get("selected_by_human") is not True:
        blockers.append("edit inputs were not selected by a human")
    inputs = manifest.document.get("input_clips")
    selected_ids: list[str] = []
    allowed_candidates: set[tuple[str, str, str]] = set()
    ranked_shots = ranking.document.get("shots")
    if isinstance(ranked_shots, Sequence) and not isinstance(
        ranked_shots, (str, bytes, bytearray)
    ):
        for ranked_shot in ranked_shots:
            if not isinstance(ranked_shot, Mapping):
                continue
            shot_id = ranked_shot.get("shot_id")
            candidates = ranked_shot.get("candidates")
            if not isinstance(shot_id, str) or not isinstance(
                candidates, Sequence
            ) or isinstance(candidates, (str, bytes, bytearray)):
                continue
            for candidate in candidates:
                if (
                    isinstance(candidate, Mapping)
                    and isinstance(candidate.get("file"), str)
                    and isinstance(candidate.get("media_sha256"), str)
                ):
                    allowed_candidates.add(
                        (
                            shot_id,
                            candidate["file"],
                            candidate["media_sha256"],
                        )
                    )
    unranked_selection = False
    if isinstance(inputs, Sequence) and not isinstance(
        inputs, (str, bytes, bytearray)
    ):
        for item in inputs:
            if isinstance(item, Mapping) and isinstance(item.get("shot_id"), str):
                selected_ids.append(item["shot_id"])
                identity = (
                    item["shot_id"],
                    item.get("path"),
                    item.get("sha256"),
                )
                if identity not in allowed_candidates:
                    unranked_selection = True
    packet_ids = _packet_shot_ids(packet)
    if set(selected_ids) != set(packet_ids) or len(selected_ids) != len(packet_ids):
        blockers.append("edit-manifest must select exactly one clip per packet shot")
    if unranked_selection:
        blockers.append(
            "edit-manifest selected clip is not a current ranked candidate"
        )
    return tuple(blockers)


def _rough_cut_blockers(
    observation: EpisodeStateObservation,
    ranking: ArtifactSnapshot,
    edit_manifest: ArtifactSnapshot,
    report: ArtifactSnapshot | None,
) -> tuple[str, ...]:
    if report is None:
        return ("rough-cut-report missing",)
    blockers: list[str] = []
    if not observation.graph.matches(ranking, report.document.get("ranking_ref")):
        blockers.append("rough-cut-report is not bound to the current ranking")
    status = report.document.get("status")
    if not isinstance(status, str) or status.lower() not in {"pass", "warn", "ready"}:
        blockers.append(f"rough-cut status is {status!r}, not pass/warn/ready")
    selected = edit_manifest.document.get("input_clips")
    assembled = report.document.get("inputs")
    selected_identities: set[tuple[object, object, object]] = set()
    assembled_identities: set[tuple[object, object, object]] = set()
    if isinstance(selected, Sequence) and not isinstance(
        selected, (str, bytes, bytearray)
    ):
        for item in selected:
            if isinstance(item, Mapping):
                selected_identities.add(
                    (item.get("shot_id"), item.get("path"), item.get("sha256"))
                )
    if isinstance(assembled, Sequence) and not isinstance(
        assembled, (str, bytes, bytearray)
    ):
        for item in assembled:
            if isinstance(item, Mapping):
                assembled_identities.add(
                    (item.get("shot_id"), item.get("path"), item.get("sha256"))
                )
    if assembled_identities != selected_identities:
        blockers.append("rough-cut inputs differ from the current edit manifest")
    return tuple(blockers)


def _final_delivery_blockers(
    observation: EpisodeStateObservation,
    *,
    delivery: ArtifactSnapshot,
    edit_manifest: ArtifactSnapshot,
    rough_cut: ArtifactSnapshot,
) -> tuple[str, ...]:
    blockers: list[str] = []
    lineage = delivery.document.get("lineage_refs")
    lineage_identities: set[tuple[object, object, object]] = set()
    if isinstance(lineage, Sequence) and not isinstance(
        lineage, (str, bytes, bytearray)
    ):
        for item in lineage:
            if isinstance(item, Mapping):
                lineage_identities.add(
                    (
                        item.get("path"),
                        item.get("sha256"),
                        item.get("artifact_version"),
                    )
                )
    required_lineage = {
        _reference_identity(edit_manifest.reference),
        _reference_identity(rough_cut.reference),
    }
    if not required_lineage <= lineage_identities:
        blockers.append(
            "final-delivery lineage must include current edit manifest and rough cut"
        )

    qc_ref = delivery.document.get("technical_qc_ref")
    matching_qc = [
        item
        for item in observation.graph.family(PipelineKind.SHOT_QC.value)
        if observation.graph.matches(item, qc_ref)
    ]
    if len(matching_qc) != 1:
        blockers.append(
            "final-delivery technical_qc_ref must match one current QC artifact"
        )
    elif matching_qc[0].document.get("verdict") not in {"pass", "warn"}:
        blockers.append("final-delivery technical QC must pass or warn")
    elif (
        delivery.document.get("technical_verdict")
        != matching_qc[0].document.get("verdict")
    ):
        blockers.append(
            "final-delivery technical_verdict differs from its QC artifact"
        )
    elif delivery.document.get("selected_output") != matching_qc[0].document.get(
        "subject"
    ):
        blockers.append(
            "final-delivery selected_output differs from the technical-QC subject"
        )
    return tuple(blockers)


def next_step_to_mapping(
    plan: NextStepPlan,
    *,
    rules_version: str | None = None,
) -> dict[str, object]:
    """Serialize a deterministic plan to ``next-step/1.0``."""

    resolved_rules = rules_version or plan.observation.rules_version
    if not resolved_rules:
        raise OrchestrationPlanError(
            "rules_version is required when observation has no single rules version"
        )
    source_artifacts = [
        {
            "path": item.path,
            "sha256": item.sha256,
            "artifact_version": item.artifact_version,
        }
        for item in plan.observation.artifacts
        if item.is_current
    ]
    document: dict[str, object] = {
        "artifact_version": "next-step/1.0",
        "rules_version": resolved_rules,
        "next_step_id": str(plan.next_step_id),
        "source_refs": {},
        "next_actor_role": plan.next_actor_role,
        "action_type": plan.action_type,
        "instructions": plan.instructions,
        "target_paths": list(plan.target_paths),
        "approval_required": plan.approval_required,
        "auto_execution": plan.auto_execution,
        "prerequisites": list(plan.prerequisites),
        "blockers": list(plan.blockers),
        "prohibited_actions": list(plan.prohibited_actions),
        "extensions": {
            "video_factory.orchestration": {
                "contract_version": "2.0",
                "payload": {
                    "workflow_mode": plan.workflow_mode.value,
                    "transition_applied": plan.transition_applied,
                    "present_kinds": sorted(plan.observation.present_kinds),
                    "source_artifacts": source_artifacts,
                    "graph_findings": list(plan.observation.findings),
                },
            }
        },
    }
    if plan.gate_context_sha256 is not None:
        extension = document["extensions"]
        assert isinstance(extension, dict)
        orchestration = extension["video_factory.orchestration"]
        assert isinstance(orchestration, dict)
        payload = orchestration["payload"]
        assert isinstance(payload, dict)
        payload["gate_context_sha256"] = plan.gate_context_sha256
    if plan.next_phase is not None:
        document["next_phase"] = plan.next_phase
    if plan.next_procedure is not None:
        document["next_procedure"] = plan.next_procedure
    if plan.completed_phase is not None:
        document["completed_phase"] = plan.completed_phase
    return document


def observation_to_mapping(observation: EpisodeStateObservation) -> dict[str, object]:
    return {
        "present_kinds": sorted(observation.present_kinds),
        "approved_kinds": sorted(observation.approved_kinds),
        "unapproved_kinds": sorted(observation.unapproved_kinds),
        "episode_ids": list(observation.episode_ids),
        "rules_versions": list(observation.rules_versions),
        "findings": list(observation.findings),
        "artifacts": [
            {
                "kind": item.kind.value,
                "artifact_version": item.artifact_version,
                "path": item.path,
                "sha256": item.sha256,
                "is_current": item.is_current,
                "approved_by_human": item.approved_by_human,
                "verdict": item.verdict,
            }
            for item in observation.artifacts
        ],
    }
