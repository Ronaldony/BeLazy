"""Journal-claim-bound W04 authority reservation adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Protocol

from video_factory.approvals import GateContext
from video_factory.authority import (
    ActionAuthorityRequest,
    ActionRiskAssessment,
    AuthorityDecision,
    AuthorityVerificationReceipt,
    UnverifiedStandingAuthorization,
    VerificationPurpose,
)
from video_factory.domain import ArtifactReference, HashDigest, OpaqueId
from video_factory.domain import ArtifactVersion, RelativeArtifactPath
from video_factory.config.canonical import canonical_json_bytes, canonical_sha256
from video_factory.runtime import JournalState

from .boundary import FixtureRuntimeBoundary
from .filesystem import _write_new, read_stable_regular_bytes


class ClaimBoundAuthorizationLedger(Protocol):
    """Trusted ledger whose reservation key is the durable journal claim."""

    def verify_current(
        self,
        request: ActionAuthorityRequest,
        risk: ActionRiskAssessment,
        presented_grant: UnverifiedStandingAuthorization | None,
        authority_references: tuple[ArtifactReference, ...],
        *,
        current_context: GateContext,
        evaluated_at: datetime,
    ) -> AuthorityVerificationReceipt | None: ...

    def revalidate_and_reserve_current(
        self,
        decision: AuthorityDecision,
        request: ActionAuthorityRequest,
        *,
        current_context: GateContext,
        workspace_observation_sha256: HashDigest,
        adapter_id: OpaqueId,
        service_identity: OpaqueId,
        evaluated_at: datetime,
        purpose: VerificationPurpose,
        runtime_claim_sha256: HashDigest,
    ) -> AuthorityVerificationReceipt | None:
        """Atomically verify current state for one stable reservation claim.

        Every call returns a fresh timestamp-bound receipt.  Budget/idempotency
        reservation remains stable because the durable runtime claim is the
        ledger key; a cached old receipt is never current verification.
        """
        ...


def runtime_authority_reservation_sha256(
    verified: object,
    *,
    runtime_claim_sha256: HashDigest,
    expected_purpose: VerificationPurpose,
) -> HashDigest:
    """Derive the stable reservation identity, excluding fresh receipt time."""

    receipt = getattr(verified, "receipt", None)
    request_sha256 = getattr(verified, "request_sha256", None)
    purpose = getattr(verified, "purpose", None)
    if (
        receipt is None
        or getattr(receipt, "receipt_sha256", None) is None
        or request_sha256 is None
        or purpose is not expected_purpose
    ):
        raise ValueError("fresh authority verification returned an invalid receipt")
    # The caller's core authority boundary has already proved receipt/request
    # cross-bindings.  The reservation identity intentionally binds the
    # durable claim and exact request, not evaluated_at/ledger-head fields that
    # must change on every fresh check.
    return canonical_sha256(
        {
            "artifact_version": "runtime-authority-reservation/1.1",
            "runtime_claim_sha256": str(runtime_claim_sha256),
            "request_sha256": str(request_sha256),
            "purpose": expected_purpose.value,
        }
    )


@dataclass(frozen=True, slots=True)
class ClaimBoundLedgerAdapter:
    ledger: ClaimBoundAuthorizationLedger
    runtime_claim_sha256: HashDigest

    def verify_current(self, *args, **kwargs):
        return self.ledger.verify_current(*args, **kwargs)

    def revalidate_and_reserve_current(
        self,
        decision,
        request,
        *,
        current_context,
        workspace_observation_sha256,
        adapter_id,
        service_identity,
        evaluated_at,
        purpose,
    ):
        return self.ledger.revalidate_and_reserve_current(
            decision,
            request,
            current_context=current_context,
            workspace_observation_sha256=workspace_observation_sha256,
            adapter_id=adapter_id,
            service_identity=service_identity,
            evaluated_at=evaluated_at,
            purpose=purpose,
            runtime_claim_sha256=self.runtime_claim_sha256,
        )


class FixtureAuthoritySettlementStore:
    """Durable, idempotent settlement evidence for isolated fixture ledgers."""

    artifact_version = "fixture-authority-settlement/1.0"

    def __init__(self, boundary: FixtureRuntimeBoundary) -> None:
        self._boundary = boundary
        self._directory = boundary.require_directory(
            "authority-settlements", create=True
        )

    @staticmethod
    def _key(
        journal_id: OpaqueId,
        claim_sha256s: tuple[HashDigest, ...],
    ) -> HashDigest:
        return canonical_sha256(
            {
                "artifact_version": "fixture-authority-settlement-key/1.0",
                "journal_id": str(journal_id),
                "reservation_claim_sha256s": [str(item) for item in claim_sha256s],
            }
        )

    def finalize_current(
        self,
        *,
        journal_id: OpaqueId,
        reservation_claim_sha256s: tuple[HashDigest, ...],
        reservation_sha256s: tuple[HashDigest, ...],
        final_state: JournalState,
        evaluated_at: datetime,
    ) -> ArtifactReference | None:
        self._boundary.assert_current()
        if (
            not reservation_claim_sha256s
            or len(reservation_claim_sha256s) != len(reservation_sha256s)
            or len(set(reservation_claim_sha256s)) != len(reservation_claim_sha256s)
            or evaluated_at.tzinfo is None
            or evaluated_at.utcoffset() is None
            or final_state
            not in {
                JournalState.SUCCEEDED,
                JournalState.FAILED,
                JournalState.PARTIAL,
                JournalState.UNCERTAIN,
                JournalState.RECONCILED,
            }
        ):
            return None
        key = self._key(journal_id, reservation_claim_sha256s)
        path = self._directory / f"settlement-{key}.json"
        if path.exists():
            stable, payload = read_stable_regular_bytes(path)
            try:
                mapping = json.loads(payload)
            except Exception:
                return None
            if (
                mapping.get("journal_id") != str(journal_id)
                or mapping.get("reservation_claim_sha256s")
                != [str(item) for item in reservation_claim_sha256s]
                or mapping.get("reservation_sha256s")
                != [str(item) for item in reservation_sha256s]
                or mapping.get("final_state") != final_state.value
                or hashlib.sha256(payload).hexdigest() != str(stable.exact_sha256)
            ):
                return None
            return ArtifactReference(
                RelativeArtifactPath(f"runtime-settlements/settlement-{key}.json"),
                stable.exact_sha256,
                ArtifactVersion(self.artifact_version),
            )
        mapping = {
            "artifact_version": self.artifact_version,
            "settlement_key_sha256": str(key),
            "journal_id": str(journal_id),
            "reservation_claim_sha256s": [
                str(item) for item in reservation_claim_sha256s
            ],
            "reservation_sha256s": [str(item) for item in reservation_sha256s],
            "final_state": final_state.value,
            "settled_at": evaluated_at.isoformat(),
            "fixture_only": True,
            "production_enabled": False,
        }
        payload = canonical_json_bytes(mapping)
        try:
            _write_new(path, payload)
        except FileExistsError:
            return self.finalize_current(
                journal_id=journal_id,
                reservation_claim_sha256s=reservation_claim_sha256s,
                reservation_sha256s=reservation_sha256s,
                final_state=final_state,
                evaluated_at=evaluated_at,
            )
        return ArtifactReference(
            RelativeArtifactPath(f"runtime-settlements/settlement-{key}.json"),
            HashDigest(hashlib.sha256(payload).hexdigest()),
            ArtifactVersion(self.artifact_version),
        )


__all__ = [
    "ClaimBoundAuthorizationLedger",
    "ClaimBoundLedgerAdapter",
    "FixtureAuthoritySettlementStore",
    "runtime_authority_reservation_sha256",
]
