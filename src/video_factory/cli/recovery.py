"""General recovery command contracts (retry / resume / invalidate / reopen).

These handlers describe recovery *intent* against opaque target IDs. They do not
dispatch paid work, rewrite storage, or invent a production workflow mode
(OD-004). Storage engines that would execute the intent arrive later.

Policy rationale (documented, not arbitrary):

- Rapid is a preview_only draft surface (ADR-006). Production recovery commands
  have no meaningful path there, so all four commands fail closed under Rapid.
- invalidate is a destructive action. ADR-006 places
  ``destructive_action_approval`` only on Controlled. Therefore invalidate is
  Controlled-only, independent of cascade.
- invalidate --cascade multiplies artifact impact. Without
  ``immutable_audit_log_required`` there is no durable multi-artifact trail, so
  cascade additionally requires that flag (also Controlled under ADR-006).
- retry / resume / reopen under Standard are allowed as contract_only: Standard
  already has generation/publish human gates and hash binding, so "continue or
  re-attempt work" is meaningful without cascade destruction. Full execution
  still waits on later orchestrator/storage backing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from video_factory.domain import IdempotencyKey, OpaqueId
from video_factory.engine import WorkflowMode
from video_factory.policy import (
    ApprovalKind,
    WorkflowPolicy,
    WorkflowPolicyError,
    resolve_workflow_policy,
)

RecoveryCommandName = Literal["retry", "resume", "invalidate", "reopen"]


class RecoveryPolicyError(WorkflowPolicyError):
    """Raised when a recovery command conflicts with the resolved workflow policy."""


class RecoveryAcceptance(StrEnum):
    """Result of evaluating a recovery contract without executing storage."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class RecoveryIntent:
    """Pure description of a recovery request (no side effects on disk)."""

    command: RecoveryCommandName
    target_id: OpaqueId
    workflow_mode: WorkflowMode
    stage: str | None = None
    cascade: bool = False
    idempotency_key: IdempotencyKey | None = None


@dataclass(frozen=True, slots=True)
class RecoveryContractResult:
    """Contract-only evaluation outcome for a recovery command."""

    acceptance: RecoveryAcceptance
    intent: RecoveryIntent
    policy: WorkflowPolicy
    message: str
    # True only when this call registered a new idempotent retry request.
    dispatched_new: bool


def assert_recovery_command_permitted(
    mode: str | WorkflowMode | None,
    command: RecoveryCommandName,
    *,
    cascade: bool = False,
) -> WorkflowPolicy:
    """Fail closed when mode is missing or the policy rejects the recovery surface.

    Always resolves mode through ``resolve_workflow_policy`` so OD-004's
    no-silent-default rule applies at the CLI recovery boundary.
    """

    policy = resolve_workflow_policy(mode)

    if policy.mode is WorkflowMode.RAPID:
        raise RecoveryPolicyError(
            f"{command} is not permitted under rapid: rapid is preview_only "
            "draft/preflight with no production recovery path (ADR-006)"
        )

    if command == "invalidate":
        if ApprovalKind.DESTRUCTIVE_ACTION_APPROVAL not in policy.required_approval_kinds:
            raise RecoveryPolicyError(
                "invalidate requires destructive_action_approval in the workflow "
                "policy (controlled under ADR-006); standard and rapid omit it"
            )
        if cascade and not policy.immutable_audit_log_required:
            raise RecoveryPolicyError(
                "invalidate --cascade requires immutable_audit_log_required so a "
                "multi-artifact invalidation leaves a durable trail "
                "(controlled under ADR-006)"
            )

    # Standard: retry / resume / reopen remain meaningful as contract_only.
    # Controlled: all four recovery commands are meaningful as contract_only.
    return policy


class RetryIdempotencyLedger:
    """Session ledger that records at most one new retry intent per key.

    Mirrors ADR-004 request-envelope idempotency: the same key must not create
    a second dispatch. This ledger is in-memory only; durable storage arrives
    with a later phase.
    """

    def __init__(self) -> None:
        self._by_key: dict[IdempotencyKey, RecoveryIntent] = {}

    def register(
        self,
        key: IdempotencyKey,
        intent: RecoveryIntent,
    ) -> tuple[bool, RecoveryIntent]:
        """Return (is_new, stored_intent). Second call with same key is not new."""

        existing = self._by_key.get(key)
        if existing is not None:
            return False, existing
        self._by_key[key] = intent
        return True, intent

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and IdempotencyKey(key) in self._by_key

    def clear(self) -> None:
        self._by_key.clear()


