from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json

import pytest

from video_factory.artifacts import validate_artifact_mapping
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    IdempotencyKey,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.runtime import (
    JournalState,
    MigrationMode,
    PublicationStatus,
    RuntimeActionKind,
    RuntimeContractError,
    build_execution_intent,
    build_execution_journal_event,
    build_execution_receipt,
    build_migration_cutover_state,
    build_publication_intent,
    build_publication_receipt,
    build_projection_parity_receipt,
    runtime_artifact_from_bytes,
    runtime_artifact_to_bytes,
)


NOW = datetime(2026, 8, 10, 0, 0, tzinfo=UTC).isoformat()


def _sha(character: str) -> HashDigest:
    return HashDigest(character * 64)


def _ref(name: str, version: str = "runtime-test-evidence/1.0") -> ArtifactReference:
    return ArtifactReference(
        RelativeArtifactPath(f"runtime-test/{name}.json"),
        HashDigest(hashlib.sha256(name.encode()).hexdigest()),
        ArtifactVersion(version),
    )


def _artifacts():
    execution_intent = build_execution_intent(
        request_id=OpaqueId("request-runtime-test"),
        request_sha256=_sha("a"),
        idempotency_key=IdempotencyKey("runtime-test-key"),
        action_kind=RuntimeActionKind.EXECUTOR,
        action_id=OpaqueId("fixture-task"),
        workspace_id=OpaqueId("workspace-runtime-test"),
        plan_sha256=_sha("b"),
        gate_context_sha256=_sha("c"),
        workspace_observation_sha256=_sha("d"),
        authority_decision_sha256=_sha("e"),
        authority_receipt_sha256=_sha("f"),
        service_identity=OpaqueId("fixture-runtime-service"),
        service_identity_attestation=_ref("a", "runtime-identity-attestation/1.0"),
        credential_handle_id=None,
        credential_verification=None,
        destination=None,
        created_at=NOW,
    )
    event = build_execution_journal_event(
        journal_id=OpaqueId("journal-runtime-test"),
        sequence=0,
        previous_event_sha256=None,
        intent_sha256=execution_intent.intent_sha256,
        authority_receipt_sha256=None,
        workspace_observation_sha256=None,
        reservation_sha256=None,
        state=JournalState.PLANNED,
        operation_id=None,
        occurred_at=NOW,
        reason_codes=(),
        external_reference_id=None,
        may_have_started=False,
    )
    execution_receipt = build_execution_receipt(
        intent_sha256=execution_intent.intent_sha256,
        journal_id=OpaqueId("journal-runtime-test"),
        journal_head_sha256=_sha("1"),
        final_state=JournalState.SUCCEEDED,
        started_at=NOW,
        completed_at=NOW,
        output_refs=(_ref("b"),),
        measured_cost_minor_units=1,
        currency="USD",
        before_workspace_observation_sha256=_sha("d"),
        after_workspace_observation_sha256=_sha("2"),
        authority_receipt_sha256=_sha("f"),
        settlement_record=_ref("c", "authority-settlement-record/1.0"),
        reconciliation_record=None,
        reason_codes=(),
    )
    publication_intent = build_publication_intent(
        request_id=OpaqueId("request-publication-test"),
        idempotency_key=IdempotencyKey("publication-test-key"),
        release_candidate=_ref("d", "release-candidate/1.0"),
        release_candidate_sha256=_sha("3"),
        release_assessment=_ref("e", "release-assessment/1.0"),
        release_assessment_sha256=_sha("4"),
        destination=_ref("f", "destination-binding/1.0"),
        destination_sha256=_sha("5"),
        workspace_id=OpaqueId("workspace-runtime-test"),
        channel_id=OpaqueId("channel-runtime-test"),
        concept_id=OpaqueId("concept-runtime-test"),
        episode_id=OpaqueId("episode-runtime-test"),
        gate_context_sha256=_sha("6"),
        workspace_observation_sha256=_sha("7"),
        publisher_id=OpaqueId("fixture-publisher"),
        publisher_variant_id=OpaqueId("fixture-publisher-v1"),
        destination_id=OpaqueId("fixture-destination"),
        cost_minor_units=10,
        currency="USD",
        retry_index=0,
        created_at=NOW,
    )
    publication_receipt = build_publication_receipt(
        publication_intent_sha256=publication_intent.intent_sha256,
        journal_id=OpaqueId("journal-publication-test"),
        journal_head_sha256=_sha("8"),
        release_candidate=publication_intent.release_candidate,
        release_assessment=publication_intent.release_assessment,
        destination=publication_intent.destination,
        workspace_before_verifier_record=_ref(
            "g", "publication-workspace-verification/1.0"
        ),
        workspace_after_verifier_record=_ref(
            "h", "publication-workspace-verification/1.0"
        ),
        action_request_sha256=_sha("9"),
        authority_decision_sha256=_sha("a"),
        authority_receipt_sha256=_sha("b"),
        service_identity=OpaqueId("fixture-runtime-service"),
        external_reference_id=OpaqueId("external-publication-test"),
        external_request_id="external-request",
        external_session_id="external-session",
        started_at=NOW,
        completed_at=NOW,
        status=PublicationStatus.SUCCEEDED,
        published=True,
        measured_cost_minor_units=1,
        currency="USD",
        settlement_record=_ref("i", "authority-settlement-record/1.0"),
        reconciliation_record=None,
        reason_codes=(),
    )
    parity = build_projection_parity_receipt(
        consumer_id=OpaqueId("fixture-consumer"),
        view_kind=OpaqueId("brief"),
        legacy_artifact=_ref("j", "brief/1.0"),
        projection_artifact=_ref("k", "blueprint-projection/1.0"),
        source_blueprint_sha256=_sha("c"),
        projection_sha256=_sha("d"),
        legacy_normalized_sha256=_sha("e"),
        projection_normalized_sha256=_sha("e"),
        normalizer_id=OpaqueId("fixture-normalizer"),
        normalizer_version="1.0",
        normalizer_sha256=_sha("f"),
        verifier_record=_ref("l", "projection-parity-verification/1.0"),
        policy_bundle_sha256=_sha("1"),
        evaluated_at=NOW,
        parity_pass=True,
        difference_count=0,
        authority_effect="none",
    )
    parity_ref = ArtifactReference(
        RelativeArtifactPath("runtime-migration/parity.json"),
        parity.receipt_sha256,
        ArtifactVersion(parity.artifact_version),
    )
    cutover = build_migration_cutover_state(
        migration_id=OpaqueId("migration-runtime-test"),
        consumer_id=parity.consumer_id,
        view_kind=parity.view_kind,
        generation=1,
        mode=MigrationMode.PROJECTION_READ_ONLY,
        previous_state_sha256=_sha("2"),
        parity_receipts=(parity_ref,),
        feature_flag_sha256=_sha("3"),
        activation_record=_ref("m", "migration-activation-verification/1.0"),
        rollback_record=None,
        activated_at=NOW,
        rollback_required=True,
        read_only=True,
        projections_are_authority=False,
        authority_effect="none",
    )
    return (
        execution_intent,
        event,
        execution_receipt,
        publication_intent,
        publication_receipt,
        parity,
        cutover,
    )


