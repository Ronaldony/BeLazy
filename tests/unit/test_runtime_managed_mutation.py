from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path

import pytest

from video_factory.authority import VerificationPurpose, target_policy_bundle
from video_factory.config.canonical import canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    IdempotencyKey,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.mutation import (
    ChangeRequest,
    ContentObject,
    ExpectedBefore,
    MutationExecutionAuthorization,
    MutationKind,
    MutationOperationIntent,
    MutationReceiptStatus,
    MutationRiskTier,
    RevisionEntry,
    WorkspaceRevision,
    WorkspaceRevisionOrigin,
    WorkspaceTrustState,
    mutation_content_observation_sha256,
    mutation_execution_authorization_id,
    plan_mutation,
    workspace_observation_sha256,
    workspace_revision_to_mapping,
)
from video_factory_runtime import (
    ContentAddressedFixtureStore,
    FixtureManagedMutationExecutor,
    FixtureRuntimeBoundary,
    FixtureWorkspaceObserver,
    ManagedMutationRuntimeError,
    RuntimeAuthorityReservation,
    SQLiteExecutionJournal,
)
import video_factory_runtime.filesystem as runtime_filesystem


NOW = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
SERVICE = OpaqueId("mutation-service")
WORKSPACE = OpaqueId("workspace-runtime")
REVISION = OpaqueId("revision-runtime")


def _ref(path: str, digit: str, version: str) -> ArtifactReference:
    return ArtifactReference(
        RelativeArtifactPath(path),
        HashDigest(digit * 64),
        ArtifactVersion(version),
    )


class Clock:
    def __init__(self, offset: int = 0) -> None:
        self.count = offset

    def now(self) -> datetime:
        self.count += 1
        return NOW + timedelta(seconds=self.count)


class Identity:
    reference = _ref("identity/mutation-service.json", "a", "service-attestation/1.0")

    def attest_current(self, service_identity, *, evaluated_at):
        assert service_identity == SERVICE
        assert evaluated_at.tzinfo is not None
        return self.reference


class KillSwitch:
    def __init__(self, engaged: bool = False) -> None:
        self.value = engaged

    def engaged(self, *, evaluated_at):
        assert evaluated_at.tzinfo is not None
        return self.value


class Authority:
    def __init__(self) -> None:
        self.calls = 0
        self.verifications = 0
        self.claims = {}

    def revalidate(
        self,
        plan,
        authorization,
        *,
        workspace_observation_sha256,
        service_identity,
        evaluated_at,
        purpose,
        runtime_claim_sha256,
    ):
        reservation = self.claims.get(runtime_claim_sha256)
        if reservation is None:
            self.calls += 1
            reservation = HashDigest(f"{self.calls + 100:064x}")
            self.claims[runtime_claim_sha256] = reservation
        self.verifications += 1
        assert authorization.plan_sha256 == plan.plan_sha256
        assert service_identity == SERVICE
        assert evaluated_at.tzinfo is not None
        assert purpose in {VerificationPurpose.MUTATION, VerificationPurpose.RECONCILE}
        result = RuntimeAuthorityReservation(
            HashDigest(f"{self.verifications:064x}"),
            reservation,
        )
        return result


class Settlement:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def finalize_current(
        self,
        *,
        journal_id,
        reservation_claim_sha256s,
        reservation_sha256s,
        final_state,
        evaluated_at,
    ):
        self.calls.append(
            (
                journal_id,
                reservation_claim_sha256s,
                reservation_sha256s,
                final_state,
                evaluated_at,
            )
        )
        digest = canonical_sha256(
            {
                "journal_id": str(journal_id),
                "claims": [str(item) for item in reservation_claim_sha256s],
                "reservations": [str(item) for item in reservation_sha256s],
                "state": final_state.value,
            }
        )
        return ArtifactReference(
            RelativeArtifactPath(f"settlement/{journal_id}.json"),
            digest,
            ArtifactVersion("authority-settlement/1.0"),
        )


