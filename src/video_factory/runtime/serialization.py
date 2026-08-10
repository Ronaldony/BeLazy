"""Strict canonical serialization for W06 pure runtime evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import re

from video_factory.artifacts import validate_artifact_mapping
from video_factory.config.canonical import canonical_json_bytes, canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    IdempotencyKey,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.json_boundary import (
    JsonInputError,
    parse_json_bytes,
    parse_rfc3339_datetime,
    require_json_object,
)
from video_factory.mutation.paths import MutationPathError, require_managed_path

from .contracts import (
    EXECUTION_INTENT_VERSION,
    EXECUTION_RECEIPT_VERSION,
    JOURNAL_EVENT_VERSION,
    MIGRATION_CUTOVER_VERSION,
    PROJECTION_PARITY_VERSION,
    PUBLICATION_INTENT_VERSION,
    PUBLICATION_RECEIPT_VERSION,
    ExecutionIntent,
    ExecutionJournalEvent,
    ExecutionReceipt,
    JournalState,
    MigrationCutoverState,
    MigrationMode,
    PublicationIntent,
    PublicationReceipt,
    PublicationStatus,
    ProjectionParityReceipt,
    RuntimeActionKind,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_VERSION = re.compile(r"^[a-z][a-z0-9-]{0,63}/[0-9]+\.[0-9]+$")
_TERMINAL = frozenset(
    {
        JournalState.SUCCEEDED,
        JournalState.FAILED,
        JournalState.PARTIAL,
        JournalState.UNCERTAIN,
        JournalState.RECONCILED,
    }
)


class RuntimeContractError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _reject(reason_code: str, message: str) -> None:
    raise RuntimeContractError(reason_code, message)


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _reject("runtime.contract.sha256", f"{label} must be a lowercase SHA-256")
    return value


def _token(value: object, label: str) -> str:
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        _reject("runtime.contract.token", f"{label} must be an opaque token")
    return value


def _version(value: object, label: str) -> str:
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        _reject("runtime.contract.version", f"{label} must be kind/major.minor")
    return value


def _time(value: object, label: str) -> str:
    if not isinstance(value, str):
        _reject("runtime.contract.datetime", f"{label} must be RFC 3339")
    try:
        parse_rfc3339_datetime(value)
    except ValueError as error:
        raise RuntimeContractError(
            "runtime.contract.datetime", f"{label} must be RFC 3339"
        ) from error
    return value


def _ref(value: ArtifactReference, label: str) -> dict[str, object]:
    if not isinstance(value, ArtifactReference):
        _reject("runtime.contract.reference", f"{label} must be an artifact reference")
    try:
        path = require_managed_path(value.path, label=f"{label}.path")
    except MutationPathError as error:
        raise RuntimeContractError(error.reason_code, str(error)) from error
    return {
        "path": str(path),
        "sha256": _sha(str(value.sha256), f"{label}.sha256"),
        "artifact_version": _version(str(value.artifact_version), f"{label}.version"),
    }


def _ref_from_mapping(value: object, label: str) -> ArtifactReference:
    if not isinstance(value, Mapping) or set(value) != {
        "path",
        "sha256",
        "artifact_version",
    }:
        _reject("runtime.contract.reference", f"{label} must be an exact reference")
    return ArtifactReference(
        RelativeArtifactPath(str(value["path"])),
        HashDigest(str(value["sha256"])),
        ArtifactVersion(str(value["artifact_version"])),
    )


def _exact_mapping(
    document: Mapping[str, object], expected: frozenset[str], label: str
) -> dict[str, object]:
    value = dict(document)
    if set(value) != expected:
        _reject("runtime.contract.mapping", f"{label} fields are not exact")
    return value


def _optional_sha(value: object) -> HashDigest | None:
    return HashDigest(str(value)) if value is not None else None


def _optional_token(value: object) -> OpaqueId | None:
    return OpaqueId(str(value)) if value is not None else None


def _optional_ref(value: object, label: str) -> ArtifactReference | None:
    return _ref_from_mapping(value, label) if value is not None else None


def _object_array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        _reject("runtime.contract.array", f"{label} must be an array")
    return value


def _schema_validated(mapping: dict[str, object]) -> dict[str, object]:
    report = validate_artifact_mapping(mapping)
    if not report.ok:
        _reject(
            "runtime.contract.schema",
            "runtime artifact mapping violates its registered schema",
        )
    return mapping


def _reason_codes(values: tuple[str, ...]) -> list[str]:
    if not isinstance(values, tuple) or any(
        not isinstance(item, str) or not item for item in values
    ):
        _reject("runtime.contract.reason_codes", "reason codes must be non-empty strings")
    if values != tuple(sorted(set(values))):
        _reject("runtime.contract.reason_codes", "reason codes must be sorted and unique")
    return list(values)


def _intent_identity(value: ExecutionIntent) -> dict[str, object]:
    if value.artifact_version != EXECUTION_INTENT_VERSION:
        _reject("runtime.intent.version", "unsupported execution intent version")
    if not isinstance(value.action_kind, RuntimeActionKind):
        _reject("runtime.intent.action_kind", "execution action kind is invalid")
    if (
        not isinstance(value.planned_effect_ids, tuple)
        or len(set(value.planned_effect_ids)) != len(value.planned_effect_ids)
        or any(not isinstance(item, str) for item in value.planned_effect_ids)
        or (
            value.action_kind is RuntimeActionKind.MUTATION
            and not value.planned_effect_ids
        )
        or (
            value.action_kind is not RuntimeActionKind.MUTATION
            and value.planned_effect_ids
        )
    ):
        _reject(
            "runtime.intent.effect_plan",
            "planned effect ids must be exact, unique, and mutation-only",
        )
    destination = value.destination
    if value.action_kind is RuntimeActionKind.PUBLICATION:
        if not isinstance(destination, str) or not destination:
            _reject("runtime.intent.destination", "publication requires a destination")
        if value.credential_handle_id is None or value.credential_verification is None:
            _reject(
                "runtime.intent.credential",
                "publication requires a broker-issued credential lease and verification",
            )
    elif destination is not None and (not isinstance(destination, str) or not destination):
        _reject("runtime.intent.destination", "destination is invalid")
    if value.credential_handle_id is not None:
        _token(str(value.credential_handle_id), "credential_handle_id")
    if (value.credential_handle_id is None) != (value.credential_verification is None):
        _reject(
            "runtime.intent.credential",
            "credential lease id and broker verification must be present together",
        )
    if (
        value.credential_verification is not None
        and str(value.credential_verification.artifact_version)
        != "credential-registration-attestation/1.0"
    ):
        _reject(
            "runtime.intent.credential",
            "durable credential evidence must be a broker registration attestation",
        )
    return {
        "artifact_version": value.artifact_version,
        "request_id": _token(str(value.request_id), "request_id"),
        "request_sha256": _sha(str(value.request_sha256), "request_sha256"),
        "idempotency_key": _token(str(value.idempotency_key), "idempotency_key"),
        "action_kind": value.action_kind.value,
        "action_id": _token(str(value.action_id), "action_id"),
        "planned_effect_ids": [
            _token(str(item), "planned_effect_ids")
            for item in value.planned_effect_ids
        ],
        "workspace_id": _token(str(value.workspace_id), "workspace_id"),
        "plan_sha256": _sha(str(value.plan_sha256), "plan_sha256"),
        "gate_context_sha256": _sha(str(value.gate_context_sha256), "gate_context_sha256"),
        "workspace_observation_sha256": _sha(
            str(value.workspace_observation_sha256), "workspace_observation_sha256"
        ),
        "authority_decision_sha256": _sha(
            str(value.authority_decision_sha256), "authority_decision_sha256"
        ),
        "authority_receipt_sha256": (
            _sha(str(value.authority_receipt_sha256), "authority_receipt_sha256")
            if value.authority_receipt_sha256 is not None
            else None
        ),
        "service_identity": _token(str(value.service_identity), "service_identity"),
        "service_identity_attestation": _ref(
            value.service_identity_attestation, "service_identity_attestation"
        ),
        "credential_handle_id": (
            str(value.credential_handle_id)
            if value.credential_handle_id is not None
            else None
        ),
        "credential_verification": (
            _ref(value.credential_verification, "credential_verification")
            if value.credential_verification is not None
            else None
        ),
        "destination": destination,
        "created_at": _time(value.created_at, "created_at"),
    }


def execution_intent_to_mapping(value: ExecutionIntent) -> dict[str, object]:
    identity = _intent_identity(value)
    digest = canonical_sha256(identity)
    if value.intent_sha256 != digest or str(value.intent_id) != f"execution-intent-{str(digest)[:20]}":
        _reject("runtime.intent.identity", "execution intent identity mismatch")
    return _schema_validated({
        **identity,
        "intent_id": str(value.intent_id),
        "intent_sha256": str(value.intent_sha256),
    })


def execution_intent_from_mapping(
    document: Mapping[str, object],
) -> ExecutionIntent:
    value = _exact_mapping(
        document,
        frozenset(
            {
                "artifact_version", "intent_id", "intent_sha256", "request_id",
                "request_sha256", "idempotency_key", "action_kind", "action_id",
                "planned_effect_ids", "workspace_id", "plan_sha256", "gate_context_sha256",
                "workspace_observation_sha256", "authority_decision_sha256",
                "authority_receipt_sha256", "service_identity",
                "service_identity_attestation", "credential_handle_id",
                "credential_verification", "destination",
                "created_at",
            }
        ),
        "execution intent",
    )
    destination = value["destination"]
    intent = ExecutionIntent(
        artifact_version=str(value["artifact_version"]),
        intent_id=OpaqueId(str(value["intent_id"])),
        intent_sha256=HashDigest(str(value["intent_sha256"])),
        request_id=OpaqueId(str(value["request_id"])),
        request_sha256=HashDigest(str(value["request_sha256"])),
        idempotency_key=IdempotencyKey(str(value["idempotency_key"])),
        action_kind=RuntimeActionKind(str(value["action_kind"])),
        action_id=OpaqueId(str(value["action_id"])),
        planned_effect_ids=tuple(
            OpaqueId(str(item))
            for item in _object_array(
                value["planned_effect_ids"], "planned_effect_ids"
            )
        ),
        workspace_id=OpaqueId(str(value["workspace_id"])),
        plan_sha256=HashDigest(str(value["plan_sha256"])),
        gate_context_sha256=HashDigest(str(value["gate_context_sha256"])),
        workspace_observation_sha256=HashDigest(
            str(value["workspace_observation_sha256"])
        ),
        authority_decision_sha256=HashDigest(
            str(value["authority_decision_sha256"])
        ),
        authority_receipt_sha256=_optional_sha(value["authority_receipt_sha256"]),
        service_identity=OpaqueId(str(value["service_identity"])),
        service_identity_attestation=_ref_from_mapping(
            value["service_identity_attestation"], "service_identity_attestation"
        ),
        credential_handle_id=_optional_token(value["credential_handle_id"]),
        credential_verification=_optional_ref(
            value["credential_verification"], "credential_verification"
        ),
        destination=str(destination) if destination is not None else None,
        created_at=str(value["created_at"]),
    )
    execution_intent_to_mapping(intent)
    return intent


def build_execution_intent(**values: object) -> ExecutionIntent:
    values.setdefault("planned_effect_ids", ())
    provisional = ExecutionIntent(
        artifact_version=EXECUTION_INTENT_VERSION,
        intent_id=OpaqueId("pending"),
        intent_sha256=HashDigest("0" * 64),
        **values,
    )
    digest = canonical_sha256(_intent_identity(provisional))
    return replace(
        provisional,
        intent_id=OpaqueId(f"execution-intent-{str(digest)[:20]}"),
        intent_sha256=digest,
    )


def _event_identity(value: ExecutionJournalEvent) -> dict[str, object]:
    if value.artifact_version != JOURNAL_EVENT_VERSION:
        _reject("runtime.journal.version", "unsupported journal event version")
    if not isinstance(value.state, JournalState):
        _reject("runtime.journal.state", "journal state is invalid")
    if not isinstance(value.sequence, int) or isinstance(value.sequence, bool) or value.sequence < 0:
        _reject("runtime.journal.sequence", "journal sequence must be non-negative")
    if (value.sequence == 0) != (value.previous_event_sha256 is None):
        _reject("runtime.journal.chain", "only the first event may omit its predecessor")
    if value.previous_event_sha256 is not None:
        _sha(str(value.previous_event_sha256), "previous_event_sha256")
    authority_receipt = (
        _sha(str(value.authority_receipt_sha256), "authority_receipt_sha256")
        if value.authority_receipt_sha256 is not None
        else None
    )
    workspace_observation = (
        _sha(
            str(value.workspace_observation_sha256),
            "workspace_observation_sha256",
        )
        if value.workspace_observation_sha256 is not None
        else None
    )
    reservation = (
        _sha(str(value.reservation_sha256), "reservation_sha256")
        if value.reservation_sha256 is not None
        else None
    )
    if value.state is JournalState.PLANNED and any(
        item is not None
        for item in (authority_receipt, workspace_observation, reservation)
    ):
        _reject(
            "runtime.journal.phase_binding",
            "planned event cannot claim authority or reservation",
        )
    if value.state is JournalState.AUTHORIZED and (
        authority_receipt is None
        or workspace_observation is None
        or reservation is not None
    ):
        _reject(
            "runtime.journal.phase_binding",
            "authorized event requires fresh authority and workspace evidence only",
        )
    if value.state is JournalState.FAILED and (
        (authority_receipt is None) != (workspace_observation is None)
        or (authority_receipt is None and reservation is not None)
    ):
        _reject(
            "runtime.journal.phase_binding",
            "failed event carries an incomplete authority phase binding",
        )
    if value.state not in {
        JournalState.PLANNED,
        JournalState.AUTHORIZED,
        JournalState.FAILED,
    } and (
        authority_receipt is None
        or workspace_observation is None
        or reservation is None
    ):
        _reject(
            "runtime.journal.phase_binding",
            "reserved and later events require authority, workspace, and reservation",
        )
    if not isinstance(value.may_have_started, bool):
        _reject("runtime.journal.may_have_started", "may_have_started must be boolean")
    if value.state in {
        JournalState.PLANNED,
        JournalState.AUTHORIZED,
        JournalState.RESERVED,
    } and value.may_have_started:
        _reject(
            "runtime.journal.may_have_started",
            "pre-dispatch work cannot have started",
        )
    if value.state in {
        JournalState.DISPATCHING,
        JournalState.DISPATCHED,
        JournalState.RECONCILING,
        JournalState.PARTIAL,
        JournalState.UNCERTAIN,
    } and value.may_have_started is not True:
        _reject(
            "runtime.journal.may_have_started",
            "dispatch and unresolved states must preserve may-have-started",
        )
    if value.operation_id is not None:
        _token(str(value.operation_id), "operation_id")
    if value.external_reference_id is not None:
        _token(str(value.external_reference_id), "external_reference_id")
    return {
        "artifact_version": value.artifact_version,
        "journal_id": _token(str(value.journal_id), "journal_id"),
        "sequence": value.sequence,
        "previous_event_sha256": (
            str(value.previous_event_sha256)
            if value.previous_event_sha256 is not None
            else None
        ),
        "intent_sha256": _sha(str(value.intent_sha256), "intent_sha256"),
        "authority_receipt_sha256": authority_receipt,
        "workspace_observation_sha256": workspace_observation,
        "reservation_sha256": reservation,
        "state": value.state.value,
        "operation_id": str(value.operation_id) if value.operation_id is not None else None,
        "occurred_at": _time(value.occurred_at, "occurred_at"),
        "reason_codes": _reason_codes(value.reason_codes),
        "external_reference_id": (
            str(value.external_reference_id)
            if value.external_reference_id is not None
            else None
        ),
        "may_have_started": value.may_have_started,
    }


def execution_journal_event_to_mapping(value: ExecutionJournalEvent) -> dict[str, object]:
    identity = _event_identity(value)
    digest = canonical_sha256(identity)
    if value.event_sha256 != digest or str(value.event_id) != f"journal-event-{str(digest)[:20]}":
        _reject("runtime.journal.identity", "journal event identity mismatch")
    return _schema_validated({
        **identity,
        "event_id": str(value.event_id),
        "event_sha256": str(value.event_sha256),
    })


def execution_journal_event_from_mapping(
    document: Mapping[str, object],
) -> ExecutionJournalEvent:
    value = _exact_mapping(
        document,
        frozenset(
            {
                "artifact_version", "event_id", "event_sha256", "journal_id",
                "sequence", "previous_event_sha256", "intent_sha256",
                "authority_receipt_sha256", "workspace_observation_sha256",
                "reservation_sha256", "state", "operation_id", "occurred_at",
                "reason_codes", "external_reference_id", "may_have_started",
            }
        ),
        "execution journal event",
    )
    reasons = _object_array(value["reason_codes"], "reason_codes")
    event = ExecutionJournalEvent(
        artifact_version=str(value["artifact_version"]),
        event_id=OpaqueId(str(value["event_id"])),
        event_sha256=HashDigest(str(value["event_sha256"])),
        journal_id=OpaqueId(str(value["journal_id"])),
        sequence=value["sequence"],
        previous_event_sha256=_optional_sha(value["previous_event_sha256"]),
        intent_sha256=HashDigest(str(value["intent_sha256"])),
        authority_receipt_sha256=_optional_sha(value["authority_receipt_sha256"]),
        workspace_observation_sha256=_optional_sha(
            value["workspace_observation_sha256"]
        ),
        reservation_sha256=_optional_sha(value["reservation_sha256"]),
        state=JournalState(str(value["state"])),
        operation_id=_optional_token(value["operation_id"]),
        occurred_at=str(value["occurred_at"]),
        reason_codes=tuple(str(item) for item in reasons),
        external_reference_id=_optional_token(value["external_reference_id"]),
        may_have_started=value["may_have_started"],
    )
    execution_journal_event_to_mapping(event)
    return event


def build_execution_journal_event(**values: object) -> ExecutionJournalEvent:
    provisional = ExecutionJournalEvent(
        artifact_version=JOURNAL_EVENT_VERSION,
        event_id=OpaqueId("pending"),
        event_sha256=HashDigest("0" * 64),
        **values,
    )
    digest = canonical_sha256(_event_identity(provisional))
    return replace(
        provisional,
        event_id=OpaqueId(f"journal-event-{str(digest)[:20]}"),
        event_sha256=digest,
    )


def _receipt_identity(value: ExecutionReceipt) -> dict[str, object]:
    if value.artifact_version != EXECUTION_RECEIPT_VERSION:
        _reject("runtime.receipt.version", "unsupported execution receipt version")
    if value.final_state not in _TERMINAL:
        _reject("runtime.receipt.state", "execution receipt must be terminal")
    started = parse_rfc3339_datetime(_time(value.started_at, "started_at"))
    completed = parse_rfc3339_datetime(_time(value.completed_at, "completed_at"))
    if completed < started:
        _reject("runtime.receipt.time_order", "execution completed before it started")
    if value.authority_effect != "none":
        _reject("runtime.receipt.authority", "execution evidence cannot create authority")
    if value.authority_receipt_sha256 is None:
        if value.final_state is not JournalState.FAILED:
            _reject(
                "runtime.receipt.authority_missing",
                "only a definite pre-authorization failure may omit authority evidence",
            )
        if value.settlement_record is not None:
            _reject(
                "runtime.receipt.pre_authorization_effect",
                "pre-authorization failure cannot report reservation settlement",
            )
    if value.measured_cost_minor_units is not None and (
        not isinstance(value.measured_cost_minor_units, int)
        or isinstance(value.measured_cost_minor_units, bool)
        or value.measured_cost_minor_units < 0
    ):
        _reject("runtime.receipt.cost", "measured cost is invalid")
    if (value.measured_cost_minor_units is None) != (value.currency is None):
        _reject("runtime.receipt.cost", "cost and currency must be present together")
    if value.currency is not None and not value.currency:
        _reject("runtime.receipt.currency", "currency cannot be empty")
    if value.final_state is JournalState.RECONCILED:
        if value.reconciliation_record is None:
            _reject("runtime.receipt.reconciliation", "reconciled receipt needs evidence")
    elif value.reconciliation_record is not None:
        _reject("runtime.receipt.reconciliation", "only reconciled receipt may carry evidence")
    if value.after_workspace_observation_sha256 is None and value.final_state not in {
        JournalState.PARTIAL,
        JournalState.UNCERTAIN,
    }:
        _reject(
            "runtime.receipt.workspace_after",
            "only partial or uncertain execution may lack after-workspace evidence",
        )
    return {
        "artifact_version": value.artifact_version,
        "intent_sha256": _sha(str(value.intent_sha256), "intent_sha256"),
        "journal_id": _token(str(value.journal_id), "journal_id"),
        "journal_head_sha256": _sha(str(value.journal_head_sha256), "journal_head_sha256"),
        "final_state": value.final_state.value,
        "started_at": value.started_at,
        "completed_at": value.completed_at,
        "output_refs": [_ref(item, "output_ref") for item in value.output_refs],
        "measured_cost_minor_units": value.measured_cost_minor_units,
        "currency": value.currency,
        "before_workspace_observation_sha256": _sha(
            str(value.before_workspace_observation_sha256),
            "before_workspace_observation_sha256",
        ),
        "after_workspace_observation_sha256": (
            _sha(
                str(value.after_workspace_observation_sha256),
                "after_workspace_observation_sha256",
            )
            if value.after_workspace_observation_sha256 is not None
            else None
        ),
        "authority_receipt_sha256": (
            _sha(str(value.authority_receipt_sha256), "authority_receipt_sha256")
            if value.authority_receipt_sha256 is not None
            else None
        ),
        "settlement_record": (
            _ref(value.settlement_record, "settlement_record")
            if value.settlement_record is not None
            else None
        ),
        "reconciliation_record": (
            _ref(value.reconciliation_record, "reconciliation_record")
            if value.reconciliation_record is not None
            else None
        ),
        "reason_codes": _reason_codes(value.reason_codes),
        "authority_effect": value.authority_effect,
    }



def execution_receipt_to_mapping(value: ExecutionReceipt) -> dict[str, object]:
    identity = _receipt_identity(value)
    digest = canonical_sha256(identity)
    if value.receipt_sha256 != digest or str(value.receipt_id) != f"execution-receipt-{str(digest)[:20]}":
        _reject("runtime.receipt.identity", "execution receipt identity mismatch")
    return _schema_validated({
        **identity,
        "receipt_id": str(value.receipt_id),
        "receipt_sha256": str(value.receipt_sha256),
    })


def execution_receipt_from_mapping(
    document: Mapping[str, object],
) -> ExecutionReceipt:
    value = _exact_mapping(
        document,
        frozenset(
            {
                "artifact_version", "receipt_id", "receipt_sha256", "intent_sha256",
                "journal_id", "journal_head_sha256", "final_state", "started_at",
                "completed_at", "output_refs", "measured_cost_minor_units", "currency",
                "before_workspace_observation_sha256",
                "after_workspace_observation_sha256", "authority_receipt_sha256",
                "settlement_record", "reconciliation_record", "reason_codes",
                "authority_effect",
            }
        ),
        "execution receipt",
    )
    outputs = _object_array(value["output_refs"], "output_refs")
    reasons = _object_array(value["reason_codes"], "reason_codes")
    cost = value["measured_cost_minor_units"]
    currency = value["currency"]
    receipt = ExecutionReceipt(
        artifact_version=str(value["artifact_version"]),
        receipt_id=OpaqueId(str(value["receipt_id"])),
        receipt_sha256=HashDigest(str(value["receipt_sha256"])),
        intent_sha256=HashDigest(str(value["intent_sha256"])),
        journal_id=OpaqueId(str(value["journal_id"])),
        journal_head_sha256=HashDigest(str(value["journal_head_sha256"])),
        final_state=JournalState(str(value["final_state"])),
        started_at=str(value["started_at"]),
        completed_at=str(value["completed_at"]),
        output_refs=tuple(
            _ref_from_mapping(item, "output_ref") for item in outputs
        ),
        measured_cost_minor_units=cost if cost is not None else None,
        currency=str(currency) if currency is not None else None,
        before_workspace_observation_sha256=HashDigest(
            str(value["before_workspace_observation_sha256"])
        ),
        after_workspace_observation_sha256=_optional_sha(
            value["after_workspace_observation_sha256"]
        ),
        authority_receipt_sha256=_optional_sha(value["authority_receipt_sha256"]),
        settlement_record=_optional_ref(value["settlement_record"], "settlement_record"),
        reconciliation_record=_optional_ref(
            value["reconciliation_record"], "reconciliation_record"
        ),
        reason_codes=tuple(str(item) for item in reasons),
        authority_effect=str(value["authority_effect"]),
    )
    execution_receipt_to_mapping(receipt)
    return receipt


def build_execution_receipt(**values: object) -> ExecutionReceipt:
    provisional = ExecutionReceipt(
        artifact_version=EXECUTION_RECEIPT_VERSION,
        receipt_id=OpaqueId("pending"),
        receipt_sha256=HashDigest("0" * 64),
        **values,
    )
    digest = canonical_sha256(_receipt_identity(provisional))
    return replace(
        provisional,
        receipt_id=OpaqueId(f"execution-receipt-{str(digest)[:20]}"),
        receipt_sha256=digest,
    )


def _publication_intent_identity(value: PublicationIntent) -> dict[str, object]:
    if value.artifact_version != PUBLICATION_INTENT_VERSION:
        _reject("runtime.publication.intent_version", "unsupported publication intent")
    if value.authority_effect != "none":
        _reject(
            "runtime.publication.intent_authority",
            "publication intent cannot create authority",
        )
    if (
        not isinstance(value.cost_minor_units, int)
        or isinstance(value.cost_minor_units, bool)
        or value.cost_minor_units < 0
        or not isinstance(value.retry_index, int)
        or isinstance(value.retry_index, bool)
        or value.retry_index < 0
        or not isinstance(value.currency, str)
        or not value.currency
    ):
        _reject(
            "runtime.publication.intent_limits",
            "publication limits are invalid",
        )
    candidate = _ref(value.release_candidate, "release_candidate")
    assessment = _ref(value.release_assessment, "release_assessment")
    destination = _ref(value.destination, "destination")
    paths = tuple(
        item["path"].casefold() for item in (candidate, assessment, destination)
    )
    if len(paths) != len(set(paths)):
        _reject(
            "runtime.publication.intent_alias",
            "publication intent references contain a path alias",
        )
    return {
        "artifact_version": value.artifact_version,
        "request_id": _token(str(value.request_id), "request_id"),
        "idempotency_key": _token(
            str(value.idempotency_key), "idempotency_key"
        ),
        "release_candidate": candidate,
        "release_candidate_sha256": _sha(
            str(value.release_candidate_sha256), "release_candidate_sha256"
        ),
        "release_assessment": assessment,
        "release_assessment_sha256": _sha(
            str(value.release_assessment_sha256), "release_assessment_sha256"
        ),
        "destination": destination,
        "destination_sha256": _sha(
            str(value.destination_sha256), "destination_sha256"
        ),
        "workspace_id": _token(str(value.workspace_id), "workspace_id"),
        "channel_id": _token(str(value.channel_id), "channel_id"),
        "concept_id": _token(str(value.concept_id), "concept_id"),
        "episode_id": _token(str(value.episode_id), "episode_id"),
        "gate_context_sha256": _sha(
            str(value.gate_context_sha256), "gate_context_sha256"
        ),
        "workspace_observation_sha256": _sha(
            str(value.workspace_observation_sha256),
            "workspace_observation_sha256",
        ),
        "publisher_id": _token(str(value.publisher_id), "publisher_id"),
        "publisher_variant_id": _token(
            str(value.publisher_variant_id), "publisher_variant_id"
        ),
        "destination_id": _token(str(value.destination_id), "destination_id"),
        "cost_minor_units": value.cost_minor_units,
        "currency": value.currency,
        "retry_index": value.retry_index,
        "created_at": _time(value.created_at, "created_at"),
        "authority_effect": value.authority_effect,
    }


def publication_intent_to_mapping(value: PublicationIntent) -> dict[str, object]:
    identity = _publication_intent_identity(value)
    digest = canonical_sha256(identity)
    if (
        value.intent_sha256 != digest
        or str(value.intent_id) != f"publication-intent-{str(digest)[:20]}"
    ):
        _reject(
            "runtime.publication.intent_identity",
            "publication intent identity mismatch",
        )
    return _schema_validated({
        **identity,
        "intent_id": str(value.intent_id),
        "intent_sha256": str(value.intent_sha256),
    })


def publication_intent_from_mapping(
    document: Mapping[str, object],
) -> PublicationIntent:
    value = _exact_mapping(
        document,
        frozenset(
            {
                "artifact_version", "intent_id", "intent_sha256", "request_id",
                "idempotency_key", "release_candidate", "release_candidate_sha256",
                "release_assessment", "release_assessment_sha256", "destination",
                "destination_sha256", "workspace_id", "channel_id", "concept_id",
                "episode_id", "gate_context_sha256", "workspace_observation_sha256",
                "publisher_id", "publisher_variant_id", "destination_id",
                "cost_minor_units", "currency", "retry_index", "created_at",
                "authority_effect",
            }
        ),
        "publication intent",
    )
    intent = PublicationIntent(
        artifact_version=str(value["artifact_version"]),
        intent_id=OpaqueId(str(value["intent_id"])),
        intent_sha256=HashDigest(str(value["intent_sha256"])),
        request_id=OpaqueId(str(value["request_id"])),
        idempotency_key=IdempotencyKey(str(value["idempotency_key"])),
        release_candidate=_ref_from_mapping(
            value["release_candidate"], "release_candidate"
        ),
        release_candidate_sha256=HashDigest(
            str(value["release_candidate_sha256"])
        ),
        release_assessment=_ref_from_mapping(
            value["release_assessment"], "release_assessment"
        ),
        release_assessment_sha256=HashDigest(
            str(value["release_assessment_sha256"])
        ),
        destination=_ref_from_mapping(value["destination"], "destination"),
        destination_sha256=HashDigest(str(value["destination_sha256"])),
        workspace_id=OpaqueId(str(value["workspace_id"])),
        channel_id=OpaqueId(str(value["channel_id"])),
        concept_id=OpaqueId(str(value["concept_id"])),
        episode_id=OpaqueId(str(value["episode_id"])),
        gate_context_sha256=HashDigest(str(value["gate_context_sha256"])),
        workspace_observation_sha256=HashDigest(
            str(value["workspace_observation_sha256"])
        ),
        publisher_id=OpaqueId(str(value["publisher_id"])),
        publisher_variant_id=OpaqueId(str(value["publisher_variant_id"])),
        destination_id=OpaqueId(str(value["destination_id"])),
        cost_minor_units=value["cost_minor_units"],
        currency=str(value["currency"]),
        retry_index=value["retry_index"],
        created_at=str(value["created_at"]),
        authority_effect=str(value["authority_effect"]),
    )
    publication_intent_to_mapping(intent)
    return intent


def build_publication_intent(**values: object) -> PublicationIntent:
    provisional = PublicationIntent(
        artifact_version=PUBLICATION_INTENT_VERSION,
        intent_id=OpaqueId("pending"),
        intent_sha256=HashDigest("0" * 64),
        **values,
    )
    digest = canonical_sha256(_publication_intent_identity(provisional))
    return replace(
        provisional,
        intent_id=OpaqueId(f"publication-intent-{str(digest)[:20]}"),
        intent_sha256=digest,
    )


def _publication_receipt_identity(value: PublicationReceipt) -> dict[str, object]:
    if value.artifact_version != PUBLICATION_RECEIPT_VERSION:
        _reject(
            "runtime.publication.receipt_version",
            "unsupported publication receipt",
        )
    if not isinstance(value.status, PublicationStatus):
        _reject("runtime.publication.receipt_status", "publication status is invalid")
    if value.authority_effect != "none":
        _reject(
            "runtime.publication.receipt_authority",
            "publication receipt cannot create authority",
        )
    started = parse_rfc3339_datetime(_time(value.started_at, "started_at"))
    completed = parse_rfc3339_datetime(_time(value.completed_at, "completed_at"))
    if completed < started:
        _reject(
            "runtime.publication.receipt_time",
            "publication receipt completed before it started",
        )
    expected_published = {
        PublicationStatus.SUCCEEDED: True,
        PublicationStatus.FAILED: False,
        PublicationStatus.UNCERTAIN: None,
    }
    if value.status in expected_published and value.published is not expected_published[
        value.status
    ]:
        _reject(
            "runtime.publication.receipt_result",
            "publication status and published result disagree",
        )
    if value.status is PublicationStatus.RECONCILED and type(value.published) is not bool:
        _reject(
            "runtime.publication.receipt_result",
            "reconciled publication needs a definite published result",
        )
    if value.status is PublicationStatus.RECONCILED:
        if value.reconciliation_record is None:
            _reject(
                "runtime.publication.receipt_reconciliation",
                "reconciled publication lacks reconciliation evidence",
            )
    elif value.reconciliation_record is not None:
        _reject(
            "runtime.publication.receipt_reconciliation",
            "only reconciled publication may carry reconciliation evidence",
        )
    if value.status in {
        PublicationStatus.SUCCEEDED,
        PublicationStatus.FAILED,
        PublicationStatus.RECONCILED,
    } and value.workspace_after_verifier_record is None:
        _reject(
            "runtime.publication.receipt_workspace",
            "definite publication result requires after-workspace verification",
        )
    if value.status in {PublicationStatus.SUCCEEDED, PublicationStatus.RECONCILED}:
        if value.settlement_record is None:
            _reject(
                "runtime.publication.receipt_settlement",
                "settled publication lacks reservation settlement",
            )
    external_values = (value.external_request_id, value.external_session_id)
    if value.external_reference_id is None:
        if any(item is not None for item in external_values):
            _reject(
                "runtime.publication.receipt_external",
                "external identifiers lack their bound reference id",
            )
    elif all(item is None for item in external_values):
        _reject(
            "runtime.publication.receipt_external",
            "external reference id lacks opaque provider identifiers",
        )
    for item in external_values:
        if item is not None:
            _token(item, "external_reference")
    if value.status is PublicationStatus.SUCCEEDED and value.external_reference_id is None:
        _reject(
            "runtime.publication.receipt_external",
            "successful publication requires an external reference",
        )
    if value.measured_cost_minor_units is not None and (
        not isinstance(value.measured_cost_minor_units, int)
        or isinstance(value.measured_cost_minor_units, bool)
        or value.measured_cost_minor_units < 0
    ):
        _reject(
            "runtime.publication.receipt_cost", "publication cost is invalid"
        )
    if (value.measured_cost_minor_units is None) != (value.currency is None):
        _reject(
            "runtime.publication.receipt_cost",
            "publication cost and currency must be present together",
        )
    reasons = _reason_codes(value.reason_codes)
    if value.status is PublicationStatus.SUCCEEDED and reasons:
        _reject(
            "runtime.publication.receipt_reasons",
            "successful publication cannot carry failure reasons",
        )
    if value.status is not PublicationStatus.SUCCEEDED and not reasons:
        _reject(
            "runtime.publication.receipt_reasons",
            "non-success publication requires a reason",
        )
    return {
        "artifact_version": value.artifact_version,
        "publication_intent_sha256": _sha(
            str(value.publication_intent_sha256), "publication_intent_sha256"
        ),
        "journal_id": _token(str(value.journal_id), "journal_id"),
        "journal_head_sha256": _sha(
            str(value.journal_head_sha256), "journal_head_sha256"
        ),
        "release_candidate": _ref(value.release_candidate, "release_candidate"),
        "release_assessment": _ref(value.release_assessment, "release_assessment"),
        "destination": _ref(value.destination, "destination"),
        "workspace_before_verifier_record": _ref(
            value.workspace_before_verifier_record,
            "workspace_before_verifier_record",
        ),
        "workspace_after_verifier_record": (
            _ref(
                value.workspace_after_verifier_record,
                "workspace_after_verifier_record",
            )
            if value.workspace_after_verifier_record is not None
            else None
        ),
        "action_request_sha256": _sha(
            str(value.action_request_sha256), "action_request_sha256"
        ),
        "authority_decision_sha256": _sha(
            str(value.authority_decision_sha256), "authority_decision_sha256"
        ),
        "authority_receipt_sha256": _sha(
            str(value.authority_receipt_sha256), "authority_receipt_sha256"
        ),
        "service_identity": _token(str(value.service_identity), "service_identity"),
        "external_reference_id": (
            _token(str(value.external_reference_id), "external_reference_id")
            if value.external_reference_id is not None
            else None
        ),
        "external_request_id": value.external_request_id,
        "external_session_id": value.external_session_id,
        "started_at": value.started_at,
        "completed_at": value.completed_at,
        "status": value.status.value,
        "published": value.published,
        "measured_cost_minor_units": value.measured_cost_minor_units,
        "currency": value.currency,
        "settlement_record": (
            _ref(value.settlement_record, "settlement_record")
            if value.settlement_record is not None
            else None
        ),
        "reconciliation_record": (
            _ref(value.reconciliation_record, "reconciliation_record")
            if value.reconciliation_record is not None
            else None
        ),
        "reason_codes": reasons,
        "authority_effect": value.authority_effect,
    }


def publication_receipt_to_mapping(value: PublicationReceipt) -> dict[str, object]:
    identity = _publication_receipt_identity(value)
    digest = canonical_sha256(identity)
    if (
        value.receipt_sha256 != digest
        or str(value.receipt_id) != f"publication-receipt-{str(digest)[:20]}"
    ):
        _reject(
            "runtime.publication.receipt_identity",
            "publication receipt identity mismatch",
        )
    return _schema_validated({
        **identity,
        "receipt_id": str(value.receipt_id),
        "receipt_sha256": str(value.receipt_sha256),
    })


def publication_receipt_from_mapping(
    document: Mapping[str, object],
) -> PublicationReceipt:
    value = _exact_mapping(
        document,
        frozenset(
            {
                "artifact_version", "receipt_id", "receipt_sha256",
                "publication_intent_sha256", "journal_id", "journal_head_sha256",
                "release_candidate", "release_assessment", "destination",
                "workspace_before_verifier_record", "workspace_after_verifier_record",
                "action_request_sha256", "authority_decision_sha256",
                "authority_receipt_sha256", "service_identity",
                "external_reference_id", "external_request_id", "external_session_id",
                "started_at", "completed_at", "status", "published",
                "measured_cost_minor_units", "currency", "settlement_record",
                "reconciliation_record", "reason_codes", "authority_effect",
            }
        ),
        "publication receipt",
    )
    reasons = _object_array(value["reason_codes"], "reason_codes")
    cost = value["measured_cost_minor_units"]
    currency = value["currency"]
    external_request = value["external_request_id"]
    external_session = value["external_session_id"]
    receipt = PublicationReceipt(
        artifact_version=str(value["artifact_version"]),
        receipt_id=OpaqueId(str(value["receipt_id"])),
        receipt_sha256=HashDigest(str(value["receipt_sha256"])),
        publication_intent_sha256=HashDigest(
            str(value["publication_intent_sha256"])
        ),
        journal_id=OpaqueId(str(value["journal_id"])),
        journal_head_sha256=HashDigest(str(value["journal_head_sha256"])),
        release_candidate=_ref_from_mapping(
            value["release_candidate"], "release_candidate"
        ),
        release_assessment=_ref_from_mapping(
            value["release_assessment"], "release_assessment"
        ),
        destination=_ref_from_mapping(value["destination"], "destination"),
        workspace_before_verifier_record=_ref_from_mapping(
            value["workspace_before_verifier_record"],
            "workspace_before_verifier_record",
        ),
        workspace_after_verifier_record=_optional_ref(
            value["workspace_after_verifier_record"],
            "workspace_after_verifier_record",
        ),
        action_request_sha256=HashDigest(str(value["action_request_sha256"])),
        authority_decision_sha256=HashDigest(
            str(value["authority_decision_sha256"])
        ),
        authority_receipt_sha256=HashDigest(
            str(value["authority_receipt_sha256"])
        ),
        service_identity=OpaqueId(str(value["service_identity"])),
        external_reference_id=_optional_token(value["external_reference_id"]),
        external_request_id=(
            str(external_request) if external_request is not None else None
        ),
        external_session_id=(
            str(external_session) if external_session is not None else None
        ),
        started_at=str(value["started_at"]),
        completed_at=str(value["completed_at"]),
        status=PublicationStatus(str(value["status"])),
        published=value["published"],
        measured_cost_minor_units=cost if cost is not None else None,
        currency=str(currency) if currency is not None else None,
        settlement_record=_optional_ref(value["settlement_record"], "settlement_record"),
        reconciliation_record=_optional_ref(
            value["reconciliation_record"], "reconciliation_record"
        ),
        reason_codes=tuple(str(item) for item in reasons),
        authority_effect=str(value["authority_effect"]),
    )
    publication_receipt_to_mapping(receipt)
    return receipt


def build_publication_receipt(**values: object) -> PublicationReceipt:
    provisional = PublicationReceipt(
        artifact_version=PUBLICATION_RECEIPT_VERSION,
        receipt_id=OpaqueId("pending"),
        receipt_sha256=HashDigest("0" * 64),
        **values,
    )
    digest = canonical_sha256(_publication_receipt_identity(provisional))
    return replace(
        provisional,
        receipt_id=OpaqueId(f"publication-receipt-{str(digest)[:20]}"),
        receipt_sha256=digest,
    )


def _parity_identity(value: ProjectionParityReceipt) -> dict[str, object]:
    if value.artifact_version != PROJECTION_PARITY_VERSION:
        _reject("runtime.parity.version", "unsupported projection parity version")
    if value.authority_effect != "none":
        _reject("runtime.parity.authority", "parity evidence cannot create authority")
    if not isinstance(value.parity_pass, bool):
        _reject("runtime.parity.pass", "parity_pass must be boolean")
    if (
        not isinstance(value.difference_count, int)
        or isinstance(value.difference_count, bool)
        or value.difference_count < 0
    ):
        _reject("runtime.parity.differences", "difference_count is invalid")
    if value.parity_pass != (value.difference_count == 0):
        _reject("runtime.parity.result", "parity result and difference count disagree")
    if value.parity_pass and value.legacy_normalized_sha256 != value.projection_normalized_sha256:
        _reject("runtime.parity.digest", "passing parity requires equal normalized digests")
    return {
        "artifact_version": value.artifact_version,
        "consumer_id": _token(str(value.consumer_id), "consumer_id"),
        "view_kind": _token(str(value.view_kind), "view_kind"),
        "legacy_artifact": _ref(value.legacy_artifact, "legacy_artifact"),
        "projection_artifact": _ref(value.projection_artifact, "projection_artifact"),
        "source_blueprint_sha256": _sha(
            str(value.source_blueprint_sha256), "source_blueprint_sha256"
        ),
        "projection_sha256": _sha(
            str(value.projection_sha256), "projection_sha256"
        ),
        "legacy_normalized_sha256": _sha(
            str(value.legacy_normalized_sha256), "legacy_normalized_sha256"
        ),
        "projection_normalized_sha256": _sha(
            str(value.projection_normalized_sha256), "projection_normalized_sha256"
        ),
        "normalizer_id": _token(str(value.normalizer_id), "normalizer_id"),
        "normalizer_version": _token(value.normalizer_version, "normalizer_version"),
        "normalizer_sha256": _sha(str(value.normalizer_sha256), "normalizer_sha256"),
        "verifier_record": _ref(value.verifier_record, "verifier_record"),
        "policy_bundle_sha256": _sha(str(value.policy_bundle_sha256), "policy_bundle_sha256"),
        "evaluated_at": _time(value.evaluated_at, "evaluated_at"),
        "parity_pass": value.parity_pass,
        "difference_count": value.difference_count,
        "authority_effect": value.authority_effect,
    }


def projection_parity_receipt_to_mapping(value: ProjectionParityReceipt) -> dict[str, object]:
    identity = _parity_identity(value)
    digest = canonical_sha256(identity)
    if value.receipt_sha256 != digest or str(value.receipt_id) != f"projection-parity-{str(digest)[:20]}":
        _reject("runtime.parity.identity", "projection parity identity mismatch")
    return _schema_validated({
        **identity,
        "receipt_id": str(value.receipt_id),
        "receipt_sha256": str(value.receipt_sha256),
    })


def projection_parity_receipt_from_mapping(
    document: Mapping[str, object],
) -> ProjectionParityReceipt:
    value = _exact_mapping(
        document,
        frozenset(
            {
                "artifact_version",
                "receipt_id",
                "receipt_sha256",
                "consumer_id",
                "view_kind",
                "legacy_artifact",
                "projection_artifact",
                "source_blueprint_sha256",
                "projection_sha256",
                "legacy_normalized_sha256",
                "projection_normalized_sha256",
                "normalizer_id",
                "normalizer_version",
                "normalizer_sha256",
                "verifier_record",
                "policy_bundle_sha256",
                "evaluated_at",
                "parity_pass",
                "difference_count",
                "authority_effect",
            }
        ),
        "projection parity receipt",
    )
    receipt = ProjectionParityReceipt(
        artifact_version=str(value["artifact_version"]),
        receipt_id=OpaqueId(str(value["receipt_id"])),
        receipt_sha256=HashDigest(str(value["receipt_sha256"])),
        consumer_id=OpaqueId(str(value["consumer_id"])),
        view_kind=OpaqueId(str(value["view_kind"])),
        legacy_artifact=_ref_from_mapping(value["legacy_artifact"], "legacy_artifact"),
        projection_artifact=_ref_from_mapping(
            value["projection_artifact"], "projection_artifact"
        ),
        source_blueprint_sha256=HashDigest(str(value["source_blueprint_sha256"])),
        projection_sha256=HashDigest(str(value["projection_sha256"])),
        legacy_normalized_sha256=HashDigest(
            str(value["legacy_normalized_sha256"])
        ),
        projection_normalized_sha256=HashDigest(
            str(value["projection_normalized_sha256"])
        ),
        normalizer_id=OpaqueId(str(value["normalizer_id"])),
        normalizer_version=str(value["normalizer_version"]),
        normalizer_sha256=HashDigest(str(value["normalizer_sha256"])),
        verifier_record=_ref_from_mapping(value["verifier_record"], "verifier_record"),
        policy_bundle_sha256=HashDigest(str(value["policy_bundle_sha256"])),
        evaluated_at=str(value["evaluated_at"]),
        parity_pass=value["parity_pass"],
        difference_count=value["difference_count"],
        authority_effect=str(value["authority_effect"]),
    )
    projection_parity_receipt_to_mapping(receipt)
    return receipt


def build_projection_parity_receipt(**values: object) -> ProjectionParityReceipt:
    provisional = ProjectionParityReceipt(
        artifact_version=PROJECTION_PARITY_VERSION,
        receipt_id=OpaqueId("pending"),
        receipt_sha256=HashDigest("0" * 64),
        **values,
    )
    digest = canonical_sha256(_parity_identity(provisional))
    return replace(
        provisional,
        receipt_id=OpaqueId(f"projection-parity-{str(digest)[:20]}"),
        receipt_sha256=digest,
    )


def _cutover_identity(value: MigrationCutoverState) -> dict[str, object]:
    if value.artifact_version != MIGRATION_CUTOVER_VERSION:
        _reject("runtime.cutover.version", "unsupported migration cutover version")
    if not isinstance(value.mode, MigrationMode):
        _reject("runtime.cutover.mode", "migration mode is invalid")
    if not isinstance(value.generation, int) or isinstance(value.generation, bool) or value.generation < 0:
        _reject("runtime.cutover.generation", "migration generation is invalid")
    if (value.generation == 0) != (value.previous_state_sha256 is None):
        _reject("runtime.cutover.chain", "only generation zero may omit its predecessor")
    if value.previous_state_sha256 is not None:
        _sha(str(value.previous_state_sha256), "previous_state_sha256")
    if value.read_only is not True or value.projections_are_authority is not False or value.authority_effect != "none":
        _reject("runtime.cutover.authority", "W06 projection cutover must remain read-only and non-authorizing")
    if value.mode is MigrationMode.PROJECTION_READ_ONLY and not value.parity_receipts:
        _reject("runtime.cutover.parity", "projection activation requires parity receipts")
    if value.mode is MigrationMode.LEGACY_ONLY and value.parity_receipts:
        _reject("runtime.cutover.parity", "legacy-only mode cannot consume parity receipts")
    if value.mode in {
        MigrationMode.DUAL_READ_COMPARE,
        MigrationMode.PROJECTION_READ_ONLY,
    } and value.rollback_required is not True:
        _reject("runtime.cutover.rollback", "active migration modes require rollback readiness")
    if value.mode is MigrationMode.ROLLED_BACK and value.rollback_required:
        _reject("runtime.cutover.rollback", "completed rollback cannot remain required")
    if (value.mode is MigrationMode.ROLLED_BACK) != (value.rollback_record is not None):
        _reject("runtime.cutover.rollback", "only rolled-back state requires a rollback record")
    refs = tuple(_ref(item, "parity_receipt") for item in value.parity_receipts)
    identities = tuple((item["path"], item["sha256"], item["artifact_version"]) for item in refs)
    if identities != tuple(sorted(set(identities))):
        _reject("runtime.cutover.parity", "parity receipts must be sorted and unique")
    return {
        "artifact_version": value.artifact_version,
        "migration_id": _token(str(value.migration_id), "migration_id"),
        "consumer_id": _token(str(value.consumer_id), "consumer_id"),
        "view_kind": _token(str(value.view_kind), "view_kind"),
        "generation": value.generation,
        "mode": value.mode.value,
        "previous_state_sha256": (
            str(value.previous_state_sha256)
            if value.previous_state_sha256 is not None
            else None
        ),
        "legacy_artifact": _ref(value.legacy_artifact, "legacy_artifact"),
        "parity_receipts": list(refs),
        "feature_flag_sha256": _sha(str(value.feature_flag_sha256), "feature_flag_sha256"),
        "activation_record": _ref(value.activation_record, "activation_record"),
        "rollback_record": (
            _ref(value.rollback_record, "rollback_record")
            if value.rollback_record is not None
            else None
        ),
        "activated_at": _time(value.activated_at, "activated_at"),
        "rollback_required": value.rollback_required,
        "read_only": value.read_only,
        "projections_are_authority": value.projections_are_authority,
        "authority_effect": value.authority_effect,
    }


def migration_cutover_state_to_mapping(value: MigrationCutoverState) -> dict[str, object]:
    identity = _cutover_identity(value)
    digest = canonical_sha256(identity)
    if value.state_sha256 != digest or str(value.state_id) != f"migration-state-{str(digest)[:20]}":
        _reject("runtime.cutover.identity", "migration cutover identity mismatch")
    return _schema_validated({
        **identity,
        "state_id": str(value.state_id),
        "state_sha256": str(value.state_sha256),
    })


def migration_cutover_state_from_mapping(
    document: Mapping[str, object],
) -> MigrationCutoverState:
    value = _exact_mapping(
        document,
        frozenset(
            {
                "artifact_version",
                "state_id",
                "state_sha256",
                "migration_id",
                "consumer_id",
                "view_kind",
                "generation",
                "mode",
                "previous_state_sha256",
                "legacy_artifact",
                "parity_receipts",
                "feature_flag_sha256",
                "activation_record",
                "rollback_record",
                "activated_at",
                "rollback_required",
                "read_only",
                "projections_are_authority",
                "authority_effect",
            }
        ),
        "migration cutover state",
    )
    parity = value["parity_receipts"]
    if not isinstance(parity, list):
        _reject("runtime.cutover.parity", "parity receipts must be an array")
    previous = value["previous_state_sha256"]
    rollback = value["rollback_record"]
    state = MigrationCutoverState(
        artifact_version=str(value["artifact_version"]),
        state_id=OpaqueId(str(value["state_id"])),
        state_sha256=HashDigest(str(value["state_sha256"])),
        migration_id=OpaqueId(str(value["migration_id"])),
        consumer_id=OpaqueId(str(value["consumer_id"])),
        view_kind=OpaqueId(str(value["view_kind"])),
        generation=value["generation"],
        mode=MigrationMode(str(value["mode"])),
        previous_state_sha256=(
            HashDigest(str(previous)) if previous is not None else None
        ),
        legacy_artifact=_ref_from_mapping(
            value["legacy_artifact"], "legacy_artifact"
        ),
        parity_receipts=tuple(
            _ref_from_mapping(item, "parity_receipt") for item in parity
        ),
        feature_flag_sha256=HashDigest(str(value["feature_flag_sha256"])),
        activation_record=_ref_from_mapping(
            value["activation_record"], "activation_record"
        ),
        rollback_record=(
            _ref_from_mapping(rollback, "rollback_record")
            if rollback is not None
            else None
        ),
        activated_at=str(value["activated_at"]),
        rollback_required=value["rollback_required"],
        read_only=value["read_only"],
        projections_are_authority=value["projections_are_authority"],
        authority_effect=str(value["authority_effect"]),
    )
    migration_cutover_state_to_mapping(state)
    return state


def build_migration_cutover_state(**values: object) -> MigrationCutoverState:
    provisional = MigrationCutoverState(
        artifact_version=MIGRATION_CUTOVER_VERSION,
        state_id=OpaqueId("pending"),
        state_sha256=HashDigest("0" * 64),
        **values,
    )
    digest = canonical_sha256(_cutover_identity(provisional))
    return replace(
        provisional,
        state_id=OpaqueId(f"migration-state-{str(digest)[:20]}"),
        state_sha256=digest,
    )


def runtime_artifact_to_bytes(value: object) -> bytes:
    if isinstance(value, ExecutionIntent):
        mapping = execution_intent_to_mapping(value)
    elif isinstance(value, ExecutionJournalEvent):
        mapping = execution_journal_event_to_mapping(value)
    elif isinstance(value, ExecutionReceipt):
        mapping = execution_receipt_to_mapping(value)
    elif isinstance(value, PublicationIntent):
        mapping = publication_intent_to_mapping(value)
    elif isinstance(value, PublicationReceipt):
        mapping = publication_receipt_to_mapping(value)
    elif isinstance(value, ProjectionParityReceipt):
        mapping = projection_parity_receipt_to_mapping(value)
    elif isinstance(value, MigrationCutoverState):
        mapping = migration_cutover_state_to_mapping(value)
    else:
        _reject("runtime.contract.type", "unsupported runtime artifact type")
    return canonical_json_bytes(mapping)


def runtime_artifact_from_mapping(document: Mapping[str, object]) -> object:
    version = document.get("artifact_version")
    parser = {
        EXECUTION_INTENT_VERSION: execution_intent_from_mapping,
        JOURNAL_EVENT_VERSION: execution_journal_event_from_mapping,
        EXECUTION_RECEIPT_VERSION: execution_receipt_from_mapping,
        PUBLICATION_INTENT_VERSION: publication_intent_from_mapping,
        PUBLICATION_RECEIPT_VERSION: publication_receipt_from_mapping,
        PROJECTION_PARITY_VERSION: projection_parity_receipt_from_mapping,
        MIGRATION_CUTOVER_VERSION: migration_cutover_state_from_mapping,
    }.get(version)
    if parser is None:
        _reject("runtime.contract.version", "unsupported runtime artifact version")
    return parser(document)


def runtime_artifact_from_bytes(payload: bytes) -> object:
    try:
        document = require_json_object(parse_json_bytes(payload))
    except (JsonInputError, ValueError, TypeError) as error:
        raise RuntimeContractError(
            "runtime.contract.json", "runtime artifact is not strict JSON"
        ) from error
    return runtime_artifact_from_mapping(document)


__all__ = [
    "RuntimeContractError",
    "build_execution_intent",
    "build_execution_journal_event",
    "build_execution_receipt",
    "build_migration_cutover_state",
    "build_publication_intent",
    "build_publication_receipt",
    "build_projection_parity_receipt",
    "execution_intent_to_mapping",
    "execution_intent_from_mapping",
    "execution_journal_event_from_mapping",
    "execution_journal_event_to_mapping",
    "execution_receipt_from_mapping",
    "execution_receipt_to_mapping",
    "migration_cutover_state_from_mapping",
    "migration_cutover_state_to_mapping",
    "publication_intent_to_mapping",
    "publication_intent_from_mapping",
    "publication_receipt_from_mapping",
    "publication_receipt_to_mapping",
    "projection_parity_receipt_from_mapping",
    "projection_parity_receipt_to_mapping",
    "runtime_artifact_to_bytes",
    "runtime_artifact_from_bytes",
    "runtime_artifact_from_mapping",
]