# Process-local default ledger for CLI dispatch. Tests may inject their own.
DEFAULT_RETRY_LEDGER = RetryIdempotencyLedger()


def request_retry(
    target_id: str,
    mode: str | WorkflowMode | None,
    *,
    idempotency_key: str,
    ledger: RetryIdempotencyLedger | None = None,
) -> RecoveryContractResult:
    """Convert a failed-target retry into at most one contract-only request."""

    if not idempotency_key or not str(idempotency_key).strip():
        raise RecoveryPolicyError("retry requires a non-empty idempotency_key")

    policy = assert_recovery_command_permitted(mode, "retry")
    key = IdempotencyKey(str(idempotency_key).strip())
    intent = RecoveryIntent(
        command="retry",
        target_id=OpaqueId(str(target_id)),
        workflow_mode=policy.mode,
        idempotency_key=key,
    )
    active = ledger if ledger is not None else DEFAULT_RETRY_LEDGER
    is_new, stored = active.register(key, intent)
    if is_new:
        return RecoveryContractResult(
            acceptance=RecoveryAcceptance.ACCEPTED,
            intent=stored,
            policy=policy,
            message=(
                "retry intent accepted (contract_only); no orchestrator dispatch "
                "or paid external call is performed in this phase"
            ),
            dispatched_new=True,
        )
    return RecoveryContractResult(
        acceptance=RecoveryAcceptance.DUPLICATE,
        intent=stored,
        policy=policy,
        message=(
            f"retry idempotency_key {key!r} already registered; "
            "duplicate dispatch suppressed"
        ),
        dispatched_new=False,
    )


def request_resume(
    target_id: str,
    mode: str | WorkflowMode | None,
    *,
    stage: str | None = None,
) -> RecoveryContractResult:
    """Describe resume-from-last-approved-point (same stage continuation)."""

    policy = assert_recovery_command_permitted(mode, "resume")
    intent = RecoveryIntent(
        command="resume",
        target_id=OpaqueId(str(target_id)),
        workflow_mode=policy.mode,
        stage=stage,
    )
    stage_note = f" from stage {stage!r}" if stage else " from last approved point"
    return RecoveryContractResult(
        acceptance=RecoveryAcceptance.ACCEPTED,
        intent=intent,
        policy=policy,
        message=(
            f"resume intent accepted{stage_note} (contract_only); "
            "episode state store is not yet backed"
        ),
        dispatched_new=False,
    )


def request_invalidate(
    target_id: str,
    mode: str | WorkflowMode | None,
    *,
    cascade: bool = False,
) -> RecoveryContractResult:
    """Describe invalidation intent only; does not destroy artifacts."""

    policy = assert_recovery_command_permitted(mode, "invalidate", cascade=cascade)
    intent = RecoveryIntent(
        command="invalidate",
        target_id=OpaqueId(str(target_id)),
        workflow_mode=policy.mode,
        cascade=cascade,
    )
    cascade_note = " with cascade" if cascade else ""
    return RecoveryContractResult(
        acceptance=RecoveryAcceptance.ACCEPTED,
        intent=intent,
        policy=policy,
        message=(
            f"invalidate intent accepted{cascade_note} (contract_only); "
            "storage engine does not execute destruction in this phase"
        ),
        dispatched_new=False,
    )


def request_reopen(
    target_id: str,
    mode: str | WorkflowMode | None,
    *,
    stage: str,
) -> RecoveryContractResult:
    """Describe reopen-by-returning-to-earlier-stage intent.

    Distinct from resume: resume continues the same stage; reopen moves the
    workflow cursor back to ``stage`` before continuing.
    """

    if not stage or not str(stage).strip():
        raise RecoveryPolicyError("reopen requires a non-empty --from stage")

    policy = assert_recovery_command_permitted(mode, "reopen")
    cleaned = str(stage).strip()
    intent = RecoveryIntent(
        command="reopen",
        target_id=OpaqueId(str(target_id)),
        workflow_mode=policy.mode,
        stage=cleaned,
    )
    return RecoveryContractResult(
        acceptance=RecoveryAcceptance.ACCEPTED,
        intent=intent,
        policy=policy,
        message=(
            f"reopen intent accepted from stage {cleaned!r} (contract_only); "
            "episode state store is not yet backed"
        ),
        dispatched_new=False,
    )
