from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import pickle
from types import SimpleNamespace

import pytest

from video_factory.approvals import GateContext
from video_factory.authority import VerificationPurpose
from video_factory.config.canonical import canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    CapabilityId,
    HashDigest,
    IdempotencyKey,
    OpaqueId,
    RelativeArtifactPath,
    RequestId,
    RoleId,
)
from video_factory.engine import ExecutionMode
from video_factory.mutation import workspace_observation_sha256
from video_factory.providers import (
    AdapterKind,
    AllowedOutput,
    CapabilityDescriptor,
    CostMeasurement,
    ExecutorAuthorityScope,
    ExternalReference,
    ExternalStateUncertain,
    Outcome,
    RequestEnvelope,
    ResultEnvelope,
    SideEffect,
    UncertaintyEvidence,
    UncertaintyModel,
    request_envelope_sha256,
)
from video_factory.runtime import JournalState, RuntimeActionKind
from video_factory_runtime import (
    DurableDispatchInputs,
    DurableExecutionError,
    DurableExecutorRuntime,
    FixtureCredentialBroker,
    FixtureCredentialRegistration,
    FixtureRuntimeBoundary,
    FixtureWorkspaceObserver,
    OpaqueCredentialHandle,
    SQLiteExecutionJournal,
    credential_scope_sha256,
)
import video_factory_runtime.durable_execution as durable_execution


NOW = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
INPUT_VERSION = ArtifactVersion("fixture-input/1.0")
OUTPUT_VERSION = ArtifactVersion("fixture-output/1.0")
SERVICE = OpaqueId("executor-service")


class Clock:
    def __init__(self, offset: int = 0) -> None:
        self.count = offset

    def now(self) -> datetime:
        self.count += 1
        return NOW + timedelta(seconds=self.count)


class Identity:
    reference = ArtifactReference(
        RelativeArtifactPath("identity/executor-service.json"),
        HashDigest("a" * 64),
        ArtifactVersion("service-attestation/1.0"),
    )

    def attest_current(self, service_identity, *, evaluated_at):
        assert service_identity == SERVICE
        return self.reference


class KillSwitch:
    def engaged(self, *, evaluated_at):
        return False


class ReboundAttestationBroker:
    def __init__(self, delegate) -> None:
        self.delegate = delegate

    def verify_current(self, credential_reference, scope, *, evaluated_at):
        lease = self.delegate.verify_current(
            credential_reference, scope, evaluated_at=evaluated_at
        )
        if lease is None or scope.purpose is not VerificationPurpose.RECONCILE:
            return lease
        return replace(
            lease,
            attestation_record=ArtifactReference(
                RelativeArtifactPath("credentials/rebound-attestation.json"),
                HashDigest("1" * 64),
                ArtifactVersion("credential-registration-attestation/1.0"),
            ),
        )


class Settlement:
    def __init__(self) -> None:
        self.calls = 0

    def finalize_current(
        self,
        *,
        journal_id,
        reservation_claim_sha256s,
        reservation_sha256s,
        final_state,
        evaluated_at,
    ):
        self.calls += 1
        return ArtifactReference(
            RelativeArtifactPath(f"settlement/{journal_id}-{self.calls}.json"),
            canonical_sha256(
                {
                    "journal_id": str(journal_id),
                    "claims": [str(item) for item in reservation_claim_sha256s],
                    "reservations": [str(item) for item in reservation_sha256s],
                    "state": final_state.value,
                }
            ),
            ArtifactVersion("authority-settlement/1.0"),
        )