@pytest.mark.parametrize("index", range(7))
def test_runtime_artifacts_strict_schema_round_trip(index: int) -> None:
    artifact = _artifacts()[index]
    payload = runtime_artifact_to_bytes(artifact)
    assert runtime_artifact_from_bytes(payload) == artifact
    assert validate_artifact_mapping(json.loads(payload)).ok


def test_runtime_bytes_boundary_rejects_duplicate_unknown_and_nonfinite() -> None:
    with pytest.raises(RuntimeContractError):
        runtime_artifact_from_bytes(
            b'{"artifact_version":"execution-intent/1.0","artifact_version":"execution-intent/1.0"}'
        )
    with pytest.raises(RuntimeContractError):
        runtime_artifact_from_bytes(b'{"artifact_version":"runtime-unknown/1.0"}')
    with pytest.raises(RuntimeContractError):
        runtime_artifact_from_bytes(
            b'{"artifact_version":"execution-intent/1.0","value":NaN}'
        )


def test_direct_dataclass_rebound_cannot_bypass_runtime_invariants() -> None:
    execution_receipt = _artifacts()[2]
    publication_receipt = _artifacts()[4]
    cutover = _artifacts()[6]
    with pytest.raises(RuntimeContractError):
        runtime_artifact_to_bytes(
            replace(execution_receipt, after_workspace_observation_sha256=None)
        )
    with pytest.raises(RuntimeContractError):
        runtime_artifact_to_bytes(replace(publication_receipt, published=1))
    with pytest.raises(RuntimeContractError):
        runtime_artifact_to_bytes(replace(cutover, projections_are_authority=True))
