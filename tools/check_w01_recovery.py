"""Verify the W01 checkpoint recovery anchor and ignored local state."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any


OBJECT_ID = re.compile(r"^[0-9a-f]{40}$")
FINAL_TEST_IDS = {
    "w01-standard-backend",
    "w01-final-focused",
    "w01-final-full",
    "w01-final-boundaries",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path.name}")
    return value


def _safe_relative(value: object) -> str | None:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return value


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _git_bytes(root: Path, commit: str, relative_path: str) -> bytes:
    completed = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={root.as_posix()}",
            "show",
            f"{commit}:{relative_path}",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return completed.stdout


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={root.as_posix()}",
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ],
        cwd=root,
        check=False,
        capture_output=True,
    )
    return completed.returncode == 0


def _semantic_projection(state: dict[str, Any]) -> dict[str, Any]:
    wave = next(item for item in state["waves"] if item["id"] == "W01-TRUST-BOUNDARY")
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


def validate_w01_recovery(root: Path) -> list[str]:
    errors: list[str] = []
    try:
        anchor = _load_object(root / "reports/autopilot/waves/W01/recovery-anchor.json")
        state = _load_object(root / ".be-lazy/autopilot/state.json")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"anchor_or_state_unreadable:{type(exc).__name__}"]

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
    if any(not isinstance(item, str) or not OBJECT_ID.fullmatch(item) for item in object_values):
        return ["anchor_object_id_invalid"]

    checkpoint_commit = checkpoint["commit"]
    seal_commit: str | None = None
    try:
        head = _git(root, "rev-parse", "HEAD")
        seal_commit = _git(
            root,
            "log",
            "--diff-filter=A",
            "--format=%H",
            "--",
            "reports/autopilot/waves/W01/recovery-anchor.json",
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
        if not _is_ancestor(root, evidence["commit"], checkpoint_commit):
            errors.append("evidence_not_checkpoint_ancestor")
        if not _is_ancestor(root, checkpoint_commit, head):
            errors.append("checkpoint_not_head_ancestor")
        if not _is_ancestor(root, checkpoint_commit, seal_commit):
            errors.append("checkpoint_not_seal_ancestor")
        if not _is_ancestor(root, seal_commit, head):
            errors.append("seal_not_head_ancestor")
    except (OSError, IndexError, subprocess.SubprocessError):
        errors.append("anchor_git_lookup_failed")

    for path_key, digest_key in (
        ("provenance_path", "provenance_sha256"),
        ("receipt_path", "receipt_sha256"),
        ("review_path", "review_sha256"),
        ("execplan_path", "execplan_sha256"),
    ):
        relative = _safe_relative(evidence.get(path_key))
        expected = evidence.get(digest_key)
        if relative is None or not isinstance(expected, str):
            errors.append(f"{path_key}_binding_invalid")
            continue
        try:
            if path_key == "execplan_path" and seal_commit is not None:
                observed = hashlib.sha256(
                    _git_bytes(root, seal_commit, relative)
                ).hexdigest()
            else:
                observed = _sha256(root / relative)
            if observed != expected:
                errors.append(f"{path_key}_digest_mismatch")
        except (OSError, subprocess.SubprocessError):
            errors.append(f"{path_key}_unreadable")

    state_binding = anchor.get("state", {})
    schema_relative = _safe_relative(state_binding.get("schema_path"))
    try:
        if schema_relative is None or _sha256(root / schema_relative) != state_binding.get("schema_sha256"):
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
        state_wave = next(
            item for item in state["waves"] if item["id"] == "W01-TRUST-BOUNDARY"
        )
        if state_wave["checkpoint_commit"] != checkpoint_commit:
            errors.append("state_checkpoint_mismatch")
    except (KeyError, StopIteration, TypeError):
        errors.append("state_checkpoint_unreadable")

    return sorted(set(errors))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = validate_w01_recovery(root)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        print(f"w01_recovery=FAIL violations={len(errors)}")
        return 1
    print("w01_recovery=PASS violations=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
