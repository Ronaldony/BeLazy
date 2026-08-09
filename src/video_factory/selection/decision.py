"""Pure automatic candidate selection with exact current authority binding."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
import hashlib

from video_factory.approvals import GateContext, gate_context_to_mapping
from video_factory.artifacts import validate_artifact_mapping
from video_factory.authority import TrustedAuthorizationLedger
from video_factory.config import canonical_json_bytes, canonical_sha256
from video_factory.domain import ArtifactReference, HashDigest, OpaqueId
from video_factory.json_boundary import parse_rfc3339_datetime
from video_factory.quality import (
    InitialAuthorityEvidence,
    CurrentQualityEvaluationVerifier,
    CurrentQualityEvidenceResolver,
    MediaSubject,
    QualityBundle,
    QualityBundleStatus,
    QualityContractError,
    QualityPolicy,
    quality_bundle_bytes_sha256,
    require_target_quality_policy,
    target_quality_policy,
    validate_initial_authority_evidence,
    verify_quality_bundle,
    validate_quality_bundle,
)
from video_factory.quality.validation import (
    media_subject_to_mapping,
    reference_to_mapping,
    require_bps,
    require_gate_context,
    require_media_subject,
    require_reason_codes,
    require_reference,
    require_reference_consistency,
    require_sha256,
    require_token,
)

from .contracts import (
    CandidateDecision,
    CandidateDecisionStatus,
    CandidateDecisionVerificationInputs,
    CandidateOption,
    CurrentCandidateConfidenceVerifier,
    RankedCandidate,
    SelectionContractError,
    ShotCandidateDecision,
    ShotCandidateSet,
)


CANDIDATE_DECISION_VERSION = "candidate-decision/1.0"
CANDIDATE_CONFIDENCE_RECEIPT_VERSION = "candidate-confidence-receipt/1.0"
AUTO_SELECT_CANDIDATES_ACTION_ID = "auto_select_candidates"
AUTO_SELECT_CANDIDATES_CAPABILITY_ID = "auto_select_candidates"


def _subject_key(subject: MediaSubject) -> tuple[object, ...]:
    return (
        str(subject.shot_id),
        str(subject.component_id),
        str(subject.reference.path).casefold(),
        str(subject.reference.sha256),
        str(subject.reference.artifact_version),
        subject.byte_length,
        str(subject.workspace_observation_sha256),
        str(subject.observation_receipt_ref.path).casefold(),
        str(subject.observation_receipt_ref.sha256),
    )


def _reference_sort_key(reference: ArtifactReference) -> tuple[str, str, str, str]:
    return (
        str(reference.path).casefold(),
        str(reference.path),
        str(reference.sha256),
        str(reference.artifact_version),
    )


def _option_mapping(value: CandidateOption) -> dict[str, object]:
    require_media_subject(value.subject, "candidate subject")
    require_token(str(value.adapter_id), "candidate adapter_id")
    require_bps(value.confidence_bps, "candidate confidence")
    require_reference(value.confidence_receipt_ref, "candidate confidence_receipt_ref")
    if (
        str(value.confidence_receipt_ref.artifact_version)
        != CANDIDATE_CONFIDENCE_RECEIPT_VERSION
    ):
        raise SelectionContractError(
            "selection.confidence.receipt_version",
            "candidate confidence receipt has an unsupported artifact version",
        )
    return {
        "subject": media_subject_to_mapping(value.subject),
        "adapter_id": str(value.adapter_id),
        "confidence_bps": value.confidence_bps,
        "confidence_receipt_ref": reference_to_mapping(
            value.confidence_receipt_ref
        ),
    }


def _selection_input_mapping(
    workspace_id: str,
    channel_id: str,
    concept_id: str,
    episode_id: str,
    bundle_ref: ArtifactReference,
    bundle_sha256: HashDigest,
    policy: QualityPolicy,
    context: GateContext,
    shots: tuple[ShotCandidateSet, ...],
) -> dict[str, object]:
    return {
        "workspace_id": workspace_id,
        "channel_id": channel_id,
        "concept_id": concept_id,
        "episode_id": episode_id,
        "quality_bundle_ref": reference_to_mapping(bundle_ref),
        "quality_bundle_sha256": str(bundle_sha256),
        "policy_sha256": str(policy.policy_sha256),
        "gate_context": gate_context_to_mapping(context),
        "shots": [
            {
                "shot_id": str(shot.shot_id),
                "candidates": [_option_mapping(item) for item in shot.candidates],
            }
            for shot in shots
        ],
    }


def _ranked_mapping(value: RankedCandidate) -> dict[str, object]:
    return {
        "subject": media_subject_to_mapping(value.subject),
        "adapter_id": str(value.adapter_id),
        "quality_score_bps": value.quality_score_bps,
        "confidence_bps": value.confidence_bps,
        "confidence_receipt_ref": reference_to_mapping(
            value.confidence_receipt_ref
        ),
    }


def _shot_mapping(value: ShotCandidateDecision) -> dict[str, object]:
    return {
        "shot_id": str(value.shot_id),
        "status": value.status.value,
        "ranked_candidates": [
            _ranked_mapping(item) for item in value.ranked_candidates
        ],
        "selected_subject": (
            media_subject_to_mapping(value.selected_subject)
            if value.selected_subject is not None
            else None
        ),
        "margin_to_second_bps": value.margin_to_second_bps,
        "reason_codes": list(value.reason_codes),
    }


def _identity(value: CandidateDecision) -> dict[str, object]:
    return {
        "artifact_version": value.artifact_version,
        "workspace_id": str(value.workspace_id),
        "channel_id": str(value.channel_id),
        "concept_id": str(value.concept_id),
        "episode_id": str(value.episode_id),
        "quality_bundle_ref": reference_to_mapping(value.quality_bundle_ref),
        "quality_bundle_sha256": str(value.quality_bundle_sha256),
        "policy_sha256": str(value.policy_sha256),
        "gate_context": gate_context_to_mapping(value.gate_context),
        "evaluated_at": value.evaluated_at,
        "selection_input_sha256": str(value.selection_input_sha256),
        "authority_request_sha256": (
            str(value.authority_request_sha256)
            if value.authority_request_sha256 is not None
            else None
        ),
        "authority_decision_sha256": (
            str(value.authority_decision_sha256)
            if value.authority_decision_sha256 is not None
            else None
        ),
        "authority_receipt_sha256": (
            str(value.authority_receipt_sha256)
            if value.authority_receipt_sha256 is not None
            else None
        ),
        "shots": [_shot_mapping(item) for item in value.shots],
        "status": value.status.value,
        "reason_codes": list(value.reason_codes),
        "authority_effect": value.authority_effect,
    }


def candidate_decision_to_mapping(value: CandidateDecision) -> dict[str, object]:
    validate_candidate_decision_structure(value)
    mapping = {
        **_identity(value),
        "decision_id": str(value.decision_id),
        "decision_sha256": str(value.decision_sha256),
    }
    result = validate_artifact_mapping(mapping)
    if not result.ok:
        raise SelectionContractError(
            "selection.schema",
            "; ".join(result.error_texts),
        )
    return mapping


def candidate_decision_bytes_sha256(value: CandidateDecision) -> HashDigest:
    return HashDigest(
        hashlib.sha256(
            canonical_json_bytes(candidate_decision_to_mapping(value))
        ).hexdigest()
    )


def _normalize_shots(
    shots: Sequence[ShotCandidateSet],
) -> tuple[ShotCandidateSet, ...]:
    values = tuple(sorted(shots, key=lambda item: str(item.shot_id)))
    if not values or len({str(item.shot_id) for item in values}) != len(values):
        raise SelectionContractError(
            "selection.shots",
            "candidate shot ids must be non-empty and unique",
        )
    references: list[ArtifactReference] = []
    subject_keys: set[tuple[object, ...]] = set()
    for shot in values:
        require_token(str(shot.shot_id), "shot_id")
        if len(shot.candidates) < 2:
            raise SelectionContractError(
                "selection.margin.unavailable",
                "each shot requires at least two candidates",
            )
        ordered_candidates = tuple(
            sorted(
                shot.candidates,
                key=lambda item: (_subject_key(item.subject), str(item.adapter_id)),
            )
        )
        if shot.candidates != ordered_candidates:
            raise SelectionContractError(
                "selection.candidates.order",
                "candidate inputs must use canonical subject order",
            )
        for item in shot.candidates:
            _option_mapping(item)
            if item.subject.shot_id != shot.shot_id:
                raise SelectionContractError(
                    "selection.candidates.shot",
                    "candidate media is bound to another shot",
                )
            key = _subject_key(item.subject)
            if key in subject_keys:
                raise SelectionContractError(
                    "selection.candidates.duplicate",
                    "candidate media identity is duplicated",
                )
            subject_keys.add(key)
            references.extend(
                (
                    item.subject.reference,
                    item.subject.observation_receipt_ref,
                    item.confidence_receipt_ref,
                )
            )
    require_reference_consistency(references, allow_exact_reuse=False)
    return values


def candidate_selection_input_sha256(
    *,
    workspace_id: str,
    channel_id: str,
    concept_id: str,
    episode_id: str,
    quality_bundle_ref: ArtifactReference,
    quality_bundle: QualityBundle,
    candidate_sets: Sequence[ShotCandidateSet],
    policy: QualityPolicy,
    current_context: GateContext,
) -> HashDigest:
    """Return the exact non-authorizing input digest used by W04 authority."""

    validate_quality_bundle(quality_bundle)
    require_target_quality_policy(policy)
    require_gate_context(current_context)
    workspace = require_token(workspace_id, "workspace_id")
    channel = require_token(channel_id, "channel_id")
    concept = require_token(concept_id, "concept_id")
    episode = require_token(episode_id, "episode_id")
    require_reference(quality_bundle_ref, "quality_bundle_ref")
    if (
        str(quality_bundle.episode_id) != episode
        or quality_bundle.policy_sha256 != policy.policy_sha256
        or quality_bundle.gate_context != current_context
        or str(quality_bundle_ref.artifact_version) != "quality-bundle/1.0"
        or quality_bundle_ref.sha256 != quality_bundle_bytes_sha256(quality_bundle)
    ):
        raise SelectionContractError(
            "selection.input",
            "selection input is not bound to the exact bundle, policy, and context",
        )
    shots = _normalize_shots(candidate_sets)
    candidate_keys = {
        _subject_key(item.subject) for shot in shots for item in shot.candidates
    }
    bundle_keys = {_subject_key(item.subject) for item in quality_bundle.subjects}
    if candidate_keys != bundle_keys:
        raise SelectionContractError(
            "selection.coverage",
            "candidate sets must exactly cover QualityBundle media subjects",
        )
    return canonical_sha256(
        _selection_input_mapping(
            workspace,
            channel,
            concept,
            episode,
            quality_bundle_ref,
            quality_bundle.bundle_sha256,
            policy,
            current_context,
            shots,
        )
    )


def build_candidate_decision(
    *,
    workspace_id: str,
    channel_id: str,
    concept_id: str,
    episode_id: str,
    quality_bundle_ref: ArtifactReference,
    quality_bundle: QualityBundle,
    candidate_sets: Sequence[ShotCandidateSet],
    policy: QualityPolicy,
    current_context: GateContext,
    evaluated_at: datetime,
    authority: InitialAuthorityEvidence | None,
    authority_ledger: TrustedAuthorizationLedger | None,
    quality_resolver: CurrentQualityEvidenceResolver | None,
    evaluation_verifier: CurrentQualityEvaluationVerifier | None,
    confidence_verifier: CurrentCandidateConfidenceVerifier | None,
    authority_references: Sequence[ArtifactReference] = (),
) -> CandidateDecision:
    validate_quality_bundle(quality_bundle)
    require_target_quality_policy(policy)
    require_gate_context(current_context)
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise SelectionContractError(
            "selection.time",
            "evaluated_at must be timezone-aware",
        )
    workspace = require_token(workspace_id, "workspace_id")
    channel = require_token(channel_id, "channel_id")
    concept = require_token(concept_id, "concept_id")
    episode = require_token(episode_id, "episode_id")
    quality_evaluated_at = parse_rfc3339_datetime(quality_bundle.evaluated_at)
    if quality_evaluated_at > evaluated_at:
        raise SelectionContractError(
            "selection.time",
            "QualityBundle evaluation cannot postdate candidate selection",
        )
    if (
        str(quality_bundle.episode_id) != episode
        or quality_bundle.policy_sha256 != policy.policy_sha256
        or quality_bundle.gate_context != current_context
    ):
        raise SelectionContractError(
            "selection.bundle",
            "QualityBundle is stale or belongs to another episode, policy, or context",
        )
    require_reference(quality_bundle_ref, "quality_bundle_ref")
    if (
        str(quality_bundle_ref.artifact_version) != "quality-bundle/1.0"
        or str(quality_bundle_ref.sha256)
        != str(quality_bundle_bytes_sha256(quality_bundle))
    ):
        raise SelectionContractError(
            "selection.bundle_ref",
            "QualityBundle reference does not bind its exact canonical bytes",
        )
    shots = _normalize_shots(candidate_sets)
    summary_by_subject = {
        _subject_key(item.subject): item for item in quality_bundle.subjects
    }
    candidate_subjects = tuple(
        item.subject for shot in shots for item in shot.candidates
    )
    if set(map(_subject_key, candidate_subjects)) != set(summary_by_subject):
        raise SelectionContractError(
            "selection.coverage",
            "candidate sets must exactly cover QualityBundle media subjects",
        )
    selection_input_sha256 = candidate_selection_input_sha256(
        workspace_id=workspace,
        channel_id=channel,
        concept_id=concept,
        episode_id=episode,
        quality_bundle_ref=quality_bundle_ref,
        quality_bundle=quality_bundle,
        candidate_sets=shots,
        policy=policy,
        current_context=current_context,
    )
    authority_reasons: list[str] = []
    quality_current_reasons: list[str] = []
    if quality_resolver is None or evaluation_verifier is None:
        quality_current_reasons.append("selection.quality.current_evidence_missing")
    else:
        try:
            verify_quality_bundle(
                quality_bundle,
                current_context=current_context,
                policy=policy,
                resolver=quality_resolver,
                evaluation_verifier=evaluation_verifier,
                evaluated_at=evaluated_at.isoformat(),
            )
        except QualityContractError:
            quality_current_reasons.append(
                "selection.quality.current_evidence_invalid"
            )
    confidence_current_reasons: list[str] = []
    if confidence_verifier is None:
        confidence_current_reasons.append(
            "selection.confidence.current_evidence_missing"
        )
    else:
        for shot in shots:
            for option in shot.candidates:
                try:
                    current_option = confidence_verifier.verify_current(
                        option,
                        gate_context=current_context,
                        policy=policy,
                        evaluated_at=evaluated_at,
                    )
                except Exception:
                    current_option = None
                if current_option != option:
                    confidence_current_reasons.append(
                        "selection.confidence.current_evidence_invalid"
                    )
                    break
            if confidence_current_reasons:
                break
    candidate_refs = tuple(item.reference for item in candidate_subjects)
    confidence_refs = tuple(
        item.confidence_receipt_ref
        for shot in shots
        for item in shot.candidates
    )
    authority_inputs = tuple(
        sorted(
            (quality_bundle_ref, *candidate_refs, *confidence_refs),
            key=_reference_sort_key,
        )
    )
    require_reference_consistency(authority_inputs, allow_exact_reuse=False)
    if authority is None or authority_ledger is None:
        authority_reasons.append("selection.authority.missing")
    else:
        try:
            request = authority.request
            if (
                request.request_envelope_sha256 != selection_input_sha256
                or str(request.scope.workspace_id) != workspace
                or str(request.scope.channel_id) != channel
                or str(request.scope.concept_id) != concept
                or str(request.scope.episode_id) != episode
                or request.scope.candidate_count != len(candidate_subjects)
                or request.scope.cost_minor_units != 0
                or request.scope.retry_index != 0
                or request.scope.allowed_outputs
                or request.scope.provider_id is not None
                or request.scope.model_id is not None
            ):
                raise QualityContractError(
                    "selection.authority.scope",
                    "candidate authority request does not bind the exact selection input",
                )
            validate_initial_authority_evidence(
                authority,
                current_context=current_context,
                evaluated_at=evaluated_at,
                expected_action_id=AUTO_SELECT_CANDIDATES_ACTION_ID,
                expected_capability_id=AUTO_SELECT_CANDIDATES_CAPABILITY_ID,
                expected_plan_sha256=str(current_context.executable_plan_sha256),
                expected_input_artifacts=authority_inputs,
                expected_destination=None,
                expected_side_effect=False,
                ledger=authority_ledger,
                authority_references=authority_references,
            )
        except (QualityContractError, ValueError):
            authority_reasons.append("selection.authority.invalid")
    bundle_reasons: list[str] = []
    bundle_denied = bool(quality_current_reasons or confidence_current_reasons)
    bundle_reasons.extend(quality_current_reasons)
    bundle_reasons.extend(confidence_current_reasons)
    if quality_bundle.hard_failure_reason_codes:
        bundle_reasons.append("selection.quality.hard_failure")
        bundle_denied = True
    if quality_bundle.safety_failure_reason_codes:
        bundle_reasons.append("selection.quality.safety_failure")
        bundle_denied = True
    if quality_bundle.status is QualityBundleStatus.BLOCKED:
        bundle_reasons.append("selection.quality.blocked")
        bundle_denied = True
    elif quality_bundle.status is QualityBundleStatus.INCONCLUSIVE:
        bundle_reasons.append("selection.quality.inconclusive")
    decisions: list[ShotCandidateDecision] = []
    overall_reasons: set[str] = {*authority_reasons, *bundle_reasons}
    for shot in shots:
        ranked = tuple(
            sorted(
                (
                    RankedCandidate(
                        subject=item.subject,
                        adapter_id=item.adapter_id,
                        quality_score_bps=summary_by_subject[
                            _subject_key(item.subject)
                        ].aggregate_score_bps,
                        confidence_bps=item.confidence_bps,
                        confidence_receipt_ref=item.confidence_receipt_ref,
                    )
                    for item in shot.candidates
                ),
                key=lambda item: (
                    -item.quality_score_bps,
                    -item.confidence_bps,
                    _subject_key(item.subject),
                    str(item.adapter_id),
                ),
            )
        )
        top, second = ranked[0], ranked[1]
        margin = top.quality_score_bps - second.quality_score_bps
        summary = summary_by_subject[_subject_key(top.subject)]
        reasons = {*authority_reasons, *bundle_reasons}
        denied = bundle_denied
        if summary.hard_failure_reason_codes:
            reasons.add("selection.quality.hard_failure")
            denied = True
        if summary.safety_failure_reason_codes:
            reasons.add("selection.quality.safety_failure")
            denied = True
        if summary.status is QualityBundleStatus.BLOCKED:
            reasons.add("selection.quality.blocked")
            denied = True
        elif summary.status is QualityBundleStatus.INCONCLUSIVE:
            reasons.add("selection.quality.inconclusive")
        if top.quality_score_bps < policy.minimum_candidate_score_bps:
            reasons.add("selection.score.below_threshold")
        if top.confidence_bps < policy.minimum_confidence_bps:
            reasons.add("selection.confidence.below_threshold")
        if (
            top.quality_score_bps == second.quality_score_bps
            and top.confidence_bps == second.confidence_bps
        ):
            reasons.add("selection.margin.tie")
        elif margin < policy.minimum_margin_bps:
            reasons.add("selection.margin.below_threshold")
        if denied:
            status = CandidateDecisionStatus.DENIED
        elif reasons:
            status = CandidateDecisionStatus.ESCALATION_REQUIRED
        else:
            status = CandidateDecisionStatus.AUTO_SELECTED
        selected = top.subject if status is CandidateDecisionStatus.AUTO_SELECTED else None
        ordered_reasons = tuple(sorted(reasons))
        overall_reasons.update(ordered_reasons)
        decisions.append(
            ShotCandidateDecision(
                shot.shot_id,
                status,
                ranked,
                selected,
                margin,
                ordered_reasons,
            )
        )
    if any(item.status is CandidateDecisionStatus.DENIED for item in decisions):
        overall_status = CandidateDecisionStatus.DENIED
    elif all(
        item.status is CandidateDecisionStatus.AUTO_SELECTED for item in decisions
    ):
        overall_status = CandidateDecisionStatus.AUTO_SELECTED
    else:
        overall_status = CandidateDecisionStatus.ESCALATION_REQUIRED
    authority_valid = authority is not None and not authority_reasons
    provisional = CandidateDecision(
        artifact_version=CANDIDATE_DECISION_VERSION,
        decision_id=OpaqueId("pending"),
        decision_sha256=HashDigest("0" * 64),
        workspace_id=OpaqueId(workspace),
        channel_id=OpaqueId(channel),
        concept_id=OpaqueId(concept),
        episode_id=OpaqueId(episode),
        quality_bundle_ref=quality_bundle_ref,
        quality_bundle_sha256=quality_bundle.bundle_sha256,
        policy_sha256=policy.policy_sha256,
        gate_context=current_context,
        evaluated_at=evaluated_at.isoformat(),
        selection_input_sha256=selection_input_sha256,
        authority_request_sha256=(
            authority.request.request_sha256 if authority_valid else None
        ),
        authority_decision_sha256=(
            authority.decision.decision_sha256 if authority_valid else None
        ),
        authority_receipt_sha256=(
            authority.receipt.receipt_sha256 if authority_valid else None
        ),
        shots=tuple(decisions),
        status=overall_status,
        reason_codes=tuple(sorted(overall_reasons)),
        authority_effect="none",
    )
    digest = canonical_sha256(_identity(provisional))
    return validate_candidate_decision_structure(
        replace(
            provisional,
            decision_id=OpaqueId(f"candidate-decision-{str(digest)[:20]}"),
            decision_sha256=digest,
        )
    )


def validate_candidate_decision_structure(
    value: CandidateDecision,
) -> CandidateDecision:
    if (
        value.artifact_version != CANDIDATE_DECISION_VERSION
        or value.authority_effect != "none"
        or not isinstance(value.status, CandidateDecisionStatus)
    ):
        raise SelectionContractError(
            "selection.contract",
            "CandidateDecision is not a non-authorizing W05 decision",
        )
    require_token(str(value.workspace_id), "workspace_id")
    require_token(str(value.channel_id), "channel_id")
    require_token(str(value.concept_id), "concept_id")
    require_token(str(value.episode_id), "episode_id")
    require_reference(value.quality_bundle_ref, "quality_bundle_ref")
    if str(value.quality_bundle_ref.artifact_version) != "quality-bundle/1.0":
        raise SelectionContractError(
            "selection.bundle_ref",
            "candidate decision has an unsupported QualityBundle reference",
        )
    require_sha256(str(value.quality_bundle_sha256), "quality_bundle_sha256")
    require_sha256(str(value.policy_sha256), "policy_sha256")
    if value.policy_sha256 != require_target_quality_policy(
        target_quality_policy()
    ).policy_sha256:
        raise SelectionContractError(
            "selection.policy",
            "candidate decision uses another quality policy",
        )
    require_gate_context(value.gate_context)
    parse_rfc3339_datetime(value.evaluated_at)
    require_sha256(str(value.selection_input_sha256), "selection_input_sha256")
    authority_values = (
        value.authority_request_sha256,
        value.authority_decision_sha256,
        value.authority_receipt_sha256,
    )
    if any(item is None for item in authority_values) != all(
        item is None for item in authority_values
    ):
        raise SelectionContractError(
            "selection.authority.binding",
            "authority binding is partial",
        )
    for item in authority_values:
        if item is not None:
            require_sha256(str(item), "authority_sha256")
    if (
        not value.shots
        or value.shots
        != tuple(sorted(value.shots, key=lambda item: str(item.shot_id)))
        or len({str(item.shot_id) for item in value.shots}) != len(value.shots)
    ):
        raise SelectionContractError(
            "selection.shots",
            "shot decisions are empty, duplicated, or non-canonical",
        )
    all_refs: list[ArtifactReference] = [value.quality_bundle_ref]
    candidate_refs: list[ArtifactReference] = []
    for shot in value.shots:
        require_token(str(shot.shot_id), "shot_id")
        if not isinstance(shot.status, CandidateDecisionStatus):
            raise SelectionContractError(
                "selection.status",
                "shot status is not a CandidateDecisionStatus",
            )
        if len(shot.ranked_candidates) < 2:
            raise SelectionContractError(
                "selection.margin.unavailable",
                "shot decision needs at least two ranked candidates",
            )
        expected_order = tuple(
            sorted(
                shot.ranked_candidates,
                key=lambda item: (
                    -item.quality_score_bps,
                    -item.confidence_bps,
                    _subject_key(item.subject),
                    str(item.adapter_id),
                ),
            )
        )
        if shot.ranked_candidates != expected_order:
            raise SelectionContractError(
                "selection.rank",
                "ranked candidates are not deterministic",
            )
        for item in shot.ranked_candidates:
            require_media_subject(item.subject, "ranked candidate")
            if item.subject.shot_id != shot.shot_id:
                raise SelectionContractError(
                    "selection.candidates.shot",
                    "ranked media belongs to another shot",
                )
            require_token(str(item.adapter_id), "adapter_id")
            require_bps(item.quality_score_bps, "quality_score_bps")
            require_bps(item.confidence_bps, "confidence_bps")
            _option_mapping(
                CandidateOption(
                    subject=item.subject,
                    adapter_id=item.adapter_id,
                    confidence_bps=item.confidence_bps,
                    confidence_receipt_ref=item.confidence_receipt_ref,
                )
            )
            all_refs.extend(
                (
                    item.subject.reference,
                    item.subject.observation_receipt_ref,
                    item.confidence_receipt_ref,
                )
            )
            candidate_refs.extend(
                (
                    item.subject.reference,
                    item.subject.observation_receipt_ref,
                    item.confidence_receipt_ref,
                )
            )
        expected_margin = (
            shot.ranked_candidates[0].quality_score_bps
            - shot.ranked_candidates[1].quality_score_bps
        )
        require_bps(shot.margin_to_second_bps, "margin_to_second_bps")
        if shot.margin_to_second_bps != expected_margin:
            raise SelectionContractError(
                "selection.margin",
                "stored margin differs from ranking",
            )
        require_reason_codes(
            shot.reason_codes,
            "shot.reason_codes",
            allow_empty=shot.status is CandidateDecisionStatus.AUTO_SELECTED,
        )
        expected_selected = (
            shot.ranked_candidates[0].subject
            if shot.status is CandidateDecisionStatus.AUTO_SELECTED
            else None
        )
        if shot.selected_subject != expected_selected:
            raise SelectionContractError(
                "selection.selected",
                "selected media is inconsistent with decision status",
            )
    require_reference_consistency(all_refs, allow_exact_reuse=True)
    require_reference_consistency(candidate_refs, allow_exact_reuse=False)
    reconstructed_shots = tuple(
        ShotCandidateSet(
            shot_id=shot.shot_id,
            candidates=tuple(
                sorted(
                    (
                        CandidateOption(
                            subject=item.subject,
                            adapter_id=item.adapter_id,
                            confidence_bps=item.confidence_bps,
                            confidence_receipt_ref=item.confidence_receipt_ref,
                        )
                        for item in shot.ranked_candidates
                    ),
                    key=lambda item: (
                        _subject_key(item.subject),
                        str(item.adapter_id),
                    ),
                )
            ),
        )
        for shot in value.shots
    )
    expected_selection_input = canonical_sha256(
        _selection_input_mapping(
            str(value.workspace_id),
            str(value.channel_id),
            str(value.concept_id),
            str(value.episode_id),
            value.quality_bundle_ref,
            value.quality_bundle_sha256,
            target_quality_policy(),
            value.gate_context,
            reconstructed_shots,
        )
    )
    if value.selection_input_sha256 != expected_selection_input:
        raise SelectionContractError(
            "selection.input_rebound",
            "selection input digest differs from persisted candidate inputs",
        )
    if any(item.status is CandidateDecisionStatus.DENIED for item in value.shots):
        expected_status = CandidateDecisionStatus.DENIED
    elif all(
        item.status is CandidateDecisionStatus.AUTO_SELECTED for item in value.shots
    ):
        expected_status = CandidateDecisionStatus.AUTO_SELECTED
    else:
        expected_status = CandidateDecisionStatus.ESCALATION_REQUIRED
    expected_reasons = tuple(
        sorted({code for shot in value.shots for code in shot.reason_codes})
    )
    if value.status is not expected_status or value.reason_codes != expected_reasons:
        raise SelectionContractError(
            "selection.status",
            "aggregate selection status is inconsistent",
        )
    if value.status is CandidateDecisionStatus.AUTO_SELECTED and any(
        item is None for item in authority_values
    ):
        raise SelectionContractError(
            "selection.authority.missing",
            "automatic selection requires exact authority evidence",
        )
    digest = canonical_sha256(_identity(value))
    if (
        str(digest) != str(value.decision_sha256)
        or str(value.decision_id) != f"candidate-decision-{str(digest)[:20]}"
    ):
        raise SelectionContractError(
            "selection.digest",
            "CandidateDecision identity mismatch",
        )
    return value


def verify_candidate_decision(
    value: CandidateDecision,
    verification: CandidateDecisionVerificationInputs,
) -> CandidateDecision:
    validate_candidate_decision_structure(value)
    persisted_at = parse_rfc3339_datetime(value.evaluated_at)
    if (
        verification.verified_at.tzinfo is None
        or verification.verified_at.utcoffset() is None
        or persisted_at > verification.verified_at
    ):
        raise SelectionContractError(
            "selection.verification_time",
            "current verification must be timezone-aware and not predate the decision",
        )
    origin_has_authority = value.authority_request_sha256 is not None
    origin_evidence_present = verification.origin_authority is not None
    origin_ledger_present = verification.origin_authority_ledger is not None
    if (
        origin_evidence_present != origin_ledger_present
        or origin_has_authority != origin_evidence_present
    ):
        raise SelectionContractError(
            "selection.origin_authority",
            "persisted authority lineage requires exact original evidence",
        )
    origin_expected = build_candidate_decision(
        workspace_id=verification.workspace_id,
        channel_id=verification.channel_id,
        concept_id=verification.concept_id,
        episode_id=verification.episode_id,
        quality_bundle_ref=verification.quality_bundle_ref,
        quality_bundle=verification.quality_bundle,
        candidate_sets=verification.candidate_sets,
        policy=verification.policy,
        current_context=verification.current_context,
        evaluated_at=persisted_at,
        authority=verification.origin_authority,
        authority_ledger=verification.origin_authority_ledger,
        quality_resolver=verification.quality_resolver,
        evaluation_verifier=verification.evaluation_verifier,
        confidence_verifier=verification.confidence_verifier,
        authority_references=verification.authority_references,
    )
    if value != origin_expected:
        raise SelectionContractError(
            "selection.origin_rebound",
            "CandidateDecision differs from exact original issuance evidence",
        )
    expected = build_candidate_decision(
        workspace_id=verification.workspace_id,
        channel_id=verification.channel_id,
        concept_id=verification.concept_id,
        episode_id=verification.episode_id,
        quality_bundle_ref=verification.quality_bundle_ref,
        quality_bundle=verification.quality_bundle,
        candidate_sets=verification.candidate_sets,
        policy=verification.policy,
        current_context=verification.current_context,
        evaluated_at=verification.verified_at,
        authority=verification.authority,
        authority_ledger=verification.authority_ledger,
        quality_resolver=verification.quality_resolver,
        evaluation_verifier=verification.evaluation_verifier,
        confidence_verifier=verification.confidence_verifier,
        authority_references=verification.authority_references,
    )
    persisted_semantics = {
        **_identity(value),
        "evaluated_at": None,
        "authority_decision_sha256": None,
        "authority_receipt_sha256": None,
    }
    current_semantics = {
        **_identity(expected),
        "evaluated_at": None,
        "authority_decision_sha256": None,
        "authority_receipt_sha256": None,
    }
    if persisted_semantics != current_semantics:
        raise SelectionContractError(
            "selection.semantic_rebound",
            "CandidateDecision does not match a clean current recomputation",
        )
    return value


__all__ = [
    "AUTO_SELECT_CANDIDATES_ACTION_ID",
    "AUTO_SELECT_CANDIDATES_CAPABILITY_ID",
    "CANDIDATE_CONFIDENCE_RECEIPT_VERSION",
    "CANDIDATE_DECISION_VERSION",
    "build_candidate_decision",
    "candidate_decision_bytes_sha256",
    "candidate_decision_to_mapping",
    "candidate_selection_input_sha256",
    "validate_candidate_decision_structure",
    "verify_candidate_decision",
]