class Executor:
    adapter_id = OpaqueId("fixture-executor")

    def __init__(self, observer, *, behavior: str = "success") -> None:
        self.observer = observer
        self.behavior = behavior
        self.calls = 0
        self.reconcile_calls = 0
        self.received_credentials: list[OpaqueCredentialHandle] = []
        self.received_input_bundles = []

    def dispatch(self, request, scope, *, credential_lease, input_bundle):
        self.calls += 1
        self.received_credentials.append(credential_lease.handle)
        self.received_input_bundles.append(input_bundle)
        if self.behavior == "crash":
            raise KeyboardInterrupt("simulated process death")
        if self.behavior == "timeout":
            raise TimeoutError("fixture timeout")
        if self.behavior == "external-uncertain":
            raise ExternalStateUncertain(
                "fixture external state is unknown",
                ExternalReference("external-request", "external-session"),
            )
        if self.behavior == "unreported":
            (self.observer.root / "unexpected.txt").write_bytes(b"unexpected")
            output = ()
        else:
            target = self.observer.root / "artifacts" / "results" / "result.bin"
            target.write_bytes(b"result")
            output = (
                ArtifactReference(
                    RelativeArtifactPath("artifacts/results/result.bin"),
                    HashDigest(
                        "f6a214f7a5fcda0c2cee9660b7fc29f5649e3c68aad48e20e950137c98913a68"
                    ),
                    OUTPUT_VERSION,
                ),
            )
        return ResultEnvelope(
            request.request_id,
            Outcome.SUCCEEDED,
            output,
            ExternalReference("external-request", "external-session"),
            CostMeasurement(Decimal("1"), "USD", False),
            UncertaintyEvidence(False, None, None),
        )

    def reconcile(
        self,
        request,
        scope,
        external_reference,
        *,
        credential_lease,
        input_bundle,
    ):
        self.reconcile_calls += 1
        self.received_credentials.append(credential_lease.handle)
        self.received_input_bundles.append(input_bundle)
        if self.behavior == "reconcile-crash":
            raise KeyboardInterrupt("simulated reconciliation process death")
        if self.behavior == "reconcile-uncertain":
            return ResultEnvelope(
                request.request_id,
                Outcome.EXTERNAL_UNCERTAIN,
                (),
                external_reference,
                CostMeasurement(None, None, True),
                UncertaintyEvidence(True, "still pending", None),
            )
        if self.behavior != "reconcile-success":
            raise AssertionError("unexpected reconciliation")
        target = self.observer.root / "artifacts" / "results" / "result.bin"
        target.write_bytes(b"result")
        return ResultEnvelope(
            request.request_id,
            Outcome.SUCCEEDED,
            (
                ArtifactReference(
                    RelativeArtifactPath("artifacts/results/result.bin"),
                    HashDigest(
                        "f6a214f7a5fcda0c2cee9660b7fc29f5649e3c68aad48e20e950137c98913a68"
                    ),
                    OUTPUT_VERSION,
                ),
            ),
            external_reference,
            CostMeasurement(Decimal("1"), "USD", False),
            UncertaintyEvidence(False, None, None),
        )


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, behavior: str = "success"):
    boundary = FixtureRuntimeBoundary.initialize(
        tmp_path / "runtime",
        runtime_id="durable-executor-test",
        fixture_only=True,
    )
    observer = FixtureWorkspaceObserver(
        boundary, workspace_id=OpaqueId("workspace-executor")
    )
    (observer.root / "artifacts").mkdir()
    (observer.root / "artifacts" / "results").mkdir()
    input_payload = b"input"
    (observer.root / "artifacts" / "input.bin").write_bytes(input_payload)
    observation = observer.observe(revision_id=OpaqueId("revision-executor"))
    request = RequestEnvelope(
        RequestId("request-executor"),
        CapabilityId("fixture-task"),
        ExecutionMode.AUTOMATED,
        HashDigest("b" * 64),
        (
            ArtifactReference(
                RelativeArtifactPath("artifacts/input.bin"),
                HashDigest(
                    "c96c6d5be8d08a12e7b5cdc1b207fa6b2430974c86803d8891675e76fd992c20"
                ),
                INPUT_VERSION,
            ),
        ),
        (
            AllowedOutput(
                RelativeArtifactPath("artifacts/results"),
                frozenset({OUTPUT_VERSION}),
            ),
        ),
        IdempotencyKey("executor-key"),
        RoleId("creator"),
        RoleId("reviewer"),
    )
    descriptor = CapabilityDescriptor(
        OpaqueId("fixture-executor"),
        AdapterKind.EXECUTOR,
        "1.0",
        frozenset({CapabilityId("fixture-task")}),
        frozenset({ExecutionMode.AUTOMATED}),
        frozenset({INPUT_VERSION}),
        frozenset({OUTPUT_VERSION}),
        frozenset({SideEffect.EXTERNAL_CALL, SideEffect.LOCAL_WRITE}),
        UncertaintyModel(True, True),
    )
    context = GateContext(
        HashDigest("1" * 64),
        HashDigest("2" * 64),
        HashDigest("3" * 64),
        request.effective_config_sha256,
        observation.manifest_sha256,
        HashDigest("5" * 64),
        HashDigest("6" * 64),
    )
    scope = ExecutorAuthorityScope(
        OpaqueId("workspace-executor"),
        OpaqueId("channel-a"),
        OpaqueId("concept-a"),
        OpaqueId("episode-a"),
        descriptor.adapter_id,
        OpaqueId("model-a"),
        "fixture:executor",
        5,
        "USD",
        1,
        0,
    )
    authority_request = SimpleNamespace(
        executable_plan_sha256=HashDigest("6" * 64),
        action_id=OpaqueId("run-external-generation"),
        scope=SimpleNamespace(
            channel_id=scope.channel_id,
            concept_id=scope.concept_id,
            episode_id=scope.episode_id,
        ),
    )
    authority_decision = SimpleNamespace(
        decision_sha256=HashDigest("7" * 64),
        evaluated_at="2026-08-10T00:00:00Z",
    )
    calls: list[dict[str, object]] = []

    def fake_enforcement(*args, **kwargs):
        recorded = dict(kwargs)
        recorded["_runtime_claim_sha256"] = getattr(
            kwargs.get("authority_ledger"), "runtime_claim_sha256", None
        )
        calls.append(recorded)
        purpose = kwargs.get("verification_purpose", VerificationPurpose.DISPATCH)
        return SimpleNamespace(
            receipt=SimpleNamespace(
                receipt_sha256=HashDigest(
                    "8" * 64
                    if purpose is VerificationPurpose.DISPATCH
                    else "d" * 64
                )
            ),
            request_sha256=HashDigest("9" * 64),
            purpose=purpose,
        )

    monkeypatch.setattr(
        durable_execution, "verify_adapter_dispatch_authority", fake_enforcement
    )
    inputs = DurableDispatchInputs(
        request=request,
        descriptor=descriptor,
        orchestration_authorization=SimpleNamespace(),
        current_context=context,
        workspace_observation=observation,
        expected_workspace_id=OpaqueId("workspace-executor"),
        expected_workspace_revision_id=OpaqueId("revision-executor"),
        expected_workspace_revision=SimpleNamespace(),
        expected_workspace_revision_sha256=HashDigest("a" * 64),
        authority_request=authority_request,
        authority_decision=authority_decision,
        authority_ledger=SimpleNamespace(),
        service_identity=SERVICE,
        authority_scope=scope,
        credential_reference=ArtifactReference(
            RelativeArtifactPath("credentials/executor.json"),
            HashDigest("e" * 64),
            ArtifactVersion("credential-reference/1.0"),
        ),
    )
    executor = Executor(observer, behavior=behavior)
    journal = SQLiteExecutionJournal(boundary)
    credential_scopes = tuple(
        sorted(
            {
                credential_scope_sha256(
                    DurableExecutorRuntime._credential_scope(inputs, purpose)
                )
                for purpose in (
                    VerificationPurpose.DISPATCH,
                    VerificationPurpose.RECONCILE,
                )
            }
        )
    )
    broker = FixtureCredentialBroker(
        boundary,
        (
            FixtureCredentialRegistration(
                inputs.credential_reference,
                credential_scopes,
                NOW - timedelta(seconds=1),
                NOW + timedelta(days=1),
                1,
            ),
        ),
    )
    runtime = DurableExecutorRuntime(
        boundary,
        journal=journal,
        observer=observer,
        executor=executor,
        identity_attestor=Identity(),
        kill_switch=KillSwitch(),
        credential_broker=broker,
        settlement=Settlement(),
        clock=Clock(),
    )
    return runtime, journal, observer, executor, inputs, calls


