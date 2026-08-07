"""Verify that W00 provenance is bound to immutable Git and evidence objects."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


OBJECT_ID = re.compile(r"^[0-9a-f]{40}$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_value(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={root.as_posix()}",
            *arguments,
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip().lower()


def _git_is_ancestor(root: Path, ancestor: str, descendant: str = "HEAD") -> bool:
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


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path.name}")
    return value


def validate_provenance(root: Path) -> list[str]:
    errors: list[str] = []
    provenance_path = root / "docs/provenance/source-baseline.json"
    try:
        provenance = _load_json(provenance_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"source_baseline_unreadable:{type(exc).__name__}"]

    target = provenance.get("target")
    evidence = provenance.get("test_evidence")
    if not isinstance(target, dict):
        return ["target_binding_missing"]
    if not isinstance(evidence, dict):
        return ["test_evidence_missing"]

    commit = target.get("baseline_snapshot_commit")
    tree = target.get("baseline_snapshot_tree")
    if not isinstance(commit, str) or not OBJECT_ID.fullmatch(commit):
        errors.append("baseline_snapshot_commit_invalid")
    if not isinstance(tree, str) or not OBJECT_ID.fullmatch(tree):
        errors.append("baseline_snapshot_tree_invalid")
    if not errors:
        try:
            if _git_value(root, "rev-parse", f"{commit}^{{commit}}") != commit:
                errors.append("baseline_snapshot_commit_mismatch")
            if _git_value(root, "rev-parse", f"{commit}^{{tree}}") != tree:
                errors.append("baseline_snapshot_tree_mismatch")
            if not _git_is_ancestor(root, commit):
                errors.append("baseline_snapshot_not_ancestor")
        except (OSError, subprocess.SubprocessError):
            errors.append("baseline_snapshot_git_lookup_failed")

    tested = evidence.get("tested_target")
    if not isinstance(tested, dict):
        errors.append("tested_target_binding_missing")
    else:
        tested_commit = tested.get("commit")
        tested_tree = tested.get("tree")
        if not isinstance(tested_commit, str) or not OBJECT_ID.fullmatch(tested_commit):
            errors.append("tested_target_commit_invalid")
        if not isinstance(tested_tree, str) or not OBJECT_ID.fullmatch(tested_tree):
            errors.append("tested_target_tree_invalid")
        if (
            isinstance(tested_commit, str)
            and OBJECT_ID.fullmatch(tested_commit)
            and isinstance(tested_tree, str)
            and OBJECT_ID.fullmatch(tested_tree)
        ):
            try:
                if _git_value(root, "rev-parse", f"{tested_commit}^{{tree}}") != tested_tree:
                    errors.append("tested_target_tree_mismatch")
                if not _git_is_ancestor(root, tested_commit):
                    errors.append("tested_target_not_ancestor")
            except (OSError, subprocess.SubprocessError):
                errors.append("tested_target_git_lookup_failed")

    for evidence_id in ("baseline_report", "w00_test_receipt"):
        item = evidence.get(evidence_id)
        if not isinstance(item, dict):
            errors.append(f"{evidence_id}_binding_missing")
            continue
        relative_path = item.get("path")
        expected_digest = item.get("sha256")
        if not isinstance(relative_path, str) or not isinstance(expected_digest, str):
            errors.append(f"{evidence_id}_binding_invalid")
            continue
        path = root / relative_path
        try:
            observed_digest = _sha256(path)
        except OSError:
            errors.append(f"{evidence_id}_unreadable")
            continue
        if observed_digest != expected_digest:
            errors.append(f"{evidence_id}_digest_mismatch")

    receipt_item = evidence.get("w00_test_receipt")
    if isinstance(receipt_item, dict) and isinstance(receipt_item.get("path"), str):
        try:
            receipt = _load_json(root / receipt_item["path"])
            subject = receipt["subject"]["target"]
            tested_target = evidence.get("tested_target", {})
            if subject.get("tested_commit") != tested_target.get("commit"):
                errors.append("receipt_tested_commit_mismatch")
            if subject.get("tested_tree") != tested_target.get("tree"):
                errors.append("receipt_tested_tree_mismatch")
            if receipt["subject"]["source"]["archive_sha256"] != provenance.get(
                "expected_sha256"
            ):
                errors.append("receipt_source_digest_mismatch")
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            errors.append("receipt_subject_invalid")

    return sorted(set(errors))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = validate_provenance(root)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        print(f"w00_provenance=FAIL violations={len(errors)}")
        return 1
    print("w00_provenance=PASS violations=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