def _setup(
    tmp_path: Path,
    *,
    operation_count: int = 1,
    kinds: tuple[MutationKind, ...] | None = None,
):
    kinds = kinds or tuple(MutationKind.REPLACE for _ in range(operation_count))
    assert len(kinds) == operation_count
    boundary = FixtureRuntimeBoundary.initialize(
        tmp_path / "runtime",
        runtime_id="runtime-mutation-test",
        fixture_only=True,
    )
    observer = FixtureWorkspaceObserver(boundary, workspace_id=WORKSPACE)
    (observer.root / "artifacts").mkdir()
    initial_payloads = (b"old-a", b"old-b")[:operation_count]
    for index, (kind, payload) in enumerate(zip(kinds, initial_payloads, strict=True)):
        if kind is not MutationKind.CREATE:
            (observer.root / "artifacts" / f"{index}.txt").write_bytes(payload)
    if MutationKind.MOVE in kinds:
        (observer.root / "archive").mkdir()
    before = observer.observe(revision_id=REVISION)
    files = tuple(item for item in before.entries if item.node_kind.value == "file")
    revision = WorkspaceRevision(
        revision_id=REVISION,
        workspace_id=WORKSPACE,
        origin=WorkspaceRevisionOrigin.RECONCILED_BASELINE,
        parent_revision_id=None,
        reconciliation_evidence=_ref(
            "reconciliation/baseline.json", "b", "workspace-reconciliation/1.0"
        ),
        manifest_sha256=before.manifest_sha256,
        created_at="2026-08-09T23:59:00Z",
        plan_id=OpaqueId("baseline-plan"),
        receipt_id=OpaqueId("baseline-receipt"),
        trust_state=WorkspaceTrustState.TRUSTED,
        entries=tuple(
            RevisionEntry(
                item.path,
                1,
                item.exact_sha256,
                item.byte_length,
                False,
                OpaqueId(f"baseline-{index}"),
            )
            for index, item in enumerate(files)
        ),
    )
    store = ContentAddressedFixtureStore(boundary)
    content_by_index = {
        index: store.put(
            object_id=OpaqueId(f"content-{index}"),
            payload=f"new-{index}".encode("utf-8"),
        )
        for index, kind in enumerate(kinds)
        if kind in {MutationKind.CREATE, MutationKind.REPLACE}
    }
    content_observations = tuple(content_by_index[index] for index in content_by_index)
    file_by_path = {str(item.path): item for item in files}
    operation_items = []
    for index, kind in enumerate(kinds):
        path = (
            RelativeArtifactPath(f"artifacts/new-{index}.txt")
            if kind is MutationKind.CREATE
            else RelativeArtifactPath(f"artifacts/{index}.txt")
        )
        existing = file_by_path.get(str(path))
        content = content_by_index.get(index)
        operation_items.append(
            MutationOperationIntent(
                kind=kind,
                path=path,
                expected_before=ExpectedBefore(
                    existing is not None,
                    existing.exact_sha256 if existing is not None else None,
                ),
                new_content=(
                    ContentObject(
                        content.object_id,
                        content.exact_sha256,
                        content.byte_length,
                    )
                    if content is not None
                    else None
                ),
                destination_path=(
                    RelativeArtifactPath(f"archive/{index}.txt")
                    if kind is MutationKind.MOVE
                    else None
                ),
            )
        )
    operations = tuple(operation_items)
    request = ChangeRequest(
        request_id=OpaqueId("request-runtime-mutation"),
        workspace_id=WORKSPACE,
        requester_id=OpaqueId("requester-runtime"),
        requested_at="2026-08-10T00:00:00Z",
        before_revision_id=REVISION,
        before_manifest_sha256=before.manifest_sha256,
        idempotency_key=IdempotencyKey("runtime-mutation-key"),
        risk_tier=MutationRiskTier.R1,
        operations=operations,
    )
    plan = plan_mutation(
        request,
        revision,
        before,
        policy_bundle_sha256=target_policy_bundle().bundle_sha256,
    )
    authority_ref = _ref(
        "authority/decision.json", "c", "authority-decision/1.0"
    )
    human_refs = (
        _ref("verification/human-1.json", "d", "principal-verification/1.0"),
        _ref("verification/human-2.json", "e", "principal-verification/1.0"),
    )
    evidence_refs = (
        _ref("verification/snapshot.json", "6", "evidence-verification/1.0"),
        _ref("verification/incident.json", "7", "evidence-verification/1.0"),
        _ref("verification/audit.json", "8", "evidence-verification/1.0"),
    )
    revision_sha = canonical_sha256(workspace_revision_to_mapping(revision))
    provisional = MutationExecutionAuthorization(
        authorization_id=OpaqueId("pending"),
        plan_id=plan.plan_id,
        plan_sha256=plan.plan_sha256,
        workspace_id=WORKSPACE,
        revision_id=REVISION,
        workspace_revision_sha256=revision_sha,
        manifest_sha256=before.manifest_sha256,
        workspace_observation_sha256=workspace_observation_sha256(before),
        idempotency_key=plan.idempotency_key,
        idempotency_reservation=_ref(
            "idempotency/reservation.json", "9", "idempotency-reservation/1.0"
        ),
        content_observation_sha256=mutation_content_observation_sha256(
            content_observations
        ),
        content_observations=content_observations,
        content_verifications=tuple(
            sorted(
                (item.resolver_evidence for item in content_observations),
                key=lambda item: (
                    str(item.path),
                    str(item.sha256),
                    str(item.artifact_version),
                ),
            )
        ),
        service_identity=SERVICE,
        evaluated_at="2026-08-10T00:00:00Z",
        gate_context_sha256=HashDigest("4" * 64),
        authority_decision=authority_ref,
        break_glass_authorization_id=OpaqueId("break-glass-runtime"),
        break_glass_authorization_sha256=HashDigest("5" * 64),
        break_glass_request_sha256=HashDigest("f" * 64),
        human_approval_verifications=human_refs,
        break_glass_evidence_verifications=evidence_refs,
    )
    authorization = replace(
        provisional,
        authorization_id=mutation_execution_authorization_id(provisional),
    )
    return boundary, observer, store, plan, authorization