def test_success_is_journaled_and_exact_replay_never_calls_executor_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, journal, observer, executor, inputs, enforcement_calls = _setup(
        tmp_path, monkeypatch
    )

    first = runtime.dispatch(inputs)
    second = runtime.dispatch(inputs)

    assert first == second
    assert first.outcome is Outcome.SUCCEEDED
    assert executor.calls == 1
    assert len(executor.received_credentials) == 1
    assert isinstance(executor.received_credentials[0], OpaqueCredentialHandle)
    with pytest.raises(TypeError):
        pickle.dumps(executor.received_credentials[0])
    assert executor.received_input_bundles[0].inputs[0].payload == b"input"
    assert len(enforcement_calls) == 2
    assert journal.unresolved() == ()


def test_terminal_exact_replay_does_not_require_fresh_effect_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, journal, observer, executor, inputs, enforcement_calls = _setup(
        tmp_path, monkeypatch
    )
    first = runtime.dispatch(inputs)

    class NoCurrentIdentity:
        def attest_current(self, service_identity, *, evaluated_at):
            raise AssertionError("terminal replay must not re-attest identity")

    class EngagedKillSwitch:
        def engaged(self, *, evaluated_at):
            raise AssertionError("terminal replay must not consult the kill switch")

    class StaleWorkspace:
        def observe(self, *, revision_id):
            raise AssertionError("terminal replay must not re-read workspace bytes")

    runtime._identity = NoCurrentIdentity()
    runtime._kill_switch = EngagedKillSwitch()
    runtime._observer = StaleWorkspace()

    replay = runtime.dispatch(inputs)

    assert replay == first
    assert executor.calls == 1
    assert len(enforcement_calls) == 2
    assert journal.unresolved() == ()


