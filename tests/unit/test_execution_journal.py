"""W06 durable execution journal and runtime-boundary tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

import pytest

from video_factory.authority import VerificationPurpose
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
    RuntimeActionKind,
    build_execution_intent,
    build_execution_receipt,
    execution_intent_to_mapping,
    execution_receipt_to_mapping,
)
from video_factory_runtime import (
    ExecutionJournalError,
    FixtureAuthoritySettlementStore,
    FixtureRuntimeBoundary,
    RuntimeBoundaryError,
    SQLiteExecutionJournal,
)


NOW = "2026-08-10T00:00:00Z"
AUTHORITY_SHA = HashDigest("a" * 64)
OBSERVATION_SHA = HashDigest("b" * 64)
RESERVATION_SHA = HashDigest("c" * 64)


def _ref(path: str, digest: str, version: str) -> ArtifactReference:
    return ArtifactReference(
        path=RelativeArtifactPath(path),
        sha256=HashDigest(digest * 64),
        artifact_version=ArtifactVersion(version),
    )


def _boundary(tmp_path: Path) -> FixtureRuntimeBoundary:
    return FixtureRuntimeBoundary.initialize(
        tmp_path / "runtime",
        runtime_id="runtime-test",
        fixture_only=True,
    )


def _intent(
    *,
    key: str = "key-a",
    request_sha: str = "1",
    action_kind: RuntimeActionKind = RuntimeActionKind.EXECUTOR,
    planned_effect_ids: tuple[OpaqueId, ...] = (),
):
    return build_execution_intent(
        request_id=OpaqueId("request-a"),
        request_sha256=HashDigest(request_sha * 64),
        idempotency_key=IdempotencyKey(key),
        action_kind=action_kind,
        action_id=OpaqueId("run-external-generation"),
        planned_effect_ids=planned_effect_ids,
        workspace_id=OpaqueId("workspace-a"),
        plan_sha256=HashDigest("2" * 64),
        gate_context_sha256=HashDigest("3" * 64),
        workspace_observation_sha256=OBSERVATION_SHA,
        authority_decision_sha256=HashDigest("4" * 64),
        authority_receipt_sha256=None,
        service_identity=OpaqueId("service-a"),
        service_identity_attestation=_ref(
            "identity/service-a.json", "5", "service-identity-attestation/1.0"
        ),
        credential_handle_id=None,
        credential_verification=None,
        destination=None,
        created_at=NOW,
    )


def _advance_to_dispatched(journal: SQLiteExecutionJournal, journal_id: OpaqueId):
    journal.append_state(
        journal_id,
        JournalState.AUTHORIZED,
        occurred_at="2026-08-10T00:00:01Z",
        authority_receipt_sha256=AUTHORITY_SHA,
        workspace_observation_sha256=OBSERVATION_SHA,
    )
    journal.append_state(
        journal_id,
        JournalState.RESERVED,
        occurred_at="2026-08-10T00:00:02Z",
        reservation_sha256=RESERVATION_SHA,
    )
    head = journal.load(journal_id).events[-1].event_sha256
    journal.claim_dispatch(
        journal_id,
        expected_head_sha256=head,
        occurred_at="2026-08-10T00:00:03Z",
    )
    return journal.append_state(
        journal_id,
        JournalState.DISPATCHED,
        occurred_at="2026-08-10T00:00:04Z",
    )


def test_fixture_boundary_is_explicit_immutable_and_not_adopted(tmp_path: Path) -> None:
    with pytest.raises(RuntimeBoundaryError) as not_explicit:
        FixtureRuntimeBoundary.initialize(
            tmp_path / "disabled", runtime_id="runtime-test", fixture_only=False
        )
    assert not_explicit.value.reason_code == "runtime.boundary.fixture_required"

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "production.txt").write_text("do not adopt", encoding="utf-8")
    with pytest.raises(RuntimeBoundaryError) as nonempty:
        FixtureRuntimeBoundary.initialize(
            occupied, runtime_id="runtime-test", fixture_only=True
        )
    assert nonempty.value.reason_code == "runtime.boundary.nonempty"

    boundary = _boundary(tmp_path)
    reopened = FixtureRuntimeBoundary.open(
        boundary.root, expected_runtime_id="runtime-test"
    )
    assert reopened == boundary
    with pytest.raises(RuntimeBoundaryError) as rebound:
        FixtureRuntimeBoundary.open(boundary.root, expected_runtime_id="other")
    assert rebound.value.reason_code == "runtime.boundary.marker_rebound"


def test_sqlite_journal_survives_restart_and_returns_terminal_receipt(
    tmp_path: Path,
) -> None:
    boundary = _boundary(tmp_path)
    intent = _intent()
    assert execution_intent_to_mapping(intent)["authority_receipt_sha256"] is None
    journal = SQLiteExecutionJournal(boundary)
    snapshot, newly_reserved = journal.reserve_planned(intent, occurred_at=NOW)
    assert newly_reserved is True
    _advance_to_dispatched(journal, snapshot.journal_id)

    restarted = SQLiteExecutionJournal(
        FixtureRuntimeBoundary.open(boundary.root, expected_runtime_id="runtime-test")
    )
    unresolved = restarted.load(snapshot.journal_id)
    assert unresolved.state is JournalState.DISPATCHED
    assert unresolved.reconcile_only is True
    terminal, receipt = restarted.finish(
        snapshot.journal_id,
        JournalState.SUCCEEDED,
        occurred_at="2026-08-10T00:00:05Z",
        receipt_factory=lambda event: build_execution_receipt(
            intent_sha256=intent.intent_sha256,
            journal_id=snapshot.journal_id,
            journal_head_sha256=event.event_sha256,
            final_state=JournalState.SUCCEEDED,
            started_at="2026-08-10T00:00:03Z",
            completed_at="2026-08-10T00:00:05Z",
            output_refs=(_ref("outputs/result.json", "6", "media-output/1.0"),),
            measured_cost_minor_units=10,
            currency="USD",
            before_workspace_observation_sha256=OBSERVATION_SHA,
            after_workspace_observation_sha256=HashDigest("d" * 64),
            authority_receipt_sha256=AUTHORITY_SHA,
            settlement_record=_ref(
                "ledger/settlement.json", "7", "authority-settlement/1.0"
            ),
            reconciliation_record=None,
            reason_codes=(),
        ),
    )
    assert terminal.state is JournalState.SUCCEEDED
    loaded = SQLiteExecutionJournal(boundary).load(snapshot.journal_id)
    assert loaded.receipt == receipt
    assert execution_receipt_to_mapping(loaded.receipt)["final_state"] == "succeeded"


def test_atomic_idempotency_same_digest_replays_and_other_digest_conflicts(
    tmp_path: Path,
) -> None:
    boundary = _boundary(tmp_path)
    first = SQLiteExecutionJournal(boundary)
    second = SQLiteExecutionJournal(boundary)
    intent = _intent()

    def reserve(store: SQLiteExecutionJournal):
        return store.reserve_planned(intent, occurred_at=NOW)[1]

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(reserve, (first, second)))
    assert outcomes == [False, True]
    replay, newly_reserved = first.reserve_planned(intent, occurred_at=NOW)
    assert newly_reserved is False
    assert replay.intent == intent

    rebound = _intent(request_sha="9")
    with pytest.raises(ExecutionJournalError) as conflict:
        second.reserve_planned(rebound, occurred_at=NOW)
    assert conflict.value.reason_code == "runtime.journal.idempotency_mismatch"


def test_dispatching_restart_is_reconcile_only_and_never_redispatches(
    tmp_path: Path,
) -> None:
    boundary = _boundary(tmp_path)
    journal = SQLiteExecutionJournal(boundary)
    snapshot, _ = journal.reserve_planned(_intent(), occurred_at=NOW)
    journal.append_state(
        snapshot.journal_id,
        JournalState.AUTHORIZED,
        occurred_at="2026-08-10T00:00:01Z",
        authority_receipt_sha256=AUTHORITY_SHA,
        workspace_observation_sha256=OBSERVATION_SHA,
    )
    journal.append_state(
        snapshot.journal_id,
        JournalState.RESERVED,
        occurred_at="2026-08-10T00:00:02Z",
        reservation_sha256=RESERVATION_SHA,
    )
    head = journal.load(snapshot.journal_id).events[-1].event_sha256
    journal.claim_dispatch(
        snapshot.journal_id,
        expected_head_sha256=head,
        occurred_at="2026-08-10T00:00:03Z",
    )
    restarted = SQLiteExecutionJournal(boundary).load(snapshot.journal_id)
    assert restarted.state is JournalState.DISPATCHING
    assert restarted.reconcile_only is True
    with pytest.raises(ExecutionJournalError) as retry:
        journal.claim_dispatch(
            snapshot.journal_id,
            expected_head_sha256=head,
            occurred_at="2026-08-10T00:00:04Z",
        )
    assert retry.value.reason_code in {
        "runtime.journal.dispatch_stale",
        "runtime.journal.dispatch_replay",
    }


def test_generic_journal_cannot_terminalize_uncertain_work(tmp_path: Path) -> None:
    journal = SQLiteExecutionJournal(_boundary(tmp_path))
    snapshot, _ = journal.reserve_planned(_intent(), occurred_at=NOW)
    _advance_to_dispatched(journal, snapshot.journal_id)
    journal.append_state(
        snapshot.journal_id,
        JournalState.UNCERTAIN,
        occurred_at="2026-08-10T00:00:05Z",
        reason_codes=("runtime.external.timeout",),
    )

    assert not hasattr(journal, "reconcile")
    loaded = journal.load(snapshot.journal_id)
    assert loaded.state is JournalState.UNCERTAIN
    assert loaded.receipt is None
    with pytest.raises(ExecutionJournalError) as terminalized:
        journal.append_state(
            snapshot.journal_id,
            JournalState.RECONCILED,
            occurred_at="2026-08-10T00:00:06Z",
        )
    assert terminalized.value.reason_code == "runtime.journal.receipt_required"


def test_hash_chain_corruption_and_illegal_transition_fail_closed(tmp_path: Path) -> None:
    journal = SQLiteExecutionJournal(_boundary(tmp_path))
    snapshot, _ = journal.reserve_planned(_intent(), occurred_at=NOW)
    with pytest.raises(ExecutionJournalError) as illegal:
        journal.append_state(
            snapshot.journal_id,
            JournalState.RESERVED,
            occurred_at="2026-08-10T00:00:01Z",
        )
    assert illegal.value.reason_code == "runtime.journal.transition"

    connection = sqlite3.connect(journal.database_path)
    try:
        connection.execute(
            "UPDATE events SET event_json = ? WHERE journal_id = ? AND sequence = 0",
            ("{}", str(snapshot.journal_id)),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ExecutionJournalError) as corrupt:
        journal.load(snapshot.journal_id)
    assert corrupt.value.reason_code in {
        "runtime.journal.chain",
        "runtime.journal.corrupt",
    }


def test_pre_effect_authority_and_reservation_can_be_refreshed_after_restart(
    tmp_path: Path,
) -> None:
    journal = SQLiteExecutionJournal(_boundary(tmp_path))
    snapshot, _ = journal.reserve_planned(_intent(), occurred_at=NOW)
    journal.append_state(
        snapshot.journal_id,
        JournalState.AUTHORIZED,
        occurred_at="2026-08-10T00:00:01Z",
        authority_receipt_sha256=AUTHORITY_SHA,
        workspace_observation_sha256=OBSERVATION_SHA,
    )
    journal.append_state(
        snapshot.journal_id,
        JournalState.AUTHORIZED,
        occurred_at="2026-08-10T00:00:01Z",
        authority_receipt_sha256=HashDigest("e" * 64),
        workspace_observation_sha256=OBSERVATION_SHA,
    )
    journal.append_state(
        snapshot.journal_id,
        JournalState.RESERVED,
        occurred_at="2026-08-10T00:00:02Z",
        reservation_sha256=RESERVATION_SHA,
    )
    refreshed = journal.append_state(
        snapshot.journal_id,
        JournalState.RESERVED,
        occurred_at="2026-08-10T00:00:02Z",
        authority_receipt_sha256=HashDigest("f" * 64),
        reservation_sha256=HashDigest("9" * 64),
    )
    assert refreshed.state is JournalState.RESERVED
    assert refreshed.authority_receipt_sha256 == HashDigest("f" * 64)
    assert refreshed.reservation_sha256 == HashDigest("9" * 64)


def test_reservation_claim_survives_restart_and_settles_idempotently(
    tmp_path: Path,
) -> None:
    boundary = _boundary(tmp_path)
    journal = SQLiteExecutionJournal(boundary)
    snapshot, _ = journal.reserve_planned(_intent(), occurred_at=NOW)
    claim = journal.prepare_reservation_claim(
        snapshot.journal_id,
        purpose=VerificationPurpose.DISPATCH,
        effect_id=OpaqueId("executor-dispatch"),
        expected_head_sha256=snapshot.events[-1].event_sha256,
        occurred_at="2026-08-10T00:00:01Z",
    )
    restarted = SQLiteExecutionJournal(boundary)
    assert restarted.prepare_reservation_claim(
        snapshot.journal_id,
        purpose=VerificationPurpose.DISPATCH,
        effect_id=OpaqueId("executor-dispatch"),
        expected_head_sha256=snapshot.events[-1].event_sha256,
        occurred_at="2026-08-10T00:00:02Z",
    ) == claim
    assert restarted.unsettled_reservation_claims() == ()

    restarted.record_reservation_result(
        claim,
        authority_receipt_sha256=AUTHORITY_SHA,
        reservation_sha256=RESERVATION_SHA,
    )
    assert SQLiteExecutionJournal(boundary).unsettled_reservation_claims() == (claim,)
    settlement = _ref(
        "ledger/claim-settlement.json", "7", "authority-settlement/1.0"
    )
    restarted.record_claim_settlement(
        (claim,), settlement_record=settlement, final_state=JournalState.SUCCEEDED
    )
    restarted.record_claim_settlement(
        (claim,), settlement_record=settlement, final_state=JournalState.SUCCEEDED
    )
    assert SQLiteExecutionJournal(boundary).unsettled_reservation_claims() == ()
    with pytest.raises(ExecutionJournalError) as rebound:
        restarted.record_claim_settlement(
            (claim,),
            settlement_record=_ref(
                "ledger/other-settlement.json", "8", "authority-settlement/1.0"
            ),
            final_state=JournalState.SUCCEEDED,
        )
    assert rebound.value.reason_code == "runtime.journal.settlement_rebound"


def test_load_rejects_event_table_identity_rebound(tmp_path: Path) -> None:
    journal = SQLiteExecutionJournal(_boundary(tmp_path))
    snapshot, _ = journal.reserve_planned(_intent(), occurred_at=NOW)
    connection = sqlite3.connect(journal.database_path)
    try:
        connection.execute(
            "UPDATE events SET event_sha256 = ? WHERE journal_id = ? AND sequence = 0",
            ("e" * 64, str(snapshot.journal_id)),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ExecutionJournalError) as rebound:
        journal.load(snapshot.journal_id)
    assert rebound.value.reason_code in {
        "runtime.journal.chain",
        "runtime.journal.corrupt",
    }


def test_fixture_settlement_store_is_restart_idempotent(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path)
    first = FixtureAuthoritySettlementStore(boundary)
    claim_sha = HashDigest("d" * 64)
    initial = first.finalize_current(
        journal_id=OpaqueId("journal-settlement"),
        reservation_claim_sha256s=(claim_sha,),
        reservation_sha256s=(RESERVATION_SHA,),
        final_state=JournalState.SUCCEEDED,
        evaluated_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    replay = FixtureAuthoritySettlementStore(boundary).finalize_current(
        journal_id=OpaqueId("journal-settlement"),
        reservation_claim_sha256s=(claim_sha,),
        reservation_sha256s=(RESERVATION_SHA,),
        final_state=JournalState.SUCCEEDED,
        evaluated_at=datetime(2026, 8, 10, tzinfo=UTC) + timedelta(hours=1),
    )
    assert initial is not None
    assert replay == initial
    assert FixtureAuthoritySettlementStore(boundary).finalize_current(
        journal_id=OpaqueId("journal-settlement"),
        reservation_claim_sha256s=(claim_sha,),
        reservation_sha256s=(HashDigest("e" * 64),),
        final_state=JournalState.SUCCEEDED,
        evaluated_at=datetime(2026, 8, 10, tzinfo=UTC) + timedelta(hours=2),
    ) is None


def test_mutation_dispatch_claim_requires_the_exact_next_operation(
    tmp_path: Path,
) -> None:
    journal = SQLiteExecutionJournal(_boundary(tmp_path))
    operations = (OpaqueId("operation-1"), OpaqueId("operation-2"))
    snapshot, _ = journal.reserve_planned(
        _intent(
            action_kind=RuntimeActionKind.MUTATION,
            planned_effect_ids=operations,
        ),
        occurred_at=NOW,
        planned_effect_ids=operations,
    )
    journal.append_state(
        snapshot.journal_id,
        JournalState.AUTHORIZED,
        occurred_at="2026-08-10T00:00:01Z",
        authority_receipt_sha256=AUTHORITY_SHA,
        workspace_observation_sha256=OBSERVATION_SHA,
    )
    journal.append_state(
        snapshot.journal_id,
        JournalState.RESERVED,
        occurred_at="2026-08-10T00:00:02Z",
        reservation_sha256=RESERVATION_SHA,
    )
    head = journal.load(snapshot.journal_id).events[-1].event_sha256
    with pytest.raises(ExecutionJournalError) as skipped:
        journal.claim_dispatch(
            snapshot.journal_id,
            expected_head_sha256=head,
            occurred_at="2026-08-10T00:00:03Z",
            operation_id=operations[1],
        )
    assert skipped.value.reason_code == "runtime.journal.dispatch_replay"

    journal.claim_dispatch(
        snapshot.journal_id,
        expected_head_sha256=head,
        occurred_at="2026-08-10T00:00:03Z",
        operation_id=operations[0],
    )
    journal.append_state(
        snapshot.journal_id,
        JournalState.DISPATCHED,
        occurred_at="2026-08-10T00:00:04Z",
        operation_id=operations[0],
    )
    next_head = journal.load(snapshot.journal_id).events[-1].event_sha256
    second = journal.claim_dispatch(
        snapshot.journal_id,
        expected_head_sha256=next_head,
        occurred_at="2026-08-10T00:00:05Z",
        operation_id=operations[1],
    )
    assert second.operation_id == operations[1]


def test_mutation_effect_plan_is_hash_bound_to_the_execution_intent(
    tmp_path: Path,
) -> None:
    journal = SQLiteExecutionJournal(_boundary(tmp_path))
    operations = (OpaqueId("operation-1"), OpaqueId("operation-2"))
    snapshot, _ = journal.reserve_planned(
        _intent(
            action_kind=RuntimeActionKind.MUTATION,
            planned_effect_ids=operations,
        ),
        occurred_at=NOW,
        planned_effect_ids=operations,
    )
    with sqlite3.connect(journal.database_path) as connection:
        connection.execute(
            "UPDATE executions SET effect_plan_json = ? WHERE journal_id = ?",
            ('["operation-2","operation-1"]', str(snapshot.journal_id)),
        )

    with pytest.raises(ExecutionJournalError) as rebound:
        journal.load(snapshot.journal_id)

    assert rebound.value.reason_code == "runtime.journal.intent_rebound"


def test_dispatch_claim_revalidates_the_row_and_event_chain_atomically(
    tmp_path: Path,
) -> None:
    journal = SQLiteExecutionJournal(_boundary(tmp_path))
    operations = (OpaqueId("operation-1"),)
    snapshot, _ = journal.reserve_planned(
        _intent(
            action_kind=RuntimeActionKind.MUTATION,
            planned_effect_ids=operations,
        ),
        occurred_at=NOW,
        planned_effect_ids=operations,
    )
    journal.append_state(
        snapshot.journal_id,
        JournalState.AUTHORIZED,
        occurred_at="2026-08-10T00:00:01Z",
        authority_receipt_sha256=AUTHORITY_SHA,
        workspace_observation_sha256=OBSERVATION_SHA,
    )
    reserved = journal.append_state(
        snapshot.journal_id,
        JournalState.RESERVED,
        occurred_at="2026-08-10T00:00:02Z",
        reservation_sha256=RESERVATION_SHA,
    )
    with sqlite3.connect(journal.database_path) as connection:
        connection.execute(
            "UPDATE executions SET effect_plan_json = ? WHERE journal_id = ?",
            ('["operation-other"]', str(snapshot.journal_id)),
        )

    with pytest.raises(ExecutionJournalError) as rebound:
        journal.claim_dispatch(
            snapshot.journal_id,
            expected_head_sha256=reserved.event_sha256,
            occurred_at="2026-08-10T00:00:03Z",
            operation_id=operations[0],
        )

    assert rebound.value.reason_code == "runtime.journal.intent_rebound"