def _executor(
    tmp_path: Path,
    *,
    operation_count: int = 1,
    kinds: tuple[MutationKind, ...] | None = None,
    fault_hook=None,
):
    boundary, observer, store, plan, authorization = _setup(
        tmp_path, operation_count=operation_count, kinds=kinds
    )
    journal = SQLiteExecutionJournal(boundary)
    authority = Authority()
    settlement = Settlement()
    executor = FixtureManagedMutationExecutor(
        boundary,
        journal=journal,
        observer=observer,
        content_store=store,
        authority=authority,
        identity_attestor=Identity(),
        kill_switch=KillSwitch(),
        settlement=settlement,
        clock=Clock(),
        service_identity=SERVICE,
        fault_hook=fault_hook,
    )
    return executor, journal, observer, authority, settlement, plan, authorization


def test_managed_mutation_success_is_durable_and_exact_replay_is_read_only(
    tmp_path: Path,
) -> None:
    executor, journal, observer, authority, settlement, plan, authorization = _executor(
        tmp_path
    )

    first = executor.apply(plan, authorization)
    calls_after_first = authority.calls
    second = executor.apply(plan, authorization)

    assert first == second
    assert first.status is MutationReceiptStatus.SUCCEEDED
    assert (observer.root / "artifacts" / "0.txt").read_bytes() == b"new-0"
    assert authority.calls == calls_after_first == 1
    assert len(settlement.calls) == 1
    snapshots = journal.unresolved()
    assert snapshots == ()


def test_terminal_mutation_replay_does_not_mint_fresh_effect_authority(
    tmp_path: Path,
) -> None:
    executor, journal, observer, authority, settlement, plan, authorization = _executor(
        tmp_path
    )
    first = executor.apply(plan, authorization)

    class NoCurrentIdentity:
        def attest_current(self, service_identity, *, evaluated_at):
            raise AssertionError("terminal replay must not re-attest identity")

    class EngagedKillSwitch:
        def engaged(self, *, evaluated_at):
            raise AssertionError("terminal replay must not consult the kill switch")

    class StaleWorkspace:
        def observe(self, *, revision_id):
            raise AssertionError("terminal replay must not re-read workspace bytes")

    executor._identity = NoCurrentIdentity()
    executor._kill_switch = EngagedKillSwitch()
    executor._observer = StaleWorkspace()

    replay = executor.apply(plan, authorization)

    assert replay == first
    assert authority.calls == 1
    assert len(settlement.calls) == 1
    assert journal.unresolved() == ()