def test_timeout_is_uncertain_and_exact_replay_is_not_redispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, journal, observer, executor, inputs, enforcement_calls = _setup(
        tmp_path, monkeypatch, behavior="timeout"
    )

    first = runtime.dispatch(inputs)
    second = runtime.dispatch(inputs)

    assert first == second
    assert first.outcome is Outcome.EXTERNAL_UNCERTAIN
    assert executor.calls == 1
    assert len(enforcement_calls) == 2
    assert len(journal.unresolved()) == 1


def test_unreported_workspace_write_turns_result_into_uncertain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, journal, observer, executor, inputs, enforcement_calls = _setup(
        tmp_path, monkeypatch, behavior="unreported"
    )

    result = runtime.dispatch(inputs)

    assert result.outcome is Outcome.EXTERNAL_UNCERTAIN
    assert result.uncertainty.reason == "runtime.executor.unreported_change"
    assert len(journal.unresolved()) == 1


def test_process_death_after_dispatch_marker_requires_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, journal, observer, executor, inputs, enforcement_calls = _setup(
        tmp_path, monkeypatch, behavior="crash"
    )
    with pytest.raises(KeyboardInterrupt):
        runtime.dispatch(inputs)
    assert len(journal.unresolved()) == 1
    assert len(journal.unsettled_reservation_claims()) == 1

    settlement = Settlement()
    restarted = DurableExecutorRuntime(
        runtime._boundary,
        journal=journal,
        observer=observer,
        executor=executor,
        identity_attestor=Identity(),
        kill_switch=KillSwitch(),
        credential_broker=runtime._credentials,
        settlement=settlement,
        clock=Clock(100),
    )
    with pytest.raises(DurableExecutionError) as caught:
        restarted.dispatch(inputs)
    assert caught.value.reason_code == "runtime.executor.reconcile_required"
    assert executor.calls == 1
    executor.behavior = "reconcile-success"
    reconciled = restarted.reconcile_pending(
        journal.unresolved()[0].journal_id, inputs
    )
    assert reconciled.outcome is Outcome.SUCCEEDED
    assert journal.unsettled_reservation_claims() == ()
    assert settlement.calls == 1


def test_uncertain_result_reconciles_without_redispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, journal, observer, executor, inputs, enforcement_calls = _setup(
        tmp_path, monkeypatch, behavior="timeout"
    )
    assert runtime.dispatch(inputs).outcome is Outcome.EXTERNAL_UNCERTAIN
    snapshot = journal.unresolved()[0]
    executor.behavior = "reconcile-success"

    reconciled = runtime.reconcile_pending(snapshot.journal_id, inputs)
    replay = runtime.dispatch(inputs)

    assert reconciled.outcome is Outcome.SUCCEEDED
    assert replay == reconciled
    assert executor.calls == 1
    assert executor.reconcile_calls == 1
    assert [call["verification_purpose"] for call in enforcement_calls] == [
        VerificationPurpose.DISPATCH,
        VerificationPurpose.DISPATCH,
        VerificationPurpose.RECONCILE,
        VerificationPurpose.RECONCILE,
    ]
    loaded = journal.load(snapshot.journal_id)
    assert loaded.state is JournalState.RECONCILED
    assert loaded.receipt is not None
    assert loaded.receipt.reconciliation_record is not None
    assert journal.unresolved() == ()


def test_reconcile_rejects_credential_attestation_rebound_before_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, journal, observer, executor, inputs, enforcement_calls = _setup(
        tmp_path, monkeypatch, behavior="timeout"
    )
    runtime.dispatch(inputs)
    snapshot = journal.unresolved()[0]
    executor.behavior = "reconcile-success"
    runtime._credentials = ReboundAttestationBroker(runtime._credentials)

    with pytest.raises(DurableExecutionError) as caught:
        runtime.reconcile_pending(snapshot.journal_id, inputs)

    assert caught.value.reason_code == "runtime.executor.credential_rebound"
    assert executor.reconcile_calls == 0


