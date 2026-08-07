"""Managed mutation contracts, pure planning, trust, and runtime guards."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import unicodedata

import pytest

from video_factory.approvals import GateContext
from video_factory.artifacts import validate_artifact_mapping
from video_factory.config import canonical_json_bytes, canonical_sha256
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
    BreakGlassEvidenceVerification,
    ChangeRequest,
    ContentObject,
    ContentObjectObservation,
    ExpectedBefore,
    IdempotencyReservation,
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
    WorkspaceRevisionOrigin,
    WorkspaceRevisionError,
    WorkspaceTrustState,
    break_glass_authorization_to_mapping,
    break_glass_request_sha256,
    change_request_to_mapping,
    derive_workspace_revision,
    detect_workspace_drift,
    drift_report_to_mapping,
    mutation_artifact_from_bytes,
    mutation_artifact_from_mapping,
    mutation_artifact_to_mapping,
    mutation_content_observation_sha256,
    mutation_plan_to_mapping,
    mutation_execution_authorization_sha256,
    mutation_execution_authorization_id,
    mutation_receipt_id,
    mutation_receipt_to_mapping,
    path_collision_key,
    plan_mutation,
    require_collision_free,
    require_managed_path,
    require_no_link_or_reparse_ancestor,
    validate_mutation_execution_authorization,
    validate_mutation_execution_authorization_for_plan,
    validate_mutation_plan,
    workspace_observation_sha256,
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
        origin=WorkspaceRevisionOrigin.RECONCILED_BASELINE,
        parent_revision_id=None,
        reconciliation_evidence=_reference(
            "reconciliation/baseline.json",
            SHA_C,
            "workspace-reconciliation/1.0",
        ),
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
                    path=RelativeArtifactPath("archive"),
                    node_kind=PathNodeKind.DIRECTORY,
                    exact_sha256=None,
                    byte_length=None,
                ),
                PathObservation(
                    path=RelativeArtifactPath("artifacts"),
                    node_kind=PathNodeKind.DIRECTORY,
                    exact_sha256=None,
                    byte_length=None,
                ),
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


def _plan(
    *,
    risk: MutationRiskTier = MutationRiskTier.R1,
    kind: MutationKind = MutationKind.REPLACE,
):
    request = (
        _request(
            risk=risk,
            kind=MutationKind.CREATE,
            path="artifacts/new.txt",
            expected=None,
            key=f"create-{risk.value}",
        )
        if kind is MutationKind.CREATE
        else _request(risk=risk, kind=kind)
    )
    return plan_mutation(
        request,
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


def _after_observation(plan) -> WorkspaceObservation:
    latest: dict[str, tuple[HashDigest, int]] = {}
    for entry in _revision().entries:
        if entry.tombstone:
            latest.pop(str(entry.path), None)
        else:
            assert entry.content_sha256 is not None and entry.byte_length is not None
            latest[str(entry.path)] = (entry.content_sha256, entry.byte_length)
    for operation in plan.operations:
        path = str(operation.path)
        if operation.kind in {MutationKind.CREATE, MutationKind.REPLACE}:
            assert operation.new_content is not None
            latest[path] = (
                operation.new_content.exact_sha256,
                operation.new_content.byte_length,
            )
        elif operation.kind is MutationKind.DELETE:
            latest.pop(path, None)
        else:
            assert operation.destination_path is not None
            source = latest.pop(path)
            latest[str(operation.destination_path)] = source
    directories = {
        "/".join(path.split("/")[:index])
        for path in latest
        for index in range(1, len(path.split("/")))
    }
    return WorkspaceObservation(
        workspace_id=plan.workspace_id,
        revision_id=plan.before_revision_id,
        manifest_sha256=MANIFEST_B,
        trust_state=WorkspaceTrustState.TRUSTED,
        complete=True,
        entries=tuple(
            PathObservation(
                RelativeArtifactPath(path),
                PathNodeKind.DIRECTORY,
                None,
                None,
            )
            for path in sorted(directories)
        )
        + tuple(
            PathObservation(
                RelativeArtifactPath(path),
                PathNodeKind.FILE,
                digest,
                length,
            )
            for path, (digest, length) in sorted(latest.items())
        ),
    )


def _receipt(plan, *, authorization=None, after_observation=None) -> MutationReceipt:
    authorization = authorization or _execution_authorization(plan)
    after_observation = after_observation or _after_observation(plan)
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
    provisional = MutationReceipt(
        receipt_id=OpaqueId("receipt-a"),
        plan_id=plan.plan_id,
        plan_sha256=plan.plan_sha256,
        workspace_id=plan.workspace_id,
        idempotency_key=plan.idempotency_key,
        executor_identity=OpaqueId("mutation-service"),
        execution_authorization_id=authorization.authorization_id,
        execution_authorization_sha256=mutation_execution_authorization_sha256(
            authorization
        ),
        before_workspace_observation_sha256=(
            authorization.workspace_observation_sha256
        ),
        after_workspace_observation_sha256=workspace_observation_sha256(
            after_observation
        ),
        idempotency_reservation=authorization.idempotency_reservation,
        journal_record=_reference(
            "journal/mutation-a.json",
            SHA_C,
            "mutation-journal-record/1.0",
        ),
        started_at="2026-08-07T01:00:00Z",
        completed_at="2026-08-07T01:01:00Z",
        status=MutationReceiptStatus.SUCCEEDED,
        before_manifest_sha256=plan.before_manifest_sha256,
        after_manifest_sha256=MANIFEST_B,
        operation_results=tuple(results),
        rollback_or_reconciliation_required=False,
        rollback_or_reconciliation_plan=None,
    )
    return replace(provisional, receipt_id=mutation_receipt_id(provisional))


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
        "CONIN$",
        "conout$.txt",
        "a/CLOCK$",
        "a/CONIN$/child.txt",
        "a/COM¹.txt",
        "a/LPT².txt",
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
    regular_file = PathObservation(
        RelativeArtifactPath("assets"), PathNodeKind.FILE, SHA_A, 10
    )
    with pytest.raises(MutationPathError) as non_directory:
        require_no_link_or_reparse_ancestor(
            "assets/file.txt", {"assets": regular_file}
        )
    assert non_directory.value.reason_code == (
        "mutation.path.non_directory_ancestor"
    )
    with pytest.raises(MutationPathError) as missing_ancestor:
        require_no_link_or_reparse_ancestor("assets/file.txt", {})
    assert missing_ancestor.value.reason_code == "mutation.path.ancestor_unobserved"


def test_planner_rejects_casefolded_ancestor_overlap_and_file_ancestors() -> None:
    first = _request(
        kind=MutationKind.CREATE,
        path="Assets",
        expected=None,
        key="ancestor-first",
    ).operations[0]
    second = _request(
        kind=MutationKind.CREATE,
        path="assets/file.txt",
        expected=None,
        key="ancestor-second",
    ).operations[0]
    request = replace(
        _request(
            kind=MutationKind.CREATE,
            path="new.txt",
            expected=None,
            key="ancestor-pair",
        ),
        operations=(first, second),
    )
    with pytest.raises(MutationPlanError) as overlap:
        plan_mutation(
            request,
            _revision(entries=()),
            _observation(entries=()),
            policy_bundle_sha256=POLICY_SHA,
        )
    assert overlap.value.reason_code == "mutation.path.overlap"

    child = _request(
        kind=MutationKind.CREATE,
        path="artifacts/a.txt/child.txt",
        expected=None,
        key="file-ancestor",
    )
    with pytest.raises(MutationPlanError) as file_ancestor:
        plan_mutation(
            child,
            _revision(),
            _observation(),
            policy_bundle_sha256=POLICY_SHA,
        )
    assert file_ancestor.value.reason_code == (
        "mutation.path.non_directory_ancestor"
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
    with pytest.raises(MutationPlanError) as missing_baseline_evidence:
        workspace_revision_to_mapping(
            replace(_revision(), reconciliation_evidence=None)
        )
    assert missing_baseline_evidence.value.reason_code == (
        "mutation.revision.baseline_provenance"
    )


def test_workspace_trust_requires_exact_revision_workspace_and_current_bytes() -> None:
    revision = _revision()
    revision_sha256 = str(canonical_sha256(workspace_revision_to_mapping(revision)))
    arguments = {
        "expected_workspace_id": WORKSPACE_ID,
        "expected_revision_id": REVISION_ID,
        "expected_manifest_sha256": MANIFEST_A,
        "expected_revision": revision,
        "expected_revision_sha256": revision_sha256,
    }
    assert workspace_trust_blockers(_observation(), **arguments) == ()

    foreign = replace(_observation(), workspace_id=OpaqueId("workspace-foreign"))
    assert "mutation.workspace.workspace_mismatch" in workspace_trust_blockers(
        foreign, **arguments
    )
    changed = _observation(digest=SHA_C)
    assert "mutation.workspace.content_drift" in workspace_trust_blockers(
        changed, **arguments
    )
    added = replace(
        _observation(),
        entries=(
            *_observation().entries,
            PathObservation(
                RelativeArtifactPath("artifacts/unplanned.txt"),
                PathNodeKind.FILE,
                SHA_B,
                12,
            ),
        ),
    )
    assert "mutation.workspace.content_drift" in workspace_trust_blockers(
        added, **arguments
    )
    missing_parent = replace(
        _observation(),
        entries=tuple(
            item
            for item in _observation().entries
            if item.node_kind is PathNodeKind.FILE
        ),
    )
    assert "mutation.path.ancestor_unobserved" in workspace_trust_blockers(
        missing_parent, **arguments
    )


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


@pytest.mark.parametrize(
    "kind", (MutationKind.REPLACE, MutationKind.DELETE, MutationKind.MOVE)
)
@pytest.mark.parametrize(
    "declared",
    (MutationRiskTier.R1, MutationRiskTier.R2, MutationRiskTier.R3),
)
def test_risk_policy_cannot_be_downgraded_by_requester(
    kind: MutationKind,
    declared: MutationRiskTier,
) -> None:
    request = _request(
        kind=kind,
        destination="archive/a.txt" if kind is MutationKind.MOVE else None,
        risk=declared,
        key=f"risk-{kind.value}-{declared.value}",
    )
    plan = plan_mutation(
        request,
        _revision(),
        _observation(),
        policy_bundle_sha256=POLICY_SHA,
    )
    assert plan.risk_tier is MutationRiskTier.R4
    with pytest.raises(MutationRuntimeError) as no_break_glass:
        MutationPreSideEffectGuard().authorize(
            plan,
            _observation(),
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(plan),
            evaluated_at=EVALUATED_AT,
            authority_verifier=_TrustedAuthority(),
            **_runtime_dependencies(),
        )
    assert no_break_glass.value.reason_code == "mutation.break_glass.missing"


@pytest.mark.parametrize(
    "path",
    (
        "artifacts/new.txt",
        "generated/output.json",
        "drafts/brief.json",
        "docs/governance/rules.yaml",
        "policies/rules.json",
        ".github/workflows/release.yml",
        "public/releases/video.json",
    ),
)
@pytest.mark.parametrize(
    "declared",
    (MutationRiskTier.R1, MutationRiskTier.R2, MutationRiskTier.R3),
)
def test_w02_risk_policy_defaults_every_create_to_r4(
    path: str,
    declared: MutationRiskTier,
) -> None:
    request = _request(
        kind=MutationKind.CREATE,
        path=path,
        expected=None,
        risk=declared,
        key=f"conservative-{declared.value}-{path.replace('/', '-')}",
    )
    observed_entries = list(_observation().entries)
    observed_paths = {str(item.path) for item in observed_entries}
    parts = path.split("/")
    for index in range(1, len(parts)):
        ancestor = "/".join(parts[:index])
        if ancestor not in observed_paths:
            observed_entries.append(
                PathObservation(
                    RelativeArtifactPath(ancestor),
                    PathNodeKind.DIRECTORY,
                    None,
                    None,
                )
            )
            observed_paths.add(ancestor)
    observation = _observation(entries=tuple(observed_entries))
    plan = plan_mutation(
        request,
        _revision(),
        observation,
        policy_bundle_sha256=POLICY_SHA,
    )
    assert plan.risk_tier is MutationRiskTier.R4
    with pytest.raises(MutationRuntimeError) as missing_break_glass:
        MutationPreSideEffectGuard().authorize(
            plan,
            observation,
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(plan),
            evaluated_at=EVALUATED_AT,
            authority_verifier=_TrustedAuthority(),
            **_runtime_dependencies(),
        )
    assert missing_break_glass.value.reason_code == "mutation.break_glass.missing"


def test_w02_plan_contract_rejects_a_rebound_lower_risk_tier() -> None:
    plan = _plan(kind=MutationKind.CREATE)
    with pytest.raises(MutationPlanError) as lowered:
        validate_mutation_plan(replace(plan, risk_tier=MutationRiskTier.R1))
    assert lowered.value.reason_code == "mutation.plan.risk_downgrade"

    mapping = mutation_plan_to_mapping(plan)
    mapping["risk_tier"] = "R1"
    assert not validate_artifact_mapping(mapping).ok


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
    revision = derive_workspace_revision(
        _revision(),
        plan,
        receipt,
        _execution_authorization(plan),
        _after_observation(plan),
    )
    assert revision.parent_revision_id == REVISION_ID
    assert revision.origin is WorkspaceRevisionOrigin.MANAGED_MUTATION
    assert revision.reconciliation_evidence is None
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
        status=MutationReceiptStatus.FAILED,
        operation_results=(
            replace(
                receipt.operation_results[0],
                outcome=OperationOutcome.FAILED,
                after_sha256=None,
                after_byte_length=None,
                reason_code="mutation.operation.failed",
            ),
        ),
        rollback_or_reconciliation_required=True,
        rollback_or_reconciliation_plan=_reference(
            "recovery/mutation-a.json",
            SHA_B,
            "mutation-recovery-plan/1.0",
        ),
    )
    partial = replace(partial, receipt_id=mutation_receipt_id(partial))
    with pytest.raises(Exception, match="fully successful"):
        derive_workspace_revision(
            _revision(),
            plan,
            partial,
            _execution_authorization(plan),
            _after_observation(plan),
        )

    with pytest.raises(WorkspaceRevisionError) as reused_receipt:
        derive_workspace_revision(
            _revision(),
            plan,
            replace(receipt, after_manifest_sha256=SHA_C),
            _execution_authorization(plan),
            _after_observation(plan),
        )
    assert reused_receipt.value.reason_code == "mutation.receipt.identity"

    tampered_parent = replace(
        _revision(),
        entries=(replace(_revision().entries[0], byte_length=11),),
    )
    with pytest.raises(WorkspaceRevisionError) as parent_digest:
        derive_workspace_revision(
            tampered_parent,
            plan,
            receipt,
            _execution_authorization(plan),
            _after_observation(plan),
        )
    assert parent_digest.value.reason_code == (
        "mutation.revision.parent_digest_mismatch"
    )


def test_revision_promotion_rejects_forged_authorization_and_after_bytes() -> None:
    plan = _plan()
    authorization = _execution_authorization(plan)
    after_observation = _after_observation(plan)
    receipt = _receipt(
        plan,
        authorization=authorization,
        after_observation=after_observation,
    )

    other_service = replace(
        authorization,
        service_identity=OpaqueId("other-service"),
    )
    other_service = replace(
        other_service,
        authorization_id=mutation_execution_authorization_id(other_service),
    )
    with pytest.raises(WorkspaceRevisionError) as wrong_authorization:
        derive_workspace_revision(
            _revision(),
            plan,
            receipt,
            other_service,
            after_observation,
        )
    assert wrong_authorization.value.reason_code == (
        "mutation.revision.receipt_authorization_binding"
    )

    wrong_reservation = replace(
        receipt,
        idempotency_reservation=_reference(
            "idempotency/foreign.json",
            SHA_B,
            "idempotency-reservation/1.0",
        ),
    )
    wrong_reservation = replace(
        wrong_reservation,
        receipt_id=mutation_receipt_id(wrong_reservation),
    )
    with pytest.raises(WorkspaceRevisionError) as reservation_binding:
        derive_workspace_revision(
            _revision(),
            plan,
            wrong_reservation,
            authorization,
            after_observation,
        )
    assert reservation_binding.value.reason_code == (
        "mutation.revision.receipt_authorization_binding"
    )

    changed_entries = tuple(
        replace(item, exact_sha256=SHA_C)
        if item.node_kind is PathNodeKind.FILE
        else item
        for item in after_observation.entries
    )
    changed_observation = replace(after_observation, entries=changed_entries)
    rebound_receipt = replace(
        receipt,
        after_workspace_observation_sha256=workspace_observation_sha256(
            changed_observation
        ),
    )
    rebound_receipt = replace(
        rebound_receipt,
        receipt_id=mutation_receipt_id(rebound_receipt),
    )
    with pytest.raises(WorkspaceRevisionError) as changed_bytes:
        derive_workspace_revision(
            _revision(),
            plan,
            rebound_receipt,
            authorization,
            changed_observation,
        )
    assert changed_bytes.value.reason_code == (
        "mutation.revision.after_content_mismatch"
    )


def test_plan_aware_authorization_rejects_rebound_missing_evidence() -> None:
    plan = _plan()
    authorization = _execution_authorization(plan)
    stripped = replace(
        authorization,
        content_observation_sha256=mutation_content_observation_sha256(()),
        content_observations=(),
        content_verifications=(),
        break_glass_authorization_id=None,
        break_glass_authorization_sha256=None,
        break_glass_request_sha256=None,
        human_approval_verifications=(),
        break_glass_evidence_verifications=(),
    )
    stripped = replace(
        stripped,
        authorization_id=mutation_execution_authorization_id(stripped),
    )
    assert validate_mutation_execution_authorization(stripped) == stripped
    with pytest.raises(MutationPlanError) as missing_content:
        validate_mutation_execution_authorization_for_plan(stripped, plan)
    assert missing_content.value.reason_code == (
        "mutation.authorization.content_binding"
    )

    break_glass_stripped = replace(
        authorization,
        break_glass_authorization_id=None,
        break_glass_authorization_sha256=None,
        break_glass_request_sha256=None,
        human_approval_verifications=(),
        break_glass_evidence_verifications=(),
    )
    break_glass_stripped = replace(
        break_glass_stripped,
        authorization_id=mutation_execution_authorization_id(
            break_glass_stripped
        ),
    )
    with pytest.raises(MutationPlanError) as missing_r4:
        validate_mutation_execution_authorization_for_plan(
            break_glass_stripped,
            plan,
        )
    assert missing_r4.value.reason_code == (
        "mutation.authorization.r4_evidence_missing"
    )

    rebound_receipt = _receipt(
        plan,
        authorization=stripped,
        after_observation=_after_observation(plan),
    )
    with pytest.raises(WorkspaceRevisionError) as promotion:
        derive_workspace_revision(
            _revision(),
            plan,
            rebound_receipt,
            stripped,
            _after_observation(plan),
        )
    assert promotion.value.reason_code == (
        "mutation.authorization.content_binding"
    )


@pytest.mark.parametrize(
    ("status", "recovery_required"),
    [
        (MutationReceiptStatus.UNCERTAIN, True),
        (MutationReceiptStatus.RECONCILED, False),
    ],
)
def test_receipt_uncertain_and_reconciled_states_are_explicit(
    status: MutationReceiptStatus,
    recovery_required: bool,
) -> None:
    plan = _plan()
    receipt = replace(
        _receipt(plan),
        status=status,
        rollback_or_reconciliation_required=recovery_required,
        rollback_or_reconciliation_plan=_reference(
            "recovery/mutation-a.json",
            SHA_B,
            "mutation-reconciliation/1.0",
        ),
    )
    receipt = replace(receipt, receipt_id=mutation_receipt_id(receipt))
    mapping = mutation_receipt_to_mapping(receipt)
    assert validate_artifact_mapping(mapping).ok
    assert mutation_artifact_from_mapping(mapping) == receipt

    with pytest.raises(WorkspaceRevisionError) as not_successful:
        derive_workspace_revision(
            _revision(),
            plan,
            receipt,
            _execution_authorization(plan),
            _after_observation(plan),
        )
    assert not_successful.value.reason_code == (
        "mutation.revision.incomplete_receipt"
    )


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
                path=RelativeArtifactPath("structural"),
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


def test_drift_validates_revision_and_compares_exact_byte_length() -> None:
    wrong_length = replace(
        _observation(),
        entries=tuple(
            replace(item, byte_length=11)
            if item.node_kind is PathNodeKind.FILE
            else item
            for item in _observation().entries
        ),
    )
    report = detect_workspace_drift(
        _revision(),
        wrong_length,
        detected_at="2026-08-07T01:05:00Z",
    )
    assert report is not None
    assert any(
        finding.reason_code == "mutation.drift.modified"
        for finding in report.findings
    )

    invalid_revision = replace(_revision(), reconciliation_evidence=None)
    with pytest.raises(WorkspaceRevisionError) as invalid_expected:
        detect_workspace_drift(
            invalid_revision,
            _observation(),
            detected_at="2026-08-07T01:05:00Z",
        )
    assert invalid_expected.value.reason_code == (
        "mutation.revision.baseline_provenance"
    )


def test_all_six_contracts_validate_and_round_trip_through_strict_bytes() -> None:
    request = _request()
    plan = _plan()
    receipt = _receipt(plan)
    revision = derive_workspace_revision(
        _revision(),
        plan,
        receipt,
        _execution_authorization(plan),
        _after_observation(plan),
    )
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
    assert inconsistent.value.reason_code == "mutation.receipt.operation_shape"

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


class _TrustedContent:
    def __init__(self, *, digest: HashDigest | None = None) -> None:
        self.digest = digest
        self.calls = 0

    def resolve_current(self, content, *, evaluated_at):
        self.calls += 1
        return ContentObjectObservation(
            object_id=content.object_id,
            exact_sha256=self.digest or content.exact_sha256,
            byte_length=content.byte_length,
            resolver_evidence=_reference(
                f"content/{content.object_id}.json",
                self.digest or content.exact_sha256,
                "content-resolution/1.0",
            ),
        )


class _TrustedIdempotency:
    def __init__(
        self,
        *,
        plan_sha256: HashDigest | None = None,
        newly_reserved: bool = True,
        existing_receipt_id: OpaqueId | None = None,
    ) -> None:
        self.plan_sha256 = plan_sha256
        self.newly_reserved = newly_reserved
        self.existing_receipt_id = existing_receipt_id
        self.calls = 0

    def reserve_current(
        self,
        plan,
        observation,
        *,
        workspace_observation_sha256,
        service_identity,
        evaluated_at,
    ):
        self.calls += 1
        return IdempotencyReservation(
            idempotency_key=plan.idempotency_key,
            plan_sha256=self.plan_sha256 or plan.plan_sha256,
            workspace_observation_sha256=workspace_observation_sha256,
            reservation_record=_reference(
                "idempotency/reservation.json",
                SHA_A,
                "idempotency-reservation/1.0",
            ),
            newly_reserved=self.newly_reserved,
            existing_receipt_id=self.existing_receipt_id,
        )


def _runtime_dependencies(
    *,
    ledger: _TrustedIdempotency | None = None,
    resolver: _TrustedContent | None = None,
) -> dict[str, object]:
    return {
        "expected_revision": _revision(),
        "content_resolver": resolver or _TrustedContent(),
        "idempotency_ledger": ledger or _TrustedIdempotency(),
    }


class _TrustedHumans:
    def __init__(self, principals: set[str]) -> None:
        self.principals = principals

    def verify_current_human_approval(
        self,
        approval,
        authorization,
        *,
        expected_request_sha256,
        current_context,
        evaluated_at,
    ):
        if str(approval.principal_id) not in self.principals:
            return None
        number = str(approval.principal_id).rsplit("-", 1)[-1]
        return _reference(
            f"verification/human-{number}.json",
            HashDigest(number * 64),
            "human-approval-verification/1.0",
        )


class _TrustedBreakGlassEvidence:
    def verify_current_evidence(
        self,
        authorization,
        plan,
        *,
        expected_request_sha256,
        current_context,
        evaluated_at,
    ):
        return BreakGlassEvidenceVerification(
            break_glass_request_sha256=expected_request_sha256,
            snapshot_verification=_reference(
                "verification/snapshot.json", SHA_A, "evidence-verification/1.0"
            ),
            incident_verification=_reference(
                "verification/incident.json", SHA_B, "evidence-verification/1.0"
            ),
            audit_verification=_reference(
                "verification/audit.json", SHA_C, "evidence-verification/1.0"
            ),
        )


def _human(
    number: int,
    binding: HashDigest = HashDigest("0" * 64),
) -> AuthenticatedHumanApproval:
    digest = HashDigest(str(number) * 64)
    return AuthenticatedHumanApproval(
        principal_id=OpaqueId(f"human-{number}"),
        break_glass_request_sha256=binding,
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
    provisional = BreakGlassAuthorization(
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
        incident_record=_reference(
            "incidents/incident-a.json", SHA_B, "incident-record/1.0"
        ),
        audit_record=_reference(
            "audit/break-glass.json", SHA_C, "immutable-audit-record/1.0"
        ),
        session_id=OpaqueId("session-a"),
        executor_identity=OpaqueId("mutation-service"),
    )
    binding = break_glass_request_sha256(provisional)
    return replace(
        provisional,
        approvals=tuple(
            replace(approval, break_glass_request_sha256=binding)
            for approval in provisional.approvals
        ),
    )


def _authorization_dependencies(
    plan,
    *,
    ledger: _TrustedIdempotency | None = None,
    resolver: _TrustedContent | None = None,
) -> dict[str, object]:
    options: dict[str, object] = {
        "authority_verifier": _TrustedAuthority(),
        **_runtime_dependencies(ledger=ledger, resolver=resolver),
    }
    if plan.risk_tier is MutationRiskTier.R4:
        options.update(
            {
                "break_glass": _break_glass(plan),
                "break_glass_policy": BreakGlassPolicy(
                    maximum_validity_seconds=3600
                ),
                "human_authenticator": _TrustedHumans(
                    {"human-1", "human-2"}
                ),
                "break_glass_evidence_verifier": (
                    _TrustedBreakGlassEvidence()
                ),
            }
        )
    return options


def _execution_authorization(plan):
    return MutationPreSideEffectGuard().authorize(
        plan,
        _observation(),
        service_identity=OpaqueId("mutation-service"),
        current_context=_gate_context(plan),
        evaluated_at=EVALUATED_AT,
        **_authorization_dependencies(plan),
    )


def test_pre_side_effect_guard_rechecks_workspace_authority_and_idempotency() -> None:
    guard = MutationPreSideEffectGuard()
    plan = _plan()
    assert plan.risk_tier is MutationRiskTier.R4
    break_glass = _break_glass(plan)
    granted = guard.authorize(
        plan,
        _observation(),
        service_identity=OpaqueId("mutation-service"),
        current_context=_gate_context(plan),
        evaluated_at=EVALUATED_AT,
        authority_verifier=_TrustedAuthority(),
        break_glass=break_glass,
        break_glass_policy=BreakGlassPolicy(maximum_validity_seconds=3600),
        human_authenticator=_TrustedHumans({"human-1", "human-2"}),
        break_glass_evidence_verifier=_TrustedBreakGlassEvidence(),
        **_runtime_dependencies(),
    )
    assert granted.plan_sha256 == plan.plan_sha256
    assert granted.manifest_sha256 == MANIFEST_A
    assert granted.gate_context_sha256
    assert granted.workspace_observation_sha256
    assert granted.content_observation_sha256
    assert len(granted.content_verifications) == 1
    assert granted.idempotency_reservation.artifact_version == (
        "idempotency-reservation/1.0"
    )

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
            **_runtime_dependencies(),
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
            **_runtime_dependencies(),
        )
    assert killed.value.reason_code == "mutation.runtime.kill_switch"

    with pytest.raises(MutationRuntimeError) as stale:
        guard.authorize(
            plan,
            _observation(digest=SHA_C),
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(plan),
            evaluated_at=EVALUATED_AT,
            **_runtime_dependencies(),
        )
    assert stale.value.reason_code == "mutation.workspace.content_drift"

    idempotent_plan = _plan(kind=MutationKind.CREATE)
    with pytest.raises(MutationRuntimeError) as conflict:
        guard.authorize(
            idempotent_plan,
            _observation(),
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(idempotent_plan),
            evaluated_at=EVALUATED_AT,
            **_authorization_dependencies(
                idempotent_plan,
                ledger=_TrustedIdempotency(plan_sha256=SHA_C)
            ),
        )
    assert conflict.value.reason_code == "mutation.idempotency.conflict"

    with pytest.raises(MutationRuntimeError) as replay:
        guard.authorize(
            idempotent_plan,
            _observation(),
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(idempotent_plan),
            evaluated_at=EVALUATED_AT,
            **_authorization_dependencies(
                idempotent_plan,
                ledger=_TrustedIdempotency(
                    newly_reserved=False,
                    existing_receipt_id=OpaqueId("receipt-a"),
                )
            ),
        )
    assert replay.value.reason_code == "mutation.idempotency.replay"

    elevated = _plan(risk=MutationRiskTier.R2, kind=MutationKind.CREATE)
    assert elevated.risk_tier is MutationRiskTier.R4
    with pytest.raises(MutationRuntimeError) as missing:
        guard.authorize(
            elevated,
            _observation(),
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(elevated),
            evaluated_at=EVALUATED_AT,
            **_runtime_dependencies(),
    )
    assert missing.value.reason_code == "mutation.authority.verifier_missing"
    with pytest.raises(MutationRuntimeError) as no_lower_tier_bypass:
        guard.authorize(
            elevated,
            _observation(),
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(elevated),
            evaluated_at=EVALUATED_AT,
            authority_verifier=_TrustedAuthority(),
            **_runtime_dependencies(),
        )
    assert no_lower_tier_bypass.value.reason_code == "mutation.break_glass.missing"

    with pytest.raises(MutationRuntimeError) as content_changed:
        guard.authorize(
            idempotent_plan,
            _observation(),
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(idempotent_plan),
            evaluated_at=EVALUATED_AT,
            authority_verifier=_TrustedAuthority(),
            **_runtime_dependencies(resolver=_TrustedContent(digest=SHA_C)),
        )
    assert content_changed.value.reason_code == "mutation.content.mismatch"


def test_guard_deduplicates_reused_content_before_idempotency_reservation() -> None:
    first = _request(
        kind=MutationKind.CREATE,
        path="artifacts/one.txt",
        expected=None,
        key="shared-content",
    )
    second_operation = replace(
        first.operations[0],
        path=RelativeArtifactPath("artifacts/two.txt"),
    )
    request = replace(
        first,
        operations=(first.operations[0], second_operation),
    )
    plan = plan_mutation(
        request,
        _revision(),
        _observation(),
        policy_bundle_sha256=POLICY_SHA,
    )
    resolver = _TrustedContent()
    ledger = _TrustedIdempotency()
    authorization = MutationPreSideEffectGuard().authorize(
        plan,
        _observation(),
        service_identity=OpaqueId("mutation-service"),
        current_context=_gate_context(plan),
        evaluated_at=EVALUATED_AT,
        **_authorization_dependencies(
            plan,
            resolver=resolver,
            ledger=ledger,
        ),
    )
    assert len(plan.operations) == 2
    assert resolver.calls == 1
    assert ledger.calls == 1
    assert len(authorization.content_observations) == 1
    assert len(authorization.content_verifications) == 1

    conflicting_operation = replace(
        second_operation,
        new_content=ContentObject(OpaqueId("object-b"), SHA_C, 13),
    )
    with pytest.raises(MutationPlanError) as conflicting_identity:
        plan_mutation(
            replace(
                request,
                operations=(first.operations[0], conflicting_operation),
            ),
            _revision(),
            _observation(),
            policy_bundle_sha256=POLICY_SHA,
        )
    assert conflicting_identity.value.reason_code == (
        "mutation.content.object_identity_conflict"
    )

    rebound_plan = replace(
        plan,
        operations=(
            plan.operations[0],
            replace(
                plan.operations[1],
                new_content=ContentObject(
                    OpaqueId("object-b"),
                    SHA_C,
                    13,
                ),
            ),
        ),
    )
    with pytest.raises(MutationPlanError) as rebound_identity:
        validate_mutation_plan(rebound_plan)
    assert rebound_identity.value.reason_code == (
        "mutation.content.object_identity_conflict"
    )


def test_plan_aware_authorization_requires_one_observation_per_content_object() -> None:
    first = _request(
        kind=MutationKind.CREATE,
        path="artifacts/one.txt",
        expected=None,
        key="two-content-objects",
    )
    second_operation = replace(
        first.operations[0],
        path=RelativeArtifactPath("artifacts/two.txt"),
        new_content=ContentObject(OpaqueId("object-c"), SHA_C, 13),
    )
    plan = plan_mutation(
        replace(
            first,
            operations=(first.operations[0], second_operation),
        ),
        _revision(),
        _observation(),
        policy_bundle_sha256=POLICY_SHA,
    )
    authorization = _execution_authorization(plan)
    assert len(authorization.content_observations) == 2
    assert len(authorization.content_verifications) == 2

    remaining_observations = (authorization.content_observations[0],)
    remaining_verifications = (
        remaining_observations[0].resolver_evidence,
    )
    partial = replace(
        authorization,
        content_observations=remaining_observations,
        content_verifications=remaining_verifications,
        content_observation_sha256=mutation_content_observation_sha256(
            remaining_observations
        ),
    )
    partial = replace(
        partial,
        authorization_id=mutation_execution_authorization_id(partial),
    )
    assert validate_mutation_execution_authorization(partial) == partial
    with pytest.raises(MutationPlanError) as incomplete:
        validate_mutation_execution_authorization_for_plan(partial, plan)
    assert incomplete.value.reason_code == "mutation.authorization.content_binding"

    partial_receipt = _receipt(
        plan,
        authorization=partial,
        after_observation=_after_observation(plan),
    )
    with pytest.raises(WorkspaceRevisionError) as promotion:
        derive_workspace_revision(
            _revision(),
            plan,
            partial_receipt,
            partial,
            _after_observation(plan),
        )
    assert promotion.value.reason_code == "mutation.authorization.content_binding"


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
        authority_verifier=_TrustedAuthority(),
        break_glass=evidence,
        break_glass_policy=BreakGlassPolicy(maximum_validity_seconds=3600),
        human_authenticator=authenticator,
        break_glass_evidence_verifier=_TrustedBreakGlassEvidence(),
        **_runtime_dependencies(),
    )
    assert authorized.break_glass_authorization_id == evidence.authorization_id
    assert authorized.break_glass_authorization_sha256 is not None
    assert authorized.break_glass_request_sha256 == break_glass_request_sha256(
        evidence
    )
    assert len(authorized.human_approval_verifications) == 2
    assert len(authorized.break_glass_evidence_verifications) == 3

    unrelated = replace(
        evidence,
        incident_id=OpaqueId("incident-other"),
        incident_record=_reference(
            "incidents/incident-other.json", SHA_C, "incident-record/1.0"
        ),
    )
    with pytest.raises(MutationRuntimeError) as reused_approvals:
        guard.authorize(
            plan,
            _observation(),
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(plan),
            evaluated_at=EVALUATED_AT,
            authority_verifier=_TrustedAuthority(),
            break_glass=unrelated,
            break_glass_policy=BreakGlassPolicy(maximum_validity_seconds=3600),
            human_authenticator=authenticator,
            break_glass_evidence_verifier=_TrustedBreakGlassEvidence(),
            **_runtime_dependencies(),
        )
    assert reused_approvals.value.reason_code == (
        "mutation.break_glass.approval_binding"
    )

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
            authority_verifier=_TrustedAuthority(),
            break_glass=same_person,
            break_glass_policy=BreakGlassPolicy(maximum_validity_seconds=3600),
            human_authenticator=authenticator,
            break_glass_evidence_verifier=_TrustedBreakGlassEvidence(),
            **_runtime_dependencies(),
        )
    assert duplicate.value.reason_code == "mutation.break_glass.approver_distinctness"

    with pytest.raises(MutationRuntimeError) as expired:
        guard.authorize(
            plan,
            _observation(),
            service_identity=OpaqueId("mutation-service"),
            current_context=_gate_context(plan),
            evaluated_at=datetime(2026, 8, 7, 1, 30, tzinfo=timezone.utc),
            authority_verifier=_TrustedAuthority(),
            break_glass=evidence,
            break_glass_policy=BreakGlassPolicy(maximum_validity_seconds=3600),
            human_authenticator=authenticator,
            break_glass_evidence_verifier=_TrustedBreakGlassEvidence(),
            **_runtime_dependencies(),
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
            authority_verifier=_TrustedAuthority(),
            break_glass=evidence,
            break_glass_policy=BreakGlassPolicy(maximum_validity_seconds=3600),
            human_authenticator=untrusted,
            break_glass_evidence_verifier=_TrustedBreakGlassEvidence(),
            **_runtime_dependencies(),
        )
    assert not_authenticated.value.reason_code == (
        "mutation.break_glass.human_authentication"
    )
