"""Durable, fixture-only migration control for read-only Blueprint projections.

The module never marks a projection current and never grants workflow authority.
It only selects a read path for an explicitly named fixture consumer after an
exact pair was admitted to a pinned parity corpus and a separate trusted
activation verifier approved the transition.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Iterator, Protocol

from video_factory.artifacts import validate_artifact_mapping
from video_factory.blueprint import (
    BlueprintProjection,
    blueprint_artifact_from_bytes,
    blueprint_projection_artifact_sha256,
    blueprint_projection_to_mapping,
)
from video_factory.config.canonical import canonical_json_bytes, canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.json_boundary import (
    JsonInputError,
    parse_json_bytes,
    parse_rfc3339_datetime,
    require_json_object,
)
from video_factory.runtime import (
    MigrationCutoverState,
    MigrationMode,
    ProjectionParityReceipt,
    build_migration_cutover_state,
    build_projection_parity_receipt,
    migration_cutover_state_from_mapping,
    migration_cutover_state_to_mapping,
    projection_parity_receipt_to_mapping,
)

from .boundary import FixtureRuntimeBoundary
from .filesystem import read_stable_regular_file


PARITY_VERIFIER_RECORD_VERSION = "projection-parity-verification/1.0"
MIGRATION_ACTIVATION_RECORD_VERSION = "migration-activation-verification/1.0"
MIGRATION_ROLLBACK_RECORD_VERSION = "migration-rollback-verification/1.0"
MIGRATION_DB_VERSION = "sqlite-migration-registry/1.0"


class MigrationRuntimeError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class PinnedParityCase:
    consumer_id: OpaqueId
    view_kind: OpaqueId
    legacy_artifact_sha256: HashDigest
    projection_artifact_sha256: HashDigest
    policy_bundle_sha256: HashDigest
    legacy_normalized_sha256: HashDigest
    projection_normalized_sha256: HashDigest
    difference_count: int


@dataclass(frozen=True, slots=True)
class ProjectionParityVerification:
    legacy_normalized_sha256: HashDigest
    projection_normalized_sha256: HashDigest
    normalizer_id: OpaqueId
    normalizer_version: str
    normalizer_sha256: HashDigest
    verifier_record: ArtifactReference
    difference_count: int


class TrustedProjectionParityVerifier(Protocol):
    def verify_exact(
        self,
        *,
        consumer_id: OpaqueId,
        view_kind: OpaqueId,
        legacy_artifact: ArtifactReference,
        legacy_document: bytes,
        projection: BlueprintProjection,
        projection_artifact: ArtifactReference,
        projection_document: bytes,
        policy_bundle_sha256: HashDigest,
        evaluated_at: datetime,
    ) -> ProjectionParityVerification | None: ...


@dataclass(frozen=True, slots=True)
class MigrationActivationRequest:
    migration_id: OpaqueId
    consumer_id: OpaqueId
    view_kind: OpaqueId
    generation: int
    target_mode: MigrationMode
    previous_state_sha256: HashDigest | None
    parity_receipts: tuple[ArtifactReference, ...]
    feature_flag_sha256: HashDigest
    policy_bundle_sha256: HashDigest
    evaluated_at: datetime

    @property
    def request_sha256(self) -> HashDigest:
        return canonical_sha256(_activation_request_mapping(self))


@dataclass(frozen=True, slots=True)
class MigrationApprovalVerification:
    request_sha256: HashDigest
    record: ArtifactReference
    approved: bool


class TrustedMigrationApprovalVerifier(Protocol):
    def verify_current(
        self, request: MigrationActivationRequest
    ) -> MigrationApprovalVerification | None: ...


@dataclass(frozen=True, slots=True)
class MigrationPolicy:
    policy_bundle_sha256: HashDigest
    max_parity_age: timedelta
    allowed_consumers: tuple[tuple[OpaqueId, OpaqueId], ...]


@dataclass(frozen=True, slots=True)
class ReadOnlyProjectionSelection:
    selected_artifact: ArtifactReference
    selected_source: str
    migration_state_sha256: HashDigest
    read_only: bool = True
    authority_effect: str = "none"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MigrationRuntimeError(
            "runtime.migration.time", "migration time must be timezone-aware"
        )
    return value


def _reference_mapping(value: ArtifactReference) -> dict[str, str]:
    return {
        "path": str(value.path),
        "sha256": str(value.sha256),
        "artifact_version": str(value.artifact_version),
    }


def _activation_request_mapping(
    value: MigrationActivationRequest,
) -> dict[str, object]:
    return {
        "artifact_version": "migration-activation-request/1.0",
        "migration_id": str(value.migration_id),
        "consumer_id": str(value.consumer_id),
        "view_kind": str(value.view_kind),
        "generation": value.generation,
        "target_mode": value.target_mode.value,
        "previous_state_sha256": (
            str(value.previous_state_sha256)
            if value.previous_state_sha256 is not None
            else None
        ),
        "parity_receipts": [
            _reference_mapping(item) for item in value.parity_receipts
        ],
        "feature_flag_sha256": str(value.feature_flag_sha256),
        "policy_bundle_sha256": str(value.policy_bundle_sha256),
        "evaluated_at": _aware(value.evaluated_at).isoformat(),
        "requested_authority_effect": "none",
        "requested_read_only": True,
    }


def _write_new(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _persist_mapping(
    directory: Path,
    *,
    file_name: str,
    logical_path: str,
    artifact_version: str,
    mapping: dict[str, object],
) -> ArtifactReference:
    payload = canonical_json_bytes(mapping)
    digest = HashDigest(hashlib.sha256(payload).hexdigest())
    path = directory / file_name
    if path.exists():
        stable = read_stable_regular_file(path)
        if stable.exact_sha256 != digest or stable.byte_length != len(payload):
            raise MigrationRuntimeError(
                "runtime.migration.artifact_conflict",
                "migration evidence path already contains different bytes",
            )
    else:
        _write_new(path, payload)
    return ArtifactReference(
        RelativeArtifactPath(logical_path),
        digest,
        ArtifactVersion(artifact_version),
    )


class FixturePinnedParityVerifier:
    """Exact-byte verifier backed by a test-owned, immutable parity corpus.

    It intentionally cannot infer semantic equivalence for an unseen pair.
    Production activation therefore remains impossible until a separately
    reviewed versioned normalizer supplies an equivalent trusted port.
    """

    normalizer_id = OpaqueId("fixture-pinned-parity-normalizer")
    normalizer_version = "1.0"

    def __init__(
        self,
        boundary: FixtureRuntimeBoundary,
        *,
        cases: tuple[PinnedParityCase, ...],
    ) -> None:
        self._directory = boundary.require_directory("migration-evidence", create=True)
        ordered = tuple(
            sorted(
                cases,
                key=lambda item: (
                    str(item.consumer_id),
                    str(item.view_kind),
                    str(item.legacy_artifact_sha256),
                    str(item.projection_artifact_sha256),
                    str(item.policy_bundle_sha256),
                ),
            )
        )
        if not ordered or ordered != cases:
            raise MigrationRuntimeError(
                "runtime.migration.corpus_order",
                "fixture parity corpus must be non-empty and canonically ordered",
            )
        identities = tuple(
            (
                str(item.consumer_id),
                str(item.view_kind),
                str(item.legacy_artifact_sha256),
                str(item.projection_artifact_sha256),
                str(item.policy_bundle_sha256),
            )
            for item in ordered
        )
        if len(set(identities)) != len(identities):
            raise MigrationRuntimeError(
                "runtime.migration.corpus_duplicate",
                "fixture parity corpus contains duplicate identities",
            )
        for item in ordered:
            if (
                not isinstance(item.difference_count, int)
                or isinstance(item.difference_count, bool)
                or item.difference_count < 0
                or (item.difference_count == 0)
                != (
                    item.legacy_normalized_sha256
                    == item.projection_normalized_sha256
                )
            ):
                raise MigrationRuntimeError(
                    "runtime.migration.corpus_result",
                    "fixture parity corpus result is internally inconsistent",
                )
        self._cases = {identity: item for identity, item in zip(identities, ordered)}
        self.normalizer_sha256 = canonical_sha256(
            {
                "normalizer_id": str(self.normalizer_id),
                "normalizer_version": self.normalizer_version,
                "fixture_only": True,
                "production_activation_enabled": False,
                "cases": [
                    {
                        "consumer_id": str(item.consumer_id),
                        "view_kind": str(item.view_kind),
                        "legacy_artifact_sha256": str(item.legacy_artifact_sha256),
                        "projection_artifact_sha256": str(
                            item.projection_artifact_sha256
                        ),
                        "policy_bundle_sha256": str(item.policy_bundle_sha256),
                        "legacy_normalized_sha256": str(
                            item.legacy_normalized_sha256
                        ),
                        "projection_normalized_sha256": str(
                            item.projection_normalized_sha256
                        ),
                        "difference_count": item.difference_count,
                    }
                    for item in ordered
                ],
            }
        )

    @staticmethod
    def _validate_legacy(
        legacy_artifact: ArtifactReference, legacy_document: bytes
    ) -> None:
        if hashlib.sha256(legacy_document).hexdigest() != str(
            legacy_artifact.sha256
        ):
            raise MigrationRuntimeError(
                "runtime.migration.legacy_rebound",
                "legacy bytes differ from the immutable reference",
            )
        try:
            mapping = require_json_object(parse_json_bytes(legacy_document))
        except (JsonInputError, ValueError, TypeError) as error:
            raise MigrationRuntimeError(
                "runtime.migration.legacy_json", "legacy artifact is not strict JSON"
            ) from error
        report = validate_artifact_mapping(mapping)
        if not report.ok or mapping.get("artifact_version") != str(
            legacy_artifact.artifact_version
        ):
            raise MigrationRuntimeError(
                "runtime.migration.legacy_schema",
                "legacy artifact does not satisfy its exact registered schema",
            )

    @staticmethod
    def _validate_projection(
        projection: BlueprintProjection,
        projection_artifact: ArtifactReference,
        projection_document: bytes,
    ) -> None:
        if hashlib.sha256(projection_document).hexdigest() != str(
            projection_artifact.sha256
        ):
            raise MigrationRuntimeError(
                "runtime.migration.projection_rebound",
                "projection bytes differ from the immutable reference",
            )
        parsed = blueprint_artifact_from_bytes(projection_document)
        if not isinstance(parsed, BlueprintProjection) or parsed != projection:
            raise MigrationRuntimeError(
                "runtime.migration.projection_parse",
                "projection bytes do not reconstruct the exact projection",
            )
        blueprint_projection_to_mapping(projection)
        if (
            str(projection_artifact.artifact_version) != "blueprint-projection/1.0"
            or projection_artifact.sha256
            != blueprint_projection_artifact_sha256(projection)
        ):
            raise MigrationRuntimeError(
                "runtime.migration.projection_reference",
                "projection reference is not bound to the exact projection",
            )

    def verify_exact(
        self,
        *,
        consumer_id: OpaqueId,
        view_kind: OpaqueId,
        legacy_artifact: ArtifactReference,
        legacy_document: bytes,
        projection: BlueprintProjection,
        projection_artifact: ArtifactReference,
        projection_document: bytes,
        policy_bundle_sha256: HashDigest,
        evaluated_at: datetime,
    ) -> ProjectionParityVerification | None:
        self._validate_legacy(legacy_artifact, legacy_document)
        self._validate_projection(
            projection, projection_artifact, projection_document
        )
        if (
            str(view_kind) != projection.view_kind.value
            or str(legacy_artifact.artifact_version)
            != projection.legacy_artifact_version
        ):
            return None
        key = (
            str(consumer_id),
            str(view_kind),
            str(legacy_artifact.sha256),
            str(projection_artifact.sha256),
            str(policy_bundle_sha256),
        )
        case = self._cases.get(key)
        if case is None:
            return None
        record_mapping = {
            "artifact_version": PARITY_VERIFIER_RECORD_VERSION,
            "consumer_id": str(consumer_id),
            "view_kind": str(view_kind),
            "legacy_artifact": _reference_mapping(legacy_artifact),
            "projection_artifact": _reference_mapping(projection_artifact),
            "source_blueprint_sha256": str(projection.source_blueprint_sha256),
            "projection_sha256": str(projection.projection_sha256),
            "normalizer_id": str(self.normalizer_id),
            "normalizer_version": self.normalizer_version,
            "normalizer_sha256": str(self.normalizer_sha256),
            "policy_bundle_sha256": str(policy_bundle_sha256),
            "evaluated_at": _aware(evaluated_at).isoformat(),
            "legacy_normalized_sha256": str(case.legacy_normalized_sha256),
            "projection_normalized_sha256": str(
                case.projection_normalized_sha256
            ),
            "difference_count": case.difference_count,
            "parity_pass": case.difference_count == 0,
            "fixture_only": True,
            "production_activation_enabled": False,
            "authority_effect": "none",
        }
        record_sha = canonical_sha256(record_mapping)
        record = _persist_mapping(
            self._directory,
            file_name=f"parity-verification-{str(record_sha)[:20]}.json",
            logical_path=f"runtime-migration/parity-verification-{str(record_sha)[:20]}.json",
            artifact_version=PARITY_VERIFIER_RECORD_VERSION,
            mapping=record_mapping,
        )
        return ProjectionParityVerification(
            legacy_normalized_sha256=case.legacy_normalized_sha256,
            projection_normalized_sha256=case.projection_normalized_sha256,
            normalizer_id=self.normalizer_id,
            normalizer_version=self.normalizer_version,
            normalizer_sha256=self.normalizer_sha256,
            verifier_record=record,
            difference_count=case.difference_count,
        )


class DurableReadOnlyMigrationRuntime:
    """Durable migration coordinator with an append-only generation chain."""

    def __init__(
        self,
        boundary: FixtureRuntimeBoundary,
        *,
        parity_verifier: TrustedProjectionParityVerifier,
        approval_verifier: TrustedMigrationApprovalVerifier,
        policy: MigrationPolicy,
    ) -> None:
        if policy.max_parity_age <= timedelta(0):
            raise MigrationRuntimeError(
                "runtime.migration.policy", "parity freshness window must be positive"
            )
        if policy.allowed_consumers != tuple(sorted(set(policy.allowed_consumers))):
            raise MigrationRuntimeError(
                "runtime.migration.policy",
                "allowed consumer/view pairs must be sorted and unique",
            )
        self._parity_verifier = parity_verifier
        self._approval_verifier = approval_verifier
        self._policy = policy
        self._directory = boundary.require_directory("migration-state", create=True)
        self._evidence_directory = boundary.require_directory(
            "migration-receipts", create=True
        )
        self._database = self._directory / "migration.sqlite3"
        self._initialize_database()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS migration_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS migration_states (
                    migration_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    state_sha256 TEXT NOT NULL UNIQUE,
                    state_json TEXT NOT NULL,
                    PRIMARY KEY (migration_id, generation)
                );
                CREATE TABLE IF NOT EXISTS migration_current (
                    migration_id TEXT PRIMARY KEY,
                    generation INTEGER NOT NULL,
                    state_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS verified_parity_receipts (
                    receipt_sha256 TEXT PRIMARY KEY,
                    receipt_json TEXT NOT NULL,
                    reference_path TEXT NOT NULL,
                    reference_sha256 TEXT NOT NULL,
                    reference_version TEXT NOT NULL,
                    verifier_record_sha256 TEXT NOT NULL,
                    verified_at TEXT NOT NULL
                );
                """
            )
            row = connection.execute(
                "SELECT value FROM migration_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO migration_meta(key, value) VALUES('schema_version', ?)",
                    (MIGRATION_DB_VERSION,),
                )
            elif row["value"] != MIGRATION_DB_VERSION:
                raise MigrationRuntimeError(
                    "runtime.migration.database_version",
                    "migration database version is unsupported",
                )

    def _require_allowed(self, consumer_id: OpaqueId, view_kind: OpaqueId) -> None:
        if (consumer_id, view_kind) not in self._policy.allowed_consumers:
            raise MigrationRuntimeError(
                "runtime.migration.consumer",
                "migration consumer/view is not enabled by the current policy",
            )

    def verify_pair(
        self,
        *,
        consumer_id: OpaqueId,
        view_kind: OpaqueId,
        legacy_artifact: ArtifactReference,
        legacy_document: bytes,
        projection: BlueprintProjection,
        projection_artifact: ArtifactReference,
        projection_document: bytes,
        evaluated_at: datetime,
    ) -> tuple[ProjectionParityReceipt, ArtifactReference]:
        self._require_allowed(consumer_id, view_kind)
        verification = self._parity_verifier.verify_exact(
            consumer_id=consumer_id,
            view_kind=view_kind,
            legacy_artifact=legacy_artifact,
            legacy_document=legacy_document,
            projection=projection,
            projection_artifact=projection_artifact,
            projection_document=projection_document,
            policy_bundle_sha256=self._policy.policy_bundle_sha256,
            evaluated_at=_aware(evaluated_at),
        )
        if verification is None:
            raise MigrationRuntimeError(
                "runtime.migration.parity_unverified",
                "trusted parity verifier did not verify the exact pair",
            )
        receipt = build_projection_parity_receipt(
            consumer_id=consumer_id,
            view_kind=view_kind,
            legacy_artifact=legacy_artifact,
            projection_artifact=projection_artifact,
            source_blueprint_sha256=projection.source_blueprint_sha256,
            projection_sha256=projection.projection_sha256,
            legacy_normalized_sha256=verification.legacy_normalized_sha256,
            projection_normalized_sha256=verification.projection_normalized_sha256,
            normalizer_id=verification.normalizer_id,
            normalizer_version=verification.normalizer_version,
            normalizer_sha256=verification.normalizer_sha256,
            verifier_record=verification.verifier_record,
            policy_bundle_sha256=self._policy.policy_bundle_sha256,
            evaluated_at=_aware(evaluated_at).isoformat(),
            parity_pass=verification.difference_count == 0,
            difference_count=verification.difference_count,
            authority_effect="none",
        )
        mapping = projection_parity_receipt_to_mapping(receipt)
        reference = _persist_mapping(
            self._evidence_directory,
            file_name=f"{receipt.receipt_id}.json",
            logical_path=f"runtime-migration/{receipt.receipt_id}.json",
            artifact_version=receipt.artifact_version,
            mapping=mapping,
        )
        payload = canonical_json_bytes(mapping).decode("utf-8")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM verified_parity_receipts WHERE receipt_sha256 = ?",
                (str(receipt.receipt_sha256),),
            ).fetchone()
            exact = (
                payload,
                str(reference.path),
                str(reference.sha256),
                str(reference.artifact_version),
                str(receipt.verifier_record.sha256),
                receipt.evaluated_at,
            )
            if existing is not None:
                stored = (
                    existing["receipt_json"],
                    existing["reference_path"],
                    existing["reference_sha256"],
                    existing["reference_version"],
                    existing["verifier_record_sha256"],
                    existing["verified_at"],
                )
                if stored != exact:
                    raise MigrationRuntimeError(
                        "runtime.migration.parity_registry_conflict",
                        "trusted parity receipt identity is already bound to other bytes",
                    )
            else:
                connection.execute(
                    """
                    INSERT INTO verified_parity_receipts(
                        receipt_sha256, receipt_json, reference_path,
                        reference_sha256, reference_version,
                        verifier_record_sha256, verified_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (str(receipt.receipt_sha256), *exact),
                )
        return receipt, reference

    def _verify_parity_receipts(
        self,
        *,
        consumer_id: OpaqueId,
        view_kind: OpaqueId,
        receipts: tuple[tuple[ProjectionParityReceipt, ArtifactReference], ...],
        evaluated_at: datetime,
    ) -> tuple[ArtifactReference, ...]:
        references: list[ArtifactReference] = []
        for receipt, reference in receipts:
            mapping = projection_parity_receipt_to_mapping(receipt)
            payload = canonical_json_bytes(mapping).decode("utf-8")
            expected_sha = HashDigest(
                hashlib.sha256(canonical_json_bytes(mapping)).hexdigest()
            )
            if (
                reference.sha256 != expected_sha
                or str(reference.artifact_version) != receipt.artifact_version
                or receipt.consumer_id != consumer_id
                or receipt.view_kind != view_kind
                or receipt.policy_bundle_sha256 != self._policy.policy_bundle_sha256
                or receipt.parity_pass is not True
                or receipt.difference_count != 0
            ):
                raise MigrationRuntimeError(
                    "runtime.migration.parity_binding",
                    "parity receipt is not bound to the exact migration",
                )
            with self._connect() as connection:
                trusted = connection.execute(
                    "SELECT * FROM verified_parity_receipts WHERE receipt_sha256 = ?",
                    (str(receipt.receipt_sha256),),
                ).fetchone()
            if trusted is None or (
                trusted["receipt_json"] != payload
                or trusted["reference_path"] != str(reference.path)
                or trusted["reference_sha256"] != str(reference.sha256)
                or trusted["reference_version"] != str(reference.artifact_version)
                or trusted["verifier_record_sha256"]
                != str(receipt.verifier_record.sha256)
                or trusted["verified_at"] != receipt.evaluated_at
            ):
                raise MigrationRuntimeError(
                    "runtime.migration.parity_unregistered",
                    "parity receipt was not durably registered by trusted exact-byte verification",
                )
            persisted = read_stable_regular_file(
                self._evidence_directory / f"{receipt.receipt_id}.json"
            )
            if persisted.exact_sha256 != reference.sha256:
                raise MigrationRuntimeError(
                    "runtime.migration.parity_evidence_rebound",
                    "persisted parity receipt bytes differ from the trusted registry",
                )
            verified_at = parse_rfc3339_datetime(receipt.evaluated_at)
            if verified_at > evaluated_at or evaluated_at - verified_at > self._policy.max_parity_age:
                raise MigrationRuntimeError(
                    "runtime.migration.parity_stale",
                    "parity receipt is future-dated or stale",
                )
            references.append(reference)
        ordered = tuple(
            sorted(
                references,
                key=lambda item: (
                    str(item.path), str(item.sha256), str(item.artifact_version)
                ),
            )
        )
        if ordered != tuple(references) or len(set(ordered)) != len(ordered):
            raise MigrationRuntimeError(
                "runtime.migration.parity_order",
                "parity receipt references must be sorted and unique",
            )
        return ordered

    def _approval(
        self, request: MigrationActivationRequest
    ) -> MigrationApprovalVerification:
        verification = self._approval_verifier.verify_current(request)
        expected_version = (
            MIGRATION_ROLLBACK_RECORD_VERSION
            if request.target_mode is MigrationMode.ROLLED_BACK
            else MIGRATION_ACTIVATION_RECORD_VERSION
        )
        if (
            verification is None
            or verification.approved is not True
            or verification.request_sha256 != request.request_sha256
            or str(verification.record.artifact_version) != expected_version
        ):
            raise MigrationRuntimeError(
                "runtime.migration.approval",
                "trusted activation verifier did not approve the exact transition",
            )
        return verification

    def initialize_legacy(
        self,
        *,
        migration_id: OpaqueId,
        consumer_id: OpaqueId,
        view_kind: OpaqueId,
        feature_flag_sha256: HashDigest,
        evaluated_at: datetime,
    ) -> MigrationCutoverState:
        self._require_allowed(consumer_id, view_kind)
        request = MigrationActivationRequest(
            migration_id=migration_id,
            consumer_id=consumer_id,
            view_kind=view_kind,
            generation=0,
            target_mode=MigrationMode.LEGACY_ONLY,
            previous_state_sha256=None,
            parity_receipts=(),
            feature_flag_sha256=feature_flag_sha256,
            policy_bundle_sha256=self._policy.policy_bundle_sha256,
            evaluated_at=_aware(evaluated_at),
        )
        approval = self._approval(request)
        state = build_migration_cutover_state(
            migration_id=migration_id,
            consumer_id=consumer_id,
            view_kind=view_kind,
            generation=0,
            mode=MigrationMode.LEGACY_ONLY,
            previous_state_sha256=None,
            parity_receipts=(),
            feature_flag_sha256=feature_flag_sha256,
            activation_record=approval.record,
            rollback_record=None,
            activated_at=evaluated_at.isoformat(),
            rollback_required=False,
            read_only=True,
            projections_are_authority=False,
            authority_effect="none",
        )
        self._insert_initial(state)
        return state

    def _insert_initial(self, state: MigrationCutoverState) -> None:
        payload = canonical_json_bytes(migration_cutover_state_to_mapping(state)).decode(
            "utf-8"
        )
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO migration_states VALUES (?, ?, ?, ?)",
                    (
                        str(state.migration_id),
                        state.generation,
                        str(state.state_sha256),
                        payload,
                    ),
                )
                connection.execute(
                    "INSERT INTO migration_current VALUES (?, ?, ?)",
                    (
                        str(state.migration_id),
                        state.generation,
                        str(state.state_sha256),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise MigrationRuntimeError(
                "runtime.migration.exists", "migration state already exists"
            ) from error

    def load(self, migration_id: OpaqueId) -> MigrationCutoverState:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT c.generation AS current_generation,
                       c.state_sha256 AS current_state_sha256,
                       s.state_json
                  FROM migration_current c
                  JOIN migration_states s
                    ON s.migration_id = c.migration_id
                   AND s.generation = c.generation
                 WHERE c.migration_id = ?
                """,
                (str(migration_id),),
            ).fetchone()
        if row is None:
            raise MigrationRuntimeError(
                "runtime.migration.missing", "migration state does not exist"
            )
        try:
            mapping = require_json_object(parse_json_bytes(row["state_json"].encode()))
            state = migration_cutover_state_from_mapping(mapping)
            history = self.history(migration_id)
            if (
                not history
                or history[-1] != state
                or row["current_generation"] != state.generation
                or row["current_state_sha256"] != str(state.state_sha256)
            ):
                raise MigrationRuntimeError(
                    "runtime.migration.current_rebound",
                    "migration current pointer differs from the immutable chain",
                )
            approval = self._approval(
                MigrationActivationRequest(
                    migration_id=state.migration_id,
                    consumer_id=state.consumer_id,
                    view_kind=state.view_kind,
                    generation=state.generation,
                    target_mode=state.mode,
                    previous_state_sha256=state.previous_state_sha256,
                    parity_receipts=state.parity_receipts,
                    feature_flag_sha256=state.feature_flag_sha256,
                    policy_bundle_sha256=self._policy.policy_bundle_sha256,
                    evaluated_at=parse_rfc3339_datetime(state.activated_at),
                )
            )
            if approval.record != state.activation_record:
                raise MigrationRuntimeError(
                    "runtime.migration.approval_rebound",
                    "migration state is not bound to its current activation record",
                )
            return state
        except Exception as error:
            if isinstance(error, MigrationRuntimeError):
                raise
            raise MigrationRuntimeError(
                "runtime.migration.state_corrupt", "migration state is invalid"
            ) from error

    @staticmethod
    def _transition_allowed(current: MigrationMode, target: MigrationMode) -> bool:
        return target in {
            MigrationMode.LEGACY_ONLY: {MigrationMode.DUAL_READ_COMPARE},
            MigrationMode.DUAL_READ_COMPARE: {
                MigrationMode.PROJECTION_READ_ONLY,
                MigrationMode.ROLLED_BACK,
            },
            MigrationMode.PROJECTION_READ_ONLY: {MigrationMode.ROLLED_BACK},
            MigrationMode.ROLLED_BACK: {MigrationMode.DUAL_READ_COMPARE},
        }[current]

    def transition(
        self,
        *,
        migration_id: OpaqueId,
        target_mode: MigrationMode,
        feature_flag_sha256: HashDigest,
        parity_receipts: tuple[
            tuple[ProjectionParityReceipt, ArtifactReference], ...
        ] = (),
        evaluated_at: datetime,
    ) -> MigrationCutoverState:
        current = self.load(migration_id)
        when = _aware(evaluated_at)
        if not self._transition_allowed(current.mode, target_mode):
            raise MigrationRuntimeError(
                "runtime.migration.transition", "migration state transition is not allowed"
            )
        refs = self._verify_parity_receipts(
            consumer_id=current.consumer_id,
            view_kind=current.view_kind,
            receipts=parity_receipts,
            evaluated_at=when,
        )
        if target_mode is MigrationMode.PROJECTION_READ_ONLY and not refs:
            raise MigrationRuntimeError(
                "runtime.migration.parity_missing",
                "projection read-only mode requires fresh passing parity receipts",
            )
        if target_mode is not MigrationMode.PROJECTION_READ_ONLY and refs:
            raise MigrationRuntimeError(
                "runtime.migration.parity_unexpected",
                "only projection activation consumes parity receipts",
            )
        request = MigrationActivationRequest(
            migration_id=current.migration_id,
            consumer_id=current.consumer_id,
            view_kind=current.view_kind,
            generation=current.generation + 1,
            target_mode=target_mode,
            previous_state_sha256=current.state_sha256,
            parity_receipts=refs,
            feature_flag_sha256=feature_flag_sha256,
            policy_bundle_sha256=self._policy.policy_bundle_sha256,
            evaluated_at=when,
        )
        approval = self._approval(request)
        state = build_migration_cutover_state(
            migration_id=current.migration_id,
            consumer_id=current.consumer_id,
            view_kind=current.view_kind,
            generation=current.generation + 1,
            mode=target_mode,
            previous_state_sha256=current.state_sha256,
            parity_receipts=refs,
            feature_flag_sha256=feature_flag_sha256,
            activation_record=approval.record,
            rollback_record=(
                approval.record if target_mode is MigrationMode.ROLLED_BACK else None
            ),
            activated_at=when.isoformat(),
            rollback_required=target_mode
            in {
                MigrationMode.DUAL_READ_COMPARE,
                MigrationMode.PROJECTION_READ_ONLY,
            },
            read_only=True,
            projections_are_authority=False,
            authority_effect="none",
        )
        payload = canonical_json_bytes(migration_cutover_state_to_mapping(state)).decode(
            "utf-8"
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT generation, state_sha256 FROM migration_current WHERE migration_id = ?",
                (str(migration_id),),
            ).fetchone()
            if row is None or (
                row["generation"] != current.generation
                or row["state_sha256"] != str(current.state_sha256)
            ):
                raise MigrationRuntimeError(
                    "runtime.migration.concurrent",
                    "migration state changed during transition",
                )
            connection.execute(
                "INSERT INTO migration_states VALUES (?, ?, ?, ?)",
                (
                    str(state.migration_id),
                    state.generation,
                    str(state.state_sha256),
                    payload,
                ),
            )
            connection.execute(
                "UPDATE migration_current SET generation = ?, state_sha256 = ? WHERE migration_id = ?",
                (
                    state.generation,
                    str(state.state_sha256),
                    str(state.migration_id),
                ),
            )
        return state

    def history(self, migration_id: OpaqueId) -> tuple[MigrationCutoverState, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT state_json FROM migration_states WHERE migration_id = ? ORDER BY generation",
                (str(migration_id),),
            ).fetchall()
        states = tuple(
            migration_cutover_state_from_mapping(
                require_json_object(parse_json_bytes(row["state_json"].encode()))
            )
            for row in rows
        )
        for index, state in enumerate(states):
            if (
                state.migration_id != migration_id
                or state.generation != index
                or (index == 0) != (state.previous_state_sha256 is None)
                or (
                    index > 0
                    and state.previous_state_sha256 != states[index - 1].state_sha256
                )
                or (
                    index > 0
                    and (
                        state.consumer_id != states[0].consumer_id
                        or state.view_kind != states[0].view_kind
                    )
                )
            ):
                raise MigrationRuntimeError(
                    "runtime.migration.chain",
                    "migration state history is incomplete, reordered, or rebound",
                )
        return states

    def select_read_only(
        self,
        state: MigrationCutoverState,
        *,
        legacy_artifact: ArtifactReference,
        projection_artifact: ArtifactReference,
        parity_receipt: ProjectionParityReceipt | None,
        parity_receipt_reference: ArtifactReference | None,
    ) -> ReadOnlyProjectionSelection:
        migration_cutover_state_to_mapping(state)
        if self.load(state.migration_id) != state:
            raise MigrationRuntimeError(
                "runtime.migration.state_stale",
                "projection selection requires the exact current durable state",
            )
        if state.mode is MigrationMode.ROLLED_BACK:
            if parity_receipt is None or parity_receipt_reference is None:
                raise MigrationRuntimeError(
                    "runtime.migration.rollback_target_missing",
                    "rollback selection requires the exact trusted legacy target receipt",
                )
            trusted_refs = {
                item
                for historical in self.history(state.migration_id)
                for item in historical.parity_receipts
            }
            mapping = projection_parity_receipt_to_mapping(parity_receipt)
            payload = canonical_json_bytes(mapping).decode("utf-8")
            expected_sha = HashDigest(hashlib.sha256(payload.encode("utf-8")).hexdigest())
            with self._connect() as connection:
                trusted = connection.execute(
                    "SELECT receipt_json FROM verified_parity_receipts WHERE receipt_sha256 = ?",
                    (str(parity_receipt.receipt_sha256),),
                ).fetchone()
            if (
                parity_receipt_reference not in trusted_refs
                or parity_receipt_reference.sha256 != expected_sha
                or parity_receipt.legacy_artifact != legacy_artifact
                or parity_receipt.consumer_id != state.consumer_id
                or parity_receipt.view_kind != state.view_kind
                or trusted is None
                or trusted["receipt_json"] != payload
            ):
                raise MigrationRuntimeError(
                    "runtime.migration.rollback_target_rebound",
                    "rollback legacy target differs from trusted parity provenance",
                )
            return ReadOnlyProjectionSelection(
                selected_artifact=legacy_artifact,
                selected_source="legacy_rollback",
                migration_state_sha256=state.state_sha256,
            )
        if state.mode is not MigrationMode.PROJECTION_READ_ONLY:
            return ReadOnlyProjectionSelection(
                selected_artifact=legacy_artifact,
                selected_source="legacy",
                migration_state_sha256=state.state_sha256,
            )
        if parity_receipt is None or parity_receipt_reference is None:
            raise MigrationRuntimeError(
                "runtime.migration.selection_parity",
                "projection selection requires its exact parity receipt",
            )
        mapping = projection_parity_receipt_to_mapping(parity_receipt)
        expected_sha = HashDigest(hashlib.sha256(canonical_json_bytes(mapping)).hexdigest())
        if (
            parity_receipt_reference not in state.parity_receipts
            or parity_receipt_reference.sha256 != expected_sha
            or parity_receipt.consumer_id != state.consumer_id
            or parity_receipt.view_kind != state.view_kind
            or parity_receipt.legacy_artifact != legacy_artifact
            or parity_receipt.projection_artifact != projection_artifact
            or parity_receipt.parity_pass is not True
            or parity_receipt.authority_effect != "none"
        ):
            raise MigrationRuntimeError(
                "runtime.migration.selection_rebound",
                "projection selection differs from the activated parity evidence",
            )
        return ReadOnlyProjectionSelection(
            selected_artifact=projection_artifact,
            selected_source="projection_read_only",
            migration_state_sha256=state.state_sha256,
        )


__all__ = [
    "MIGRATION_ACTIVATION_RECORD_VERSION",
    "MIGRATION_DB_VERSION",
    "MIGRATION_ROLLBACK_RECORD_VERSION",
    "PARITY_VERIFIER_RECORD_VERSION",
    "DurableReadOnlyMigrationRuntime",
    "FixturePinnedParityVerifier",
    "MigrationActivationRequest",
    "MigrationApprovalVerification",
    "MigrationPolicy",
    "MigrationRuntimeError",
    "PinnedParityCase",
    "ProjectionParityVerification",
    "ReadOnlyProjectionSelection",
    "TrustedMigrationApprovalVerifier",
    "TrustedProjectionParityVerifier",
]