def test_reconcile_binds_full_credential_reference_not_digest_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, journal, observer, executor, inputs, enforcement_calls = _setup(
        tmp_path, monkeypatch, behavior="timeout"
    )
    runtime.dispatch(inputs)
    snapshot = journal.unresolved()[0]
    rebound = replace(
        inputs,
        credential_reference=replace(
            inputs.credential_reference,
            path=RelativeArtifactPath("credentials/foreign-executor.json"),
        ),
    )

    with pytest.raises(DurableExecutionError) as caught:
        runtime.reconcile_pending(snapshot.journal_id, rebound)

    assert caught.value.reason_code == "runtime.executor.reconcile_rebound"
    assert executor.reconcile_calls == 0


def test_reconcile_can_remain_uncertain_then_settle_without_redispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, journal, observer, executor, inputs, enforcement_calls = _setup(
        tmp_path, monkeypatch, behavior="timeout"
    )
    runtime.dispatch(inputs)
    snapshot = journal.unresolved()[0]
    executor.behavior = "reconcile-uncertain"

    pending = runtime.reconcile_pending(snapshot.journal_id, inputs)

    assert pending.outcome is Outcome.EXTERNAL_UNCERTAIN
    current = journal.load(snapshot.journal_id)
    assert current.state is JournalState.UNCERTAIN
    assert current.receipt is not None
    assert current.receipt.journal_head_sha256 == current.events[-1].event_sha256

    executor.behavior = "reconcile-success"
    settled = runtime.reconcile_pending(snapshot.journal_id, inputs)

    assert settled.outcome is Outcome.SUCCEEDED
    assert executor.calls == 1
    assert executor.reconcile_calls == 2
    assert journal.load(snapshot.journal_id).state is JournalState.RECONCILED


def test_process_death_during_reconcile_preserves_a_loadable_reconcile_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, journal, observer, executor, inputs, enforcement_calls = _setup(
        tmp_path, monkeypatch, behavior="timeout"
    )
    runtime.dispatch(inputs)
    snapshot = journal.unresolved()[0]
    executor.behavior = "reconcile-crash"

    with pytest.raises(KeyboardInterrupt):
        runtime.reconcile_pending(snapshot.journal_id, inputs)

    interrupted = journal.load(snapshot.journal_id)
    assert interrupted.state is JournalState.RECONCILING
    assert interrupted.receipt is None
    executor.behavior = "reconcile-success"
    settled = runtime.reconcile_pending(snapshot.journal_id, inputs)
    assert settled.outcome is Outcome.SUCCEEDED
    assert executor.calls == 1
    assert executor.reconcile_calls == 2


def test_reconcile_restart_recovers_the_last_durable_external_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, journal, observer, executor, inputs, enforcement_calls = _setup(
        tmp_path, monkeypatch, behavior="external-uncertain"
    )
    initial = runtime.dispatch(inputs)
    assert initial.external_reference == ExternalReference(
        "external-request", "external-session"
    )
    snapshot = journal.unresolved()[0]
    executor.behavior = "reconcile-crash"

    with pytest.raises(KeyboardInterrupt):
        runtime.reconcile_pending(snapshot.journal_id, inputs)

    interrupted = journal.load(snapshot.journal_id)
    assert interrupted.state is JournalState.RECONCILING
    assert interrupted.receipt is None
    executor.behavior = "reconcile-success"
    settled = runtime.reconcile_pending(snapshot.journal_id, inputs)

    assert settled.outcome is Outcome.SUCCEEDED
    assert settled.external_reference == ExternalReference(
        "external-request", "external-session"
    )
    assert executor.calls == 1
    assert executor.reconcile_calls == 2


def test_reconcile_rejects_request_rebound_before_external_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, journal, observer, executor, inputs, enforcement_calls = _setup(
        tmp_path, monkeypatch, behavior="timeout"
    )
    runtime.dispatch(inputs)
    snapshot = journal.unresolved()[0]
    rebound = replace(
        inputs,
        request=replace(
            inputs.request,
            idempotency_key=IdempotencyKey("executor-key-rebound"),
        ),
    )
    executor.behavior = "reconcile-success"

    with pytest.raises(DurableExecutionError) as caught:
        runtime.reconcile_pending(snapshot.journal_id, rebound)

    assert caught.value.reason_code == "runtime.executor.reconcile_rebound"
    assert executor.reconcile_calls == 0


