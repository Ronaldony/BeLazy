"""Synthetic tests for the general CLI command registry and handlers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_factory.cli import (
    COMMAND_SPECS,
    ImplementationStatus,
    RecoveryPolicyError,
    RetryIdempotencyLedger,
    assert_recovery_command_permitted,
    handle_doctor,
    handle_not_yet_backed,
    handle_retry,
    handle_validate,
    list_commands,
    registered_names,
    request_invalidate,
    request_reopen,
    request_resume,
    request_retry,
    run_doctor,
    run_validate,
)
from video_factory.policy import ApprovalKind, WorkflowPolicyError


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_registry_contains_seventeen_unique_commands() -> None:
    names = [spec.name for spec in list_commands()]
    assert len(names) == 17
    assert len(set(names)) == 17
    assert registered_names() == frozenset(names)
    expected = {
        "init",
        "new-channel",
        "new-concept",
        "new-episode",
        "run",
        "status",
        "validate",
        "review",
        "approve",
        "retry",
        "resume",
        "invalidate",
        "reopen",
        "qc",
        "migrate",
        "export",
        "doctor",
    }
    assert set(names) == expected
    by_name = {spec.name: spec for spec in COMMAND_SPECS}
    assert by_name["doctor"].implementation_status is ImplementationStatus.IMPLEMENTED
    assert by_name["validate"].implementation_status is ImplementationStatus.IMPLEMENTED
    assert by_name["init"].implementation_status is ImplementationStatus.IMPLEMENTED
    assert by_name["init"].requires_workflow_mode is False
    assert "workspace_init" in by_name["init"].backing_modules[0]
    assert by_name["export"].implementation_status is ImplementationStatus.IMPLEMENTED
    assert by_name["export"].requires_workflow_mode is False
    assert "workspace_export" in by_name["export"].backing_modules[0]
    for recovery in ("retry", "resume", "invalidate", "reopen"):
        assert by_name[recovery].implementation_status is ImplementationStatus.CONTRACT_ONLY
        assert by_name[recovery].requires_workflow_mode is True
    # Phase 20: plan-only promotion — only migrate stays not_yet_backed.
    for name in (
        "run",
        "status",
        "qc",
        "approve",
        "review",
        "new-channel",
        "new-concept",
        "new-episode",
    ):
        assert by_name[name].implementation_status is ImplementationStatus.IMPLEMENTED
        assert by_name[name].requires_workflow_mode is True
    assert by_name["migrate"].implementation_status is ImplementationStatus.NOT_YET_BACKED


def test_doctor_runs_and_includes_core_purity_summary() -> None:
    """Unit: doctor structure + repository purity gate (deterministic in-tree).

    Host-varying facts (Python patch, media tools) are covered under
    ``live_health`` in ``test_live_health_observation.py`` and must not be
    hard-coded here.
    """

    report = run_doctor()
    assert report.core_version
    assert report.core_contract
    assert report.python_version
    assert report.purity_available is True
    assert "core_purity=" in report.purity_summary
    result = handle_doctor()
    assert result.command == "doctor"
    assert result.payload is not None
    assert "purity_summary" in result.payload
    assert "core_purity=" in str(result.payload["purity_summary"])
    assert result.exit_code == 0


def test_validate_distinguishes_valid_and_invalid_synthetic_config() -> None:
    valid = json.loads((FIXTURES / "channel_config.json").read_text(encoding="utf-8"))
    ok = run_validate(layer="channel", document=valid)
    assert ok.ok is True
    assert ok.scope_id == "channel-a"
    assert ok.errors == ()

    invalid = {"artifact_version": "channel-config/1.0", "config_contract": "1.0"}
    bad = run_validate(layer="channel", document=invalid)
    assert bad.ok is False
    assert bad.errors
    assert "missing required fields" in bad.errors[0]

    ok_result = handle_validate(layer="channel", document=valid)
    assert ok_result.exit_code == 0
    assert ok_result.status == "ok"
    bad_result = handle_validate(layer="channel", document=invalid)
    assert bad_result.exit_code == 1
    assert bad_result.status == "validation_failed"


def test_recovery_commands_require_explicit_workflow_mode() -> None:
    for call in (
        lambda: request_retry("TASK-001", None, idempotency_key="key-1"),
        lambda: request_resume("EP-001", None),
        lambda: request_invalidate("ARTIFACT-003", None),
        lambda: request_reopen("EP-001", None, stage="storyboard"),
    ):
        with pytest.raises(WorkflowPolicyError, match="does not choose a production default"):
            call()

    result = handle_retry("TASK-001", None, idempotency_key="key-1")
    assert result.exit_code == 2
    assert result.status == "policy_error"


def test_retry_idempotency_suppresses_duplicate_dispatch() -> None:
    ledger = RetryIdempotencyLedger()
    first = request_retry(
        "TASK-001",
        "standard",
        idempotency_key="idem-shared-1",
        ledger=ledger,
    )
    second = request_retry(
        "TASK-001",
        "standard",
        idempotency_key="idem-shared-1",
        ledger=ledger,
    )
    assert first.dispatched_new is True
    assert first.acceptance.value == "accepted"
    assert second.dispatched_new is False
    assert second.acceptance.value == "duplicate"
    assert second.intent.idempotency_key == first.intent.idempotency_key

    handled_a = handle_retry(
        "TASK-002",
        "controlled",
        idempotency_key="idem-shared-2",
        ledger=ledger,
    )
    handled_b = handle_retry(
        "TASK-002",
        "controlled",
        idempotency_key="idem-shared-2",
        ledger=ledger,
    )
    assert handled_a.payload is not None
    assert handled_b.payload is not None
    assert handled_a.payload["dispatched_new"] is True
    assert handled_b.payload["dispatched_new"] is False
    assert handled_b.status == "duplicate"


def test_not_yet_backed_commands_return_explicit_response() -> None:
    # migrate is the sole remaining not_yet_backed general command (Phase 20).
    result = handle_not_yet_backed("migrate", mode="standard")
    assert result.exit_code == 2
    assert result.status == ImplementationStatus.NOT_YET_BACKED.value
    assert "not yet backed" in result.message
    assert "PHASE" in result.message
    assert result.payload is not None
    assert result.payload["implementation_status"] == "not_yet_backed"

    missing_mode = handle_not_yet_backed("migrate", mode=None)
    assert missing_mode.status == "policy_error"
    assert "does not choose a production default" in missing_mode.message


def test_recovery_policy_matrix_rapid_standard_controlled() -> None:
    with pytest.raises(RecoveryPolicyError, match="not permitted under rapid"):
        assert_recovery_command_permitted("rapid", "retry")

    assert assert_recovery_command_permitted("standard", "retry").mode.value == "standard"
    assert assert_recovery_command_permitted("standard", "resume").mode.value == "standard"
    assert assert_recovery_command_permitted("standard", "reopen").mode.value == "standard"

    with pytest.raises(RecoveryPolicyError, match="destructive_action_approval"):
        assert_recovery_command_permitted("standard", "invalidate")

    controlled = assert_recovery_command_permitted("controlled", "invalidate", cascade=True)
    assert controlled.immutable_audit_log_required is True
    assert ApprovalKind.DESTRUCTIVE_ACTION_APPROVAL in controlled.required_approval_kinds

    accepted = request_invalidate("ARTIFACT-003", "controlled", cascade=True)
    assert accepted.intent.cascade is True
    reopened = request_reopen("EP-001", "controlled", stage="storyboard-review")
    assert reopened.intent.stage == "storyboard-review"
