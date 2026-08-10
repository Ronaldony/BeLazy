from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from video_factory.approvals import gate_context_sha256
from video_factory.authority import (
    AuthorityDecisionStatus,
    AuthoritySource,
    OutputScope,
    VerificationPurpose,
    build_action_authority_request,
    evaluate_authority,
    target_policy_bundle,
)
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.providers import (
    CostMeasurement,
    ExternalReference,
    Outcome,
    ResultEnvelope,
    UncertaintyEvidence,
)
from video_factory.release import (
    RELEASE_ASSESSMENT_VERSION,
    ReleaseAssessmentStatus,
    assess_release_candidate,
)
from video_factory.runtime import (
    JournalState,
    PublicationStatus,
    build_publication_intent,
)
from video_factory_runtime import (
    FixtureCredentialBroker,
    FixtureCredentialRegistration,
    FixtureRuntimeBoundary,
    OpaqueCredentialHandle,
    SQLiteExecutionJournal,
    credential_scope_sha256,
)
from video_factory_runtime.publisher import (
    DurablePublicationRuntime,
    PublicationRuntimeError,
    PublicationRuntimeInputs,
    PublicationWorkspaceVerification,
    publication_scope_references,
    release_assessment_bytes_sha256,
)
from tests.unit.test_authority_control import FakeLedger, _request
from tests.unit.test_release_control import _authority, _release_fixture


NOW = datetime(2026, 8, 8, tzinfo=UTC)
SERVICE = OpaqueId("publisher-service")


class ClaimBoundFakeLedger(FakeLedger):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.runtime_claims = {}

    def revalidate_and_reserve_current(
        self, decision, request, *, runtime_claim_sha256, **kwargs
    ):
        previous = self.runtime_claims.get(runtime_claim_sha256)
        if previous is not None:
            return previous
        receipt = super().revalidate_and_reserve_current(
            decision, request, **kwargs
        )
        if receipt is not None:
            self.runtime_claims[runtime_claim_sha256] = receipt
        return receipt


class Clock:
    def now(self):
        return NOW


class Identity:
    reference = ArtifactReference(
        RelativeArtifactPath("identity/publisher-service.json"),
        HashDigest("a" * 64),
        ArtifactVersion("service-attestation/1.0"),
    )

    def attest_current(self, service_identity, *, evaluated_at):
        assert service_identity == SERVICE
        return self.reference


class KillSwitch:
    engaged_value = False

    def engaged(self, *, evaluated_at):
        return self.engaged_value


class Settlement:
    def __init__(self):
        self.calls = 0

    def finalize_current(
        self,
        *,
        journal_id,
        reservation_claim_sha256s,
        reservation_sha256s,
        final_state,
        evaluated_at,
    ):
        self.calls += 1
        return ArtifactReference(
            RelativeArtifactPath(f"settlement/{journal_id}-{self.calls}.json"),
            HashDigest(format(self.calls, "x") * 64),
            ArtifactVersion("authority-settlement/1.0"),
        )