def test_reserved_restart_revalidates_then_dispatches_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, journal, observer, executor, inputs, enforcement_calls = _setup(
        tmp_path, monkeypatch
    )
    original_claim = journal.claim_dispatch
    captured: list[OpaqueId] = []
    failed = False

    def interrupt_before_dispatch(journal_id, **kwargs):
        nonlocal failed
        captured.append(journal_id)
        if not failed:
            failed = True
            raise RuntimeError("simulated pre-effect process death")
        return original_claim(journal_id, **kwargs)

    monkeypatch.setattr(journal, "claim_dispatch", interrupt_before_dispatch)
    with pytest.raises(RuntimeError):
        runtime.dispatch(inputs)
    assert journal.load(captured[-1]).state is JournalState.RESERVED
    assert journal.unresolved() == ()

    monkeypatch.setattr(journal, "claim_dispatch", original_claim)
    result = runtime.dispatch(inputs)

    assert result.outcome is Outcome.SUCCEEDED
    assert executor.calls == 1
    assert len(enforcement_calls) == 3


def test_reservation_result_crash_reuses_one_durable_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, journal, observer, executor, inputs, enforcement_calls = _setup(
        tmp_path, monkeypatch
    )
    original_append = journal.append_state
    interrupted = False

    def crash_before_authorized(journal_id, state, **kwargs):
        nonlocal interrupted
        if not interrupted and state is JournalState.AUTHORIZED:
            interrupted = True
            raise RuntimeError("reserve returned before journal state")
        return original_append(journal_id, state, **kwargs)

    monkeypatch.setattr(journal, "append_state", crash_before_authorized)
    with pytest.raises(RuntimeError):
        runtime.dispatch(inputs)
    planned = journal.load_by_scope(
        action_kind=RuntimeActionKind.EXECUTOR,
        action_id=OpaqueId("executor_dispatch"),
        idempotency_key=inputs.request.idempotency_key,
        request_sha256=HashDigest(request_envelope_sha256(inputs.request)),
    )
    assert planned is not None
    assert planned.state is JournalState.PLANNED
    assert len(journal.unsettled_reservation_claims()) == 1

    monkeypatch.setattr(journal, "append_state", original_append)
    assert runtime.dispatch(inputs).outcome is Outcome.SUCCEEDED
    claims = {
        call["_runtime_claim_sha256"]
        for call in enforcement_calls
        if call["verification_purpose"] is VerificationPurpose.DISPATCH
    }
    assert len(claims) == 1
    assert executor.calls == 1
    assert journal.unsettled_reservation_claims() == ()


def test_raw_secret_shaped_credential_is_rejected_before_journal_or_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, journal, observer, executor, inputs, enforcement_calls = _setup(
        tmp_path, monkeypatch
    )
    rebound = replace(inputs, credential_reference="sk-live-secret-token")

    with pytest.raises(Exception):
        runtime.dispatch(rebound)

    assert executor.calls == 0
    assert journal.unresolved() == ()
    assert all(
        b"sk-live-secret-token" not in path.read_bytes()
        for path in runtime._boundary.root.rglob("*")
        if path.is_file()
    )


def test_revoked_credential_is_rejected_before_journal_or_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, journal, observer, executor, inputs, enforcement_calls = _setup(
        tmp_path, monkeypatch
    )
    runtime._credentials.revoke(inputs.credential_reference)

    with pytest.raises(Exception):
        runtime.dispatch(inputs)

    assert executor.calls == 0
    assert journal.unresolved() == ()


def test_authority_revoked_after_dispatch_marker_blocks_external_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, journal, observer, executor, inputs, enforcement_calls = _setup(
        tmp_path, monkeypatch
    )
    first_verifier = durable_execution.verify_adapter_dispatch_authority
    invocations = 0

    def revoke_after_marker(*args, **kwargs):
        nonlocal invocations
        invocations += 1
        if invocations == 2:
            raise DurableExecutionError(
                "runtime.executor.authority_revoked", "authority changed after marker"
            )
        return first_verifier(*args, **kwargs)

    monkeypatch.setattr(
        durable_execution, "verify_adapter_dispatch_authority", revoke_after_marker
    )
    with pytest.raises(DurableExecutionError) as denied:
        runtime.dispatch(inputs)

    assert denied.value.reason_code == "runtime.executor.authority_revoked"
    assert executor.calls == 0
    assert journal.unresolved()[0].state is JournalState.DISPATCHING