@pytest.mark.parametrize(
    ("kind", "source_exists", "destination", "expected_bytes"),
    [
        (MutationKind.CREATE, False, "artifacts/new-0.txt", b"new-0"),
        (MutationKind.REPLACE, True, "artifacts/0.txt", b"new-0"),
        (MutationKind.DELETE, False, None, None),
        (MutationKind.MOVE, False, "archive/0.txt", b"old-a"),
    ],
)
def test_each_managed_mutation_kind_preserves_exact_filesystem_semantics(
    tmp_path: Path,
    kind: MutationKind,
    source_exists: bool,
    destination: str | None,
    expected_bytes: bytes | None,
) -> None:
    executor, journal, observer, authority, settlement, plan, authorization = _executor(
        tmp_path, kinds=(kind,)
    )

    receipt = executor.apply(plan, authorization)

    assert receipt.status is MutationReceiptStatus.SUCCEEDED
    source = observer.root / "artifacts" / "0.txt"
    assert source.exists() is source_exists
    if destination is not None:
        assert observer.managed_path(destination).read_bytes() == expected_bytes
    assert authority.calls == 1
    assert len(settlement.calls) == 1


def test_pre_effect_workspace_rebound_is_rejected_without_authority_call(
    tmp_path: Path,
) -> None:
    executor, journal, observer, authority, settlement, plan, authorization = _executor(
        tmp_path
    )
    (observer.root / "artifacts" / "0.txt").write_bytes(b"out-of-band")

    receipt = executor.apply(plan, authorization)

    assert receipt.status is MutationReceiptStatus.FAILED
    assert receipt.rollback_or_reconciliation_required is True
    assert authority.calls == 0
    assert settlement.calls == []
    assert (observer.root / "artifacts" / "0.txt").read_bytes() == b"out-of-band"


class TamperAfterMarker:
    def after_dispatching(self, plan, operation_id, target):
        target.write_bytes(b"changed-in-race-window")


class ReboundAfterMarker:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.executor = None
        self.observer = None

    def after_dispatching(self, plan, operation_id, target):
        assert self.executor is not None
        assert self.observer is not None
        if self.mode == "kill":
            self.executor._kill_switch.value = True
        elif self.mode == "identity":
            self.executor._identity = object()
        elif self.mode == "authority":
            self.executor._authority = type(
                "DeniedAuthority",
                (),
                {"revalidate": staticmethod(lambda *args, **kwargs: None)},
            )()
        else:
            (self.observer.root / "unplanned.txt").write_bytes(b"changed")


@pytest.mark.parametrize("mode", ("kill", "identity", "authority", "manifest"))
def test_every_post_marker_rebound_blocks_the_atomic_effect(
    tmp_path: Path, mode: str
) -> None:
    hook = ReboundAfterMarker(mode)
    executor, journal, observer, authority, settlement, plan, authorization = _executor(
        tmp_path, fault_hook=hook
    )
    hook.executor = executor
    hook.observer = observer

    receipt = executor.apply(plan, authorization)

    assert receipt.status is MutationReceiptStatus.UNCERTAIN
    assert (observer.root / "artifacts" / "0.txt").read_bytes() == b"old-a"
    assert len(settlement.calls) == 1


@pytest.mark.skipif(os.name == "nt", reason="Windows uses exact mutation handles")
def test_leaf_swap_inside_atomic_stage_is_uncertain_and_preserves_approved_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, journal, observer, authority, settlement, plan, authorization = _executor(
        tmp_path
    )
    target = observer.root / "artifacts" / "0.txt"
    approved_backup = observer.root / "artifacts" / "0.approved-backup"
    original_rename = runtime_filesystem.os.rename
    swapped = False

    def swap_leaf_before_stage(source, destination, *args, **kwargs):
        nonlocal swapped
        if not swapped and source == target.name and kwargs.get("src_dir_fd") is not None:
            swapped = True
            original_rename(target, approved_backup)
            target.write_bytes(b"unapproved-race")
        return original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(runtime_filesystem.os, "rename", swap_leaf_before_stage)
    receipt = executor.apply(plan, authorization)

    assert swapped is True
    assert receipt.status is MutationReceiptStatus.UNCERTAIN
    assert approved_backup.read_bytes() == b"old-a"
    assert target.read_bytes() == b"unapproved-race"
    assert journal.load(journal.unresolved()[0].journal_id).reconcile_only is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX exact directory capabilities")