class WorkspaceVerifier:
    def __init__(self):
        self.calls = 0
        self.deny_on_call = None

    def verify_current(self, candidate, *, revision_id, evaluated_at):
        self.calls += 1
        if self.calls == self.deny_on_call:
            return None
        refs = tuple(
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
        return PublicationWorkspaceVerification(
            workspace_id=candidate.workspace_id,
            revision_id=revision_id,
            observation_sha256=candidate.workspace_observation_sha256,
            manifest_sha256=candidate.gate_context.current_manifest_sha256,
            verified_content_refs=refs,
            verifier_record=ArtifactReference(
                RelativeArtifactPath(
                    f"runtime-publication-verifications/{self.calls}.json"
                ),
                HashDigest(format(self.calls, "x") * 64),
                ArtifactVersion("publication-workspace-verification/1.0"),
            ),
            evaluated_at=evaluated_at.isoformat(),
        )


class Publisher:
    publisher_id = OpaqueId("fixture-publisher")
    variant_id = OpaqueId("fixture-publisher-v1")

    def __init__(self, *, behavior="success"):
        self.behavior = behavior
        self.calls = 0
        self.reconcile_calls = 0
        self.credentials = []
        self.workspace_verifications = []

    def publish(
        self,
        candidate,
        destination,
        *,
        credential_lease,
        workspace_verification,
    ):
        self.calls += 1
        self.credentials.append(credential_lease.handle)
        self.workspace_verifications.append(workspace_verification)
        if self.behavior == "crash":
            raise KeyboardInterrupt("simulated publisher process death")
        if self.behavior == "timeout":
            raise TimeoutError("fixture publisher timeout")
        return ResultEnvelope(
            request_id="request-a",
            outcome=Outcome.SUCCEEDED,
            outputs=(),
            external_reference=ExternalReference("upload-a", "session-a"),
            measured_cost=CostMeasurement(Decimal("1"), "USD", False),
            uncertainty=UncertaintyEvidence(False, None, None),
        )

    def reconcile(
        self,
        candidate,
        destination,
        external_reference,
        *,
        credential_lease,
        workspace_verification,
    ):
        self.reconcile_calls += 1
        self.credentials.append(credential_lease.handle)
        self.workspace_verifications.append(workspace_verification)
        if self.behavior == "reconcile-crash":
            raise KeyboardInterrupt("simulated reconciliation process death")
        if self.behavior == "reconcile-uncertain":
            return ResultEnvelope(
                request_id="request-a",
                outcome=Outcome.EXTERNAL_UNCERTAIN,
                outputs=(),
                external_reference=external_reference,
                measured_cost=CostMeasurement(None, None, True),
                uncertainty=UncertaintyEvidence(True, "still pending", None),
            )
        if self.behavior != "reconcile-success":
            raise AssertionError("unexpected publisher reconciliation")
        return ResultEnvelope(
            request_id="request-a",
            outcome=Outcome.SUCCEEDED,
            outputs=(),
            external_reference=external_reference
            or ExternalReference("upload-a", "session-a"),
            measured_cost=CostMeasurement(Decimal("1"), "USD", False),
            uncertainty=UncertaintyEvidence(False, None, None),
        )


def _setup(tmp_path, *, behavior="success"):
    candidate, candidate_ref, destination, verification = _release_fixture()
    assessment_authority, assessment_ledger, assessment_refs = _authority(
        candidate,
        candidate_ref,
        destination,
        granted=True,
    )
    assessment = assess_release_candidate(
        release_candidate_ref=candidate_ref,
        release_candidate=candidate,
        release_candidate_verification=verification,
        destination=destination,
        current_context=candidate.gate_context,
        evaluated_at=NOW,
        authority=assessment_authority,
        authority_ledger=assessment_ledger,
        authority_references=assessment_refs,
    )
    assert assessment.status is ReleaseAssessmentStatus.READY
    assessment_ref = ArtifactReference(
        RelativeArtifactPath("release/release-assessment.json"),
        release_assessment_bytes_sha256(assessment),
        ArtifactVersion(RELEASE_ASSESSMENT_VERSION),
    )
    publisher = Publisher(behavior=behavior)
    base = _request("ready_for_human_publish")
    scope_refs = publication_scope_references(
        candidate_ref, candidate, assessment_ref
    )
    scope = replace(
        base.scope,
        workspace_id=candidate.workspace_id,
        channel_id=candidate.channel_id,
        concept_id=candidate.concept_id,
        episode_id=candidate.episode_id,
        provider_id=publisher.publisher_id,
        model_id=publisher.variant_id,
        destination=str(destination.destination_id),
        cost_minor_units=5,
        currency="USD",
        candidate_count=1,
        retry_index=0,
        input_artifacts=scope_refs,
        allowed_outputs=(
            OutputScope("runtime-publications", ("publication-receipt/1.0",)),
        ),
    )
    intent = build_publication_intent(
        request_id=base.request_id,
        idempotency_key=base.idempotency_key,
        release_candidate=candidate_ref,
        release_candidate_sha256=candidate.candidate_sha256,
        release_assessment=assessment_ref,
        release_assessment_sha256=assessment.assessment_sha256,
        destination=candidate.destination_ref,
        destination_sha256=destination.destination_sha256,
        workspace_id=candidate.workspace_id,
        channel_id=candidate.channel_id,
        concept_id=candidate.concept_id,
        episode_id=candidate.episode_id,
        gate_context_sha256=gate_context_sha256(base.gate_context),
        workspace_observation_sha256=candidate.workspace_observation_sha256,
        publisher_id=publisher.publisher_id,
        publisher_variant_id=publisher.variant_id,
        destination_id=destination.destination_id,
        cost_minor_units=5,
        currency="USD",
        retry_index=0,
        created_at=NOW.isoformat(),
    )
    publish_request = build_action_authority_request(
        request_id=str(base.request_id),
        request_envelope_sha256=str(intent.intent_sha256),
        idempotency_key=str(base.idempotency_key),
        requester_principal_id=str(base.requester_principal_id),
        plan=base.plan,
        workflow_evaluation=base.workflow_evaluation,
        workflow_evaluation_predecessors=base.workflow_evaluation_predecessors,
        profiles=base.profiles,
        scope=scope,
        hard_escalation_facts=base.hard_escalation_facts,
    )
    publish_ledger = ClaimBoundFakeLedger(
        AuthoritySource.ONE_SHOT_HUMAN, ("publisher-human",)
    )
    publish_refs = (
        ArtifactReference(
            RelativeArtifactPath("ledger/publication-approval.json"),
            HashDigest("b" * 64),
            ArtifactVersion("human-approval/1.0"),
        ),
    )
    risk, decision = evaluate_authority(
        publish_request,
        target_policy_bundle(),
        ledger=publish_ledger,
        authority_references=publish_refs,
        evaluated_at=NOW,
    )
    assert risk.effective_risk.value == "R3"
    assert decision.status is AuthorityDecisionStatus.AUTHORIZED
    inputs = PublicationRuntimeInputs(
        publication_intent=intent,
        release_candidate_ref=candidate_ref,
        release_candidate=candidate,
        release_candidate_verification=verification,
        release_assessment_ref=assessment_ref,
        release_assessment=assessment,
        release_assessment_authority=assessment_authority,
        release_assessment_ledger=assessment_ledger,
        release_assessment_authority_references=assessment_refs,
        destination=destination,
        publish_request=publish_request,
        publish_decision=decision,
        publish_ledger=publish_ledger,
        workspace_revision_id=OpaqueId("revision-publish"),
        service_identity=SERVICE,
        credential_reference=ArtifactReference(
            RelativeArtifactPath("credentials/publisher.json"),
            HashDigest("e" * 64),
            ArtifactVersion("credential-reference/1.0"),
        ),
    )
    boundary = FixtureRuntimeBoundary.initialize(
        tmp_path / "runtime", runtime_id="publication-test", fixture_only=True
    )
    journal = SQLiteExecutionJournal(boundary)
    workspace = WorkspaceVerifier()
    settlement = Settlement()
    credential_scopes = tuple(
        sorted(
            {
                credential_scope_sha256(
                    DurablePublicationRuntime._credential_scope(inputs, purpose)
                )
                for purpose in (
                    VerificationPurpose.DISPATCH,
                    VerificationPurpose.RECONCILE,
                )
            }
        )
    )
    broker = FixtureCredentialBroker(
        boundary,
        (
            FixtureCredentialRegistration(
                inputs.credential_reference,
                credential_scopes,
                NOW - timedelta(seconds=1),
                NOW + timedelta(days=1),
                1,
            ),
        ),
    )
    runtime = DurablePublicationRuntime(
        boundary,
        journal=journal,
        publisher=publisher,
        workspace_verifier=workspace,
        identity_attestor=Identity(),
        kill_switch=KillSwitch(),
        credential_broker=broker,
        settlement=settlement,
        clock=Clock(),
    )
    return runtime, journal, publisher, workspace, settlement, inputs


def test_ready_release_requires_separate_r3_publish_authority_and_durable_replay(
    tmp_path,
) -> None:
    runtime, journal, publisher, workspace, settlement, inputs = _setup(tmp_path)

    first = runtime.publish(inputs)
    second = runtime.publish(inputs)

    assert first == second
    assert first.status is PublicationStatus.SUCCEEDED
    assert first.published is True
    assert first.authority_effect == "none"
    assert publisher.calls == 1
    assert len(publisher.credentials) == 1
    assert isinstance(publisher.credentials[0], OpaqueCredentialHandle)
    assert publisher.workspace_verifications[0].verified_content_refs
    assert settlement.calls == 1
    assert journal.unresolved() == ()


def test_terminal_publication_replay_does_not_reauthorize_or_republish(tmp_path) -> None:
    runtime, journal, publisher, workspace, settlement, inputs = _setup(tmp_path)
    first = runtime.publish(inputs)
    workspace_calls = workspace.calls
    inputs.publish_ledger.deny = True

    class NoCurrentIdentity:
        def attest_current(self, service_identity, *, evaluated_at):
            raise AssertionError("terminal replay must not re-attest identity")

    class EngagedKillSwitch:
        def engaged(self, *, evaluated_at):
            raise AssertionError("terminal replay must not consult the kill switch")

    class StaleWorkspace:
        def verify_current(self, candidate, *, revision_id, evaluated_at):
            raise AssertionError("terminal replay must not re-read workspace bytes")

    runtime._identity = NoCurrentIdentity()
    runtime._kill_switch = EngagedKillSwitch()
    runtime._workspace = StaleWorkspace()

    replay = runtime.publish(inputs)

    assert replay == first
    assert publisher.calls == 1
    assert workspace.calls == workspace_calls
    assert settlement.calls == 1
    assert journal.unresolved() == ()


def test_post_publication_workspace_failure_is_durable_uncertain(tmp_path) -> None:
    runtime, journal, publisher, workspace, settlement, inputs = _setup(tmp_path)
    workspace.deny_on_call = 4

    receipt = runtime.publish(inputs)

    assert receipt.status is PublicationStatus.UNCERTAIN
    assert receipt.published is None
    assert receipt.workspace_before_verifier_record is not None
    assert receipt.workspace_after_verifier_record is None
    snapshot = journal.unresolved()[0]
    assert snapshot.receipt is not None
    assert snapshot.receipt.after_workspace_observation_sha256 is None
    assert publisher.calls == 1


def test_publish_fails_closed_when_fresh_w04_ledger_denies(
    tmp_path,
) -> None:
    runtime, journal, publisher, workspace, settlement, inputs = _setup(tmp_path)
    inputs.publish_ledger.deny = True

    with pytest.raises(ValueError):
        runtime.publish(inputs)

    assert publisher.calls == 0
    assert settlement.calls == 0
    assert journal.unresolved() == ()


def test_timeout_is_durable_uncertain_and_never_blindly_republished(tmp_path) -> None:
    runtime, journal, publisher, workspace, settlement, inputs = _setup(
        tmp_path, behavior="timeout"
    )

    first = runtime.publish(inputs)
    second = runtime.publish(inputs)

    assert first.status is PublicationStatus.UNCERTAIN
    assert first.published is None
    assert second == first
    assert publisher.calls == 1
    assert len(journal.unresolved()) == 1


def test_scope_rebound_is_rejected_before_publisher_call(tmp_path) -> None:
    runtime, journal, publisher, workspace, settlement, inputs = _setup(tmp_path)
    rebound = replace(
        inputs,
        publication_intent=replace(
            inputs.publication_intent,
            destination_id=OpaqueId("destination-rebound"),
        ),
    )

    with pytest.raises(PublicationRuntimeError):
        runtime.publish(rebound)

    assert publisher.calls == 0


def test_process_death_after_publication_marker_requires_reconciliation(
    tmp_path,
) -> None:
    runtime, journal, publisher, workspace, settlement, inputs = _setup(
        tmp_path, behavior="crash"
    )

    with pytest.raises(KeyboardInterrupt):
        runtime.publish(inputs)

    snapshot = journal.unresolved()[0]
    assert snapshot.state is JournalState.DISPATCHING
    assert len(journal.unsettled_reservation_claims()) == 1
    with pytest.raises(PublicationRuntimeError) as caught:
        runtime.publish(inputs)
    assert caught.value.reason_code == "runtime.publication.reconcile_required"
    assert publisher.calls == 1
    publisher.behavior = "reconcile-success"
    reconciled = runtime.reconcile_pending(snapshot.journal_id, inputs)
    assert reconciled.status is PublicationStatus.RECONCILED
    assert journal.unsettled_reservation_claims() == ()


def test_uncertain_publication_reconciles_without_republishing(tmp_path) -> None:
    runtime, journal, publisher, workspace, settlement, inputs = _setup(
        tmp_path, behavior="timeout"
    )
    initial = runtime.publish(inputs)
    snapshot = journal.unresolved()[0]
    publisher.behavior = "reconcile-success"

    reconciled = runtime.reconcile_pending(snapshot.journal_id, inputs)
    replay = runtime.publish(inputs)

    assert initial.status is PublicationStatus.UNCERTAIN
    assert reconciled.status is PublicationStatus.RECONCILED
    assert reconciled.published is True
    assert reconciled.reconciliation_record is not None
    assert replay == reconciled
    assert publisher.calls == 1
    assert publisher.reconcile_calls == 1
    assert journal.load(snapshot.journal_id).state is JournalState.RECONCILED
    assert journal.unresolved() == ()
    assert journal.unsettled_reservation_claims() == ()


def test_process_death_during_reconcile_preserves_history_and_retries_observation(
    tmp_path,
) -> None:
    runtime, journal, publisher, workspace, settlement, inputs = _setup(
        tmp_path, behavior="timeout"
    )
    runtime.publish(inputs)
    snapshot = journal.unresolved()[0]
    publisher.behavior = "reconcile-crash"

    with pytest.raises(KeyboardInterrupt):
        runtime.reconcile_pending(snapshot.journal_id, inputs)

    interrupted = journal.load(snapshot.journal_id)
    assert interrupted.state is JournalState.RECONCILING
    assert interrupted.receipt is None
    assert len(journal.receipt_history(snapshot.journal_id)) == 1
    publisher.behavior = "reconcile-success"
    reconciled = runtime.reconcile_pending(snapshot.journal_id, inputs)
    assert reconciled.status is PublicationStatus.RECONCILED
    assert publisher.calls == 1
    assert publisher.reconcile_calls == 2
