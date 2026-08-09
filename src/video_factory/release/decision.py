"""Deterministic release identity and final-human-approval assessment.

This module deliberately has no publisher or side-effect port.  ``READY`` is
only an eligible handoff after one current human authority receipt; it is not
publication authority and never records a publish action.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
import hashlib

from video_factory.approvals import GateContext, gate_context_sha256, gate_context_to_mapping
from video_factory.artifacts import validate_artifact_mapping
from video_factory.authority import (
    AuthorityDecisionStatus,
    AuthoritySource,
    LedgerRecordState,
    TrustedAuthorizationLedger,
    VerificationPurpose,
    approval_request_to_mapping,
    build_approval_request,
    classify_action_risk,
    evaluate_authority,
    target_policy_bundle,
    validate_action_authority_request,
    validate_authority_decision,
    validate_authority_verification_receipt,
    validate_risk_assessment,
)
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
    validate_quality_bundle,
    verify_quality_bundle,
)
from video_factory.quality.validation import (
    media_subject_to_mapping,
    reference_to_mapping,
    require_gate_context,
    require_media_subject,
    require_reason_codes,
    require_reference,
    require_reference_consistency,
    require_sha256,
    require_token,
)
from video_factory.selection import (
    CandidateDecision,
    CandidateDecisionVerificationInputs,
    CandidateDecisionStatus,
    candidate_decision_bytes_sha256,
    validate_candidate_decision_structure,
    verify_candidate_decision,
)

from .contracts import (
    DestinationBinding,
    ReleaseAssessment,
    ReleaseAssessmentStatus,
    ReleaseAuthorityEvidence,
    ReleaseCandidate,
    ReleaseCandidateVerificationInputs,
    ReleaseContractError,
    ReleaseVisibility,
)


DESTINATION_BINDING_VERSION = "destination-binding/1.0"
RELEASE_CANDIDATE_VERSION = "release-candidate/1.0"
RELEASE_ASSESSMENT_VERSION = "release-assessment/1.0"


def _release_error(error: Exception, reason: str, message: str) -> ReleaseContractError:
    return ReleaseContractError(reason, f"{message}: {error}")


def _material_context(context: GateContext) -> tuple[HashDigest, ...]:
    return (
        context.workflow_definition_sha256,
        context.policy_bundle_sha256,
        context.rules_bundle_sha256,
        context.effective_config_sha256,
        context.current_manifest_sha256,
        context.evidence_graph_sha256,
    )


def _destination_identity(value: DestinationBinding) -> dict[str, object]:
    return {
        "artifact_version": value.artifact_version,
        "platform": value.platform,
        "channel_account_id": str(value.channel_account_id),
        "visibility": value.visibility.value,
        "locale": value.locale,
        "region": value.region,
        "scheduled_for": value.scheduled_for,
        "policy_sha256": str(value.policy_sha256),
        "authority_effect": value.authority_effect,
    }


def destination_binding_to_mapping(value: DestinationBinding) -> dict[str, object]:
    validate_destination_binding(value)
    mapping = {
        **_destination_identity(value),
        "destination_id": str(value.destination_id),
        "destination_sha256": str(value.destination_sha256),
    }
    result = validate_artifact_mapping(mapping)
    if not result.ok:
        raise ReleaseContractError("release.destination.schema", "; ".join(result.error_texts))
    return mapping


def build_destination_binding(
    *,
    platform: str,
    channel_account_id: str,
    visibility: ReleaseVisibility,
    locale: str,
    region: str,
    scheduled_for: str | None,
    policy: QualityPolicy,
) -> DestinationBinding:
    require_target_quality_policy(policy)
    provisional = DestinationBinding(
        artifact_version=DESTINATION_BINDING_VERSION,
        destination_id=OpaqueId("pending"),
        destination_sha256=HashDigest("0" * 64),
        platform=require_token(platform, "platform"),
        channel_account_id=OpaqueId(
            require_token(channel_account_id, "channel_account_id")
        ),
        visibility=ReleaseVisibility(visibility),
        locale=require_token(locale, "locale"),
        region=require_token(region, "region"),
        scheduled_for=scheduled_for,
        policy_sha256=policy.policy_sha256,
        authority_effect="none",
    )
    if scheduled_for is not None:
        parse_rfc3339_datetime(scheduled_for)
    digest = canonical_sha256(_destination_identity(provisional))
    return validate_destination_binding(
        replace(
            provisional,
            destination_id=OpaqueId(f"destination-{str(digest)[:20]}"),
            destination_sha256=digest,
        )
    )


def validate_destination_binding(value: DestinationBinding) -> DestinationBinding:
    if (
        value.artifact_version != DESTINATION_BINDING_VERSION
        or value.authority_effect != "none"
        or not isinstance(value.visibility, ReleaseVisibility)
    ):
        raise ReleaseContractError(
            "release.destination.contract",
            "destination binding must be non-authorizing",
        )
    require_token(value.platform, "platform")
    require_token(str(value.channel_account_id), "channel_account_id")
    require_token(value.locale, "locale")
    require_token(value.region, "region")
    if value.scheduled_for is not None:
        parse_rfc3339_datetime(value.scheduled_for)
    policy = require_target_quality_policy(target_quality_policy())
    if value.policy_sha256 != policy.policy_sha256:
        raise ReleaseContractError(
            "release.destination.policy",
            "destination uses another target policy",
        )
    digest = canonical_sha256(_destination_identity(value))
    if (
        str(value.destination_sha256) != str(digest)
        or str(value.destination_id) != f"destination-{str(digest)[:20]}"
    ):
        raise ReleaseContractError(
            "release.destination.digest",
            "destination identity mismatch",
        )
    return value


def destination_binding_bytes_sha256(value: DestinationBinding) -> HashDigest:
    return HashDigest(
        hashlib.sha256(
            canonical_json_bytes(destination_binding_to_mapping(value))
        ).hexdigest()
    )


def _candidate_material(value: ReleaseCandidate) -> dict[str, object]:
    return {
        "episode_id": str(value.episode_id),
        "final_media": media_subject_to_mapping(value.final_media),
        "metadata_ref": reference_to_mapping(value.metadata_ref),
        "subtitle_accessibility_refs": [
            reference_to_mapping(item) for item in value.subtitle_accessibility_refs
        ],
        "thumbnail": media_subject_to_mapping(value.thumbnail),
        "quality_bundle_ref": reference_to_mapping(value.quality_bundle_ref),
        "quality_bundle_sha256": str(value.quality_bundle_sha256),
        "candidate_decision_ref": reference_to_mapping(value.candidate_decision_ref),
        "candidate_decision_sha256": str(value.candidate_decision_sha256),
        "destination_ref": reference_to_mapping(value.destination_ref),
        "destination_sha256": str(value.destination_sha256),
        "policy_sha256": str(value.policy_sha256),
        "gate_context": gate_context_to_mapping(value.gate_context),
        "workspace_observation_sha256": str(value.workspace_observation_sha256),
        "created_at": value.created_at,
        "authority_effect": value.authority_effect,
    }


def _candidate_identity(value: ReleaseCandidate) -> dict[str, object]:
    return {
        "artifact_version": value.artifact_version,
        **_candidate_material(value),
        "release_intent_sha256": str(value.release_intent_sha256),
    }


def release_candidate_to_mapping(value: ReleaseCandidate) -> dict[str, object]:
    validate_release_candidate_structure(value)
    mapping = {
        **_candidate_identity(value),
        "candidate_id": str(value.candidate_id),
        "candidate_sha256": str(value.candidate_sha256),
    }
    result = validate_artifact_mapping(mapping)
    if not result.ok:
        raise ReleaseContractError("release.candidate.schema", "; ".join(result.error_texts))
    return mapping


def _build_release_candidate(
    *,
    episode_id: str,
    final_media: MediaSubject,
    metadata_ref: ArtifactReference,
    subtitle_accessibility_refs: Sequence[ArtifactReference],
    thumbnail: MediaSubject,
    quality_bundle_ref: ArtifactReference,
    quality_bundle: QualityBundle,
    candidate_decision_ref: ArtifactReference,
    candidate_decision: CandidateDecision,
    candidate_verification: CandidateDecisionVerificationInputs,
    destination_ref: ArtifactReference,
    destination: DestinationBinding,
    policy: QualityPolicy,
    current_context: GateContext,
    created_at: str,
    verified_at: datetime,
    quality_resolver: CurrentQualityEvidenceResolver,
    evaluation_verifier: CurrentQualityEvaluationVerifier,
) -> ReleaseCandidate:
    try:
        require_target_quality_policy(policy)
        validate_quality_bundle(quality_bundle)
        validate_candidate_decision_structure(candidate_decision)
        validate_destination_binding(destination)
        require_gate_context(current_context)
        require_media_subject(final_media, "final_media")
        require_media_subject(thumbnail, "thumbnail")
    except (QualityContractError, ValueError) as error:
        raise _release_error(
            error,
            "release.candidate.input",
            "release candidate input is invalid",
        ) from error
    created = parse_rfc3339_datetime(created_at)
    if verified_at.tzinfo is None or verified_at.utcoffset() is None:
        raise ReleaseContractError(
            "release.candidate.verification_time",
            "release verification time must be timezone-aware",
        )
    if created > verified_at or candidate_verification.verified_at != verified_at:
        raise ReleaseContractError(
            "release.candidate.verification_time",
            "release constituents must be verified at the current release time",
        )
    try:
        verify_quality_bundle(
            quality_bundle,
            current_context=current_context,
            policy=policy,
            resolver=quality_resolver,
            evaluation_verifier=evaluation_verifier,
            evaluated_at=verified_at.isoformat(),
        )
        verify_candidate_decision(candidate_decision, candidate_verification)
        current_thumbnail = quality_resolver.resolve_current(
            thumbnail,
            evaluated_at=verified_at.isoformat(),
        )
    except Exception as error:
        raise ReleaseContractError(
            "release.candidate.current_evidence",
            "release constituents are not current trusted evidence",
        ) from error
    if current_thumbnail != thumbnail:
        raise ReleaseContractError(
            "release.candidate.thumbnail_stale",
            "thumbnail is not bound to current exact media bytes",
        )
    episode = require_token(episode_id, "episode_id")
    if (
        str(quality_bundle.episode_id) != episode
        or str(candidate_decision.episode_id) != episode
        or quality_bundle.gate_context != current_context
        or _material_context(candidate_decision.gate_context)
        != _material_context(current_context)
        or quality_bundle.policy_sha256 != policy.policy_sha256
        or candidate_decision.policy_sha256 != policy.policy_sha256
        or destination.policy_sha256 != policy.policy_sha256
    ):
        raise ReleaseContractError(
            "release.candidate.context",
            "release constituents belong to another episode, context, or policy",
        )
    if quality_bundle.status is not QualityBundleStatus.PASSED:
        raise ReleaseContractError(
            "release.candidate.quality",
            "release quality bundle is not fully passed",
        )
    if candidate_decision.status is not CandidateDecisionStatus.AUTO_SELECTED:
        raise ReleaseContractError(
            "release.candidate.selection",
            "release lineage lacks a current automatic candidate decision",
        )
    if (
        len(quality_bundle.subjects) != 1
        or quality_bundle.subjects[0].subject != final_media
    ):
        raise ReleaseContractError(
            "release.candidate.quality_subject",
            "release quality must exactly evaluate the final media subject",
        )
    if final_media.reference.artifact_version != "final-media/1.0":
        raise ReleaseContractError(
            "release.candidate.final_media_version",
            "final media uses an unsupported artifact version",
        )
    if thumbnail.reference.artifact_version != "thumbnail/1.0":
        raise ReleaseContractError(
            "release.candidate.thumbnail_version",
            "thumbnail uses an unsupported artifact version",
        )
    if (
        final_media.workspace_observation_sha256
        != thumbnail.workspace_observation_sha256
    ):
        raise ReleaseContractError(
            "release.candidate.workspace",
            "final media and thumbnail are not from one current workspace observation",
        )
    decision_time = parse_rfc3339_datetime(candidate_decision.evaluated_at)
    quality_time = parse_rfc3339_datetime(quality_bundle.evaluated_at)
    if not (decision_time <= quality_time <= created <= verified_at):
        raise ReleaseContractError(
            "release.candidate.time",
            "release evidence timestamps violate causal ordering",
        )
    require_reference(metadata_ref, "metadata_ref")
    if metadata_ref.artifact_version != "publish-metadata-draft/1.0":
        raise ReleaseContractError(
            "release.candidate.metadata_version",
            "metadata reference has an unsupported version",
        )
    accessibility = tuple(subtitle_accessibility_refs)
    versions = {str(item.artifact_version) for item in accessibility}
    if versions != {"subtitle-track/1.0", "accessibility-track/1.0"}:
        raise ReleaseContractError(
            "release.candidate.accessibility",
            "release needs exact subtitle and accessibility artifacts",
        )
    require_reference_consistency(accessibility, allow_exact_reuse=False)
    require_reference(quality_bundle_ref, "quality_bundle_ref")
    if (
        quality_bundle_ref.artifact_version != "quality-bundle/1.0"
        or quality_bundle_ref.sha256 != quality_bundle_bytes_sha256(quality_bundle)
    ):
        raise ReleaseContractError(
            "release.candidate.quality_ref",
            "quality reference does not bind exact bundle bytes",
        )
    require_reference(candidate_decision_ref, "candidate_decision_ref")
    if (
        candidate_decision_ref.artifact_version != "candidate-decision/1.0"
        or candidate_decision_ref.sha256
        != candidate_decision_bytes_sha256(candidate_decision)
    ):
        raise ReleaseContractError(
            "release.candidate.selection_ref",
            "candidate decision reference does not bind exact bytes",
        )
    require_reference(destination_ref, "destination_ref")
    if (
        destination_ref.artifact_version != DESTINATION_BINDING_VERSION
        or destination_ref.sha256 != destination_binding_bytes_sha256(destination)
    ):
        raise ReleaseContractError(
            "release.candidate.destination_ref",
            "destination reference does not bind exact bytes",
        )
    require_reference_consistency(
        (
            final_media.reference,
            final_media.observation_receipt_ref,
            metadata_ref,
            *accessibility,
            thumbnail.reference,
            thumbnail.observation_receipt_ref,
            quality_bundle_ref,
            candidate_decision_ref,
            destination_ref,
        ),
        allow_exact_reuse=False,
    )
    provisional = ReleaseCandidate(
        artifact_version=RELEASE_CANDIDATE_VERSION,
        candidate_id=OpaqueId("pending"),
        candidate_sha256=HashDigest("0" * 64),
        episode_id=OpaqueId(episode),
        final_media=final_media,
        metadata_ref=metadata_ref,
        subtitle_accessibility_refs=tuple(
            sorted(
                accessibility,
                key=lambda item: (
                    str(item.path).casefold(),
                    str(item.sha256),
                    str(item.artifact_version),
                ),
            )
        ),
        thumbnail=thumbnail,
        quality_bundle_ref=quality_bundle_ref,
        quality_bundle_sha256=quality_bundle.bundle_sha256,
        candidate_decision_ref=candidate_decision_ref,
        candidate_decision_sha256=candidate_decision.decision_sha256,
        destination_ref=destination_ref,
        destination_sha256=destination.destination_sha256,
        policy_sha256=policy.policy_sha256,
        gate_context=current_context,
        workspace_observation_sha256=final_media.workspace_observation_sha256,
        release_intent_sha256=HashDigest("0" * 64),
        created_at=created_at,
        authority_effect="none",
    )
    intent = canonical_sha256(_candidate_material(provisional))
    provisional = replace(provisional, release_intent_sha256=intent)
    digest = canonical_sha256(_candidate_identity(provisional))
    return validate_release_candidate_structure(
        replace(
            provisional,
            candidate_id=OpaqueId(f"release-candidate-{str(digest)[:20]}"),
            candidate_sha256=digest,
        )
    )


def build_release_candidate(
    *,
    episode_id: str,
    final_media: MediaSubject,
    metadata_ref: ArtifactReference,
    subtitle_accessibility_refs: Sequence[ArtifactReference],
    thumbnail: MediaSubject,
    quality_bundle_ref: ArtifactReference,
    quality_bundle: QualityBundle,
    candidate_decision_ref: ArtifactReference,
    candidate_decision: CandidateDecision,
    candidate_verification: CandidateDecisionVerificationInputs,
    destination_ref: ArtifactReference,
    destination: DestinationBinding,
    policy: QualityPolicy,
    current_context: GateContext,
    created_at: str,
    quality_resolver: CurrentQualityEvidenceResolver,
    evaluation_verifier: CurrentQualityEvaluationVerifier,
) -> ReleaseCandidate:
    """Create a candidate only after verification at its creation instant."""

    created = parse_rfc3339_datetime(created_at)
    return _build_release_candidate(
        episode_id=episode_id,
        final_media=final_media,
        metadata_ref=metadata_ref,
        subtitle_accessibility_refs=subtitle_accessibility_refs,
        thumbnail=thumbnail,
        quality_bundle_ref=quality_bundle_ref,
        quality_bundle=quality_bundle,
        candidate_decision_ref=candidate_decision_ref,
        candidate_decision=candidate_decision,
        candidate_verification=candidate_verification,
        destination_ref=destination_ref,
        destination=destination,
        policy=policy,
        current_context=current_context,
        created_at=created_at,
        verified_at=created,
        quality_resolver=quality_resolver,
        evaluation_verifier=evaluation_verifier,
    )


def validate_release_candidate_structure(value: ReleaseCandidate) -> ReleaseCandidate:
    if (
        value.artifact_version != RELEASE_CANDIDATE_VERSION
        or value.authority_effect != "none"
    ):
        raise ReleaseContractError(
            "release.candidate.contract",
            "release candidate must be non-authorizing",
        )
    require_token(str(value.episode_id), "episode_id")
    require_media_subject(value.final_media, "final_media")
    require_media_subject(value.thumbnail, "thumbnail")
    if str(value.final_media.reference.artifact_version) != "final-media/1.0":
        raise ReleaseContractError(
            "release.candidate.final_media_version",
            "final media uses an unsupported artifact version",
        )
    if str(value.thumbnail.reference.artifact_version) != "thumbnail/1.0":
        raise ReleaseContractError(
            "release.candidate.thumbnail_version",
            "thumbnail uses an unsupported artifact version",
        )
    require_reference(value.metadata_ref, "metadata_ref")
    if str(value.metadata_ref.artifact_version) != "publish-metadata-draft/1.0":
        raise ReleaseContractError(
            "release.candidate.metadata_version",
            "metadata reference has an unsupported version",
        )
    require_reference(value.quality_bundle_ref, "quality_bundle_ref")
    if str(value.quality_bundle_ref.artifact_version) != "quality-bundle/1.0":
        raise ReleaseContractError(
            "release.candidate.quality_ref",
            "quality reference has an unsupported version",
        )
    require_reference(value.candidate_decision_ref, "candidate_decision_ref")
    if str(value.candidate_decision_ref.artifact_version) != "candidate-decision/1.0":
        raise ReleaseContractError(
            "release.candidate.selection_ref",
            "candidate decision reference has an unsupported version",
        )
    require_reference(value.destination_ref, "destination_ref")
    if str(value.destination_ref.artifact_version) != DESTINATION_BINDING_VERSION:
        raise ReleaseContractError(
            "release.candidate.destination_ref",
            "destination reference has an unsupported version",
        )
    if len(value.subtitle_accessibility_refs) != 2 or {
        str(item.artifact_version) for item in value.subtitle_accessibility_refs
    } != {"subtitle-track/1.0", "accessibility-track/1.0"}:
        raise ReleaseContractError(
            "release.candidate.accessibility",
            "release needs exactly one subtitle and one accessibility artifact",
        )
    require_reference_consistency(
        value.subtitle_accessibility_refs,
        allow_exact_reuse=False,
    )
    if value.subtitle_accessibility_refs != tuple(
        sorted(
            value.subtitle_accessibility_refs,
            key=lambda item: (
                str(item.path).casefold(),
                str(item.sha256),
                str(item.artifact_version),
            ),
        )
    ):
        raise ReleaseContractError(
            "release.candidate.order",
            "subtitle/accessibility references are not canonical",
        )
    require_sha256(str(value.quality_bundle_sha256), "quality_bundle_sha256")
    require_sha256(
        str(value.candidate_decision_sha256),
        "candidate_decision_sha256",
    )
    require_sha256(str(value.destination_sha256), "destination_sha256")
    require_sha256(str(value.policy_sha256), "policy_sha256")
    if value.policy_sha256 != target_quality_policy().policy_sha256:
        raise ReleaseContractError(
            "release.candidate.policy",
            "release candidate uses another target quality policy",
        )
    require_gate_context(value.gate_context)
    require_sha256(
        str(value.workspace_observation_sha256),
        "workspace_observation_sha256",
    )
    require_sha256(str(value.release_intent_sha256), "release_intent_sha256")
    parse_rfc3339_datetime(value.created_at)
    if (
        value.final_media.workspace_observation_sha256
        != value.workspace_observation_sha256
        or value.thumbnail.workspace_observation_sha256
        != value.workspace_observation_sha256
    ):
        raise ReleaseContractError(
            "release.candidate.workspace",
            "release media uses another workspace observation",
        )
    require_reference_consistency(
        (
            value.final_media.reference,
            value.final_media.observation_receipt_ref,
            value.metadata_ref,
            *value.subtitle_accessibility_refs,
            value.thumbnail.reference,
            value.thumbnail.observation_receipt_ref,
            value.quality_bundle_ref,
            value.candidate_decision_ref,
            value.destination_ref,
        ),
        allow_exact_reuse=False,
    )
    intent = canonical_sha256(_candidate_material(value))
    if value.release_intent_sha256 != intent:
        raise ReleaseContractError(
            "release.candidate.intent",
            "release intent digest mismatch",
        )
    digest = canonical_sha256(_candidate_identity(value))
    if (
        value.candidate_sha256 != digest
        or str(value.candidate_id) != f"release-candidate-{str(digest)[:20]}"
    ):
        raise ReleaseContractError(
            "release.candidate.digest",
            "release candidate identity mismatch",
        )
    return value


def release_candidate_bytes_sha256(value: ReleaseCandidate) -> HashDigest:
    return HashDigest(
        hashlib.sha256(
            canonical_json_bytes(release_candidate_to_mapping(value))
        ).hexdigest()
    )


def _release_scope_refs(
    candidate_ref: ArtifactReference,
    candidate: ReleaseCandidate,
) -> tuple[ArtifactReference, ...]:
    values = (
        candidate_ref,
        candidate.final_media.reference,
        candidate.metadata_ref,
        *candidate.subtitle_accessibility_refs,
        candidate.thumbnail.reference,
        candidate.quality_bundle_ref,
        candidate.candidate_decision_ref,
        candidate.destination_ref,
    )
    return tuple(
        sorted(
            values,
            key=lambda item: (
                str(item.path).casefold(),
                str(item.sha256),
                str(item.artifact_version),
            ),
        )
    )


def _assessment_identity(value: ReleaseAssessment) -> dict[str, object]:
    return {
        "artifact_version": value.artifact_version,
        "release_candidate_ref": reference_to_mapping(value.release_candidate_ref),
        "release_candidate_sha256": str(value.release_candidate_sha256),
        "gate_context": gate_context_to_mapping(value.gate_context),
        "destination_sha256": str(value.destination_sha256),
        "evaluated_at": value.evaluated_at,
        "authority_request_sha256": (
            str(value.authority_request_sha256)
            if value.authority_request_sha256 is not None
            else None
        ),
        "risk_assessment_sha256": (
            str(value.risk_assessment_sha256)
            if value.risk_assessment_sha256 is not None
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
        "approval_request": (
            approval_request_to_mapping(value.approval_request)
            if value.approval_request is not None
            else None
        ),
        "status": value.status.value,
        "reason_codes": list(value.reason_codes),
        "required_independent_humans": value.required_independent_humans,
        "publish_performed": value.publish_performed,
        "authority_effect": value.authority_effect,
    }


def release_assessment_to_mapping(value: ReleaseAssessment) -> dict[str, object]:
    validate_release_assessment_structure(value)
    mapping = {
        **_assessment_identity(value),
        "assessment_id": str(value.assessment_id),
        "assessment_sha256": str(value.assessment_sha256),
    }
    result = validate_artifact_mapping(mapping)
    if not result.ok:
        raise ReleaseContractError("release.assessment.schema", "; ".join(result.error_texts))
    return mapping


def _validate_release_authority(
    evidence: ReleaseAuthorityEvidence,
    *,
    candidate_ref: ArtifactReference,
    candidate: ReleaseCandidate,
    destination: DestinationBinding,
    current_context: GateContext,
    evaluated_at: datetime,
    ledger: TrustedAuthorizationLedger | None,
    authority_references: Sequence[ArtifactReference],
) -> tuple[ReleaseAssessmentStatus, tuple[str, ...], object | None]:
    try:
        validate_action_authority_request(evidence.request)
        validate_risk_assessment(evidence.risk)
        validate_authority_decision(evidence.decision)
        if evidence.receipt is not None:
            validate_authority_verification_receipt(evidence.receipt)
    except ValueError as error:
        raise ReleaseContractError(
            "release.authority.invalid",
            "release authority evidence is structurally invalid",
        ) from error
    request = evidence.request
    risk = evidence.risk
    decision = evidence.decision
    expected_risk = classify_action_risk(request, target_policy_bundle())
    expected_refs = _release_scope_refs(candidate_ref, candidate)
    if (
        str(request.action_id) != "approve_publish"
        or str(request.capability_id) != "publish_approval"
        or request.gate_context != current_context
        or request.scope.destination != str(destination.destination_id)
        or request.scope.input_artifacts != expected_refs
        or request.side_effect
        or risk != expected_risk
        or not risk.supported
        or risk.action_request_sha256 != request.request_sha256
        or decision.action_request_sha256 != request.request_sha256
        or decision.gate_context_sha256 != gate_context_sha256(current_context)
        or decision.risk_assessment_sha256 != risk.assessment_sha256
        or decision.effective_risk is not risk.effective_risk
    ):
        raise ReleaseContractError(
            "release.authority.scope",
            "release authority is bound to another action, destination, context, or constituent set",
        )
    if decision.status is AuthorityDecisionStatus.AUTHORIZED:
        if evidence.receipt is None or ledger is None:
            raise ReleaseContractError(
                "release.authority.receipt_missing",
                "authorized release assessment lacks a current trusted-ledger receipt",
            )
        try:
            validate_initial_authority_evidence(
                InitialAuthorityEvidence(request, risk, decision, evidence.receipt),
                current_context=current_context,
                evaluated_at=evaluated_at,
                expected_action_id="approve_publish",
                expected_capability_id="publish_approval",
                expected_plan_sha256=str(request.executable_plan_sha256),
                expected_input_artifacts=expected_refs,
                expected_destination=str(destination.destination_id),
                expected_side_effect=False,
                ledger=ledger,
                authority_references=authority_references,
            )
        except (QualityContractError, ValueError) as error:
            raise ReleaseContractError(
                "release.authority.current",
                "release human authority is not current",
            ) from error
        receipt = evidence.receipt
        if (
            decision.source is not AuthoritySource.ONE_SHOT_HUMAN
            or receipt.authority_source is not AuthoritySource.ONE_SHOT_HUMAN
            or receipt.ledger_state is not LedgerRecordState.ACTIVE
            or receipt.purpose is not VerificationPurpose.INITIAL_DECISION
            or len(receipt.principal_verifications) != 1
        ):
            raise ReleaseContractError(
                "release.authority.human_count",
                "initial release requires exactly one current authenticated human",
            )
        return ReleaseAssessmentStatus.READY, (), None
    try:
        fresh_risk, fresh_decision = evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=ledger,
            authority_references=authority_references,
            evaluated_at=evaluated_at,
        )
    except ValueError as error:
        raise ReleaseContractError(
            "release.authority.ledger_unavailable",
            "trusted current authority verification failed",
        ) from error
    if fresh_risk != risk or fresh_decision != decision:
        raise ReleaseContractError(
            "release.authority.not_current",
            "persisted release authority differs from trusted current recomputation",
        )
    if decision.status is AuthorityDecisionStatus.HUMAN_APPROVAL_REQUIRED:
        if evidence.receipt is not None:
            raise ReleaseContractError(
                "release.authority.partial_receipt",
                "approval-required decision cannot carry an authority receipt",
            )
        approval = build_approval_request(request, risk, decision)
        if approval.required_independent_humans != 1:
            raise ReleaseContractError(
                "release.approval.human_count",
                "initial release approval must require exactly one human",
            )
        return (
            ReleaseAssessmentStatus.APPROVAL_REQUIRED,
            tuple(sorted({*decision.reason_codes, "release.approval.required"})),
            approval,
        )
    if evidence.receipt is not None:
        raise ReleaseContractError(
            "release.authority.denied_receipt",
            "denied decision cannot carry an authority receipt",
        )
    return (
        ReleaseAssessmentStatus.DENIED,
        tuple(sorted({*decision.reason_codes, "release.authority.denied"})),
        None,
    )


def assess_release_candidate(
    *,
    release_candidate_ref: ArtifactReference,
    release_candidate: ReleaseCandidate,
    release_candidate_verification: ReleaseCandidateVerificationInputs,
    destination: DestinationBinding,
    current_context: GateContext,
    evaluated_at: datetime,
    authority: ReleaseAuthorityEvidence | None,
    authority_ledger: TrustedAuthorizationLedger | None = None,
    authority_references: Sequence[ArtifactReference] = (),
) -> ReleaseAssessment:
    validate_release_candidate_structure(release_candidate)
    validate_destination_binding(destination)
    require_gate_context(current_context)
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ReleaseContractError(
            "release.assessment.time",
            "release assessment time must be timezone-aware",
        )
    if release_candidate_verification.verified_at != evaluated_at:
        raise ReleaseContractError(
            "release.assessment.verification_time",
            "release candidate must be freshly verified at assessment time",
        )
    verify_release_candidate(
        release_candidate,
        release_candidate_verification,
    )
    if parse_rfc3339_datetime(release_candidate.created_at) > evaluated_at:
        raise ReleaseContractError(
            "release.assessment.time",
            "release assessment predates its candidate",
        )
    require_reference(release_candidate_ref, "release_candidate_ref")
    if (
        release_candidate_ref.artifact_version != RELEASE_CANDIDATE_VERSION
        or release_candidate_ref.sha256
        != release_candidate_bytes_sha256(release_candidate)
    ):
        raise ReleaseContractError(
            "release.assessment.candidate_ref",
            "release candidate reference does not bind exact bytes",
        )
    if (
        release_candidate.gate_context != current_context
        or release_candidate.destination_sha256 != destination.destination_sha256
    ):
        raise ReleaseContractError(
            "release.assessment.context",
            "release candidate is stale or targets another destination",
        )
    if authority is None:
        status = ReleaseAssessmentStatus.DENIED
        reasons = ("release.authority.missing",)
        approval = None
    else:
        status, reasons, approval = _validate_release_authority(
            authority,
            candidate_ref=release_candidate_ref,
            candidate=release_candidate,
            destination=destination,
            current_context=current_context,
            evaluated_at=evaluated_at,
            ledger=authority_ledger,
            authority_references=authority_references,
        )
    provisional = ReleaseAssessment(
        artifact_version=RELEASE_ASSESSMENT_VERSION,
        assessment_id=OpaqueId("pending"),
        assessment_sha256=HashDigest("0" * 64),
        release_candidate_ref=release_candidate_ref,
        release_candidate_sha256=release_candidate.candidate_sha256,
        gate_context=current_context,
        destination_sha256=destination.destination_sha256,
        evaluated_at=evaluated_at.isoformat(),
        authority_request_sha256=(
            authority.request.request_sha256 if authority is not None else None
        ),
        risk_assessment_sha256=(
            authority.risk.assessment_sha256 if authority is not None else None
        ),
        authority_decision_sha256=(
            authority.decision.decision_sha256 if authority is not None else None
        ),
        authority_receipt_sha256=(
            authority.receipt.receipt_sha256
            if authority is not None and authority.receipt is not None
            else None
        ),
        approval_request=approval,
        status=status,
        reason_codes=reasons,
        required_independent_humans=1,
        publish_performed=False,
        authority_effect="none",
    )
    digest = canonical_sha256(_assessment_identity(provisional))
    return validate_release_assessment_structure(
        replace(
            provisional,
            assessment_id=OpaqueId(f"release-assessment-{str(digest)[:20]}"),
            assessment_sha256=digest,
        )
    )


def validate_release_assessment_structure(
    value: ReleaseAssessment,
) -> ReleaseAssessment:
    if (
        value.artifact_version != RELEASE_ASSESSMENT_VERSION
        or value.authority_effect != "none"
        or not isinstance(value.status, ReleaseAssessmentStatus)
        or type(value.publish_performed) is not bool
        or value.publish_performed is not False
        or type(value.required_independent_humans) is not int
        or value.required_independent_humans != 1
    ):
        raise ReleaseContractError(
            "release.assessment.contract",
            "release assessment is not a non-publishing single-human handoff",
        )
    require_reference(value.release_candidate_ref, "release_candidate_ref")
    if str(value.release_candidate_ref.artifact_version) != RELEASE_CANDIDATE_VERSION:
        raise ReleaseContractError(
            "release.assessment.candidate_ref",
            "release assessment references an unsupported candidate version",
        )
    require_sha256(
        str(value.release_candidate_sha256),
        "release_candidate_sha256",
    )
    require_gate_context(value.gate_context)
    require_sha256(str(value.destination_sha256), "destination_sha256")
    parse_rfc3339_datetime(value.evaluated_at)
    authority_values = (
        value.authority_request_sha256,
        value.risk_assessment_sha256,
        value.authority_decision_sha256,
    )
    if any(item is None for item in authority_values) != all(
        item is None for item in authority_values
    ):
        raise ReleaseContractError(
            "release.assessment.authority_binding",
            "release authority digest set is partial",
        )
    for item in (*authority_values, value.authority_receipt_sha256):
        if item is not None:
            require_sha256(str(item), "authority_sha256")
    require_reason_codes(
        value.reason_codes,
        "reason_codes",
        allow_empty=value.status is ReleaseAssessmentStatus.READY,
    )
    if value.status is ReleaseAssessmentStatus.READY:
        if value.authority_receipt_sha256 is None or value.approval_request is not None:
            raise ReleaseContractError(
                "release.assessment.ready",
                "ready assessment needs current human authority and no request",
            )
    elif value.status is ReleaseAssessmentStatus.APPROVAL_REQUIRED:
        if value.approval_request is None or value.authority_receipt_sha256 is not None:
            raise ReleaseContractError(
                "release.assessment.approval",
                "approval-required assessment needs one request and no evidence receipt",
            )
        if (
            value.approval_request.action_request_sha256
            != value.authority_request_sha256
            or value.approval_request.risk_assessment_sha256
            != value.risk_assessment_sha256
            or value.approval_request.gate_context_sha256
            != gate_context_sha256(value.gate_context)
            or value.approval_request.required_independent_humans != 1
            or value.approval_request.creates_authority
        ):
            raise ReleaseContractError(
                "release.assessment.approval_binding",
                "approval request is not bound to the exact release authority request",
            )
    elif value.approval_request is not None or value.authority_receipt_sha256 is not None:
        raise ReleaseContractError(
            "release.assessment.denied",
            "denied assessment cannot carry approval or authority evidence",
        )
    digest = canonical_sha256(_assessment_identity(value))
    if (
        value.assessment_sha256 != digest
        or str(value.assessment_id) != f"release-assessment-{str(digest)[:20]}"
    ):
        raise ReleaseContractError(
            "release.assessment.digest",
            "release assessment identity mismatch",
        )
    return value


def verify_release_candidate(
    value: ReleaseCandidate,
    verification: ReleaseCandidateVerificationInputs,
) -> ReleaseCandidate:
    validate_release_candidate_structure(value)
    expected = _build_release_candidate(
        episode_id=verification.episode_id,
        final_media=verification.final_media,
        metadata_ref=verification.metadata_ref,
        subtitle_accessibility_refs=verification.subtitle_accessibility_refs,
        thumbnail=verification.thumbnail,
        quality_bundle_ref=verification.quality_bundle_ref,
        quality_bundle=verification.quality_bundle,
        candidate_decision_ref=verification.candidate_decision_ref,
        candidate_decision=verification.candidate_decision,
        candidate_verification=verification.candidate_verification,
        destination_ref=verification.destination_ref,
        destination=verification.destination,
        policy=verification.policy,
        current_context=verification.current_context,
        created_at=value.created_at,
        verified_at=verification.verified_at,
        quality_resolver=verification.quality_resolver,
        evaluation_verifier=verification.evaluation_verifier,
    )
    if value != expected:
        raise ReleaseContractError(
            "release.candidate.semantic_rebound",
            "release candidate differs from clean current recomputation",
        )
    return value


def verify_release_assessment(
    value: ReleaseAssessment,
    **inputs: object,
) -> ReleaseAssessment:
    expected = assess_release_candidate(**inputs)
    if value != expected:
        raise ReleaseContractError(
            "release.assessment.semantic_rebound",
            "release assessment differs from clean current recomputation",
        )
    return value


__all__ = [
    "DESTINATION_BINDING_VERSION",
    "RELEASE_ASSESSMENT_VERSION",
    "RELEASE_CANDIDATE_VERSION",
    "ReleaseCandidateVerificationInputs",
    "assess_release_candidate",
    "build_destination_binding",
    "build_release_candidate",
    "destination_binding_bytes_sha256",
    "destination_binding_to_mapping",
    "release_assessment_to_mapping",
    "release_candidate_bytes_sha256",
    "release_candidate_to_mapping",
    "validate_destination_binding",
    "validate_release_assessment_structure",
    "validate_release_candidate_structure",
    "verify_release_assessment",
    "verify_release_candidate",
]
