"""SQLite-backed append-only execution journal for isolated W06 fixtures."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from video_factory.authority import VerificationPurpose
from video_factory.config.canonical import canonical_json_bytes, canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    IdempotencyKey,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.json_boundary import parse_rfc3339_datetime
from video_factory.runtime import (
    ExecutionIntent,
    ExecutionJournalEvent,
    ExecutionReceipt,
    JournalState,
    RuntimeActionKind,
    build_execution_journal_event,
    execution_intent_to_mapping,
    execution_journal_event_to_mapping,
    execution_receipt_from_mapping,
    execution_receipt_to_mapping,
)

from .boundary import FixtureRuntimeBoundary, RuntimeBoundaryError


SCHEMA_VERSION = "sqlite-execution-journal/1.1"


class ExecutionJournalError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class JournalSnapshot:
    journal_id: OpaqueId
    intent: ExecutionIntent
    state: JournalState
    events: tuple[ExecutionJournalEvent, ...]
    receipt: ExecutionReceipt | None
    reconcile_only: bool


@dataclass(frozen=True, slots=True)
class AuthorityReservationClaim:
    journal_id: OpaqueId
    claim_sha256: HashDigest
    purpose: VerificationPurpose
    effect_id: OpaqueId
    request_sha256: HashDigest


_ALLOWED_TRANSITIONS: dict[JournalState, frozenset[JournalState]] = {
    JournalState.PLANNED: frozenset({JournalState.AUTHORIZED, JournalState.FAILED}),
    JournalState.AUTHORIZED: frozenset(
        {JournalState.AUTHORIZED, JournalState.RESERVED, JournalState.FAILED}
    ),
    JournalState.RESERVED: frozenset(
        {JournalState.RESERVED, JournalState.DISPATCHING, JournalState.FAILED}
    ),
    JournalState.DISPATCHING: frozenset(
        {
            JournalState.DISPATCHED,
            JournalState.RECONCILING,
            JournalState.UNCERTAIN,
            JournalState.RECONCILED,
        }
    ),
    JournalState.DISPATCHED: frozenset(
        {
            JournalState.DISPATCHED,
            JournalState.SUCCEEDED,
            JournalState.FAILED,
            JournalState.PARTIAL,
            JournalState.RECONCILING,
            JournalState.UNCERTAIN,
            JournalState.RECONCILED,
        }
    ),
    JournalState.RECONCILING: frozenset(
        {
            JournalState.RECONCILING,
            JournalState.UNCERTAIN,
            JournalState.RECONCILED,
        }
    ),
    JournalState.PARTIAL: frozenset(
        {JournalState.RECONCILING, JournalState.RECONCILED}
    ),
    JournalState.UNCERTAIN: frozenset(
        {
            JournalState.RECONCILING,
            JournalState.UNCERTAIN,
            JournalState.RECONCILED,
        }
    ),
    JournalState.SUCCEEDED: frozenset(),
    JournalState.FAILED: frozenset(),
    JournalState.RECONCILED: frozenset(),
}
_RECONCILE_ONLY = frozenset(
    {
        JournalState.DISPATCHING,
        JournalState.DISPATCHED,
        JournalState.RECONCILING,
        JournalState.PARTIAL,
        JournalState.UNCERTAIN,
    }
)

_RECEIPT_REQUIRED_STATES = frozenset(
    {
        JournalState.SUCCEEDED,
        JournalState.FAILED,
        JournalState.RECONCILED,
    }
)


def journal_event_reference(event: ExecutionJournalEvent) -> ArtifactReference:
    return ArtifactReference(
        path=RelativeArtifactPath(
            f"runtime-journal/{event.journal_id}/{event.event_id}.json"
        ),
        sha256=event.event_sha256,
        artifact_version=ArtifactVersion(event.artifact_version),
    )


class SQLiteExecutionJournal:
    """Atomic idempotency reservation plus append-only hash-chain events."""

    def __init__(self, boundary: FixtureRuntimeBoundary) -> None:
        self.boundary = boundary
        journal_root = boundary.require_directory("journal", create=True)
        self.database_path = journal_root / "execution.sqlite3"
        self._initialize_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA journal_mode = DELETE")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS metadata (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS executions (
                    journal_id TEXT PRIMARY KEY,
                    scope_key TEXT NOT NULL UNIQUE,
                    request_sha256 TEXT NOT NULL,
                    intent_sha256 TEXT NOT NULL,
                    intent_json TEXT NOT NULL,
                    effect_plan_json TEXT NOT NULL,
                    current_state TEXT NOT NULL,
                    last_sequence INTEGER NOT NULL,
                    last_event_sha256 TEXT NOT NULL,
                    receipt_json TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    journal_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_sha256 TEXT NOT NULL UNIQUE,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY (journal_id, sequence),
                    FOREIGN KEY (journal_id) REFERENCES executions(journal_id)
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    journal_id TEXT NOT NULL,
                    receipt_sha256 TEXT NOT NULL UNIQUE,
                    final_state TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    PRIMARY KEY (journal_id, receipt_sha256),
                    FOREIGN KEY (journal_id) REFERENCES executions(journal_id)
                );
                CREATE TABLE IF NOT EXISTS authority_reservation_claims (
                    journal_id TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    effect_id TEXT NOT NULL,
                    claim_sha256 TEXT NOT NULL UNIQUE,
                    request_sha256 TEXT NOT NULL,
                    authority_receipt_sha256 TEXT,
                    reservation_sha256 TEXT,
                    settlement_reference_json TEXT,
                    final_state TEXT,
                    prepared_at TEXT NOT NULL,
                    PRIMARY KEY (journal_id, purpose, effect_id),
                    FOREIGN KEY (journal_id) REFERENCES executions(journal_id)
                );
                COMMIT;
                """
            )
            expected = {
                "schema_version": SCHEMA_VERSION,
                "runtime_id": str(self.boundary.runtime_id),
                "fixture_marker_sha256": str(self.boundary.marker_sha256),
            }
            connection.execute("BEGIN IMMEDIATE")
            try:
                for name, value in expected.items():
                    row = connection.execute(
                        "SELECT value FROM metadata WHERE name = ?", (name,)
                    ).fetchone()
                    if row is None:
                        connection.execute(
                            "INSERT INTO metadata(name, value) VALUES (?, ?)",
                            (name, value),
                        )
                    elif row["value"] != value:
                        raise ExecutionJournalError(
                            "runtime.journal.metadata_rebound",
                            f"journal metadata differs for {name}",
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _scope_key(intent: ExecutionIntent) -> str:
        return ":".join(
            (
                intent.action_kind.value,
                str(intent.action_id),
                str(intent.idempotency_key),
            )
        )

    @staticmethod
    def _event_json(event: ExecutionJournalEvent) -> str:
        return canonical_json_bytes(
            execution_journal_event_to_mapping(event)
        ).decode("utf-8")

    @staticmethod
    def _intent_json(intent: ExecutionIntent) -> str:
        return canonical_json_bytes(execution_intent_to_mapping(intent)).decode("utf-8")

    def _validated_intent_from_row(self, row: sqlite3.Row) -> ExecutionIntent:
        """Decode one execution row and prove its canonical intent binding."""

        try:
            intent = self._decode_intent(json.loads(row["intent_json"]))
            execution_intent_to_mapping(intent)
            planned_effect_ids = json.loads(row["effect_plan_json"])
            if (
                intent.intent_sha256 != HashDigest(row["intent_sha256"])
                or intent.request_sha256 != HashDigest(row["request_sha256"])
                or row["scope_key"] != self._scope_key(intent)
                or OpaqueId(row["journal_id"])
                != OpaqueId(f"journal-{str(intent.intent_sha256)[:24]}")
                or planned_effect_ids
                != [str(item) for item in intent.planned_effect_ids]
            ):
                raise ExecutionJournalError(
                    "runtime.journal.intent_rebound",
                    "execution row differs from its canonical intent",
                )
            return intent
        except ExecutionJournalError:
            raise
        except Exception as error:
            raise ExecutionJournalError(
                "runtime.journal.intent_rebound",
                "execution row contains an invalid canonical intent",
            ) from error

    def _validated_events_from_rows(
        self,
        row: sqlite3.Row,
        intent: ExecutionIntent,
        event_rows: list[sqlite3.Row],
    ) -> tuple[ExecutionJournalEvent, ...]:
        """Decode the exact row-bound event chain under the caller's snapshot."""

        try:
            events = tuple(
                self._decode_event(json.loads(item["event_json"]))
                for item in event_rows
            )
            previous: str | None = None
            for index, (event_row, event) in enumerate(
                zip(event_rows, events, strict=True)
            ):
                execution_journal_event_to_mapping(event)
                if (
                    event.sequence != index
                    or event_row["sequence"] != index
                    or event_row["event_sha256"] != str(event.event_sha256)
                    or event.journal_id != OpaqueId(row["journal_id"])
                    or event.intent_sha256 != intent.intent_sha256
                    or (
                        str(event.previous_event_sha256)
                        if event.previous_event_sha256 is not None
                        else None
                    )
                    != previous
                ):
                    raise ExecutionJournalError(
                        "runtime.journal.chain", "journal hash chain is invalid"
                    )
                previous = str(event.event_sha256)
            if (
                not events
                or previous != row["last_event_sha256"]
                or events[-1].state.value != row["current_state"]
                or events[-1].sequence != row["last_sequence"]
            ):
                raise ExecutionJournalError(
                    "runtime.journal.head_rebound", "journal head metadata is invalid"
                )
            return events
        except ExecutionJournalError:
            raise
        except Exception as error:
            raise ExecutionJournalError(
                "runtime.journal.chain", "journal event chain is invalid"
            ) from error

    def reserve_planned(
        self,
        intent: ExecutionIntent,
        *,
        occurred_at: str,
        planned_effect_ids: tuple[OpaqueId, ...] = (),
    ) -> tuple[JournalSnapshot, bool]:
        execution_intent_to_mapping(intent)
        parse_rfc3339_datetime(occurred_at)
        if (
            planned_effect_ids != intent.planned_effect_ids
            or (
                intent.action_kind is RuntimeActionKind.MUTATION
                and not planned_effect_ids
            )
            or (
                intent.action_kind is not RuntimeActionKind.MUTATION
                and planned_effect_ids
            )
            or len(set(planned_effect_ids)) != len(planned_effect_ids)
        ):
            raise ExecutionJournalError(
                "runtime.journal.effect_plan",
                "planned effect ids must be exact, unique, and mutation-only",
            )
        effect_plan_json = canonical_json_bytes(
            [str(item) for item in planned_effect_ids]
        ).decode("utf-8")
        journal_id = OpaqueId(f"journal-{str(intent.intent_sha256)[:24]}")
        event = build_execution_journal_event(
            journal_id=journal_id,
            sequence=0,
            previous_event_sha256=None,
            intent_sha256=intent.intent_sha256,
            authority_receipt_sha256=None,
            workspace_observation_sha256=None,
            reservation_sha256=None,
            state=JournalState.PLANNED,
            operation_id=None,
            occurred_at=occurred_at,
            reason_codes=(),
            external_reference_id=None,
            may_have_started=False,
        )
        scope_key = self._scope_key(intent)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM executions WHERE scope_key = ?", (scope_key,)
                ).fetchone()
                if existing is not None:
                    if (
                        existing["request_sha256"] != str(intent.request_sha256)
                        or existing["intent_sha256"] != str(intent.intent_sha256)
                        or existing["effect_plan_json"] != effect_plan_json
                    ):
                        raise ExecutionJournalError(
                            "runtime.journal.idempotency_mismatch",
                            "idempotency scope is bound to another request or intent",
                        )
                    connection.commit()
                    return self.load(OpaqueId(existing["journal_id"])), False
                connection.execute(
                    """
                    INSERT INTO executions(
                        journal_id, scope_key, request_sha256, intent_sha256,
                        intent_json, effect_plan_json, current_state, last_sequence,
                        last_event_sha256, receipt_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        str(journal_id),
                        scope_key,
                        str(intent.request_sha256),
                        str(intent.intent_sha256),
                        self._intent_json(intent),
                        effect_plan_json,
                        event.state.value,
                        event.sequence,
                        str(event.event_sha256),
                    ),
                )
                connection.execute(
                    "INSERT INTO events(journal_id, sequence, event_sha256, event_json) VALUES (?, ?, ?, ?)",
                    (
                        str(journal_id),
                        event.sequence,
                        str(event.event_sha256),
                        self._event_json(event),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return JournalSnapshot(journal_id, intent, JournalState.PLANNED, (event,), None, False), True

    def prepare_reservation_claim(
        self,
        journal_id: OpaqueId,
        *,
        purpose: VerificationPurpose,
        effect_id: OpaqueId,
        expected_head_sha256: HashDigest,
        occurred_at: str,
    ) -> AuthorityReservationClaim:
        """Durably create the idempotency token before calling a trusted ledger."""

        if not isinstance(purpose, VerificationPurpose):
            raise ExecutionJournalError(
                "runtime.journal.claim_purpose", "reservation claim purpose is invalid"
            )
        parse_rfc3339_datetime(occurred_at)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM executions WHERE journal_id = ?", (str(journal_id),)
                ).fetchone()
                if row is None:
                    raise ExecutionJournalError(
                        "runtime.journal.missing", "journal execution does not exist"
                    )
                intent = self._validated_intent_from_row(row)
                event_rows = connection.execute(
                    "SELECT * FROM events WHERE journal_id = ? ORDER BY sequence",
                    (str(journal_id),),
                ).fetchall()
                self._validated_events_from_rows(row, intent, event_rows)
                existing = connection.execute(
                    """
                    SELECT * FROM authority_reservation_claims
                     WHERE journal_id = ? AND purpose = ? AND effect_id = ?
                    """,
                    (str(journal_id), purpose.value, str(effect_id)),
                ).fetchone()
                material = {
                    "artifact_version": "runtime-authority-reservation-claim/1.0",
                    "journal_id": str(journal_id),
                    "purpose": purpose.value,
                    "effect_id": str(effect_id),
                    "request_sha256": row["request_sha256"],
                }
                claim = AuthorityReservationClaim(
                    journal_id=journal_id,
                    claim_sha256=canonical_sha256(material),
                    purpose=purpose,
                    effect_id=effect_id,
                    request_sha256=HashDigest(row["request_sha256"]),
                )
                if existing is not None:
                    if (
                        existing["claim_sha256"] != str(claim.claim_sha256)
                        or existing["request_sha256"] != str(claim.request_sha256)
                    ):
                        raise ExecutionJournalError(
                            "runtime.journal.claim_rebound",
                            "reservation claim identity is bound to other material",
                        )
                    connection.commit()
                    return claim
                if row["last_event_sha256"] != str(expected_head_sha256):
                    raise ExecutionJournalError(
                        "runtime.journal.claim_stale",
                        "reservation claim is based on a stale journal head",
                    )
                connection.execute(
                    """
                    INSERT INTO authority_reservation_claims(
                        journal_id, purpose, effect_id, claim_sha256,
                        request_sha256, prepared_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(journal_id),
                        purpose.value,
                        str(effect_id),
                        str(claim.claim_sha256),
                        str(claim.request_sha256),
                        occurred_at,
                    ),
                )
                connection.commit()
                return claim
            except Exception:
                connection.rollback()
                raise

    def record_reservation_result(
        self,
        claim: AuthorityReservationClaim,
        *,
        authority_receipt_sha256: HashDigest,
        reservation_sha256: HashDigest,
    ) -> None:
        """Idempotently attach the trusted ledger result to its durable claim."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM authority_reservation_claims WHERE claim_sha256 = ?",
                (str(claim.claim_sha256),),
            ).fetchone()
            if row is None or (
                row["journal_id"] != str(claim.journal_id)
                or row["purpose"] != claim.purpose.value
                or row["effect_id"] != str(claim.effect_id)
                or row["request_sha256"] != str(claim.request_sha256)
            ):
                raise ExecutionJournalError(
                    "runtime.journal.claim_missing",
                    "trusted reservation result has no exact durable claim",
                )
            previous = (row["authority_receipt_sha256"], row["reservation_sha256"])
            current = (str(authority_receipt_sha256), str(reservation_sha256))
            if previous != (None, None) and previous != current:
                raise ExecutionJournalError(
                    "runtime.journal.claim_result_rebound",
                    "reservation claim is already bound to another ledger result",
                )
            connection.execute(
                """
                UPDATE authority_reservation_claims
                   SET authority_receipt_sha256 = ?, reservation_sha256 = ?
                 WHERE claim_sha256 = ?
                """,
                (*current, str(claim.claim_sha256)),
            )
            connection.commit()

    def record_claim_settlement(
        self,
        claims: tuple[AuthorityReservationClaim, ...],
        *,
        settlement_record: ArtifactReference,
        final_state: JournalState,
    ) -> None:
        if not claims or len(set(claims)) != len(claims):
            raise ExecutionJournalError(
                "runtime.journal.settlement_claims",
                "settlement claims must be non-empty and unique",
            )
        reference_json = canonical_json_bytes(
            {
                "path": str(settlement_record.path),
                "sha256": str(settlement_record.sha256),
                "artifact_version": str(settlement_record.artifact_version),
            }
        ).decode("utf-8")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for claim in claims:
                row = connection.execute(
                    "SELECT * FROM authority_reservation_claims WHERE claim_sha256 = ?",
                    (str(claim.claim_sha256),),
                ).fetchone()
                if row is None or row["reservation_sha256"] is None:
                    raise ExecutionJournalError(
                        "runtime.journal.settlement_unreserved",
                        "cannot settle an absent or incomplete reservation claim",
                    )
                previous = (row["settlement_reference_json"], row["final_state"])
                current = (reference_json, final_state.value)
                if previous != (None, None) and previous != current:
                    raise ExecutionJournalError(
                        "runtime.journal.settlement_rebound",
                        "reservation claim is already settled to another outcome",
                    )
                connection.execute(
                    """
                    UPDATE authority_reservation_claims
                       SET settlement_reference_json = ?, final_state = ?
                     WHERE claim_sha256 = ?
                    """,
                    (reference_json, final_state.value, str(claim.claim_sha256)),
                )
            connection.commit()

    def unsettled_reservation_claims(
        self, journal_id: OpaqueId | None = None
    ) -> tuple[AuthorityReservationClaim, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM authority_reservation_claims
                 WHERE authority_receipt_sha256 IS NOT NULL
                   AND settlement_reference_json IS NULL
                   AND (? IS NULL OR journal_id = ?)
                 ORDER BY journal_id, purpose, effect_id
                """,
                (
                    str(journal_id) if journal_id is not None else None,
                    str(journal_id) if journal_id is not None else None,
                ),
            ).fetchall()
        return tuple(
            AuthorityReservationClaim(
                journal_id=OpaqueId(row["journal_id"]),
                claim_sha256=HashDigest(row["claim_sha256"]),
                purpose=VerificationPurpose(row["purpose"]),
                effect_id=OpaqueId(row["effect_id"]),
                request_sha256=HashDigest(row["request_sha256"]),
            )
            for row in rows
        )

    def reservation_result(
        self, claim: AuthorityReservationClaim
    ) -> tuple[HashDigest, HashDigest] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM authority_reservation_claims WHERE claim_sha256 = ?",
                (str(claim.claim_sha256),),
            ).fetchone()
        if row is None or (
            row["journal_id"] != str(claim.journal_id)
            or row["purpose"] != claim.purpose.value
            or row["effect_id"] != str(claim.effect_id)
            or row["request_sha256"] != str(claim.request_sha256)
        ):
            raise ExecutionJournalError(
                "runtime.journal.claim_missing",
                "reservation result lookup is not bound to the exact claim",
            )
        if row["authority_receipt_sha256"] is None or row["reservation_sha256"] is None:
            return None
        return (
            HashDigest(row["authority_receipt_sha256"]),
            HashDigest(row["reservation_sha256"]),
        )

    def append_state(
        self,
        journal_id: OpaqueId,
        state: JournalState,
        *,
        occurred_at: str,
        reason_codes: tuple[str, ...] = (),
        operation_id: OpaqueId | None = None,
        external_reference_id: OpaqueId | None = None,
        authority_receipt_sha256: HashDigest | None = None,
        workspace_observation_sha256: HashDigest | None = None,
        reservation_sha256: HashDigest | None = None,
    ) -> ExecutionJournalEvent:
        if state is JournalState.DISPATCHING:
            raise ExecutionJournalError(
                "runtime.journal.dispatch_claim_required",
                "dispatch markers require the action-aware compare-and-swap API",
            )
        if state in _RECEIPT_REQUIRED_STATES:
            raise ExecutionJournalError(
                "runtime.journal.receipt_required",
                "terminal journal states require an atomic receipt factory",
            )
        parse_rfc3339_datetime(occurred_at)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM executions WHERE journal_id = ?", (str(journal_id),)
                ).fetchone()
                if row is None:
                    raise ExecutionJournalError(
                        "runtime.journal.missing", "journal execution does not exist"
                    )
                intent = self._validated_intent_from_row(row)
                event_rows = connection.execute(
                    "SELECT * FROM events WHERE journal_id = ? ORDER BY sequence",
                    (str(journal_id),),
                ).fetchall()
                self._validated_events_from_rows(row, intent, event_rows)
                event = self._append_event_locked(
                    connection,
                    row,
                    state,
                    occurred_at=occurred_at,
                    reason_codes=reason_codes,
                    operation_id=operation_id,
                    external_reference_id=external_reference_id,
                    authority_receipt_sha256=authority_receipt_sha256,
                    workspace_observation_sha256=workspace_observation_sha256,
                    reservation_sha256=reservation_sha256,
                )
                connection.commit()
                return event
            except Exception:
                connection.rollback()
                raise

    def claim_dispatch(
        self,
        journal_id: OpaqueId,
        *,
        expected_head_sha256: HashDigest,
        occurred_at: str,
        operation_id: OpaqueId | None = None,
        authority_receipt_sha256: HashDigest | None = None,
        workspace_observation_sha256: HashDigest | None = None,
        reservation_sha256: HashDigest | None = None,
    ) -> ExecutionJournalEvent:
        """Atomically claim the one exact next effect against the current head.

        Executor and publication journals permit exactly one dispatch claim.
        Mutation journals may claim a later operation only after the previous
        operation reached ``dispatched``, and an operation id can never be
        claimed twice.  A stale caller therefore cannot turn a previous
        ``dispatched`` snapshot back into executable work.
        """

        parse_rfc3339_datetime(occurred_at)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM executions WHERE journal_id = ?", (str(journal_id),)
                ).fetchone()
                if row is None:
                    raise ExecutionJournalError(
                        "runtime.journal.missing", "journal execution does not exist"
                    )
                if row["last_event_sha256"] != str(expected_head_sha256):
                    raise ExecutionJournalError(
                        "runtime.journal.dispatch_stale",
                        "dispatch claim is based on a stale journal head",
                    )
                intent = self._validated_intent_from_row(row)
                current = JournalState(row["current_state"])
                prior = connection.execute(
                    "SELECT * FROM events WHERE journal_id = ? ORDER BY sequence",
                    (str(journal_id),),
                ).fetchall()
                prior_events = self._validated_events_from_rows(row, intent, prior)
                prior_operation_ids = {
                    str(event.operation_id)
                    for event in prior_events
                    if event.state is JournalState.DISPATCHING
                    and event.operation_id is not None
                }
                if intent.action_kind in {
                    RuntimeActionKind.EXECUTOR,
                    RuntimeActionKind.PUBLICATION,
                }:
                    allowed = current is JournalState.RESERVED and operation_id is None
                    already_claimed = bool(prior_operation_ids) or any(
                        event.state is JournalState.DISPATCHING
                        for event in prior_events
                    )
                    if not allowed or already_claimed:
                        raise ExecutionJournalError(
                            "runtime.journal.dispatch_replay",
                            "single-effect journal has already been claimed or is not reserved",
                        )
                elif intent.action_kind is RuntimeActionKind.MUTATION:
                    planned_operation_ids = intent.planned_effect_ids
                    next_index = len(prior_operation_ids)
                    expected_operation_id = (
                        planned_operation_ids[next_index]
                        if next_index < len(planned_operation_ids)
                        else None
                    )
                    if (
                        current not in {JournalState.RESERVED, JournalState.DISPATCHED}
                        or operation_id is None
                        or str(operation_id) in prior_operation_ids
                        or str(operation_id) != expected_operation_id
                    ):
                        raise ExecutionJournalError(
                            "runtime.journal.dispatch_replay",
                            "mutation dispatch claim is stale, missing, or duplicated",
                        )
                else:
                    raise ExecutionJournalError(
                        "runtime.journal.dispatch_action",
                        "this runtime action kind cannot claim an effect",
                    )
                event = self._append_event_locked(
                    connection,
                    row,
                    JournalState.DISPATCHING,
                    occurred_at=occurred_at,
                    reason_codes=(),
                    operation_id=operation_id,
                    external_reference_id=None,
                    authority_receipt_sha256=authority_receipt_sha256,
                    workspace_observation_sha256=workspace_observation_sha256,
                    reservation_sha256=reservation_sha256,
                    allow_dispatch_claim=True,
                )
                connection.commit()
                return event
            except Exception:
                connection.rollback()
                raise

    def _append_event_locked(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        state: JournalState,
        *,
        occurred_at: str,
        reason_codes: tuple[str, ...],
        operation_id: OpaqueId | None,
        external_reference_id: OpaqueId | None,
        authority_receipt_sha256: HashDigest | None,
        workspace_observation_sha256: HashDigest | None,
        reservation_sha256: HashDigest | None,
        allow_dispatch_claim: bool = False,
    ) -> ExecutionJournalEvent:
        current = JournalState(row["current_state"])
        dispatch_transition = (
            allow_dispatch_claim
            and state is JournalState.DISPATCHING
            and current in {JournalState.RESERVED, JournalState.DISPATCHED}
        )
        if state not in _ALLOWED_TRANSITIONS[current] and not dispatch_transition:
            raise ExecutionJournalError(
                "runtime.journal.transition",
                f"invalid journal transition: {current.value} -> {state.value}",
            )
        previous_json = connection.execute(
            "SELECT event_json FROM events WHERE journal_id = ? AND sequence = ?",
            (row["journal_id"], row["last_sequence"]),
        ).fetchone()
        if previous_json is None:
            raise ExecutionJournalError(
                "runtime.journal.chain_missing", "journal head event is missing"
            )
        previous_mapping = json.loads(previous_json["event_json"])
        previous_time = parse_rfc3339_datetime(previous_mapping["occurred_at"])
        if parse_rfc3339_datetime(occurred_at) < previous_time:
            raise ExecutionJournalError(
                "runtime.journal.time_order", "journal event time moved backwards"
            )
        inherited_authority = (
            authority_receipt_sha256
            if authority_receipt_sha256 is not None
            else (
                HashDigest(previous_mapping["authority_receipt_sha256"])
                if previous_mapping["authority_receipt_sha256"] is not None
                else None
            )
        )
        inherited_observation = (
            workspace_observation_sha256
            if workspace_observation_sha256 is not None
            else (
                HashDigest(previous_mapping["workspace_observation_sha256"])
                if previous_mapping["workspace_observation_sha256"] is not None
                else None
            )
        )
        inherited_reservation = (
            reservation_sha256
            if reservation_sha256 is not None
            else (
                HashDigest(previous_mapping["reservation_sha256"])
                if previous_mapping["reservation_sha256"] is not None
                else None
            )
        )
        event = build_execution_journal_event(
            journal_id=OpaqueId(row["journal_id"]),
            sequence=row["last_sequence"] + 1,
            previous_event_sha256=HashDigest(row["last_event_sha256"]),
            intent_sha256=HashDigest(row["intent_sha256"]),
            authority_receipt_sha256=inherited_authority,
            workspace_observation_sha256=inherited_observation,
            reservation_sha256=inherited_reservation,
            state=state,
            operation_id=operation_id,
            occurred_at=occurred_at,
            reason_codes=tuple(sorted(set(reason_codes))),
            external_reference_id=external_reference_id,
            may_have_started=current in _RECONCILE_ONLY or state in {
                JournalState.DISPATCHING,
                JournalState.DISPATCHED,
                JournalState.RECONCILING,
                JournalState.SUCCEEDED,
                JournalState.PARTIAL,
                JournalState.UNCERTAIN,
                JournalState.RECONCILED,
            },
        )
        connection.execute(
            "INSERT INTO events(journal_id, sequence, event_sha256, event_json) VALUES (?, ?, ?, ?)",
            (
                row["journal_id"],
                event.sequence,
                str(event.event_sha256),
                self._event_json(event),
            ),
        )
        connection.execute(
            """
            UPDATE executions
            SET current_state = ?, last_sequence = ?, last_event_sha256 = ?,
                receipt_json = NULL
            WHERE journal_id = ?
            """,
            (
                state.value,
                event.sequence,
                str(event.event_sha256),
                row["journal_id"],
            ),
        )
        return event

    def finish(
        self,
        journal_id: OpaqueId,
        state: JournalState,
        *,
        occurred_at: str,
        receipt_factory: Callable[[ExecutionJournalEvent], ExecutionReceipt],
        reason_codes: tuple[str, ...] = (),
        operation_id: OpaqueId | None = None,
        external_reference_id: OpaqueId | None = None,
        authority_receipt_sha256: HashDigest | None = None,
        workspace_observation_sha256: HashDigest | None = None,
        reservation_sha256: HashDigest | None = None,
    ) -> tuple[ExecutionJournalEvent, ExecutionReceipt]:
        """Atomically append a terminal event and its exact receipt."""

        if state not in {
            JournalState.SUCCEEDED,
            JournalState.FAILED,
            JournalState.PARTIAL,
            JournalState.UNCERTAIN,
            JournalState.RECONCILED,
        }:
            raise ExecutionJournalError(
                "runtime.journal.finish_state", "journal finish requires a terminal state"
            )
        parse_rfc3339_datetime(occurred_at)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM executions WHERE journal_id = ?", (str(journal_id),)
                ).fetchone()
                if row is None:
                    raise ExecutionJournalError(
                        "runtime.journal.missing", "journal execution does not exist"
                    )
                intent = self._validated_intent_from_row(row)
                event_rows = connection.execute(
                    "SELECT * FROM events WHERE journal_id = ? ORDER BY sequence",
                    (str(journal_id),),
                ).fetchall()
                self._validated_events_from_rows(row, intent, event_rows)
                event = self._append_event_locked(
                    connection,
                    row,
                    state,
                    occurred_at=occurred_at,
                    reason_codes=reason_codes,
                    operation_id=operation_id,
                    external_reference_id=external_reference_id,
                    authority_receipt_sha256=authority_receipt_sha256,
                    workspace_observation_sha256=workspace_observation_sha256,
                    reservation_sha256=reservation_sha256,
                )
                receipt = receipt_factory(event)
                mapping = execution_receipt_to_mapping(receipt)
                if (
                    receipt.journal_id != journal_id
                    or receipt.intent_sha256 != HashDigest(row["intent_sha256"])
                    or receipt.journal_head_sha256 != event.event_sha256
                    or receipt.final_state is not state
                ):
                    raise ExecutionJournalError(
                        "runtime.journal.receipt_rebound",
                        "receipt factory returned evidence for another journal head",
                    )
                payload = canonical_json_bytes(mapping).decode("utf-8")
                existing = connection.execute(
                    "SELECT receipt_json FROM receipts WHERE receipt_sha256 = ?",
                    (str(receipt.receipt_sha256),),
                ).fetchone()
                if existing is not None and existing["receipt_json"] != payload:
                    raise ExecutionJournalError(
                        "runtime.journal.receipt_history_conflict",
                        "receipt digest is bound to different bytes",
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO receipts"
                    "(journal_id, receipt_sha256, final_state, receipt_json) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        str(journal_id),
                        str(receipt.receipt_sha256),
                        state.value,
                        payload,
                    ),
                )
                connection.execute(
                    "UPDATE executions SET receipt_json = ? WHERE journal_id = ?",
                    (payload, str(journal_id)),
                )
                connection.commit()
                return event, receipt
            except Exception:
                connection.rollback()
                raise

    def _decode_intent(self, mapping: dict[str, object]) -> ExecutionIntent:
        from video_factory.domain import ArtifactVersion, IdempotencyKey
        from video_factory.runtime import RuntimeActionKind

        reference = mapping["service_identity_attestation"]
        assert isinstance(reference, dict)
        return ExecutionIntent(
            artifact_version=str(mapping["artifact_version"]),
            intent_id=OpaqueId(str(mapping["intent_id"])),
            intent_sha256=HashDigest(str(mapping["intent_sha256"])),
            request_id=OpaqueId(str(mapping["request_id"])),
            request_sha256=HashDigest(str(mapping["request_sha256"])),
            idempotency_key=IdempotencyKey(str(mapping["idempotency_key"])),
            action_kind=RuntimeActionKind(str(mapping["action_kind"])),
            action_id=OpaqueId(str(mapping["action_id"])),
            planned_effect_ids=tuple(
                OpaqueId(str(item)) for item in mapping["planned_effect_ids"]
            ),
            workspace_id=OpaqueId(str(mapping["workspace_id"])),
            plan_sha256=HashDigest(str(mapping["plan_sha256"])),
            gate_context_sha256=HashDigest(str(mapping["gate_context_sha256"])),
            workspace_observation_sha256=HashDigest(
                str(mapping["workspace_observation_sha256"])
            ),
            authority_decision_sha256=HashDigest(
                str(mapping["authority_decision_sha256"])
            ),
            authority_receipt_sha256=HashDigest(
                str(mapping["authority_receipt_sha256"])
            ) if mapping["authority_receipt_sha256"] is not None else None,
            service_identity=OpaqueId(str(mapping["service_identity"])),
            service_identity_attestation=ArtifactReference(
                path=RelativeArtifactPath(str(reference["path"])),
                sha256=HashDigest(str(reference["sha256"])),
                artifact_version=ArtifactVersion(str(reference["artifact_version"])),
            ),
            credential_handle_id=(
                OpaqueId(str(mapping["credential_handle_id"]))
                if mapping["credential_handle_id"] is not None
                else None
            ),
            credential_verification=(
                ArtifactReference(
                    path=RelativeArtifactPath(
                        str(mapping["credential_verification"]["path"])
                    ),
                    sha256=HashDigest(
                        str(mapping["credential_verification"]["sha256"])
                    ),
                    artifact_version=ArtifactVersion(
                        str(mapping["credential_verification"]["artifact_version"])
                    ),
                )
                if mapping["credential_verification"] is not None
                else None
            ),
            destination=(
                str(mapping["destination"])
                if mapping["destination"] is not None
                else None
            ),
            created_at=str(mapping["created_at"]),
        )

    def _decode_event(self, mapping: dict[str, object]) -> ExecutionJournalEvent:
        return ExecutionJournalEvent(
            artifact_version=str(mapping["artifact_version"]),
            event_id=OpaqueId(str(mapping["event_id"])),
            event_sha256=HashDigest(str(mapping["event_sha256"])),
            journal_id=OpaqueId(str(mapping["journal_id"])),
            sequence=int(mapping["sequence"]),
            previous_event_sha256=(
                HashDigest(str(mapping["previous_event_sha256"]))
                if mapping["previous_event_sha256"] is not None
                else None
            ),
            intent_sha256=HashDigest(str(mapping["intent_sha256"])),
            authority_receipt_sha256=(
                HashDigest(str(mapping["authority_receipt_sha256"]))
                if mapping["authority_receipt_sha256"] is not None
                else None
            ),
            workspace_observation_sha256=(
                HashDigest(str(mapping["workspace_observation_sha256"]))
                if mapping["workspace_observation_sha256"] is not None
                else None
            ),
            reservation_sha256=(
                HashDigest(str(mapping["reservation_sha256"]))
                if mapping["reservation_sha256"] is not None
                else None
            ),
            state=JournalState(str(mapping["state"])),
            operation_id=(
                OpaqueId(str(mapping["operation_id"]))
                if mapping["operation_id"] is not None
                else None
            ),
            occurred_at=str(mapping["occurred_at"]),
            reason_codes=tuple(str(item) for item in mapping["reason_codes"]),
            external_reference_id=(
                OpaqueId(str(mapping["external_reference_id"]))
                if mapping["external_reference_id"] is not None
                else None
            ),
            may_have_started=bool(mapping["may_have_started"]),
        )

    def _decode_receipt(self, mapping: dict[str, object]) -> ExecutionReceipt:
        return execution_receipt_from_mapping(mapping)

    def load(self, journal_id: OpaqueId) -> JournalSnapshot:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM executions WHERE journal_id = ?", (str(journal_id),)
            ).fetchone()
            if row is None:
                raise ExecutionJournalError(
                    "runtime.journal.missing", "journal execution does not exist"
                )
            event_rows = connection.execute(
                "SELECT * FROM events WHERE journal_id = ? ORDER BY sequence",
                (str(journal_id),),
            ).fetchall()
            receipt_rows = connection.execute(
                "SELECT receipt_sha256, final_state, receipt_json "
                "FROM receipts WHERE journal_id = ? ORDER BY receipt_sha256",
                (str(journal_id),),
            ).fetchall()
        try:
            intent = self._validated_intent_from_row(row)
            events = self._validated_events_from_rows(row, intent, event_rows)
            event_by_sha = {str(event.event_sha256): event for event in events}
            receipt = (
                self._decode_receipt(json.loads(row["receipt_json"]))
                if row["receipt_json"] is not None
                else None
            )
            if receipt is not None:
                execution_receipt_to_mapping(receipt)
                if (
                    receipt.journal_id != OpaqueId(row["journal_id"])
                    or receipt.intent_sha256 != intent.intent_sha256
                    or receipt.final_state.value != row["current_state"]
                    or str(receipt.journal_head_sha256) != row["last_event_sha256"]
                ):
                    raise ExecutionJournalError(
                        "runtime.journal.receipt_head",
                        "current receipt is not bound to the current journal head",
                    )
            for history_row in receipt_rows:
                historical = self._decode_receipt(json.loads(history_row["receipt_json"]))
                execution_receipt_to_mapping(historical)
                head_event = event_by_sha.get(str(historical.journal_head_sha256))
                if (
                    str(historical.receipt_sha256) != history_row["receipt_sha256"]
                    or historical.journal_id != OpaqueId(row["journal_id"])
                    or historical.intent_sha256 != intent.intent_sha256
                    or historical.final_state.value != history_row["final_state"]
                    or head_event is None
                    or head_event.state is not historical.final_state
                ):
                    raise ExecutionJournalError(
                        "runtime.journal.receipt_history",
                        "receipt history digest is invalid",
                    )
            state = JournalState(row["current_state"])
            return JournalSnapshot(
                journal_id=OpaqueId(row["journal_id"]),
                intent=intent,
                state=state,
                events=events,
                receipt=receipt,
                reconcile_only=state in _RECONCILE_ONLY,
            )
        except ExecutionJournalError:
            raise
        except Exception as error:
            raise ExecutionJournalError(
                "runtime.journal.corrupt", "journal content is invalid"
            ) from error

    def load_by_intent(self, intent: ExecutionIntent) -> JournalSnapshot | None:
        execution_intent_to_mapping(intent)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT journal_id, request_sha256, intent_sha256 FROM executions WHERE scope_key = ?",
                (self._scope_key(intent),),
            ).fetchone()
        if row is None:
            return None
        if (
            row["request_sha256"] != str(intent.request_sha256)
            or row["intent_sha256"] != str(intent.intent_sha256)
        ):
            raise ExecutionJournalError(
                "runtime.journal.idempotency_mismatch",
                "idempotency scope is bound to another request or intent",
            )
        return self.load(OpaqueId(row["journal_id"]))

    def load_by_scope(
        self,
        *,
        action_kind: RuntimeActionKind,
        action_id: OpaqueId,
        idempotency_key: IdempotencyKey,
        request_sha256: HashDigest,
    ) -> JournalSnapshot | None:
        """Load an exact idempotency scope without minting fresh authority.

        This read path exists so an already-terminal exact request can return
        its durable receipt even when current authority or workspace evidence
        has since expired.  Different request bytes remain a hard conflict.
        """

        scope_key = ":".join(
            (action_kind.value, str(action_id), str(idempotency_key))
        )
        with self._connect() as connection:
            row = connection.execute(
                "SELECT journal_id, request_sha256 FROM executions WHERE scope_key = ?",
                (scope_key,),
            ).fetchone()
        if row is None:
            return None
        if row["request_sha256"] != str(request_sha256):
            raise ExecutionJournalError(
                "runtime.journal.idempotency_mismatch",
                "idempotency scope is bound to another request",
            )
        return self.load(OpaqueId(row["journal_id"]))

    def receipt_history(self, journal_id: OpaqueId) -> tuple[ExecutionReceipt, ...]:
        """Return immutable receipts in durable insertion order."""

        snapshot = self.load(journal_id)
        event_by_sha = {
            event.event_sha256: event for event in snapshot.events
        }
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM executions WHERE journal_id = ?", (str(journal_id),)
            ).fetchone()
            if exists is None:
                raise ExecutionJournalError(
                    "runtime.journal.missing", "journal execution does not exist"
                )
            rows = connection.execute(
                "SELECT receipt_sha256, final_state, receipt_json "
                "FROM receipts WHERE journal_id = ? ORDER BY rowid",
                (str(journal_id),),
            ).fetchall()
        try:
            receipts = tuple(
                self._decode_receipt(json.loads(row["receipt_json"])) for row in rows
            )
            for row, receipt in zip(rows, receipts, strict=True):
                execution_receipt_to_mapping(receipt)
                head = event_by_sha.get(receipt.journal_head_sha256)
                if (
                    receipt.journal_id != journal_id
                    or receipt.intent_sha256 != snapshot.intent.intent_sha256
                    or str(receipt.receipt_sha256) != row["receipt_sha256"]
                    or receipt.final_state.value != row["final_state"]
                    or head is None
                    or head.state is not receipt.final_state
                ):
                    raise ExecutionJournalError(
                        "runtime.journal.receipt_history",
                        "historical receipt is not bound to the exact journal history",
                    )
            return receipts
        except ExecutionJournalError:
            raise
        except Exception as error:
            raise ExecutionJournalError(
                "runtime.journal.receipt_history",
                "historical receipt is invalid",
            ) from error

    def unresolved(self) -> tuple[JournalSnapshot, ...]:
        states = tuple(
            state.value for state in sorted(_RECONCILE_ONLY, key=lambda item: item.value)
        )
        placeholders = ", ".join("?" for _ in states)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT journal_id FROM executions WHERE current_state IN ({placeholders}) ORDER BY journal_id",
                states,
            ).fetchall()
        return tuple(self.load(OpaqueId(row["journal_id"])) for row in rows)

__all__ = [
    "AuthorityReservationClaim",
    "ExecutionJournalError",
    "JournalSnapshot",
    "SCHEMA_VERSION",
    "SQLiteExecutionJournal",
    "journal_event_reference",
]
