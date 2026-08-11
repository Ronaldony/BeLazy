"""Validate local autopilot state against its schema, Git, and immutable inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import jsonschema


OBJECT_ID = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_WAVES = {
    "W00-BOOTSTRAP-BASELINE": (
        "BOOT-001",
        "BOOT-002",
        "BOOT-003",
        "BOOT-004",
        "BOOT-005",
    ),
    "W01-TRUST-BOUNDARY": ("P0-PKG-001", "P0-JSON-001", "P0-AUTH-001"),
    "W02-MANAGED-MUTATION": ("MUT-001", "MUT-002", "MUT-003", "MUT-004"),
    "W03-DIRECTOR-BLUEPRINT": ("BP-001", "DIR-001", "DIR-002", "BP-002"),
    "W04-WORKFLOW-AUTHORITY": ("WF-001", "AUTH-001", "AUTH-002", "WF-002"),
    "W05-AUTOMATION-QUALITY-RELEASE": ("SEL-001", "QA-001", "REL-001"),
    "W06-RUNTIME-MIGRATION": ("RUN-001", "RUN-002", "MIG-001", "REL-002"),
    "W07-FINAL-AUDIT": ("AUDIT-001", "AUDIT-002", "AUDIT-003"),
}
DEFAULT_REVIEW_ROLES = {
    "architecture_contract",
    "security_authority_boundary",
    "test_compatibility_packaging",
}
W07_REVIEW_ROLES = DEFAULT_REVIEW_ROLES | {"documentation_migration_delivery"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip().lower()


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


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def validate_state(root: Path) -> list[str]:
    errors: list[str] = []
    state_path = root / ".be-lazy/autopilot/state.json"
    schema_path = root / ".be-lazy/autopilot/schemas/autopilot-state.schema.json"
    try:
        state = _load_object(state_path)
        schema = _load_object(schema_path)
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(state, schema)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        jsonschema.ValidationError,
        jsonschema.SchemaError,
    ) as exc:
        return [f"state_schema_or_parse:{type(exc).__name__}"]

    waves = state["waves"]
    observed = {wave["id"]: tuple(wave["task_ids"]) for wave in waves}
    if observed != EXPECTED_WAVES:
        errors.append("wave_or_task_identity_mismatch")

    try:
        head = _git(root, "rev-parse", "HEAD")
        status_output = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    except (OSError, subprocess.SubprocessError):
        return sorted(set(errors + ["git_observation_failed"]))

    target = state["target"]
    if target["current_head"] != head:
        errors.append("current_head_mismatch")
    if target["working_tree_clean"] != (status_output == ""):
        errors.append("working_tree_clean_mismatch")
    try:
        if not Path(target["path"]).samefile(root):
            errors.append("target_path_mismatch")
    except OSError:
        errors.append("target_path_unreadable")

    initial_commit = target["initial_commit"]
    if not isinstance(initial_commit, str) or not OBJECT_ID.fullmatch(initial_commit):
        errors.append("initial_commit_invalid")
    else:
        try:
            if _git(root, "rev-parse", f"{initial_commit}^{{commit}}") != initial_commit:
                errors.append("initial_commit_unresolvable")
            if not _is_ancestor(root, initial_commit, head):
                errors.append("initial_commit_not_ancestor")
        except (OSError, subprocess.SubprocessError):
            errors.append("initial_commit_lookup_failed")

    for wave in waves:
        if wave["status"] != "passed":
            continue
        checkpoint = wave["checkpoint_commit"]
        if not isinstance(checkpoint, str) or not OBJECT_ID.fullmatch(checkpoint):
            errors.append(f"{wave['id']}:checkpoint_invalid")
            continue
        try:
            if _git(root, "rev-parse", f"{checkpoint}^{{commit}}") != checkpoint:
                errors.append(f"{wave['id']}:checkpoint_unresolvable")
            if not _is_ancestor(root, checkpoint, head):
                errors.append(f"{wave['id']}:checkpoint_not_ancestor")
        except (OSError, subprocess.SubprocessError):
            errors.append(f"{wave['id']}:checkpoint_lookup_failed")

        reviews = wave["reviews"]
        latest_round = max((item["round"] for item in reviews), default=0)
        latest = [item for item in reviews if item["round"] == latest_round]
        expected_review_roles = (
            W07_REVIEW_ROLES
            if wave["id"] == "W07-FINAL-AUDIT"
            else DEFAULT_REVIEW_ROLES
        )
        if {item["role"] for item in latest} != expected_review_roles:
            errors.append(f"{wave['id']}:latest_review_roles_incomplete")
        if any(
            item.get("status") not in {"approved", "passed"}
            or item.get("critical") != 0
            or item.get("high") != 0
            for item in latest
        ):
            errors.append(f"{wave['id']}:latest_review_not_clear")

    source = state["source"]
    try:
        actual_source = _sha256(Path(source["path_or_attachment"]))
        if actual_source != source["expected_sha256"]:
            errors.append("source_expected_digest_mismatch")
        if actual_source != source["observed_sha256"] or not source["unchanged"]:
            errors.append("source_observation_mismatch")
    except OSError:
        errors.append("source_unreadable")

    handoff = state["handoff"]
    try:
        actual_manifest = _sha256(Path(handoff["path_or_attachment"]) / "handoff-manifest.json")
        if actual_manifest != handoff["manifest_sha256"] or not handoff["verified"]:
            errors.append("handoff_observation_mismatch")
    except OSError:
        errors.append("handoff_unreadable")

    return sorted(set(errors))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = validate_state(root)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        print(f"autopilot_state=FAIL violations={len(errors)}")
        return 1
    print("autopilot_state=PASS violations=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
