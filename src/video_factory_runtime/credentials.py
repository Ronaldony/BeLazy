"""Trusted, fixture-only credential leases for W06 effect ports.

The durable journal stores only an immutable credential reference and a
broker-verification record.  The non-serializable handle object is created by
the broker and is passed directly to the fixture effect port; no credential
value is accepted from a caller or written to an artifact, exception, or log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from pathlib import Path
from typing import Protocol

from video_factory.authority import VerificationPurpose
from video_factory.config.canonical import canonical_json_bytes, canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.json_boundary import parse_rfc3339_datetime

from .boundary import FixtureRuntimeBoundary
from .filesystem import _write_new, read_stable_regular_file


CREDENTIAL_ATTESTATION_VERSION = "credential-registration-attestation/1.0"
CREDENTIAL_VERIFICATION_VERSION = "credential-lease-verification/1.0"


class CredentialRuntimeError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class CredentialScope:
    request_sha256: HashDigest
    plan_sha256: HashDigest
    workspace_id: OpaqueId
    channel_id: OpaqueId
    concept_id: OpaqueId
    episode_id: OpaqueId
    service_identity: OpaqueId
    action_id: OpaqueId
    adapter_id: OpaqueId
    variant_id: OpaqueId | None
    destination: str
    purpose: VerificationPurpose


def credential_scope_sha256(value: CredentialScope) -> HashDigest:
    if not isinstance(value.purpose, VerificationPurpose):
        raise CredentialRuntimeError(
            "runtime.credential.purpose", "credential purpose is invalid"
        )
    if not value.destination:
        raise CredentialRuntimeError(
            "runtime.credential.destination", "credential destination is missing"
        )
    return canonical_sha256(
        {
            "artifact_version": "credential-scope/1.0",
            "request_sha256": str(value.request_sha256),
            "plan_sha256": str(value.plan_sha256),
            "workspace_id": str(value.workspace_id),
            "channel_id": str(value.channel_id),
            "concept_id": str(value.concept_id),
            "episode_id": str(value.episode_id),
            "service_identity": str(value.service_identity),
            "action_id": str(value.action_id),
            "adapter_id": str(value.adapter_id),
            "variant_id": str(value.variant_id) if value.variant_id is not None else None,
            "destination": value.destination,
            "purpose": value.purpose.value,
        }
    )


class OpaqueCredentialHandle:
    """Non-serializable capability object created only inside this module."""

    __slots__ = ("_identity",)

    def __init__(self, identity: object, *, _issuer: object) -> None:
        if _issuer is not _HANDLE_ISSUER:
            raise CredentialRuntimeError(
                "runtime.credential.handle_issuer",
                "opaque credential handles are issued only by a trusted broker",
            )
        self._identity = identity

    def __repr__(self) -> str:
        return "<OpaqueCredentialHandle redacted>"

    def __str__(self) -> str:
        return "<redacted>"

    def __reduce_ex__(self, protocol: int):
        raise TypeError("opaque credential handles are not serializable")


_HANDLE_ISSUER = object()


@dataclass(frozen=True, slots=True)
class CredentialLease:
    credential_reference: ArtifactReference
    scope_sha256: HashDigest
    attestation_record: ArtifactReference
    verification_record: ArtifactReference
    evaluated_at: datetime
    valid_until: datetime
    generation: int
    handle: OpaqueCredentialHandle


class TrustedCredentialBroker(Protocol):
    def verify_current(
        self,
        credential_reference: ArtifactReference,
        scope: CredentialScope,
        *,
        evaluated_at: datetime,
    ) -> CredentialLease | None: ...


@dataclass(frozen=True, slots=True)
class FixtureCredentialRegistration:
    credential_reference: ArtifactReference
    scope_sha256s: tuple[HashDigest, ...]
    valid_from: datetime
    valid_until: datetime
    generation: int


class FixtureCredentialBroker:
    """Pinned credential registry for isolated tests; never a secret store."""

    def __init__(
        self,
        boundary: FixtureRuntimeBoundary,
        registrations: tuple[FixtureCredentialRegistration, ...],
    ) -> None:
        if not registrations:
            raise CredentialRuntimeError(
                "runtime.credential.registry", "fixture credential registry is empty"
            )
        ordered = tuple(
            sorted(
                registrations,
                key=lambda item: (
                    str(item.credential_reference.path),
                    str(item.credential_reference.sha256),
                ),
            )
        )
        if ordered != registrations or len(
            {item.credential_reference for item in registrations}
        ) != len(registrations):
            raise CredentialRuntimeError(
                "runtime.credential.registry",
                "fixture credential registrations must be sorted and unique",
            )
        for item in registrations:
            if (
                item.valid_from.tzinfo is None
                or item.valid_from.utcoffset() is None
                or item.valid_until.tzinfo is None
                or item.valid_until.utcoffset() is None
                or item.valid_until <= item.valid_from
                or not isinstance(item.generation, int)
                or isinstance(item.generation, bool)
                or item.generation < 1
                or item.scope_sha256s != tuple(sorted(set(item.scope_sha256s)))
                or not item.scope_sha256s
            ):
                raise CredentialRuntimeError(
                    "runtime.credential.registration",
                    "fixture credential registration is invalid",
                )
        self._registrations = {
            item.credential_reference: item for item in registrations
        }
        self._revoked: set[ArtifactReference] = set()
        self._handles = {
            item.credential_reference: OpaqueCredentialHandle(
                object(), _issuer=_HANDLE_ISSUER
            )
            for item in registrations
        }
        self._directory = boundary.require_directory(
            "credential-verifications", create=True
        )

    def revoke(self, credential_reference: ArtifactReference) -> None:
        if credential_reference not in self._registrations:
            raise CredentialRuntimeError(
                "runtime.credential.unknown", "credential reference is not registered"
            )
        self._revoked.add(credential_reference)

    def _persist_record(
        self,
        mapping: dict[str, object],
        *,
        prefix: str,
        artifact_version: str,
    ) -> ArtifactReference:
        payload = canonical_json_bytes(mapping)
        digest = HashDigest(hashlib.sha256(payload).hexdigest())
        path = self._directory / f"{prefix}-{digest}.json"
        if path.exists():
            stable = read_stable_regular_file(path)
            if stable.exact_sha256 != digest or stable.byte_length != len(payload):
                raise CredentialRuntimeError(
                    "runtime.credential.verification_conflict",
                    "credential evidence differs for the same digest",
                )
        else:
            _write_new(path, payload)
        return ArtifactReference(
            RelativeArtifactPath(f"runtime-credentials/{prefix}-{digest}.json"),
            digest,
            ArtifactVersion(artifact_version),
        )

    def verify_current(
        self,
        credential_reference: ArtifactReference,
        scope: CredentialScope,
        *,
        evaluated_at: datetime,
    ) -> CredentialLease | None:
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise CredentialRuntimeError(
                "runtime.credential.clock", "credential evaluation time is naive"
            )
        registration = self._registrations.get(credential_reference)
        scope_sha = credential_scope_sha256(scope)
        if (
            registration is None
            or credential_reference in self._revoked
            or scope_sha not in registration.scope_sha256s
            or evaluated_at < registration.valid_from
            or evaluated_at >= registration.valid_until
        ):
            return None
        attestation_mapping = {
            "artifact_version": CREDENTIAL_ATTESTATION_VERSION,
            "credential_reference": {
                "path": str(credential_reference.path),
                "sha256": str(credential_reference.sha256),
                "artifact_version": str(credential_reference.artifact_version),
            },
            "scope_sha256": str(scope_sha),
            "valid_from": registration.valid_from.isoformat(),
            "valid_until": registration.valid_until.isoformat(),
            "generation": registration.generation,
            "fixture_only": True,
            "production_enabled": False,
            "authority_effect": "none",
        }
        attestation = self._persist_record(
            attestation_mapping,
            prefix="credential-attestation",
            artifact_version=CREDENTIAL_ATTESTATION_VERSION,
        )
        mapping = {
            "artifact_version": CREDENTIAL_VERIFICATION_VERSION,
            "credential_reference": {
                "path": str(credential_reference.path),
                "sha256": str(credential_reference.sha256),
                "artifact_version": str(credential_reference.artifact_version),
            },
            "scope_sha256": str(scope_sha),
            "attestation_sha256": str(attestation.sha256),
            "evaluated_at": evaluated_at.isoformat(),
            "valid_until": registration.valid_until.isoformat(),
            "generation": registration.generation,
            "revocation_checked": True,
            "fixture_only": True,
            "production_enabled": False,
            "authority_effect": "none",
        }
        record = self._persist_record(
            mapping,
            prefix="credential-verification",
            artifact_version=CREDENTIAL_VERIFICATION_VERSION,
        )
        return CredentialLease(
            credential_reference=credential_reference,
            scope_sha256=scope_sha,
            attestation_record=attestation,
            verification_record=record,
            evaluated_at=evaluated_at,
            valid_until=registration.valid_until,
            generation=registration.generation,
            handle=self._handles[credential_reference],
        )


def require_current_credential_lease(
    lease: CredentialLease | None,
    credential_reference: ArtifactReference,
    scope: CredentialScope,
    *,
    evaluated_at: datetime,
) -> CredentialLease:
    if lease is None:
        raise CredentialRuntimeError(
            "runtime.credential.denied", "trusted credential broker denied the scope"
        )
    expected_scope = credential_scope_sha256(scope)
    if (
        lease.credential_reference != credential_reference
        or lease.scope_sha256 != expected_scope
        or lease.evaluated_at != evaluated_at
        or lease.valid_until <= evaluated_at
        or not isinstance(lease.generation, int)
        or isinstance(lease.generation, bool)
        or lease.generation < 1
        or not isinstance(lease.handle, OpaqueCredentialHandle)
        or str(lease.attestation_record.artifact_version)
        != CREDENTIAL_ATTESTATION_VERSION
        or str(lease.verification_record.artifact_version)
        != CREDENTIAL_VERIFICATION_VERSION
    ):
        raise CredentialRuntimeError(
            "runtime.credential.rebound",
            "credential lease differs from the exact current effect scope",
        )
    parse_rfc3339_datetime(lease.evaluated_at.isoformat())
    parse_rfc3339_datetime(lease.valid_until.isoformat())
    return lease


__all__ = [
    "CREDENTIAL_ATTESTATION_VERSION",
    "CREDENTIAL_VERIFICATION_VERSION",
    "CredentialLease",
    "CredentialRuntimeError",
    "CredentialScope",
    "FixtureCredentialBroker",
    "FixtureCredentialRegistration",
    "OpaqueCredentialHandle",
    "TrustedCredentialBroker",
    "credential_scope_sha256",
    "require_current_credential_lease",
]