def test_posix_ancestor_swap_cannot_redirect_the_handle_bound_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, journal, observer, authority, settlement, plan, authorization = _executor(
        tmp_path
    )
    target = observer.root / "artifacts" / "0.txt"
    ancestor = target.parent
    rebound = observer.root / "artifacts-rebound"
    original_rename = runtime_filesystem.os.rename
    attempted = False

    def swap_ancestor_after_capabilities(source, destination, *args, **kwargs):
        nonlocal attempted
        if not attempted and source == target.name and kwargs.get("src_dir_fd") is not None:
            attempted = True
            original_rename(ancestor, rebound)
            ancestor.mkdir()
            target.write_bytes(b"unapproved-race")
        return original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(runtime_filesystem.os, "rename", swap_ancestor_after_capabilities)
    receipt = executor.apply(plan, authorization)

    assert attempted is True
    assert receipt.status is MutationReceiptStatus.UNCERTAIN
    assert target.read_bytes() == b"unapproved-race"
    assert (rebound / "0.txt").read_bytes() == b"new-0"
    assert journal.load(journal.unresolved()[0].journal_id).reconcile_only is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX exact directory capabilities")
def test_posix_create_holds_the_complete_ancestor_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, journal, observer, authority, settlement, plan, authorization = _executor(
        tmp_path, kinds=(MutationKind.CREATE,)
    )
    target = observer.root / "artifacts" / "new-0.txt"
    ancestor = target.parent
    rebound = observer.root / "artifacts-rebound"
    original_rename = runtime_filesystem.os.rename
    original_link = runtime_filesystem.os.link
    attempted = False

    def swap_ancestor_before_create(source, destination, *args, **kwargs):
        nonlocal attempted
        if (
            not attempted
            and source.endswith(".new")
            and destination == target.name
            and kwargs.get("dst_dir_fd") is not None
        ):
            attempted = True
            original_rename(ancestor, rebound)
            ancestor.mkdir()
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(runtime_filesystem.os, "link", swap_ancestor_before_create)
    receipt = executor.apply(plan, authorization)

    assert attempted is True
    assert receipt.status is MutationReceiptStatus.UNCERTAIN
    assert not target.exists()
    assert (rebound / "new-0.txt").read_bytes() == b"new-0"
    assert journal.load(journal.unresolved()[0].journal_id).reconcile_only is True


@pytest.mark.skipif(os.name != "nt", reason="Windows exact-handle containment")
def test_windows_ancestor_swap_cannot_redirect_the_handle_bound_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, journal, observer, authority, settlement, plan, authorization = _executor(
        tmp_path
    )
    target = observer.root / "artifacts" / "0.txt"
    ancestor = target.parent
    rebound = observer.root / "artifacts-rebound"
    original_replace = runtime_filesystem.os.replace
    original_rename = runtime_filesystem._WindowsMutationFile.rename_into
    attempted = False

    def swap_ancestor_after_handles_open(file_handle, directory, name):
        nonlocal attempted
        if not attempted and file_handle.path == target:
            attempted = True
            original_replace(ancestor, rebound)
            ancestor.mkdir()
            (ancestor / "0.txt").write_bytes(b"unapproved-race")
        return original_rename(file_handle, directory, name)

    monkeypatch.setattr(
        runtime_filesystem._WindowsMutationFile,
        "rename_into",
        swap_ancestor_after_handles_open,
    )
    receipt = executor.apply(plan, authorization)

    assert attempted is True
    assert receipt.status is MutationReceiptStatus.UNCERTAIN
    assert target.read_bytes() == b"old-a"
    assert not rebound.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows exact-handle containment")
def test_windows_create_holds_the_complete_ancestor_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, journal, observer, authority, settlement, plan, authorization = _executor(
        tmp_path, kinds=(MutationKind.CREATE,)
    )
    target = observer.root / "artifacts" / "new-0.txt"
    ancestor = target.parent
    rebound = observer.root / "artifacts-rebound"
    original_replace = runtime_filesystem.os.replace
    original_link = runtime_filesystem._WindowsMutationFile.link_into
    attempted = False

    def swap_ancestor_before_create(file_handle, directory, name):
        nonlocal attempted
        if not attempted and file_handle.path.name.endswith(".new"):
            attempted = True
            original_replace(ancestor, rebound)
            ancestor.mkdir()
        return original_link(file_handle, directory, name)

    monkeypatch.setattr(
        runtime_filesystem._WindowsMutationFile,
        "link_into",
        swap_ancestor_before_create,
    )
    receipt = executor.apply(plan, authorization)

    assert attempted is True
    assert receipt.status is MutationReceiptStatus.UNCERTAIN
    assert not target.exists()
    assert not (rebound / "new-0.txt").exists()


