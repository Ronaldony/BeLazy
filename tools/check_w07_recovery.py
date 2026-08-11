"""Verify the W07 completion checkpoint, final delivery, and ignored state."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
from typing import Any

from check_w02_recovery import (
    OBJECT_ID,
    _canonical_sha256,
    _git,
    _git_bytes,
    _is_ancestor,
    _load_object,
    _safe_relative,
    _sha256,
)


WAVE_ID = "W07-FINAL-AUDIT"
FINAL_TEST_IDS = {
    "w07-final-focused",
    "w07-full",
    "w07-boundaries",
    "w07-package-dependencies",
    "w07-prior-recovery",
    "w07-schema-resources",
    "w07-deterministic-wheel",
    "w07-isolated-wheel-probe",
    "w07-audit-scan",
    "w07-standard-backend",
    "w07-input-postcheck",
    "w07-final-delivery",
}
EVIDENCE_BINDINGS = (
    ("provenance_path", "provenance_sha256"),
    ("receipt_path", "receipt_sha256"),
    ("review_path", "review_sha256"),
    ("execplan_path", "execplan_sha256"),
    ("final_result_path", "final_result_sha256"),
    ("final_report_path", "final_report_sha256"),
    ("command_receipt_path", "command_receipt_sha256"),
    ("t90_path", "t90_sha256"),
    ("rollback_path", "rollback_sha256"),
)


def _semantic_projection(state: dict[str, Any]) -> dict[str, Any]:
    wave = next(item for item in state["waves"] if item["id"] == WAVE_ID)
    latest_round = max(item["round"] for item in wave["reviews"])
    return {
        "protocol_version": state["protocol_version"],
        "program_id": state["program_id"],
        "source": {
            key: state["source"][key]
            for key in ("expected_sha256", "observed_sha256", "unchanged")
        },
        "handoff": {
            key: state["handoff"][key]
            for key in ("manifest_sha256", "verified")
        },
        "target": {
            key: state["target"][key]
            for key in ("branch", "initial_commit")
        },
        "passed_wave": {
            "id": wave["id"],
            "status": wave["status"],
            "attempts": wave["attempts"],
            "task_ids": wave["task_ids"],
            "checkpoint_commit": wave["checkpoint_commit"],
            "tests": [
                {key: test[key] for key in ("id", "status", "result")}
                for test in wave["tests"]
                if test["id"] in FINAL_TEST_IDS
            ],
            "latest_reviews": [
                {
                    key: review[key]
                    for key in ("round", "role", "status", "critical", "high")
                }
                for review in wave["reviews"]
                if review["round"] == latest_round
            ],
            "unresolved_findings": wave["unresolved_findings"],
        },
    }


def validate_w07_recovery(root: Path) -> list[str]:
    errors: list[str] = []
    try:
        anchor = _load_object(root / "reports/autopilot/waves/W07/recovery-anchor.json")
        state = _load_object(root / ".be-lazy/autopilot/state.json")
        final_result = _load_object(root / "reports/autopilot/final-result.json")
    except (OSError, UnicodeError, ValueError) as exc:
        return [f"anchor_state_or_result_unreadable:{type(exc).__name__}"]

    checkpoint = anchor.get("checkpoint", {})
    implementation = anchor.get("implementation", {})
    evidence = anchor.get("evidence", {})
    object_values = (
        checkpoint.get("commit"),
        checkpoint.get("tree"),
        checkpoint.get("parent"),
        implementation.get("commit"),
        implementation.get("tree"),
        evidence.get("commit"),
        evidence.get("tree"),
    )
    if any(
        not isinstance(item, str) or not OBJECT_ID.fullmatch(item)
        for item in object_values
    ):
        return ["anchor_object_id_invalid"]

    checkpoint_commit = checkpoint["commit"]
    seal_commit: str | None = None
    try:
        head = _git(root, "rev-parse", "HEAD")
        seal_commit = _git(
            root,
            "log",
            "-1",
            "--format=%H",
            "--",
            "reports/autopilot/waves/W07/recovery-anchor.json",
        ).splitlines()[0]
        if _git(root, "rev-parse", f"{checkpoint_commit}^{{tree}}") != checkpoint["tree"]:
            errors.append("checkpoint_tree_mismatch")
        if _git(root, "rev-parse", f"{checkpoint_commit}^") != checkpoint["parent"]:
            errors.append("checkpoint_parent_mismatch")
        if _git(root, "show", "-s", "--format=%s", checkpoint_commit) != checkpoint["subject"]:
            errors.append("checkpoint_subject_mismatch")
        if _git(root, "rev-parse", f"{implementation['commit']}^{{tree}}") != implementation["tree"]:
            errors.append("implementation_tree_mismatch")
        if _git(root, "rev-parse", f"{evidence['commit']}^{{tree}}") != evidence["tree"]:
            errors.append("evidence_tree_mismatch")
        if not _is_ancestor(root, implementation["commit"], checkpoint_commit):
            errors.append("implementation_not_checkpoint_ancestor")
        if not _is_ancestor(root, checkpoint_commit, evidence["commit"]):
            errors.append("checkpoint_not_evidence_ancestor")
        if not _is_ancestor(root, checkpoint_commit, head):
            errors.append("checkpoint_not_head_ancestor")
        if not _is_ancestor(root, evidence["commit"], seal_commit):
            errors.append("evidence_not_seal_ancestor")
        if not _is_ancestor(root, seal_commit, head):
            errors.append("seal_not_head_ancestor")
    except (OSError, IndexError, subprocess.SubprocessError):
        errors.append("anchor_git_lookup_failed")

    for path_key, digest_key in EVIDENCE_BINDINGS:
        relative = _safe_relative(evidence.get(path_key))
        expected = evidence.get(digest_key)
        if relative is None or not isinstance(expected, str):
            errors.append(f"{path_key}_binding_invalid")
            continue
        try:
            committed = hashlib.sha256(
                _git_bytes(root, evidence["commit"], relative)
            ).hexdigest()
            current = _sha256(root / relative)
            if committed != expected:
                errors.append(f"{path_key}_evidence_commit_digest_mismatch")
            if current != expected:
                errors.append(f"{path_key}_current_digest_mismatch")
        except (OSError, subprocess.SubprocessError):
            errors.append(f"{path_key}_unreadable")

    state_binding = anchor.get("state", {})
    schema_relative = _safe_relative(state_binding.get("schema_path"))
    try:
        if (
            schema_relative is None
            or _sha256(root / schema_relative) != state_binding.get("schema_sha256")
        ):
            errors.append("state_schema_digest_mismatch")
    except OSError:
        errors.append("state_schema_unreadable")

    try:
        projection = _semantic_projection(state)
    except (KeyError, StopIteration, TypeError, ValueError):
        return sorted(set(errors + ["state_semantic_shape_invalid"]))
    recorded_projection = state_binding.get("semantic_projection")
    recorded_digest = state_binding.get("semantic_sha256")
    if projection != recorded_projection:
        errors.append("state_semantic_projection_mismatch")
    if _canonical_sha256(recorded_projection) != recorded_digest:
        errors.append("recorded_state_semantic_digest_mismatch")
    if _canonical_sha256(projection) != recorded_digest:
        errors.append("current_state_semantic_digest_mismatch")

    inputs = anchor.get("inputs", {})
    if inputs.get("source_archive_sha256") != state["source"]["expected_sha256"]:
        errors.append("anchor_source_digest_mismatch")
    if inputs.get("handoff_manifest_sha256") != state["handoff"]["manifest_sha256"]:
        errors.append("anchor_handoff_digest_mismatch")
    try:
        state_wave = next(item for item in state["waves"] if item["id"] == WAVE_ID)
        if state_wave["checkpoint_commit"] != checkpoint_commit:
            errors.append("state_checkpoint_mismatch")
    except (KeyError, StopIteration, TypeError):
        errors.append("state_checkpoint_unreadable")

    if state.get("status") != "complete":
        errors.append("state_not_complete")
    if final_result.get("status") != "COMPLETE":
        errors.append("final_result_not_complete")
    if final_result.get("critical_high_findings") != 0:
        errors.append("final_result_critical_high_nonzero")
    for key in (
        "remote_side_effects",
        "network_side_effects",
        "human_approval_synthesized",
        "public_identifier_migration_performed",
    ):
        if final_result.get(key) is not False:
            errors.append(f"final_result_{key}_not_false")

    return sorted(set(errors))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = validate_w07_recovery(root)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        print(f"w07_recovery=FAIL violations={len(errors)}")
        return 1
    print("w07_recovery=PASS violations=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
