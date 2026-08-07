"""Verify W01 evidence against Git, packaged schemas, tests, and reviews."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


OBJECT_ID = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_ROLES = {
    "architecture_contract",
    "security_authority_boundary",
    "test_compatibility_packaging",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path.name}")
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
    return completed.stdout.strip().lower()


def _is_ancestor(root: Path, ancestor: str, descendant: str = "HEAD") -> bool:
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


def validate_w01_provenance(root: Path) -> list[str]:
    errors: list[str] = []
    report_path = root / "docs/provenance/w01-trust-boundary.json"
    try:
        report = _load_object(report_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"report_unreadable:{type(exc).__name__}"]

    implementation = report.get("implementation")
    if not isinstance(implementation, dict):
        return ["implementation_binding_missing"]
    commit = implementation.get("commit")
    tree = implementation.get("tree")
    parent = implementation.get("parent")
    baseline = implementation.get("baseline_ancestor")
    for name, value in (
        ("commit", commit),
        ("tree", tree),
        ("parent", parent),
        ("baseline_ancestor", baseline),
    ):
        if not isinstance(value, str) or not OBJECT_ID.fullmatch(value):
            errors.append(f"implementation_{name}_invalid")
    if not any(error.startswith("implementation_") for error in errors):
        try:
            if _git(root, "rev-parse", f"{commit}^{{tree}}") != tree:
                errors.append("implementation_tree_mismatch")
            if _git(root, "rev-parse", f"{commit}^") != parent:
                errors.append("implementation_parent_mismatch")
            if not _is_ancestor(root, baseline, commit):
                errors.append("baseline_not_implementation_ancestor")
            if not _is_ancestor(root, commit):
                errors.append("implementation_not_head_ancestor")
        except (OSError, subprocess.SubprocessError):
            errors.append("implementation_git_lookup_failed")

    inputs = report.get("inputs", {})
    try:
        source = _load_object(root / "docs/provenance/source-baseline.json")
        if inputs.get("source_archive_sha256") != source.get("expected_sha256"):
            errors.append("source_digest_mismatch")
        if inputs.get("source_member_count") != source["inventory"]["members"]:
            errors.append("source_member_count_mismatch")
        handoff_path = root / "docs/provenance/handoff-manifest.json"
        if inputs.get("handoff_manifest_sha256") != _sha256(handoff_path):
            errors.append("handoff_manifest_digest_mismatch")
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        errors.append("input_provenance_unreadable")

    resources = report.get("schema_resources", {})
    manifest_relative = resources.get("manifest_path")
    if not isinstance(manifest_relative, str):
        errors.append("schema_manifest_path_invalid")
    else:
        manifest_path = root / manifest_relative
        try:
            if _sha256(manifest_path) != resources.get("manifest_sha256"):
                errors.append("schema_manifest_digest_mismatch")
            manifest = _load_object(manifest_path)
            entries = manifest["schemas"]
            if not isinstance(entries, list):
                raise ValueError("schemas must be a list")
            if len(entries) != resources.get("schema_count"):
                errors.append("schema_count_mismatch")
            registered = sum(item.get("artifact_version") is not None for item in entries)
            if registered != resources.get("registered_version_count"):
                errors.append("registered_version_count_mismatch")
            filenames = [item.get("filename") for item in entries]
            if len(filenames) != len(set(filenames)):
                errors.append("schema_filename_duplicate")
            schema_root = manifest_path.parent
            for item in entries:
                filename = item.get("filename")
                digest = item.get("sha256")
                if not isinstance(filename, str) or not isinstance(digest, str):
                    errors.append("schema_entry_invalid")
                    continue
                if _sha256(schema_root / filename) != digest:
                    errors.append(f"schema_digest_mismatch:{filename}")
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            errors.append("schema_manifest_unreadable")

    evidence = report.get("test_evidence", {})
    receipt_item = evidence.get("receipt", {})
    try:
        receipt_path = root / receipt_item["path"]
        if _sha256(receipt_path) != receipt_item.get("sha256"):
            errors.append("test_receipt_digest_mismatch")
        receipt = _load_object(receipt_path)
        subject = receipt["subject"]["target"]
        if subject.get("implementation_commit") != commit:
            errors.append("receipt_commit_mismatch")
        if subject.get("implementation_tree") != tree:
            errors.append("receipt_tree_mismatch")
        commands = {item.get("id"): item for item in receipt["commands"]}
        if commands.get("w01-focused", {}).get("exit_code") != 0:
            errors.append("focused_test_not_passed")
        if commands.get("w01-full", {}).get("exit_code") != 0:
            errors.append("full_test_not_passed")
        reviews = receipt.get("final_reviews")
        if not isinstance(reviews, list):
            errors.append("receipt_final_reviews_missing")
        else:
            roles = {item.get("role") for item in reviews}
            if roles != EXPECTED_ROLES:
                errors.append("receipt_review_roles_mismatch")
            if any(
                item.get("critical") != 0
                or item.get("high") != 0
                or item.get("medium") != 0
                or item.get("result") != "GO"
                for item in reviews
            ):
                errors.append("receipt_review_not_clear")
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        errors.append("test_receipt_unreadable")

    final_review = report.get("final_review", {})
    try:
        review_path = root / final_review["path"]
        if _sha256(review_path) != final_review.get("sha256"):
            errors.append("review_receipt_digest_mismatch")
        reviewers = final_review["reviewers"]
        if {item.get("role") for item in reviewers} != EXPECTED_ROLES:
            errors.append("review_roles_mismatch")
        if any(
            item.get("critical") != 0
            or item.get("high") != 0
            or item.get("medium") != 0
            or item.get("result") != "GO"
            for item in reviewers
        ):
            errors.append("review_gate_not_clear")
    except (OSError, KeyError, TypeError):
        errors.append("review_evidence_unreadable")

    wheel = report.get("manual_wheel", {})
    if wheel.get("role") != "STDLIB_PEP427_ACCEPTANCE_FALLBACK_ONLY":
        errors.append("manual_wheel_role_invalid")
    if not isinstance(wheel.get("sha256"), str) or not DIGEST.fullmatch(wheel["sha256"]):
        errors.append("manual_wheel_digest_invalid")
    backend = evidence.get("standard_build_backend", {})
    if backend.get("status") != "ENVIRONMENT_BLOCKED":
        errors.append("standard_backend_status_misrepresented")
    if backend.get("fallback_misrepresented_as_backend") is not False:
        errors.append("fallback_backend_claim_invalid")

    return sorted(set(errors))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = validate_w01_provenance(root)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        print(f"w01_provenance=FAIL violations={len(errors)}")
        return 1
    print("w01_provenance=PASS violations=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