def test_toctou_change_after_dispatch_marker_is_uncertain_and_not_retried(
    tmp_path: Path,
) -> None:
    executor, journal, observer, authority, settlement, plan, authorization = _executor(
        tmp_path, fault_hook=TamperAfterMarker()
    )

    first = executor.apply(plan, authorization)
    calls = authority.calls
    replay = executor.apply(plan, authorization)

    assert first == replay
    assert first.status is MutationReceiptStatus.UNCERTAIN
    assert first.rollback_or_reconciliation_required is True
    assert authority.calls == calls == 1
    assert len(settlement.calls) == 1


class ProcessCrashAfterMarker:
    def after_dispatching(self, plan, operation_id, target):
        raise KeyboardInterrupt("simulated process death")


def test_process_restart_from_dispatching_is_reconcile_only(tmp_path: Path) -> None:
    executor, journal, observer, authority, settlement, plan, authorization = _executor(
        tmp_path, fault_hook=ProcessCrashAfterMarker()
    )
    with pytest.raises(KeyboardInterrupt):
        executor.apply(plan, authorization)
    assert len(journal.unresolved()) == 1
    assert len(journal.unsettled_reservation_claims()) == 1

    restarted = FixtureManagedMutationExecutor(
        executor._boundary,
        journal=journal,
        observer=observer,
        content_store=executor._content,
        authority=authority,
        identity_attestor=Identity(),
        kill_switch=KillSwitch(),
        settlement=settlement,
        clock=Clock(100),
        service_identity=SERVICE,
    )
    with pytest.raises(ManagedMutationRuntimeError) as caught:
        restarted.apply(plan, authorization)
    assert caught.value.reason_code == "runtime.mutation.reconcile_required"
    assert (observer.root / "artifacts" / "0.txt").read_bytes() == b"old-a"

    reconciled = restarted.reconcile_pending(plan, authorization)
    assert reconciled.status is MutationReceiptStatus.RECONCILED
    assert reconciled.operation_results[0].outcome.value == "REJECTED"
    assert journal.unsettled_reservation_claims() == ()
    assert restarted.apply(plan, authorization) == reconciled


class ProcessCrashAfterEffect:
    def after_dispatching(self, plan, operation_id, target):
        return None

    def after_effect(self, plan, operation_id, target):
        raise KeyboardInterrupt("simulated death after effect")


def test_reconciliation_detects_effect_that_started_before_process_death(
    tmp_path: Path,
) -> None:
    executor, journal, observer, authority, settlement, plan, authorization = _executor(
        tmp_path, fault_hook=ProcessCrashAfterEffect()
    )
    with pytest.raises(KeyboardInterrupt):
        executor.apply(plan, authorization)
    assert (observer.root / "artifacts" / "0.txt").read_bytes() == b"new-0"
    assert len(journal.unsettled_reservation_claims()) == 1

    restarted = FixtureManagedMutationExecutor(
        executor._boundary,
        journal=journal,
        observer=observer,
        content_store=executor._content,
        authority=authority,
        identity_attestor=Identity(),
        kill_switch=KillSwitch(),
        settlement=settlement,
        clock=Clock(100),
        service_identity=SERVICE,
    )
    receipt = restarted.reconcile_pending(plan, authorization)
    assert receipt.status is MutationReceiptStatus.RECONCILED
    assert receipt.operation_results[0].outcome.value == "APPLIED"
    assert journal.unresolved() == ()
    assert journal.unsettled_reservation_claims() == ()


class CrashOnSecondOperation:
    calls = 0

    def after_dispatching(self, plan, operation_id, target):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("second operation failed after durable marker")


def test_multi_operation_post_marker_failure_is_uncertain_with_first_result(
    tmp_path: Path,
) -> None:
    executor, journal, observer, authority, settlement, plan, authorization = _executor(
        tmp_path, operation_count=2, fault_hook=CrashOnSecondOperation()
    )

    receipt = executor.apply(plan, authorization)

    assert receipt.status is MutationReceiptStatus.UNCERTAIN
    assert receipt.operation_results[0].outcome.value == "APPLIED"
    assert receipt.operation_results[1].outcome.value == "FAILED"
    assert (observer.root / "artifacts" / "0.txt").read_bytes() == b"new-0"
    assert (observer.root / "artifacts" / "1.txt").read_bytes() == b"old-b"
    assert authority.calls == 2
