"""Verify the tracked W00 recovery anchor against Git and ignored local state."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any


OBJECT_ID = re.compile(r"^[0-9a-f]{40}$")


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
        raise ValueError(f"expected JSON object: {path.name}")
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
    waves = state["waves"]
    w00 = next(wave for wave in waves if wave["id"] == "W00-BOOTSTRAP-BASELINE")
    latest_round = max(item["round"] for item in w00["reviews"])
    latest_reviews = [
        {
            key: review[key]
            for key in ("round", "role", "status", "critical", "high")
        }
        for review in w00["reviews"]
        if review["round"] == latest_round
    ]
    tests = [
        {key: test[key] for key in ("id", "status", "result")}
        for test in w00["tests"]
    ]
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
        "wave_identity_contract": [
            {"id": wave["id"], "task_ids": wave["task_ids"]}
            for wave in waves
        ],
        "passed_wave": {
            "id": w00["id"],
            "status": w00["status"],
            "attempts": w00["attempts"],
            "task_ids": w00["task_ids"],
            "checkpoint_commit": w00["checkpoint_commit"],
            "tests": tests,
            "latest_reviews": latest_reviews,
            "unresolved_findings": w00["unresolved_findings"],
        },
    }


def validate_anchor(root: Path) -> list[str]:
    errors: list[str] = []
    anchor_path = root / "reports/autopilot/waves/W00/recovery-anchor.json"
    state_path = root / ".be-lazy/autopilot/state.json"
    try:
        anchor = _load_object(anchor_path)
        state = _load_object(state_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"anchor_or_state_unreadable:{type(exc).__name__}"]

    checkpoint = anchor.get("checkpoint", {})
    tested = anchor.get("tested_evidence", {})
    commit = checkpoint.get("commit")
    tree = checkpoint.get("tree")
    parent = checkpoint.get("parent")
    tested_commit = tested.get("commit")
    tested_tree = tested.get("tree")
    object_values = (commit, tree, parent, tested_commit, tested_tree)
    if any(not isinstance(item, str) or not OBJECT_ID.fullmatch(item) for item in object_values):
        return ["anchor_object_id_invalid"]

    try:
        head = _git(root, "rev-parse", "HEAD")
        if _git(root, "rev-parse", f"{commit}^{{tree}}") != tree:
            errors.append("checkpoint_tree_mismatch")
        if _git(root, "rev-parse", f"{commit}^") != parent:
            errors.append("checkpoint_parent_mismatch")
        if _git(root, "show", "-s", "--format=%s", commit) != checkpoint.get("subject"):
            errors.append("checkpoint_subject_mismatch")
        if _git(root, "rev-parse", f"{tested_commit}^{{tree}}") != tested_tree:
            errors.append("tested_tree_mismatch")
        if not _is_ancestor(root, tested_commit, commit):
            errors.append("tested_commit_not_checkpoint_ancestor")
        if not _is_ancestor(root, commit, head):
            errors.append("checkpoint_not_head_ancestor")
    except (OSError, subprocess.SubprocessError):
        errors.append("anchor_git_lookup_failed")

    for section, path_key, digest_key in (
        (anchor.get("review", {}), "receipt_path", "receipt_sha256"),
        (anchor.get("state", {}), "schema_path", "schema_sha256"),
    ):
        relative = _safe_relative(section.get(path_key))
        expected = section.get(digest_key)
        if relative is None or not isinstance(expected, str):
            errors.append(f"{path_key}_binding_invalid")
            continue
        try:
            if _sha256(root / relative) != expected:
                errors.append(f"{path_key}_digest_mismatch")
        except OSError:
            errors.append(f"{path_key}_unreadable")

    try:
        projection = _semantic_projection(state)
        w00 = next(
            wave for wave in state["waves"] if wave["id"] == "W00-BOOTSTRAP-BASELINE"
        )
    except (KeyError, StopIteration, TypeError):
        return sorted(set(errors + ["state_semantic_shape_invalid"]))
    recorded_projection = anchor.get("state", {}).get("semantic_projection")
    recorded_digest = anchor.get("state", {}).get("semantic_sha256")
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
    if w00["checkpoint_commit"] != commit:
        errors.append("state_checkpoint_mismatch")

    return sorted(set(errors))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = validate_anchor(root)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        print(f"recovery_anchor=FAIL violations={len(errors)}")
        return 1
    print("recovery_anchor=PASS violations=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
