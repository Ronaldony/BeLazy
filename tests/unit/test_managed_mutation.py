"""Managed mutation contracts, pure planning, trust, and runtime guards."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import unicodedata

import pytest

from video_factory.approvals import GateContext
from video_factory.artifacts import validate_artifact_mapping
from video_factory.config import canonical_json_bytes
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    IdempotencyKey,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.mutation import (
    AuthenticatedHumanApproval,
    BreakGlassAuthorization,
    ChangeRequest,
    ContentObject,
    ExpectedBefore,
    IdempotencyRecord,
    MutationKind,
    MutationOperationIntent,
    MutationPathError,
    MutationPlanError,
    MutationReceipt,
    MutationReceiptStatus,
    MutationRiskTier,
    MutationSerializationError,
    OperationOutcome,
    OperationResult,
    PathNodeKind,
    PathObservation,
    RevisionEntry,
    WorkspaceObservation,
    WorkspaceRevision,
    WorkspaceTrustState,
    break_glass_authorization_to_mapping,
    change_request_to_mapping,
    derive_workspace_revision,
    detect_workspace_drift,
    drift_report_to_mapping,
    mutation_artifact_from_bytes,
    mutation_artifact_from_mapping,
    mutation_artifact_to_mapping,
    mutation_plan_to_mapping,
    mutation_receipt_to_mapping,
    path_collision_key,
    plan_mutation,
    require_collision_free,
    require_managed_path,
    require_no_link_or_reparse_ancestor,
    validate_mutation_plan,
    workspace_trust_blockers,
    workspace_revision_to_mapping,
)
from video_factory.providers import (
    BreakGlassPolicy,
    MutationPreSideEffectGuard,
    MutationRuntimeError,
)


SHA_A = HashDigest("a" * 64)
SHA_B = HashDigest("b" * 64)
SHA_C = HashDigest("c" * 64)
MANIFEST_A = HashDigest("1" * 64)
MANIFEST_B = HashDigest("2" * 64)
POLICY_SHA = HashDigest("3" * 64)
WORKSPACE_ID = OpaqueId("workspace-a")
REVISION_ID = OpaqueId("revision-a")
EVALUATED_AT = datetime(2026, 8, 7, 1, 0, tzinfo=timezone.utc)


def _reference(path: str, digest: HashDigest, version: str) -> ArtifactReference:
    return ArtifactReference(
        path=RelativeArtifactPath(path),
        sha256=digest,
        artifact_version=ArtifactVersion(version),
    )


def _revision(
    entries: tuple[RevisionEntry, ...] | None = None,
) -> WorkspaceRevision:
    return WorkspaceRevision(
        revision_id=REVISION_ID,
        workspace_id=WORKSPACE_ID,
        parent_revision_id=None,
        manifest_sha256=MANIFEST_A,
        created_at="2026-08-07T00:00:00Z",
        plan_id=OpaqueId("baseline-plan"),
        receipt_id=OpaqueId("baseline-receipt"),
        trust_state=WorkspaceTrustState.TRUSTED,
        entries=(
            entries
            if entries is not None
            else (
                RevisionEntry(
                    path=RelativeArtifactPath("artifacts/a.txt"),
                    revision_ordinal=1,
                    content_sha256=SHA_A,
                    byte_length=10,
                    tombstone=False,
                    source_operation_id=OpaqueId("baseline-operation"),
                ),
            )
        ),
    )


def _observation(
    *,
    digest: HashDigest = SHA_A,
    manifest: HashDigest = MANIFEST_A,
    revision_id: OpaqueId = REVISION_ID,
    trust: WorkspaceTrustState = WorkspaceTrustState.TRUSTED,
    complete: bool = True,
    entries: tuple[PathObservation, ...] | None = None,
) -> WorkspaceObservation:
    return WorkspaceObservation(
        workspace_id=WORKSPACE_ID,
        revision_id=revision_id,
        manifest_sha256=manifest,
        trust_state=trust,
        complete=complete,
        entries=(
            entries
            if entries is not None
            else (
                PathObservation(
                    path=RelativeArtifactPath("artifacts/a.txt"),
                    node_kind=PathNodeKind.FILE,
                    exact_sha256=digest,
                    byte_length=10,
                ),
            )
        ),
    )


def _request(
    *,
    kind: MutationKind = MutationKind.REPLACE,
    path: str = "artifacts/a.txt",
    expected: HashDigest | None = SHA_A,
    destination: str | None = None,
    risk: MutationRiskTier = MutationRiskTier.R1,
    key: str = "mutation-key-a",
) -> ChangeRequest:
    content = (
        ContentObject(OpaqueId("object-b"), SHA_B, 12)
        if kind in {MutationKind.CREATE, MutationKind.REPLACE}
        else None
    )
    return ChangeRequest(
        request_id=OpaqueId(f"request-{key}"),
        workspace_id=WORKSPACE_ID,
        requester_id=OpaqueId("requester-a"),
        requested_at="2026-08-07T00:15:00Z",
        before_revision_id=REVISION_ID,
        before_manifest_sha256=MANIFEST_A,
        idempotency_key=IdempotencyKey(key),
        risk_tier=risk,
        operations=(
            MutationOperationIntent(
                kind=kind,
                path=RelativeArtifactPath(path),
                expected_before=ExpectedBefore(expected is not None, expected),
                new_content=content,
                destination_path=(
                    RelativeArtifactPath(destination)
                    if destination is not None
                    else None
                ),
            ),
        ),
    )


def _plan(*, risk: MutationRiskTier = MutationRiskTier.R1):
    return plan_mutation(
        _request(risk=risk),
        _revision(),
        _observation(),
        policy_bundle_sha256=POLICY_SHA,
    )


def _gate_context(plan) -> GateContext:
    return GateContext(
        workflow_definition_sha256=HashDigest("4" * 64),
        policy_bundle_sha256=plan.policy_bundle_sha256,
        rules_bundle_sha256=HashDigest("5" * 64),
        effective_config_sha256=HashDigest("6" * 64),
        current_manifest_sha256=plan.before_manifest_sha256,
        evidence_graph_sha256=HashDigest("7" * 64),
        executable_plan_sha256=plan.plan_sha256,
    )


def _receipt(plan) -> MutationReceipt:
    results: list[OperationResult] = []
    for operation in plan.operations:
        if operation.new_content is not None:
            after_sha = operation.new_content.exact_sha256
            after_length = operation.new_content.byte_length
        elif operation.kind is MutationKind.MOVE:
            after_sha = operation.expected_before.exact_sha256
            after_length = 10
        else:
            after_sha = None
            after_length = None
        results.append(
            OperationResult(
                operation_id=operation.operation_id,
                kind=operation.kind,
                path=operation.path,
                destination_path=operation.destination_path,
                outcome=OperationOutcome.APPLIED,
                before_sha256=operation.expected_before.exact_sha256,
                after_sha256=after_sha,
                after_byte_length=after_length,
                reason_code="mutation.operation.applied",
            )
        )
    return MutationReceipt(
        receipt_id=OpaqueId("receipt-a"),
        plan_id=plan.plan_id,
        plan_sha256=plan.plan_sha256,
        workspace_id=plan.workspace_id,
        idempotency_key=plan.idempotency_key,
        executor_identity=OpaqueId("mutation-service"),
        started_at="2026-08-07T01:00:00Z",
        completed_at="2026-08-07T01:01:00Z",
        status=MutationReceiptStatus.SUCCEEDED,
        before_manifest_sha256=plan.before_manifest_sha256,
        after_manifest_sha256=MANIFEST_B,
        operation_results=tuple(results),
        rollback_or_reconciliation_required=False,
    )


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/absolute.txt",
        "C" + ":/absolute.txt",
        "../escape.txt",
        "a/./b.txt",
        "a//b.txt",
        "a\\b.txt",
        "a/stream:name.txt",
        "a/trailing. ",
        "a/CON.txt",
        "a/control\x00.txt",
    ],
)
def test_managed_paths_reject_cross_platform_aliases(path: str) -> None:
    with pytest.raises(MutationPathError):
        require_managed_path(path)


def test_managed_paths_reject_unicode_and_case_collisions_and_link_ancestors() -> None:
    nfc = "assets/caf\u00e9.txt"
    nfd = unicodedata.normalize("NFD", nfc)
    with pytest.raises(MutationPathError, match="NFC"):
        require_managed_path(nfd)
    assert path_collision_key("Assets/File.txt") == path_collision_key(
        "assets/file.txt"
    )
    with pytest.raises(MutationPathError, match="colliding"):
        require_collision_free(("Assets/File.txt", "assets/file.txt"))
    link = PathObservation(
        RelativeArtifactPath("assets"), PathNodeKind.SYMLINK, None, None
    )
    with pytest.raises(MutationPathError, match="symlink"):
        require_no_link_or_reparse_ancestor(
            "assets/file.txt", {"assets": link}
        )


def test_planner_is_deterministic_hash_bound_and_has_structured_semantic_diff() -> None:
    first = _plan()
    second = _plan()
    assert first == second
    assert first.plan_id == second.plan_id
    assert first.plan_sha256 == second.plan_sha256
    assert first.semantic_diff[0].before_sha256 == SHA_A
    assert first.semantic_diff[0].after_sha256 == SHA_B
    assert first.semantic_diff[0].operation_id == first.operations[0].operation_id
    assert validate_mutation_plan(first) is first

    tampered = replace(
        first,
        semantic_diff=(replace(first.semantic_diff[0], after_sha256=SHA_C),),
    )
    with pytest.raises(MutationPlanError) as captured:
        validate_mutation_plan(tampered)
    assert captured.value.reason_code == "mutation.plan.semantic_diff"


@pytest.mark.parametrize(
    ("request_change", "observation_change", "reason"),
    [
        ({"before_revision_id": OpaqueId("stale-revision")}, {}, "revision"),
        ({"before_manifest_sha256": SHA_C}, {}, "manifest"),
        ({}, {"digest": SHA_C}, "exact-before"),
        ({}, {"complete": False}, "complete"),
        ({}, {"trust": WorkspaceTrustState.UNTRUSTED}, "untrusted"),
    ],
)
def test_planner_rejects_stale_or_untrusted_current_facts(
    request_change: dict[str, object],
    observation_change: dict[str, object],
    reason: str,
) -> None:
    request = replace(_request(), **request_change)
    with pytest.raises(MutationPlanError, match=reason):
        plan_mutation(
            request,
            _revision(),
            _observation(**observation_change),
            policy_bundle_sha256=POLICY_SHA,
        )


def test_planner_rejects_internally_inconsistent_complete_observation() -> None:
    extended_revision = _revision(
        entries=(
            *_revision().entries,
            RevisionEntry(
                path=RelativeArtifactPath("artifacts/unrelated.txt"),
                revision_ordinal=1,
                content_sha256=SHA_B,
                byte_length=12,
                tombstone=False,
                source_operation_id=OpaqueId("baseline-operation-unrelated"),
            ),
        )
    )
    with pytest.raises(MutationPlanError) as missing:
        plan_mutation(
            _request(),
            extended_revision,
            _observation(),
            policy_bundle_sha256=POLICY_SHA,
        )
    assert missing.value.reason_code == "mutation.plan.workspace_drift"

    unexpected = replace(
        _observation(),
        entries=(
            *_observation().entries,
            PathObservation(
                RelativeArtifactPath("artifacts/unmanaged.txt"),
                PathNodeKind.FILE,
                SHA_B,
                12,
            ),
        ),
    )
    with pytest.raises(MutationPlanError) as added:
        plan_mutation(
            _request(),
            _revision(),
            unexpected,
            policy_bundle_sha256=POLICY_SHA,
        )
    assert added.value.reason_code == "mutation.plan.workspace_drift"


def test_workspace_trust_rejects_malformed_contract_values_fail_closed() -> None:
    malformed = replace(_observation(), trust_state="TRUSTED")
    assert workspace_trust_blockers(
        malformed,
        expected_revision_id=REVISION_ID,
        expected_manifest_sha256=MANIFEST_A,
    ) == ("mutation.workspace.trust_state",)


def test_create_and_move_preconditions_are_derived_from_current_state() -> None:
    create = _request(
        kind=MutationKind.CREATE,
        path="artifacts/new.txt",
        expected=None,
        key="create",
    )
    create_plan = plan_mutation(
        create, _revision(), _observation(), policy_bundle_sha256=POLICY_SHA
    )
    assert create_plan.operations[0].expected_before == ExpectedBefore(False, None)

    case_alias = _request(
        kind=MutationKind.CREATE,
        path="Artifacts/A.txt",
        expected=None,
        key="case-alias",
    )
    with pytest.raises(MutationPlanError) as collision:
        plan_mutation(
            case_alias,
            _revision(),
            _observation(),
            policy_bundle_sha256=POLICY_SHA,
        )
    assert collision.value.reason_code in {
        "mutation.path.observed_alias",
        "mutation.path.collision_existing",
    }

    move = _request(
        kind=MutationKind.MOVE,
        destination="archive/a.txt",
        key="move",
    )
    move_plan = plan_mutation(
        move, _revision(), _observation(), policy_bundle_sha256=POLICY_SHA
    )
    assert move_plan.semantic_diff[0].after_sha256 == SHA_A

    occupied = replace(
        _observation(),
        entries=(
            *_observation().entries,
            PathObservation(
                RelativeArtifactPath("archive/a.txt"),
                PathNodeKind.FILE,
                SHA_C,
                4,
            ),
        ),
    )
    with pytest.raises(MutationPlanError, match="destination already exists"):
        plan_mutation(move, _revision(), occupied, policy_bundle_sha256=POLICY_SHA)


def test_successful_receipt_creates_immutable_revision_and_tombstones() -> None:
    move_request = _request(
        kind=MutationKind.MOVE,
        destination="archive/a.txt",
        key="move-revision",
    )
    plan = plan_mutation(
        move_request,
        _revision(),
        _observation(),
        policy_bundle_sha256=POLICY_SHA,
    )
    receipt = _receipt(plan)
    revision = derive_workspace_revision(_revision(), plan, receipt)
    assert revision.parent_revision_id == REVISION_ID
    assert revision.manifest_sha256 == MANIFEST_B
    assert revision.entries[0] == _revision().entries[0]
    source_latest = [
        item for item in revision.entries if item.path == "artifacts/a.txt"
    ][-1]
    destination = [
        item for item in revision.entries if item.path == "archive/a.txt"
    ][-1]
    assert source_latest.tombstone is True
    assert source_latest.revision_ordinal == 2
    assert destination.tombstone is False
    assert destination.content_sha256 == SHA_A

    partial = replace(
        receipt,
        status=MutationReceiptStatus.PARTIALLY_APPLIED,
        rollback_or_reconciliation_required=True,
    )
    with pytest.raises(Exception, match="fully successful"):
        derive_workspace_revision(_revision(), plan, partial)


def test_drift_report_is_deterministic_and_invalidates_all_authority_consumers() -> None:
    observed = _observation(digest=SHA_C, manifest=MANIFEST_B)
    first = detect_workspace_drift(
        _revision(), observed, detected_at="2026-08-07T01:05:00Z"
    )
    second = detect_workspace_drift(
        _revision(), observed, detected_at="2026-08-07T01:05:00Z"
    )
    assert first == second
    assert first is not None
    assert first.trust_state is WorkspaceTrustState.UNTRUSTED
    assert first.generation_blocked and first.publish_blocked
    assert first.dependent_plans_invalidated
    assert first.authority_invalidated and first.qc_invalidated
    assert first.reconciliation_required
    assert {item.kind for item in first.findings} >= {
        first.findings[0].kind,
    }
    assert detect_workspace_drift(
        _revision(), _observation(), detected_at="2026-08-07T01:05:00Z"
    ) is None


def test_drift_ignores_structural_directories_but_keeps_path_safety_evidence() -> None:
    observation = replace(
        _observation(),
        entries=(
            PathObservation(
                path=RelativeArtifactPath("artifacts"),
                node_kind=PathNodeKind.DIRECTORY,
                exact_sha256=None,
                byte_length=None,
            ),
            *_observation().entries,
        ),
    )
    assert detect_workspace_drift(
        _revision(), observation, detected_at="2026-08-07T01:05:00Z"
    ) is None


def test_all_six_contracts_validate_and_round_trip_through_strict_bytes() -> None:
    request = _request()
    plan = _plan()
    receipt = _receipt(plan)
    revision = derive_workspace_revision(_revision(), plan, receipt)
    drift = detect_workspace_drift(
        revision,
        replace(
            _observation(manifest=SHA_C, revision_id=revision.revision_id),
            entries=(
                PathObservation(
                    RelativeArtifactPath("artifacts/a.txt"),
                    PathNodeKind.FILE,
                    SHA_C,
                    10,
                ),
            ),
        ),
        detected_at="2026-08-07T01:05:00Z",
    )
    assert drift is not None
    break_glass = _break_glass(_plan(risk=MutationRiskTier.R4))
    artifacts = (request, plan, receipt, revision, drift, break_glass)
    for artifact in artifacts:
        mapping = mutation_artifact_to_mapping(artifact)
        assert validate_artifact_mapping(mapping).ok
        parsed_mapping = mutation_artifact_from_mapping(mapping)
        parsed_bytes = mutation_artifact_from_bytes(canonical_json_bytes(mapping))
        assert parsed_mapping == artifact
        assert parsed_bytes == artifact

    duplicate = b'{"artifact_version":"change-request/1.0","artifact_version":"change-request/1.0"}'
    with pytest.raises(MutationSerializationError) as captured:
        mutation_artifact_from_bytes(duplicate)
    assert captured.value.reason_code == "json.duplicate_key"

    inconsistent_receipt = mutation_receipt_to_mapping(receipt)
    results = inconsistent_receipt["operation_results"]
    assert isinstance(results, list) and isinstance(results[0], dict)
    results[0]["outcome"] = "REJECTED"
    with pytest.raises(MutationSerializationError) as inconsistent:
        mutation_artifact_from_mapping(inconsistent_receipt)
    assert inconsistent.value.reason_code == "mutation.receipt.success_inconsistent"

    aliased_revision = workspace_revision_to_mapping(revision)
    revision_entries = aliased_revision["entries"]
    assert isinstance(revision_entries, list) and isinstance(
        revision_entries[0], dict
    )
    alias = dict(revision_entries[0])
    alias["path"] = "Artifacts/A.txt"
    revision_entries.append(alias)
    with pytest.raises(MutationSerializationError) as path_alias:
        mutation_artifact_from_mapping(aliased_revision)
    assert path_alias.value.reason_code == "mutation.path.collision_existing"

    tampered_drift = drift_report_to_mapping(drift)
    tampered_drift["report_id"] = "drift-tampered"
    with pytest.raises(MutationSerializationError) as drift_identity:
        mutation_artifact_from_mapping(tampered_drift)
    assert drift_identity.value.reason_code == "mutation.drift.identity"


class _TrustedAuthority:
    def verify(self, plan, observation, *, current_context, evaluated_at):
        return _reference(
            "authority/decision.json", SHA_C, "authority-decision/1.0"
        )


class _TrustedHumans:
    def __init__(self, principals: set[str]) -> None:
        self.principals = principals

    def verify_current_human_approval(self, approval, *, evaluated_at):
        return str(approval.principal_id) in self.principals


def _human(number: int) -> AuthenticatedHumanApproval:
    digest = HashDigest(str(number) * 64)
    return AuthenticatedHumanApproval(
        principal_id=OpaqueId(f"human-{number}"),
        authentication_evidence=_reference(
            f"identity/human-{number}.json",
            digest,
            "authenticated-principal/1.0",
        ),
        approval_record=_reference(
            f"approvals/human-{number}.json",
            digest,
            "mutation-human-approval/1.0",
        ),
        approved_at="2026-08-07T00:30:00Z",
    )


def _break_glass(plan) -> BreakGlassAuthorization:
    touched = tuple(
        RelativeArtifactPath(path)
        for path in sorted(
            {
                str(path)
                for operation in plan.operations
                for path in (operation.path, operation.destination_path)
                if path is not None
            }
        )
    )
    return BreakGlassAuthorization(
        authorization_id=OpaqueId("break-glass-a"),
        plan_sha256=plan.plan_sha256,
        workspace_id=plan.workspace_id,
        before_revision_id=plan.before_revision_id,
        before_manifest_sha256=plan.before_manifest_sha256,
        issued_at="2026-08-07T00:45:00Z",
        expires_at="2026-08-07T01:30:00Z",
        exact_paths=touched,
        operation_kinds=frozenset(item.kind for item in plan.operations),
        approvals=(_human(1), _human(2)),
        pre_change_snapshot=_reference(
            "snapshots/pre-change.json", MANIFEST_A, "workspace-snapshot/1.0"
        ),
        incident_id=OpaqueId("incident-a"),
        audit_record=_reference(
            "audit/break-glass.json", SHA_C, "immutable-audit-record/1.0"
        ),
        session_id=OpaqueId("session-a"),
        executor_identity=OpaqueId("mutation-service"),
    )


def test_pre_side_effect_guard_rechecks_workspace_authority_and_idempotency() -> None:
    guard = MutationPreSideEffectGuard()
    plan = _plan()
    granted = guard.authorize(
        plan,
        _observation(),
        service_identity=OpaqueId("mutation-service"),
        current_context=_gate_context(plan),
        evaluated_at=EVALUATED_AT,
    )
    assert granted.plan_sha256 == plan.plan_sha256
    assert granted.manifest_sha256 == MANIFEST_A
    assert granted.gate_context_sha256

    stale_context = replace(
        _gate_context(plan), executable_plan_sha256=SHA_C
    )
    with pytest.raises(MutationRuntimeError) as stale_plan_context:
        guard.authorize(
            plan,
            _observation(),
            service_identity=OpaqueId("mutation-service"),
            current_context=stale_context,
            evaluated_at=EVALUATED_AT,
        )
    assert stale_plan_context.value.reason_code == (
        "mutation.runtime.plan_context_mismatch"
    )

    with pytest.raises(MutationRuntimeError) as killed:
        guard.authorize(
            plan,
            _observation(),
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(plan),
            evaluated_at=EVALUATED_AT,
            kill_switch_engaged=True,
        )
    assert killed.value.reason_code == "mutation.runtime.kill_switch"

    with pytest.raises(MutationRuntimeError) as stale:
        guard.authorize(
            plan,
            _observation(digest=SHA_C),
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(plan),
            evaluated_at=EVALUATED_AT,
        )
    assert stale.value.reason_code == "mutation.precondition.exact_before_mismatch"

    with pytest.raises(MutationRuntimeError) as conflict:
        guard.authorize(
            plan,
            _observation(),
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(plan),
            evaluated_at=EVALUATED_AT,
            idempotency_record=IdempotencyRecord(
                plan.idempotency_key,
                SHA_C,
                MutationReceiptStatus.REJECTED,
                None,
            ),
        )
    assert conflict.value.reason_code == "mutation.idempotency.conflict"

    with pytest.raises(MutationRuntimeError) as replay:
        guard.authorize(
            plan,
            _observation(),
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(plan),
            evaluated_at=EVALUATED_AT,
            idempotency_record=IdempotencyRecord(
                plan.idempotency_key,
                plan.plan_sha256,
                MutationReceiptStatus.SUCCEEDED,
                OpaqueId("receipt-a"),
            ),
        )
    assert replay.value.reason_code == "mutation.idempotency.replay"

    elevated = _plan(risk=MutationRiskTier.R2)
    with pytest.raises(MutationRuntimeError) as missing:
        guard.authorize(
            elevated,
            _observation(),
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(elevated),
            evaluated_at=EVALUATED_AT,
        )
    assert missing.value.reason_code == "mutation.authority.verifier_missing"
    authority = guard.authorize(
        elevated,
        _observation(),
        service_identity=OpaqueId("mutation-service"),
        current_context=_gate_context(elevated),
        evaluated_at=EVALUATED_AT,
        authority_verifier=_TrustedAuthority(),
    )
    assert authority.authority_decision is not None


def test_r4_break_glass_requires_two_current_distinct_authenticated_humans() -> None:
    plan = _plan(risk=MutationRiskTier.R4)
    evidence = _break_glass(plan)
    guard = MutationPreSideEffectGuard()
    authenticator = _TrustedHumans({"human-1", "human-2"})
    authorized = guard.authorize(
        plan,
        _observation(),
        service_identity=OpaqueId("mutation-service"),
        current_context=_gate_context(plan),
        evaluated_at=EVALUATED_AT,
        break_glass=evidence,
        break_glass_policy=BreakGlassPolicy(maximum_validity_seconds=3600),
        human_authenticator=authenticator,
    )
    assert authorized.break_glass_authorization_id == evidence.authorization_id

    same_person = replace(
        evidence,
        approvals=(
            evidence.approvals[0],
            replace(evidence.approvals[1], principal_id=OpaqueId("human-1")),
        ),
    )
    with pytest.raises(MutationRuntimeError) as duplicate:
        guard.authorize(
            plan,
            _observation(),
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(plan),
            evaluated_at=EVALUATED_AT,
            break_glass=same_person,
            break_glass_policy=BreakGlassPolicy(maximum_validity_seconds=3600),
            human_authenticator=authenticator,
        )
    assert duplicate.value.reason_code == "mutation.break_glass.approver_distinctness"

    with pytest.raises(MutationRuntimeError) as expired:
        guard.authorize(
            plan,
            _observation(),
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(plan),
            evaluated_at=datetime(2026, 8, 7, 1, 30, tzinfo=timezone.utc),
            break_glass=evidence,
            break_glass_policy=BreakGlassPolicy(maximum_validity_seconds=3600),
            human_authenticator=authenticator,
        )
    assert expired.value.reason_code == "mutation.break_glass.expired_or_future"

    untrusted = _TrustedHumans({"human-1"})
    with pytest.raises(MutationRuntimeError) as not_authenticated:
        guard.authorize(
            plan,
            _observation(),
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(plan),
            evaluated_at=EVALUATED_AT,
            break_glass=evidence,
            break_glass_policy=BreakGlassPolicy(maximum_validity_seconds=3600),
            human_authenticator=untrusted,
        )
    assert not_authenticated.value.reason_code == (
        "mutation.break_glass.human_authentication"
    )
