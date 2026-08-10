"""Durable, fixture-only publication coordinator for W06.

W05 ``READY`` is only an eligible handoff.  This module requires a separate
R3 workflow action, fresh W04 reservation, current release recomputation,
fixture workspace proof, an opaque credential handle, and an append-only
journal before a publisher port can be invoked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
from pathlib import Path
import re
from typing import Protocol

from video_factory.approvals import GateContext, gate_context_sha256
from video_factory.authority import (
    ActionAuthorityRequest,
    AuthorityDecision,
    AuthorityDecisionStatus,
    TrustedAuthorizationLedger,
    VerificationPurpose,
    revalidate_authority_for_side_effect,
    validate_action_authority_request,
    validate_authority_decision,
)
from video_factory.config.canonical import canonical_json_bytes, canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
    RequestId,
)
from video_factory.json_boundary import parse_json_bytes, require_json_object
from video_factory.release import (
    RELEASE_ASSESSMENT_VERSION,
    RELEASE_CANDIDATE_VERSION,
    DESTINATION_BINDING_VERSION,
    DestinationBinding,
    ReleaseAssessment,
    ReleaseAssessmentStatus,
    ReleaseAuthorityEvidence,
    ReleaseCandidate,
    ReleaseCandidateVerificationInputs,
    destination_binding_bytes_sha256,
    release_assessment_to_mapping,
    release_candidate_bytes_sha256,
    validate_destination_binding,
    validate_release_assessment_structure,
    validate_release_candidate_structure,
    verify_release_assessment,
    verify_release_candidate,
)
from video_factory.providers import (
    CostMeasurement,
    ExternalReference,
    ExternalStateUncertain,
    Outcome,
    ResultEnvelope,
    UncertaintyEvidence,
)
from video_factory.runtime import (
    JournalState,
    ExecutionReceipt,
    PublicationIntent,
    PublicationReceipt,
    PublicationStatus,
    RuntimeActionKind,
    build_execution_intent,
    build_execution_receipt,
    build_publication_receipt,
    publication_intent_to_mapping,
    publication_receipt_from_mapping,
    publication_receipt_to_mapping,
)
from video_factory.workflow import ActionRisk, AuthorityRequirement

from .boundary import FixtureRuntimeBoundary
from .authority import ClaimBoundAuthorizationLedger, ClaimBoundLedgerAdapter
from .credentials import (
    CredentialLease,
    CredentialScope,
    TrustedCredentialBroker,
    require_current_credential_lease,
)
from .filesystem import (
    FixtureWorkspaceObserver,
    _write_new,
    read_stable_regular_file,
    read_stable_regular_bytes,
)
from .journal import JournalSnapshot, SQLiteExecutionJournal
from .managed_mutation import (
    AuthorityReservationSettlementPort,
    RuntimeIdentityAttestor,
    RuntimeKillSwitch,
    TrustedRuntimeClock,
)


PUBLICATION_WORKSPACE_VERIFICATION_VERSION = (
    "publication-workspace-verification/1.0"
)
_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class PublicationRuntimeError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class PublicationWorkspaceVerification:
    workspace_id: OpaqueId
    revision_id: OpaqueId
    observation_sha256: HashDigest
    manifest_sha256: HashDigest
    verified_content_refs: tuple[ArtifactReference, ...]
    verifier_record: ArtifactReference
    evaluated_at: str


class PublicationWorkspaceVerifier(Protocol):
    def verify_current(
        self,
        candidate: ReleaseCandidate,
        *,
        revision_id: OpaqueId,
        evaluated_at: datetime,
    ) -> PublicationWorkspaceVerification | None: ...


class FixturePublisherPort(Protocol):
    publisher_id: OpaqueId
    variant_id: OpaqueId

    def publish(
        self,
        candidate: ReleaseCandidate,
        destination: DestinationBinding,
        *,
        credential_lease: CredentialLease,
        workspace_verification: PublicationWorkspaceVerification,
    ) -> ResultEnvelope: ...

    def reconcile(
        self,
        candidate: ReleaseCandidate,
        destination: DestinationBinding,
        external_reference: ExternalReference | None,
        *,
        credential_lease: CredentialLease,
        workspace_verification: PublicationWorkspaceVerification,
    ) -> ResultEnvelope: ...


@dataclass(frozen=True, slots=True)
class PublicationRuntimeInputs:
    publication_intent: PublicationIntent
    release_candidate_ref: ArtifactReference
    release_candidate: ReleaseCandidate
    release_candidate_verification: ReleaseCandidateVerificationInputs
    release_assessment_ref: ArtifactReference
    release_assessment: ReleaseAssessment
    release_assessment_authority: ReleaseAuthorityEvidence
    release_assessment_ledger: TrustedAuthorizationLedger
    release_assessment_authority_references: tuple[ArtifactReference, ...]
    destination: DestinationBinding
    publish_request: ActionAuthorityRequest
    publish_decision: AuthorityDecision
    publish_ledger: ClaimBoundAuthorizationLedger
    workspace_revision_id: OpaqueId
    service_identity: OpaqueId
    credential_reference: ArtifactReference


def release_assessment_bytes_sha256(value: ReleaseAssessment) -> HashDigest:
    return HashDigest(
        hashlib.sha256(
            canonical_json_bytes(release_assessment_to_mapping(value))
        ).hexdigest()
    )


def publication_scope_references(
    candidate_ref: ArtifactReference,
    candidate: ReleaseCandidate,
    assessment_ref: ArtifactReference,
) -> tuple[ArtifactReference, ...]:
    values = (
        candidate_ref,
        assessment_ref,
        candidate.final_media.reference,
        candidate.metadata_ref,
        *candidate.subtitle_accessibility_refs,
        candidate.thumbnail.reference,
        candidate.quality_bundle_ref,
        candidate.candidate_decision_ref,
        candidate.destination_ref,
    )
    ordered = tuple(
        sorted(
            values,
            key=lambda item: (
                str(item.path).casefold(),
                str(item.path),
                str(item.sha256),
                str(item.artifact_version),
            ),
        )
    )
    keys: dict[str, tuple[str, str, str]] = {}
    for item in ordered:
        key = str(item.path).casefold()
        identity = (
            str(item.path),
            str(item.sha256),
            str(item.artifact_version),
        )
        previous = keys.get(key)
        if previous is not None and previous != identity:
            raise PublicationRuntimeError(
                "runtime.publication.reference_alias",
                "publication inputs contain a path alias with another identity",
            )
        if previous is not None:
            raise PublicationRuntimeError(
                "runtime.publication.reference_duplicate",
                "publication inputs contain a duplicate reference",
            )
        keys[key] = identity
    return ordered


class FixturePublicationWorkspaceVerifier:
    """Verify actual fixture bytes immediately before a publication effect."""

    def __init__(
        self,
        boundary: FixtureRuntimeBoundary,
        observer: FixtureWorkspaceObserver,
    ) -> None:
        self._observer = observer
        self._directory = boundary.require_directory(
            "publication-workspace-verifications", create=True
        )

    @staticmethod
    def _content_refs(candidate: ReleaseCandidate) -> tuple[ArtifactReference, ...]:
        return tuple(
            sorted(
                (
                    candidate.final_media.reference,
                    candidate.metadata_ref,
                    *candidate.subtitle_accessibility_refs,
                    candidate.thumbnail.reference,
                ),
                key=lambda item: (
                    str(item.path).casefold(),
                    str(item.path),
                    str(item.sha256),
                    str(item.artifact_version),
                ),
            )
        )

    @staticmethod
    def _reference_mapping(reference: ArtifactReference) -> dict[str, str]:
        return {
            "path": str(reference.path),
            "sha256": str(reference.sha256),
            "artifact_version": str(reference.artifact_version),
        }

    def verify_current(
        self,
        candidate: ReleaseCandidate,
        *,
        revision_id: OpaqueId,
        evaluated_at: datetime,
    ) -> PublicationWorkspaceVerification | None:
        try:
            validate_release_candidate_structure(candidate)
            observation = self._observer.observe(revision_id=revision_id)
            refs = self._content_refs(candidate)
            for reference in refs:
                self._observer.require_exact_file(
                    str(reference.path), expected_sha256=reference.sha256
                )
            from video_factory.mutation import workspace_observation_sha256

            observation_sha = workspace_observation_sha256(observation)
            if (
                observation.workspace_id != candidate.workspace_id
                or observation_sha != candidate.workspace_observation_sha256
                or observation.manifest_sha256
                != candidate.gate_context.current_manifest_sha256
            ):
                return None
            mapping = {
                "artifact_version": PUBLICATION_WORKSPACE_VERIFICATION_VERSION,
                "workspace_id": str(observation.workspace_id),
                "revision_id": str(observation.revision_id),
                "observation_sha256": str(observation_sha),
                "manifest_sha256": str(observation.manifest_sha256),
                "verified_content_refs": [
                    self._reference_mapping(item) for item in refs
                ],
                "evaluated_at": evaluated_at.isoformat(),
                "authority_effect": "none",
            }
            payload = canonical_json_bytes(mapping)
            digest = HashDigest(hashlib.sha256(payload).hexdigest())
            path = self._directory / f"{digest}.json"
            if path.exists():
                stable = read_stable_regular_file(path)
                if stable.exact_sha256 != digest or stable.byte_length != len(payload):
                    return None
            else:
                _write_new(path, payload)
            record = ArtifactReference(
                RelativeArtifactPath(f"runtime-publication-verifications/{digest}.json"),
                digest,
                ArtifactVersion(PUBLICATION_WORKSPACE_VERIFICATION_VERSION),
            )
            return PublicationWorkspaceVerification(
                workspace_id=observation.workspace_id,
                revision_id=observation.revision_id,
                observation_sha256=observation_sha,
                manifest_sha256=observation.manifest_sha256,
                verified_content_refs=refs,
                verifier_record=record,
                evaluated_at=evaluated_at.isoformat(),
            )
        except Exception:
            return None


class DurablePublicationRuntime:
    def __init__(
        self,
        boundary: FixtureRuntimeBoundary,
        *,
        journal: SQLiteExecutionJournal,
        publisher: FixturePublisherPort,
        workspace_verifier: PublicationWorkspaceVerifier,
        identity_attestor: RuntimeIdentityAttestor,
        kill_switch: RuntimeKillSwitch,
        credential_broker: TrustedCredentialBroker,
        settlement: AuthorityReservationSettlementPort,
        clock: TrustedRuntimeClock,
    ) -> None:
        self._journal = journal
        self._publisher = publisher
        self._workspace = workspace_verifier
        self._identity = identity_attestor
        self._kill_switch = kill_switch
        self._credentials = credential_broker
        self._settlement = settlement
        self._clock = clock
        self._receipt_directory = boundary.require_directory(
            "publication-receipts", create=True
        )
        self._recovery_directory = boundary.require_directory(
            "publication-recovery", create=True
        )

    @staticmethod
    def _credential_scope(
        inputs: PublicationRuntimeInputs, purpose: VerificationPurpose
    ) -> CredentialScope:
        return CredentialScope(
            request_sha256=inputs.publication_intent.intent_sha256,
            plan_sha256=inputs.publish_request.executable_plan_sha256,
            workspace_id=inputs.release_candidate.workspace_id,
            channel_id=inputs.release_candidate.channel_id,
            concept_id=inputs.release_candidate.concept_id,
            episode_id=inputs.release_candidate.episode_id,
            service_identity=inputs.service_identity,
            action_id=inputs.publish_request.action_id,
            adapter_id=inputs.publication_intent.publisher_id,
            variant_id=inputs.publication_intent.publisher_variant_id,
            destination=str(inputs.destination.destination_id),
            purpose=purpose,
        )

    @staticmethod
    def _credential_lease_id(reference: ArtifactReference) -> OpaqueId:
        return OpaqueId(f"credential-ref-{str(reference.sha256)[:24]}")

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise PublicationRuntimeError(
                "runtime.publication.clock", "trusted clock returned a naive time"
            )
        return value

    @staticmethod
    def _material_context(context: GateContext) -> tuple[HashDigest, ...]:
        return (
            context.workflow_definition_sha256,
            context.policy_bundle_sha256,
            context.rules_bundle_sha256,
            context.effective_config_sha256,
            context.current_manifest_sha256,
            context.evidence_graph_sha256,
        )

    @staticmethod
    def _external_id(result: ResultEnvelope) -> OpaqueId | None:
        reference = result.external_reference
        if reference is None:
            return None
        digest = canonical_sha256(
            {"request_id": reference.request_id, "session_id": reference.session_id}
        )
        return OpaqueId(f"external-{str(digest)[:20]}")

    @staticmethod
    def _reservation_sha256(
        verified: object, *, expected_purpose: VerificationPurpose
    ) -> HashDigest:
        receipt = getattr(verified, "receipt", None)
        if (
            getattr(verified, "purpose", None) is not expected_purpose
            or getattr(receipt, "receipt_sha256", None) is None
            or getattr(verified, "request_sha256", None) is None
        ):
            raise PublicationRuntimeError(
                "runtime.publication.authority_receipt",
                "fresh publication authority receipt is invalid",
            )
        return canonical_sha256(
            {
                "artifact_version": "runtime-authority-reservation/1.0",
                "receipt_sha256": str(receipt.receipt_sha256),
                "request_sha256": str(verified.request_sha256),
                "purpose": expected_purpose.value,
            }
        )

    def _current_identity(
        self,
        service_identity: OpaqueId,
        *,
        evaluated_at: datetime,
        expected: ArtifactReference | None = None,
    ) -> ArtifactReference:
        attestation = self._identity.attest_current(
            service_identity, evaluated_at=evaluated_at
        )
        if attestation is None or (expected is not None and attestation != expected):
            raise PublicationRuntimeError(
                "runtime.publication.identity",
                "publisher service identity is not currently attested",
            )
        return attestation

    def _require_switch_clear(self, *, evaluated_at: datetime) -> None:
        if self._kill_switch.engaged(evaluated_at=evaluated_at):
            raise PublicationRuntimeError(
                "runtime.publication.kill_switch", "runtime kill switch is engaged"
            )

    @staticmethod
    def _validate_workspace_verification(
        value: PublicationWorkspaceVerification,
        candidate: ReleaseCandidate,
        *,
        revision_id: OpaqueId,
        evaluated_at: datetime,
    ) -> None:
        expected_refs = FixturePublicationWorkspaceVerifier._content_refs(candidate)
        if (
            value.workspace_id != candidate.workspace_id
            or value.revision_id != revision_id
            or value.observation_sha256 != candidate.workspace_observation_sha256
            or value.manifest_sha256 != candidate.gate_context.current_manifest_sha256
            or value.verified_content_refs != expected_refs
            or value.evaluated_at != evaluated_at.isoformat()
            or str(value.verifier_record.artifact_version)
            != PUBLICATION_WORKSPACE_VERIFICATION_VERSION
        ):
            raise PublicationRuntimeError(
                "runtime.publication.workspace",
                "workspace verifier returned evidence for another release",
            )

    @staticmethod
    def _require_ref(
        reference: ArtifactReference,
        *,
        version: str,
        sha256: HashDigest,
        reason: str,
    ) -> None:
        if (
            str(reference.artifact_version) != version
            or reference.sha256 != sha256
        ):
            raise PublicationRuntimeError(reason, "artifact reference is rebound")

    def _verify_release(
        self,
        inputs: PublicationRuntimeInputs,
        *,
        evaluated_at: datetime,
    ) -> PublicationWorkspaceVerification:
        try:
            validate_release_candidate_structure(inputs.release_candidate)
            validate_release_assessment_structure(inputs.release_assessment)
            validate_destination_binding(inputs.destination)
            verify_release_assessment(
                inputs.release_assessment,
                release_candidate_ref=inputs.release_candidate_ref,
                release_candidate=inputs.release_candidate,
                release_candidate_verification=inputs.release_candidate_verification,
                destination=inputs.destination,
                current_context=inputs.release_candidate.gate_context,
                evaluated_at=evaluated_at,
                authority=inputs.release_assessment_authority,
                authority_ledger=inputs.release_assessment_ledger,
                authority_references=inputs.release_assessment_authority_references,
            )
        except Exception as error:
            raise PublicationRuntimeError(
                "runtime.publication.release_not_current",
                "W05 release candidate and assessment are not current",
            ) from error
        if inputs.release_assessment.status is not ReleaseAssessmentStatus.READY:
            raise PublicationRuntimeError(
                "runtime.publication.not_ready", "W05 release assessment is not ready"
            )
        self._require_ref(
            inputs.release_candidate_ref,
            version=RELEASE_CANDIDATE_VERSION,
            sha256=release_candidate_bytes_sha256(inputs.release_candidate),
            reason="runtime.publication.candidate_ref",
        )
        self._require_ref(
            inputs.release_assessment_ref,
            version=RELEASE_ASSESSMENT_VERSION,
            sha256=release_assessment_bytes_sha256(inputs.release_assessment),
            reason="runtime.publication.assessment_ref",
        )
        self._require_ref(
            inputs.release_candidate.destination_ref,
            version=DESTINATION_BINDING_VERSION,
            sha256=destination_binding_bytes_sha256(inputs.destination),
            reason="runtime.publication.destination_ref",
        )
        verification = self._workspace.verify_current(
            inputs.release_candidate,
            revision_id=inputs.workspace_revision_id,
            evaluated_at=evaluated_at,
        )
        if verification is None:
            raise PublicationRuntimeError(
                "runtime.publication.workspace", "current publication workspace is unverified"
            )
        self._validate_workspace_verification(
            verification,
            inputs.release_candidate,
            revision_id=inputs.workspace_revision_id,
            evaluated_at=evaluated_at,
        )
        return verification

    def _verify_candidate_current(
        self,
        inputs: PublicationRuntimeInputs,
        *,
        evaluated_at: datetime,
    ) -> PublicationWorkspaceVerification:
        try:
            validate_release_candidate_structure(inputs.release_candidate)
            validate_release_assessment_structure(inputs.release_assessment)
            validate_destination_binding(inputs.destination)
            verify_release_candidate(
                inputs.release_candidate,
                inputs.release_candidate_verification,
            )
        except Exception as error:
            raise PublicationRuntimeError(
                "runtime.publication.release_not_current",
                "release candidate is not current during reconciliation",
            ) from error
        if inputs.release_candidate_verification.verified_at != evaluated_at:
            raise PublicationRuntimeError(
                "runtime.publication.release_verification_time",
                "release candidate was not verified at reconciliation time",
            )
        self._require_ref(
            inputs.release_candidate_ref,
            version=RELEASE_CANDIDATE_VERSION,
            sha256=release_candidate_bytes_sha256(inputs.release_candidate),
            reason="runtime.publication.candidate_ref",
        )
        self._require_ref(
            inputs.release_assessment_ref,
            version=RELEASE_ASSESSMENT_VERSION,
            sha256=release_assessment_bytes_sha256(inputs.release_assessment),
            reason="runtime.publication.assessment_ref",
        )
        self._require_ref(
            inputs.release_candidate.destination_ref,
            version=DESTINATION_BINDING_VERSION,
            sha256=destination_binding_bytes_sha256(inputs.destination),
            reason="runtime.publication.destination_ref",
        )
        verification = self._workspace.verify_current(
            inputs.release_candidate,
            revision_id=inputs.workspace_revision_id,
            evaluated_at=evaluated_at,
        )
        if verification is None:
            raise PublicationRuntimeError(
                "runtime.publication.workspace",
                "publication workspace is not current during reconciliation",
            )
        self._validate_workspace_verification(
            verification,
            inputs.release_candidate,
            revision_id=inputs.workspace_revision_id,
            evaluated_at=evaluated_at,
        )
        return verification

    def _require_reconcile_binding(
        self,
        snapshot: JournalSnapshot,
        inputs: PublicationRuntimeInputs,
    ) -> None:
        intent = snapshot.intent
        checks = (
            intent.action_kind is RuntimeActionKind.PUBLICATION,
            intent.action_id == inputs.publish_request.action_id,
            intent.request_id == inputs.publication_intent.request_id,
            intent.request_sha256 == inputs.publication_intent.intent_sha256,
            intent.idempotency_key == inputs.publication_intent.idempotency_key,
            intent.workspace_id == inputs.release_candidate.workspace_id,
            intent.plan_sha256 == inputs.publish_request.executable_plan_sha256,
            intent.gate_context_sha256
            == gate_context_sha256(inputs.publish_request.gate_context),
            intent.workspace_observation_sha256
            == inputs.publication_intent.workspace_observation_sha256,
            intent.authority_decision_sha256
            == inputs.publish_decision.decision_sha256,
            intent.service_identity == inputs.service_identity,
            intent.credential_handle_id
            == self._credential_lease_id(inputs.credential_reference),
            intent.credential_verification is not None,
            intent.destination == str(inputs.destination.destination_id),
            str(inputs.publish_request.action_id) == "ready_for_human_publish",
            str(inputs.publish_request.capability_id) == "publish",
            inputs.publish_request.request_envelope_sha256
            == inputs.publication_intent.intent_sha256,
        )
        if not all(checks):
            raise PublicationRuntimeError(
                "runtime.publication.reconcile_rebound",
                "reconciliation input differs from the durable publication intent",
            )

    def _verify_binding(
        self,
        inputs: PublicationRuntimeInputs,
        workspace: PublicationWorkspaceVerification | None,
    ) -> None:
        try:
            publication_intent_to_mapping(inputs.publication_intent)
            validate_action_authority_request(inputs.publish_request)
            validate_authority_decision(inputs.publish_decision)
            validate_release_candidate_structure(inputs.release_candidate)
            validate_release_assessment_structure(inputs.release_assessment)
            validate_destination_binding(inputs.destination)
        except Exception as error:
            raise PublicationRuntimeError(
                "runtime.publication.contract", "publication input is invalid"
            ) from error
        self._require_ref(
            inputs.release_candidate_ref,
            version=RELEASE_CANDIDATE_VERSION,
            sha256=release_candidate_bytes_sha256(inputs.release_candidate),
            reason="runtime.publication.candidate_ref",
        )
        self._require_ref(
            inputs.release_assessment_ref,
            version=RELEASE_ASSESSMENT_VERSION,
            sha256=release_assessment_bytes_sha256(inputs.release_assessment),
            reason="runtime.publication.assessment_ref",
        )
        self._require_ref(
            inputs.release_candidate.destination_ref,
            version=DESTINATION_BINDING_VERSION,
            sha256=destination_binding_bytes_sha256(inputs.destination),
            reason="runtime.publication.destination_ref",
        )
        intent = inputs.publication_intent
        candidate = inputs.release_candidate
        request = inputs.publish_request
        workspace_observation_sha256 = (
            workspace.observation_sha256
            if workspace is not None
            else candidate.workspace_observation_sha256
        )
        workspace_manifest_sha256 = (
            workspace.manifest_sha256
            if workspace is not None
            else candidate.gate_context.current_manifest_sha256
        )
        expected_refs = publication_scope_references(
            inputs.release_candidate_ref,
            candidate,
            inputs.release_assessment_ref,
        )
        expected_outputs = (
            ("runtime-publications", ("publication-receipt/1.0",)),
        )
        actual_outputs = tuple(
            (item.path_prefix, item.artifact_versions)
            for item in request.scope.allowed_outputs
        )
        intent_checks = (
            intent.request_id == request.request_id,
            intent.idempotency_key == request.idempotency_key,
            intent.release_candidate == inputs.release_candidate_ref,
            intent.release_candidate_sha256 == candidate.candidate_sha256,
            intent.release_assessment == inputs.release_assessment_ref,
            intent.release_assessment_sha256
            == inputs.release_assessment.assessment_sha256,
            intent.destination == candidate.destination_ref,
            intent.destination_sha256 == inputs.destination.destination_sha256,
            intent.workspace_id == candidate.workspace_id,
            intent.channel_id == candidate.channel_id,
            intent.concept_id == candidate.concept_id,
            intent.episode_id == candidate.episode_id,
            intent.gate_context_sha256 == gate_context_sha256(request.gate_context),
            intent.workspace_observation_sha256 == workspace_observation_sha256,
            intent.publisher_id == self._publisher.publisher_id,
            intent.publisher_variant_id == self._publisher.variant_id,
            intent.destination_id == inputs.destination.destination_id,
            intent.cost_minor_units == request.scope.cost_minor_units,
            intent.currency == request.scope.currency,
            intent.retry_index == request.scope.retry_index,
        )
        request_checks = (
            str(request.action_id) == "ready_for_human_publish",
            str(request.capability_id) == "publish",
            request.action_risk is ActionRisk.R3,
            request.authority_requirement is AuthorityRequirement.HUMAN_OR_CAMPAIGN,
            request.side_effect is True,
            request.request_envelope_sha256 == intent.intent_sha256,
            request.scope.workspace_id == candidate.workspace_id,
            request.scope.channel_id == candidate.channel_id,
            request.scope.concept_id == candidate.concept_id,
            request.scope.episode_id == candidate.episode_id,
            request.scope.provider_id == self._publisher.publisher_id,
            request.scope.model_id == self._publisher.variant_id,
            request.scope.destination == str(inputs.destination.destination_id),
            request.scope.candidate_count == 1,
            request.scope.input_artifacts == expected_refs,
            actual_outputs == expected_outputs,
            request.gate_context.current_manifest_sha256
            == workspace_manifest_sha256,
            self._material_context(request.gate_context)
            == self._material_context(candidate.gate_context),
            inputs.publish_decision.action_request_sha256 == request.request_sha256,
            inputs.publish_decision.status is AuthorityDecisionStatus.AUTHORIZED,
        )
        if not all((*intent_checks, *request_checks)):
            raise PublicationRuntimeError(
                "runtime.publication.binding",
                "publication intent, release, and W04 request are not exactly bound",
            )

    @staticmethod
    def _validate_result(
        result: ResultEnvelope,
        inputs: PublicationRuntimeInputs,
    ) -> int | None:
        if str(result.request_id) != str(inputs.publication_intent.request_id):
            raise PublicationRuntimeError(
                "runtime.publication.result_request", "publisher returned another request"
            )
        if result.outputs:
            raise PublicationRuntimeError(
                "runtime.publication.result_outputs",
                "publisher cannot write managed workspace artifacts",
            )
        if result.outcome is Outcome.AWAITING_HUMAN:
            raise PublicationRuntimeError(
                "runtime.publication.result_handoff",
                "runtime publisher cannot return a human handoff",
            )
        if result.outcome is Outcome.SUCCEEDED:
            if result.external_reference is None or result.uncertainty.uncertain:
                raise PublicationRuntimeError(
                    "runtime.publication.result_success",
                    "successful publication lacks a settled external reference",
                )
        elif result.outcome is Outcome.EXTERNAL_UNCERTAIN:
            if not result.uncertainty.uncertain:
                raise PublicationRuntimeError(
                    "runtime.publication.result_uncertain",
                    "uncertain publication lacks uncertainty evidence",
                )
        elif result.outcome is not Outcome.REJECTED:
            raise PublicationRuntimeError(
                "runtime.publication.result_outcome", "publisher outcome is unsupported"
            )
        elif (
            result.uncertainty.uncertain
            or result.uncertainty.reason is not None
            or result.uncertainty.reconcile_evidence is not None
        ):
            raise PublicationRuntimeError(
                "runtime.publication.result_uncertain",
                "rejected publication carries uncertainty",
            )
        if result.external_reference is not None:
            for item in (
                result.external_reference.request_id,
                result.external_reference.session_id,
            ):
                if item is not None and _OPAQUE.fullmatch(item) is None:
                    raise PublicationRuntimeError(
                        "runtime.publication.external_reference",
                        "publisher external reference is not opaque",
                    )
        cost = result.measured_cost
        if cost.is_unknown:
            if cost.amount is not None or cost.unit is not None:
                raise PublicationRuntimeError(
                    "runtime.publication.cost", "unknown cost carries a value"
                )
            return None
        if (
            not isinstance(cost.amount, Decimal)
            or cost.amount < 0
            or cost.amount != cost.amount.to_integral_value()
            or cost.unit != inputs.publication_intent.currency
        ):
            raise PublicationRuntimeError(
                "runtime.publication.cost", "publisher cost is invalid"
            )
        measured = int(cost.amount)
        if measured > inputs.publication_intent.cost_minor_units:
            raise PublicationRuntimeError(
                "runtime.publication.cost_overrun",
                "publisher cost exceeds the reserved limit",
            )
        return measured

    def _persist_publication_receipt(
        self, receipt: PublicationReceipt
    ) -> ArtifactReference:
        mapping = publication_receipt_to_mapping(receipt)
        payload = canonical_json_bytes(mapping)
        digest = HashDigest(hashlib.sha256(payload).hexdigest())
        file_name = f"{receipt.journal_id}-{receipt.receipt_sha256}.json"
        path = self._receipt_directory / file_name
        if path.exists():
            stable = read_stable_regular_file(path)
            if stable.exact_sha256 != digest or stable.byte_length != len(payload):
                raise PublicationRuntimeError(
                    "runtime.publication.receipt_conflict",
                    "publication receipt differs for the same journal",
                )
        else:
            _write_new(path, payload)
        return ArtifactReference(
            RelativeArtifactPath(f"runtime-publications/{file_name}"),
            digest,
            ArtifactVersion("publication-receipt/1.0"),
        )

    def _reconciliation_reference(
        self,
        snapshot: JournalSnapshot,
        *,
        workspace: PublicationWorkspaceVerification,
        authority_receipt_sha256: HashDigest,
        reservation_sha256: HashDigest,
        result: ResultEnvelope,
        completed_at: datetime,
        settled: bool,
        published: bool | None,
        reason_codes: tuple[str, ...],
    ) -> ArtifactReference:
        version = "publication-reconciliation-record/1.0"
        external = result.external_reference
        mapping = {
            "artifact_version": version,
            "journal_id": str(snapshot.journal_id),
            "intent_sha256": str(snapshot.intent.intent_sha256),
            "workspace_observation_sha256": str(workspace.observation_sha256),
            "workspace_verifier_record": {
                "path": str(workspace.verifier_record.path),
                "sha256": str(workspace.verifier_record.sha256),
                "artifact_version": str(workspace.verifier_record.artifact_version),
            },
            "authority_receipt_sha256": str(authority_receipt_sha256),
            "reservation_sha256": str(reservation_sha256),
            "external_reference": (
                {
                    "request_id": external.request_id,
                    "session_id": external.session_id,
                }
                if external is not None
                else None
            ),
            "completed_at": completed_at.isoformat(),
            "settled": settled,
            "published": published,
            "redispatch_performed": False,
            "reason_codes": list(tuple(sorted(set(reason_codes)))),
            "authority_effect": "none",
        }
        payload = canonical_json_bytes(mapping)
        digest = HashDigest(hashlib.sha256(payload).hexdigest())
        file_name = f"{snapshot.journal_id}-{digest}.json"
        path = self._recovery_directory / file_name
        if path.exists():
            stable = read_stable_regular_file(path)
            if stable.exact_sha256 != digest or stable.byte_length != len(payload):
                raise PublicationRuntimeError(
                    "runtime.publication.reconciliation_conflict",
                    "publication reconciliation evidence differs",
                )
        else:
            _write_new(path, payload)
        return ArtifactReference(
            RelativeArtifactPath(f"runtime-publication-recovery/{file_name}"),
            digest,
            ArtifactVersion(version),
        )

    def _load_publication_receipt(
        self, value: JournalSnapshot | ExecutionReceipt
    ) -> PublicationReceipt:
        execution_receipt = value.receipt if isinstance(value, JournalSnapshot) else value
        if execution_receipt is None:
            raise PublicationRuntimeError(
                "runtime.publication.receipt_missing", "journal has no publication receipt"
            )
        refs = tuple(
            item
            for item in execution_receipt.output_refs
            if str(item.artifact_version) == "publication-receipt/1.0"
        )
        if len(refs) != 1:
            raise PublicationRuntimeError(
                "runtime.publication.receipt_set",
                "journal publication receipt reference is missing or ambiguous",
            )
        reference = refs[0]
        path = self._receipt_directory / Path(str(reference.path)).name
        stable, payload = read_stable_regular_bytes(path)
        try:
            mapping = require_json_object(
                parse_json_bytes(payload, source=str(path)),
                source=str(path),
            )
            receipt = publication_receipt_from_mapping(mapping)
        except Exception as error:
            raise PublicationRuntimeError(
                "runtime.publication.receipt_corrupt",
                "durable publication receipt is invalid",
            ) from error
        if stable.exact_sha256 != reference.sha256:
            raise PublicationRuntimeError(
                "runtime.publication.receipt_rebound",
                "durable publication receipt bytes changed",
            )
        return receipt

    def _finish(
        self,
        snapshot: JournalSnapshot,
        inputs: PublicationRuntimeInputs,
        *,
        result: ResultEnvelope,
        before_workspace: PublicationWorkspaceVerification,
        after_workspace: PublicationWorkspaceVerification | None,
        final_state: JournalState,
        status: PublicationStatus,
        published: bool | None,
        authority_receipt_sha256: HashDigest,
        reservation_sha256: HashDigest,
        settlement_record: ArtifactReference | None,
        reconciliation_record: ArtifactReference | None,
        measured_cost_minor_units: int | None,
        completed_at: datetime,
        reason_codes: tuple[str, ...],
    ) -> PublicationReceipt:
        domain: list[PublicationReceipt] = []
        external = result.external_reference

        def receipt_factory(event):
            receipt = build_publication_receipt(
                publication_intent_sha256=inputs.publication_intent.intent_sha256,
                journal_id=snapshot.journal_id,
                journal_head_sha256=event.event_sha256,
                release_candidate=inputs.release_candidate_ref,
                release_assessment=inputs.release_assessment_ref,
                destination=inputs.release_candidate.destination_ref,
                workspace_before_verifier_record=before_workspace.verifier_record,
                workspace_after_verifier_record=(
                    after_workspace.verifier_record
                    if after_workspace is not None
                    else None
                ),
                action_request_sha256=inputs.publish_request.request_sha256,
                authority_decision_sha256=inputs.publish_decision.decision_sha256,
                authority_receipt_sha256=authority_receipt_sha256,
                service_identity=inputs.service_identity,
                external_reference_id=self._external_id(result),
                external_request_id=(
                    external.request_id if external is not None else None
                ),
                external_session_id=(
                    external.session_id if external is not None else None
                ),
                started_at=snapshot.intent.created_at,
                completed_at=completed_at.isoformat(),
                status=status,
                published=published,
                measured_cost_minor_units=measured_cost_minor_units,
                currency=(
                    inputs.publication_intent.currency
                    if measured_cost_minor_units is not None
                    else None
                ),
                settlement_record=settlement_record,
                reconciliation_record=reconciliation_record,
                reason_codes=tuple(sorted(set(reason_codes))),
            )
            reference = self._persist_publication_receipt(receipt)
            domain.append(receipt)
            evidence_refs = {
                (
                    str(item.path),
                    str(item.sha256),
                    str(item.artifact_version),
                ): item
                for item in (
                    reference,
                    before_workspace.verifier_record,
                    *(
                        (after_workspace.verifier_record,)
                        if after_workspace is not None
                        else ()
                    ),
                )
            }
            return build_execution_receipt(
                intent_sha256=snapshot.intent.intent_sha256,
                journal_id=snapshot.journal_id,
                journal_head_sha256=event.event_sha256,
                final_state=final_state,
                started_at=snapshot.intent.created_at,
                completed_at=completed_at.isoformat(),
                output_refs=tuple(
                    evidence_refs[key] for key in sorted(evidence_refs)
                ),
                measured_cost_minor_units=measured_cost_minor_units,
                currency=(
                    inputs.publication_intent.currency
                    if measured_cost_minor_units is not None
                    else None
                ),
                before_workspace_observation_sha256=before_workspace.observation_sha256,
                after_workspace_observation_sha256=(
                    after_workspace.observation_sha256
                    if after_workspace is not None
                    else None
                ),
                authority_receipt_sha256=authority_receipt_sha256,
                settlement_record=settlement_record,
                reconciliation_record=reconciliation_record,
                reason_codes=tuple(sorted(set(reason_codes))),
            )

        self._journal.finish(
            snapshot.journal_id,
            final_state,
            occurred_at=completed_at.isoformat(),
            reason_codes=tuple(sorted(set(reason_codes))),
            external_reference_id=self._external_id(result),
            authority_receipt_sha256=authority_receipt_sha256,
            workspace_observation_sha256=(
                after_workspace.observation_sha256
                if after_workspace is not None
                else before_workspace.observation_sha256
            ),
            reservation_sha256=reservation_sha256,
            receipt_factory=receipt_factory,
        )
        if len(domain) != 1:
            raise PublicationRuntimeError(
                "runtime.publication.receipt_missing",
                "atomic publication finish did not create a receipt",
            )
        return domain[0]

    def publish(self, inputs: PublicationRuntimeInputs) -> PublicationReceipt:
        self._verify_binding(inputs, None)
        existing = self._journal.load_by_scope(
            action_kind=RuntimeActionKind.PUBLICATION,
            action_id=inputs.publish_request.action_id,
            idempotency_key=inputs.publication_intent.idempotency_key,
            request_sha256=inputs.publication_intent.intent_sha256,
        )
        if existing is not None:
            self._require_reconcile_binding(existing, inputs)
            if existing.receipt is not None:
                return self._load_publication_receipt(existing)
            if existing.reconcile_only:
                raise PublicationRuntimeError(
                    "runtime.publication.reconcile_required",
                    "may-have-published work requires reconciliation",
                )
        evaluated_at = self._aware(self._clock.now())
        attestation = self._current_identity(
            inputs.service_identity, evaluated_at=evaluated_at
        )
        initial_credential = require_current_credential_lease(
            self._credentials.verify_current(
                inputs.credential_reference,
                self._credential_scope(inputs, VerificationPurpose.DISPATCH),
                evaluated_at=evaluated_at,
            ),
            inputs.credential_reference,
            self._credential_scope(inputs, VerificationPurpose.DISPATCH),
            evaluated_at=evaluated_at,
        )
        self._require_switch_clear(evaluated_at=evaluated_at)
        workspace = self._verify_release(inputs, evaluated_at=evaluated_at)
        self._verify_binding(inputs, workspace)
        intent = build_execution_intent(
            request_id=inputs.publication_intent.request_id,
            request_sha256=inputs.publication_intent.intent_sha256,
            idempotency_key=inputs.publication_intent.idempotency_key,
            action_kind=RuntimeActionKind.PUBLICATION,
            action_id=inputs.publish_request.action_id,
            planned_effect_ids=(),
            workspace_id=inputs.release_candidate.workspace_id,
            plan_sha256=inputs.publish_request.executable_plan_sha256,
            gate_context_sha256=gate_context_sha256(inputs.publish_request.gate_context),
            workspace_observation_sha256=workspace.observation_sha256,
            authority_decision_sha256=inputs.publish_decision.decision_sha256,
            authority_receipt_sha256=None,
            service_identity=inputs.service_identity,
            service_identity_attestation=attestation,
            credential_handle_id=self._credential_lease_id(
                inputs.credential_reference
            ),
            credential_verification=initial_credential.attestation_record,
            destination=str(inputs.destination.destination_id),
            created_at=inputs.publish_decision.evaluated_at,
        )
        snapshot, created = self._journal.reserve_planned(
            intent, occurred_at=inputs.publish_decision.evaluated_at
        )
        if not created:
            if snapshot.receipt is not None:
                return self._load_publication_receipt(snapshot)
            if snapshot.reconcile_only:
                raise PublicationRuntimeError(
                    "runtime.publication.reconcile_required",
                    "may-have-published work requires reconciliation",
                )
            if snapshot.state not in {
                JournalState.PLANNED,
                JournalState.AUTHORIZED,
                JournalState.RESERVED,
            }:
                raise PublicationRuntimeError(
                    "runtime.publication.terminal_receipt_missing",
                    "terminal publication journal lacks a receipt",
                )
        claim = self._journal.prepare_reservation_claim(
            snapshot.journal_id,
            purpose=VerificationPurpose.DISPATCH,
            effect_id=OpaqueId("publication-dispatch"),
            expected_head_sha256=self._journal.load(
                snapshot.journal_id
            ).events[-1].event_sha256,
            occurred_at=evaluated_at.isoformat(),
        )
        verified = revalidate_authority_for_side_effect(
            inputs.publish_decision,
            inputs.publish_request,
            ledger=ClaimBoundLedgerAdapter(
                inputs.publish_ledger, claim.claim_sha256
            ),
            current_context=inputs.publish_request.gate_context,
            workspace_observation_sha256=str(workspace.observation_sha256),
            adapter_id=str(self._publisher.publisher_id),
            service_identity=str(inputs.service_identity),
            evaluated_at=evaluated_at,
            purpose=VerificationPurpose.DISPATCH,
        )
        reservation_sha = self._reservation_sha256(
            verified, expected_purpose=VerificationPurpose.DISPATCH
        )
        self._journal.record_reservation_result(
            claim,
            authority_receipt_sha256=verified.receipt.receipt_sha256,
            reservation_sha256=reservation_sha,
        )
        marker_at = self._aware(self._clock.now())
        self._current_identity(
            inputs.service_identity,
            evaluated_at=marker_at,
            expected=attestation,
        )
        self._require_switch_clear(evaluated_at=marker_at)
        current_credential = require_current_credential_lease(
            self._credentials.verify_current(
                inputs.credential_reference,
                self._credential_scope(inputs, VerificationPurpose.DISPATCH),
                evaluated_at=marker_at,
            ),
            inputs.credential_reference,
            self._credential_scope(inputs, VerificationPurpose.DISPATCH),
            evaluated_at=marker_at,
        )
        workspace_at_marker = self._workspace.verify_current(
            inputs.release_candidate,
            revision_id=inputs.workspace_revision_id,
            evaluated_at=marker_at,
        )
        if workspace_at_marker is None:
            raise PublicationRuntimeError(
                "runtime.publication.workspace", "publication workspace became stale"
            )
        self._validate_workspace_verification(
            workspace_at_marker,
            inputs.release_candidate,
            revision_id=inputs.workspace_revision_id,
            evaluated_at=marker_at,
        )
        if (
            workspace_at_marker.observation_sha256 != workspace.observation_sha256
            or workspace_at_marker.manifest_sha256 != workspace.manifest_sha256
        ):
            raise PublicationRuntimeError(
                "runtime.publication.workspace_rebound",
                "publication workspace changed before the effect marker",
            )
        current_state = snapshot.state
        if current_state in {JournalState.PLANNED, JournalState.AUTHORIZED}:
            self._journal.append_state(
                snapshot.journal_id,
                JournalState.AUTHORIZED,
                occurred_at=evaluated_at.isoformat(),
                authority_receipt_sha256=verified.receipt.receipt_sha256,
                workspace_observation_sha256=workspace.observation_sha256,
            )
            current_state = JournalState.AUTHORIZED
        if current_state is JournalState.AUTHORIZED:
            self._journal.append_state(
                snapshot.journal_id,
                JournalState.RESERVED,
                occurred_at=marker_at.isoformat(),
                reservation_sha256=reservation_sha,
            )
        elif current_state is JournalState.RESERVED:
            self._journal.append_state(
                snapshot.journal_id,
                JournalState.RESERVED,
                occurred_at=marker_at.isoformat(),
                authority_receipt_sha256=verified.receipt.receipt_sha256,
                workspace_observation_sha256=workspace.observation_sha256,
                reservation_sha256=reservation_sha,
            )
        dispatch_head = self._journal.load(snapshot.journal_id).events[-1].event_sha256
        self._journal.claim_dispatch(
            snapshot.journal_id,
            expected_head_sha256=dispatch_head,
            occurred_at=marker_at.isoformat(),
            authority_receipt_sha256=verified.receipt.receipt_sha256,
            workspace_observation_sha256=workspace.observation_sha256,
            reservation_sha256=reservation_sha,
        )
        effect_at = self._aware(self._clock.now())
        self._current_identity(
            inputs.service_identity,
            evaluated_at=effect_at,
            expected=attestation,
        )
        self._require_switch_clear(evaluated_at=effect_at)
        effect_credential = require_current_credential_lease(
            self._credentials.verify_current(
                inputs.credential_reference,
                self._credential_scope(inputs, VerificationPurpose.DISPATCH),
                evaluated_at=effect_at,
            ),
            inputs.credential_reference,
            self._credential_scope(inputs, VerificationPurpose.DISPATCH),
            evaluated_at=effect_at,
        )
        effect_workspace = self._workspace.verify_current(
            inputs.release_candidate,
            revision_id=inputs.workspace_revision_id,
            evaluated_at=effect_at,
        )
        if effect_workspace is None:
            raise PublicationRuntimeError(
                "runtime.publication.workspace",
                "publication workspace became stale after the dispatch marker",
            )
        self._validate_workspace_verification(
            effect_workspace,
            inputs.release_candidate,
            revision_id=inputs.workspace_revision_id,
            evaluated_at=effect_at,
        )
        if (
            effect_workspace.workspace_id != workspace_at_marker.workspace_id
            or effect_workspace.revision_id != workspace_at_marker.revision_id
            or effect_workspace.observation_sha256
            != workspace_at_marker.observation_sha256
            or effect_workspace.manifest_sha256
            != workspace_at_marker.manifest_sha256
            or effect_workspace.verified_content_refs
            != workspace_at_marker.verified_content_refs
        ):
            raise PublicationRuntimeError(
                "runtime.publication.workspace_rebound",
                "publication workspace changed after the dispatch marker",
            )
        effect_verified = revalidate_authority_for_side_effect(
            inputs.publish_decision,
            inputs.publish_request,
            ledger=ClaimBoundLedgerAdapter(
                inputs.publish_ledger, claim.claim_sha256
            ),
            current_context=inputs.publish_request.gate_context,
            workspace_observation_sha256=str(effect_workspace.observation_sha256),
            adapter_id=str(self._publisher.publisher_id),
            service_identity=str(inputs.service_identity),
            evaluated_at=effect_at,
            purpose=VerificationPurpose.DISPATCH,
        )
        effect_reservation_sha = self._reservation_sha256(
            effect_verified, expected_purpose=VerificationPurpose.DISPATCH
        )
        if (
            effect_verified.receipt.receipt_sha256
            != verified.receipt.receipt_sha256
            or effect_reservation_sha != reservation_sha
        ):
            raise PublicationRuntimeError(
                "runtime.publication.authority_rebound",
                "effect-time authority differs from the durable reservation claim",
            )
        self._journal.record_reservation_result(
            claim,
            authority_receipt_sha256=effect_verified.receipt.receipt_sha256,
            reservation_sha256=effect_reservation_sha,
        )
        try:
            result = self._publisher.publish(
                inputs.release_candidate,
                inputs.destination,
                credential_lease=effect_credential,
                workspace_verification=effect_workspace,
            )
        except ExternalStateUncertain as error:
            result = ResultEnvelope(
                RequestId(str(inputs.publication_intent.request_id)),
                Outcome.EXTERNAL_UNCERTAIN,
                (),
                error.external_reference,
                CostMeasurement(None, None, True),
                UncertaintyEvidence(True, str(error), None),
            )
        except Exception as error:
            result = ResultEnvelope(
                RequestId(str(inputs.publication_intent.request_id)),
                Outcome.EXTERNAL_UNCERTAIN,
                (),
                None,
                CostMeasurement(None, None, True),
                UncertaintyEvidence(True, f"{type(error).__name__}:publish", None),
            )
        after_call_at = self._aware(self._clock.now())
        self._journal.append_state(
            snapshot.journal_id,
            JournalState.DISPATCHED,
            occurred_at=after_call_at.isoformat(),
            external_reference_id=self._external_id(result),
        )
        try:
            measured = self._validate_result(result, inputs)
            workspace_after = self._workspace.verify_current(
                inputs.release_candidate,
                revision_id=inputs.workspace_revision_id,
                evaluated_at=after_call_at,
            )
            if workspace_after is None:
                raise PublicationRuntimeError(
                    "runtime.publication.workspace_after",
                    "publication changed or invalidated managed release bytes",
                )
            self._validate_workspace_verification(
                workspace_after,
                inputs.release_candidate,
                revision_id=inputs.workspace_revision_id,
                evaluated_at=after_call_at,
            )
            if result.outcome is Outcome.SUCCEEDED:
                final_state = JournalState.SUCCEEDED
                status = PublicationStatus.SUCCEEDED
                published: bool | None = True
                reasons: tuple[str, ...] = ()
            elif result.outcome is Outcome.REJECTED:
                final_state = JournalState.FAILED
                status = PublicationStatus.FAILED
                published = False
                reasons = ("runtime.publication.rejected",)
            else:
                final_state = JournalState.UNCERTAIN
                status = PublicationStatus.UNCERTAIN
                published = None
                reasons = ("runtime.publication.external_uncertain",)
        except Exception as error:
            measured = None
            workspace_after = None
            final_state = JournalState.UNCERTAIN
            status = PublicationStatus.UNCERTAIN
            published = None
            reasons = (
                getattr(error, "reason_code", "runtime.publication.result_invalid"),
            )
            result = ResultEnvelope(
                RequestId(str(inputs.publication_intent.request_id)),
                Outcome.EXTERNAL_UNCERTAIN,
                (),
                result.external_reference,
                CostMeasurement(None, None, True),
                UncertaintyEvidence(True, reasons[0], None),
            )
        completed_at = self._aware(self._clock.now())
        settlement_claims = self._journal.unsettled_reservation_claims(
            snapshot.journal_id
        )
        settlement_reservations = tuple(
            result[1]
            for item in settlement_claims
            for result in (self._journal.reservation_result(item),)
            if result is not None
        )
        if len(settlement_reservations) != len(settlement_claims):
            raise PublicationRuntimeError(
                "runtime.publication.settlement_reservation",
                "an unsettled reservation claim lacks its trusted ledger result",
            )
        settlement = self._settlement.finalize_current(
            journal_id=snapshot.journal_id,
            reservation_claim_sha256s=tuple(
                item.claim_sha256 for item in settlement_claims
            ),
            reservation_sha256s=settlement_reservations,
            final_state=final_state,
            evaluated_at=completed_at,
        )
        if settlement is None:
            measured = None
            final_state = JournalState.UNCERTAIN
            status = PublicationStatus.UNCERTAIN
            published = None
            reasons = ("runtime.publication.settlement_missing",)
            result = ResultEnvelope(
                RequestId(str(inputs.publication_intent.request_id)),
                Outcome.EXTERNAL_UNCERTAIN,
                (),
                result.external_reference,
                CostMeasurement(None, None, True),
                UncertaintyEvidence(True, reasons[0], None),
            )
        else:
            self._journal.record_claim_settlement(
                settlement_claims,
                settlement_record=settlement,
                final_state=final_state,
            )
        return self._finish(
            snapshot,
            inputs,
            result=result,
            before_workspace=workspace,
            after_workspace=workspace_after,
            final_state=final_state,
            status=status,
            published=published,
            authority_receipt_sha256=verified.receipt.receipt_sha256,
            reservation_sha256=reservation_sha,
            settlement_record=settlement,
            reconciliation_record=None,
            measured_cost_minor_units=measured,
            completed_at=completed_at,
            reason_codes=reasons,
        )

    def reconcile_pending(
        self,
        journal_id: OpaqueId,
        inputs: PublicationRuntimeInputs,
    ) -> PublicationReceipt:
        """Reconcile may-have-published work and never invoke publish again."""

        snapshot = self._journal.load(journal_id)
        if not snapshot.reconcile_only:
            raise PublicationRuntimeError(
                "runtime.publication.reconcile_state",
                "only may-have-published work can be reconciled",
            )
        self._require_reconcile_binding(snapshot, inputs)
        prior: PublicationReceipt | None = None
        if snapshot.receipt is not None:
            prior = self._load_publication_receipt(snapshot)
        else:
            history = self._journal.receipt_history(snapshot.journal_id)
            if history:
                prior = self._load_publication_receipt(history[-1])
        external_reference = (
            ExternalReference(prior.external_request_id, prior.external_session_id)
            if prior is not None and prior.external_reference_id is not None
            else None
        )
        evaluated_at = self._aware(self._clock.now())
        attestation = self._current_identity(
            inputs.service_identity, evaluated_at=evaluated_at
        )
        self._require_switch_clear(evaluated_at=evaluated_at)
        workspace = self._verify_candidate_current(
            inputs, evaluated_at=evaluated_at
        )
        self._verify_binding(inputs, workspace)
        claim = self._journal.prepare_reservation_claim(
            snapshot.journal_id,
            purpose=VerificationPurpose.RECONCILE,
            effect_id=OpaqueId(
                "publication-reconcile-"
                + str(
                    sum(
                        event.state is JournalState.RECONCILING
                        for event in snapshot.events
                    )
                )
            ),
            expected_head_sha256=snapshot.events[-1].event_sha256,
            occurred_at=evaluated_at.isoformat(),
        )
        verified = revalidate_authority_for_side_effect(
            inputs.publish_decision,
            inputs.publish_request,
            ledger=ClaimBoundLedgerAdapter(
                inputs.publish_ledger, claim.claim_sha256
            ),
            current_context=inputs.publish_request.gate_context,
            workspace_observation_sha256=str(workspace.observation_sha256),
            adapter_id=str(self._publisher.publisher_id),
            service_identity=str(inputs.service_identity),
            evaluated_at=evaluated_at,
            purpose=VerificationPurpose.RECONCILE,
        )
        reservation_sha = self._reservation_sha256(
            verified, expected_purpose=VerificationPurpose.RECONCILE
        )
        self._journal.record_reservation_result(
            claim,
            authority_receipt_sha256=verified.receipt.receipt_sha256,
            reservation_sha256=reservation_sha,
        )
        marker_at = self._aware(self._clock.now())
        self._current_identity(
            inputs.service_identity,
            evaluated_at=marker_at,
            expected=attestation,
        )
        self._require_switch_clear(evaluated_at=marker_at)
        current_credential = require_current_credential_lease(
            self._credentials.verify_current(
                inputs.credential_reference,
                self._credential_scope(inputs, VerificationPurpose.RECONCILE),
                evaluated_at=marker_at,
            ),
            inputs.credential_reference,
            self._credential_scope(inputs, VerificationPurpose.RECONCILE),
            evaluated_at=marker_at,
        )
        workspace_at_marker = self._workspace.verify_current(
            inputs.release_candidate,
            revision_id=inputs.workspace_revision_id,
            evaluated_at=marker_at,
        )
        if workspace_at_marker is None:
            raise PublicationRuntimeError(
                "runtime.publication.workspace", "reconciliation workspace became stale"
            )
        self._validate_workspace_verification(
            workspace_at_marker,
            inputs.release_candidate,
            revision_id=inputs.workspace_revision_id,
            evaluated_at=marker_at,
        )
        if workspace_at_marker.observation_sha256 != workspace.observation_sha256:
            raise PublicationRuntimeError(
                "runtime.publication.workspace_rebound",
                "publication workspace changed before reconciliation",
            )
        self._journal.append_state(
            snapshot.journal_id,
            JournalState.RECONCILING,
            occurred_at=marker_at.isoformat(),
            reason_codes=("runtime.publication.reconcile_started",),
            external_reference_id=(
                prior.external_reference_id if prior is not None else None
            ),
            authority_receipt_sha256=verified.receipt.receipt_sha256,
            workspace_observation_sha256=workspace.observation_sha256,
            reservation_sha256=reservation_sha,
        )
        effect_at = self._aware(self._clock.now())
        self._current_identity(
            inputs.service_identity,
            evaluated_at=effect_at,
            expected=attestation,
        )
        self._require_switch_clear(evaluated_at=effect_at)
        effect_credential = require_current_credential_lease(
            self._credentials.verify_current(
                inputs.credential_reference,
                self._credential_scope(inputs, VerificationPurpose.RECONCILE),
                evaluated_at=effect_at,
            ),
            inputs.credential_reference,
            self._credential_scope(inputs, VerificationPurpose.RECONCILE),
            evaluated_at=effect_at,
        )
        effect_workspace = self._workspace.verify_current(
            inputs.release_candidate,
            revision_id=inputs.workspace_revision_id,
            evaluated_at=effect_at,
        )
        if effect_workspace is None:
            raise PublicationRuntimeError(
                "runtime.publication.workspace",
                "reconciliation workspace became stale after its marker",
            )
        self._validate_workspace_verification(
            effect_workspace,
            inputs.release_candidate,
            revision_id=inputs.workspace_revision_id,
            evaluated_at=effect_at,
        )
        if (
            effect_workspace.workspace_id != workspace_at_marker.workspace_id
            or effect_workspace.revision_id != workspace_at_marker.revision_id
            or effect_workspace.observation_sha256
            != workspace_at_marker.observation_sha256
            or effect_workspace.manifest_sha256
            != workspace_at_marker.manifest_sha256
            or effect_workspace.verified_content_refs
            != workspace_at_marker.verified_content_refs
        ):
            raise PublicationRuntimeError(
                "runtime.publication.workspace_rebound",
                "publication workspace changed after the reconciliation marker",
            )
        effect_verified = revalidate_authority_for_side_effect(
            inputs.publish_decision,
            inputs.publish_request,
            ledger=ClaimBoundLedgerAdapter(
                inputs.publish_ledger, claim.claim_sha256
            ),
            current_context=inputs.publish_request.gate_context,
            workspace_observation_sha256=str(effect_workspace.observation_sha256),
            adapter_id=str(self._publisher.publisher_id),
            service_identity=str(inputs.service_identity),
            evaluated_at=effect_at,
            purpose=VerificationPurpose.RECONCILE,
        )
        effect_reservation_sha = self._reservation_sha256(
            effect_verified, expected_purpose=VerificationPurpose.RECONCILE
        )
        if (
            effect_verified.receipt.receipt_sha256
            != verified.receipt.receipt_sha256
            or effect_reservation_sha != reservation_sha
        ):
            raise PublicationRuntimeError(
                "runtime.publication.authority_rebound",
                "reconcile-time authority differs from the durable reservation claim",
            )
        self._journal.record_reservation_result(
            claim,
            authority_receipt_sha256=effect_verified.receipt.receipt_sha256,
            reservation_sha256=effect_reservation_sha,
        )
        try:
            result = self._publisher.reconcile(
                inputs.release_candidate,
                inputs.destination,
                external_reference,
                credential_lease=effect_credential,
                workspace_verification=effect_workspace,
            )
        except ExternalStateUncertain as error:
            result = ResultEnvelope(
                RequestId(str(inputs.publication_intent.request_id)),
                Outcome.EXTERNAL_UNCERTAIN,
                (),
                error.external_reference or external_reference,
                CostMeasurement(None, None, True),
                UncertaintyEvidence(True, str(error), None),
            )
        except Exception as error:
            result = ResultEnvelope(
                RequestId(str(inputs.publication_intent.request_id)),
                Outcome.EXTERNAL_UNCERTAIN,
                (),
                external_reference,
                CostMeasurement(None, None, True),
                UncertaintyEvidence(True, f"{type(error).__name__}:reconcile", None),
            )
        after_call_at = self._aware(self._clock.now())
        try:
            measured = self._validate_result(result, inputs)
            workspace_after = self._workspace.verify_current(
                inputs.release_candidate,
                revision_id=inputs.workspace_revision_id,
                evaluated_at=after_call_at,
            )
            if workspace_after is None:
                raise PublicationRuntimeError(
                    "runtime.publication.workspace_after",
                    "publication workspace became stale during reconciliation",
                )
            self._validate_workspace_verification(
                workspace_after,
                inputs.release_candidate,
                revision_id=inputs.workspace_revision_id,
                evaluated_at=after_call_at,
            )
            if result.outcome is Outcome.SUCCEEDED:
                settled = True
                published: bool | None = True
                reasons = ("runtime.publication.reconciled_published",)
            elif result.outcome is Outcome.REJECTED:
                settled = True
                published = False
                reasons = ("runtime.publication.reconciled_not_published",)
            else:
                settled = False
                published = None
                reasons = ("runtime.publication.reconcile_uncertain",)
        except Exception as error:
            measured = None
            workspace_after = None
            settled = False
            published = None
            reasons = (
                getattr(error, "reason_code", "runtime.publication.reconcile_invalid"),
            )
            result = ResultEnvelope(
                RequestId(str(inputs.publication_intent.request_id)),
                Outcome.EXTERNAL_UNCERTAIN,
                (),
                external_reference,
                CostMeasurement(None, None, True),
                UncertaintyEvidence(True, reasons[0], None),
            )
        final_state = (
            JournalState.RECONCILED if settled else JournalState.UNCERTAIN
        )
        status = (
            PublicationStatus.RECONCILED
            if settled
            else PublicationStatus.UNCERTAIN
        )
        settlement_claims = self._journal.unsettled_reservation_claims(
            snapshot.journal_id
        )
        settlement_reservations = tuple(
            result[1]
            for item in settlement_claims
            for result in (self._journal.reservation_result(item),)
            if result is not None
        )
        if len(settlement_reservations) != len(settlement_claims):
            raise PublicationRuntimeError(
                "runtime.publication.reconcile_reservation",
                "an unsettled reservation claim lacks its trusted ledger result",
            )
        settlement = self._settlement.finalize_current(
            journal_id=snapshot.journal_id,
            reservation_claim_sha256s=tuple(
                item.claim_sha256 for item in settlement_claims
            ),
            reservation_sha256s=settlement_reservations,
            final_state=final_state,
            evaluated_at=after_call_at,
        )
        if settlement is None:
            measured = None
            settled = False
            published = None
            final_state = JournalState.UNCERTAIN
            status = PublicationStatus.UNCERTAIN
            reasons = ("runtime.publication.reconcile_settlement_missing",)
            result = ResultEnvelope(
                RequestId(str(inputs.publication_intent.request_id)),
                Outcome.EXTERNAL_UNCERTAIN,
                (),
                external_reference,
                CostMeasurement(None, None, True),
                UncertaintyEvidence(True, reasons[0], None),
            )
        else:
            self._journal.record_claim_settlement(
                settlement_claims,
                settlement_record=settlement,
                final_state=final_state,
            )
        reconciliation = (
            self._reconciliation_reference(
                snapshot,
                workspace=workspace_after,
                authority_receipt_sha256=verified.receipt.receipt_sha256,
                reservation_sha256=reservation_sha,
                result=result,
                completed_at=after_call_at,
                settled=True,
                published=published,
                reason_codes=reasons,
            )
            if settled
            else None
        )
        return self._finish(
            snapshot,
            inputs,
            result=result,
            before_workspace=workspace,
            after_workspace=workspace_after,
            final_state=final_state,
            status=status,
            published=published,
            authority_receipt_sha256=verified.receipt.receipt_sha256,
            reservation_sha256=reservation_sha,
            settlement_record=settlement,
            reconciliation_record=reconciliation,
            measured_cost_minor_units=measured,
            completed_at=after_call_at,
            reason_codes=reasons,
        )


__all__ = [
    "DurablePublicationRuntime",
    "FixturePublicationWorkspaceVerifier",
    "FixturePublisherPort",
    "PUBLICATION_WORKSPACE_VERIFICATION_VERSION",
    "PublicationRuntimeError",
    "PublicationRuntimeInputs",
    "PublicationWorkspaceVerification",
    "PublicationWorkspaceVerifier",
    "publication_scope_references",
    "release_assessment_bytes_sha256",
]
