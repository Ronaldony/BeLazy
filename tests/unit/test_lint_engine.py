"""Synthetic tests for generic verbatim / source-lock lint."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from video_factory.domain import HashDigest, OpaqueId, RelativeArtifactPath
from video_factory.lint import (
    SourceLockRule,
    VerbatimRule,
    load_lint_rules_file,
    load_lint_rules_mapping,
    run_lint,
)


def _sha_text(text: str) -> HashDigest:
    return HashDigest(hashlib.sha256(text.encode("utf-8")).hexdigest())


def _sha_file(path: Path) -> HashDigest:
    return HashDigest(hashlib.sha256(path.read_bytes()).hexdigest())


def test_verbatim_match_and_mismatch() -> None:
    expected = "canonical subject block\nline two"
    expected_hash = _sha_text(expected)
    doc_ok = {"shots": [{"prompt_subject": expected}]}
    doc_bad = {"shots": [{"prompt_subject": expected + "x"}]}

    rule = VerbatimRule(
        rule_id=OpaqueId("verbatim-subject"),
        target_field_pointer="/shots/0/prompt_subject",
        expected_text_sha256=expected_hash,
        expected_text=expected,
    )

    report_ok = run_lint(doc_ok, [rule])
    assert report_ok.passed
    assert all(f.passed for f in report_ok.findings)

    report_bad = run_lint(doc_bad, [rule])
    assert not report_bad.passed
    failing = report_bad.failing()
    assert len(failing) == 1
    assert failing[0].constraint_id == "verbatim-subject"
    assert "does not match" in failing[0].message


def test_source_lock_detects_drift(tmp_path: Path) -> None:
    source = tmp_path / "brand" / "entity.md"
    source.parent.mkdir(parents=True)
    source.write_text("original body\n", encoding="utf-8", newline="\n")
    locked = _sha_file(source)

    rule = SourceLockRule(
        rule_id=OpaqueId("lock-entity"),
        source_path=RelativeArtifactPath("brand/entity.md"),
        source_sha256=locked,
    )
    report_ok = run_lint({}, [rule], source_root=tmp_path)
    assert report_ok.passed

    source.write_text("changed body\n", encoding="utf-8", newline="\n")
    report_drift = run_lint({}, [rule], source_root=tmp_path)
    assert not report_drift.passed
    assert "source drift" in report_drift.failing()[0].message


def test_verbatim_with_source_lock_combo(tmp_path: Path) -> None:
    body = "fixed sentence for consumer"
    source = tmp_path / "canon.txt"
    source.write_text(body, encoding="utf-8", newline="\n")
    source_hash = _sha_file(source)
    text_hash = _sha_text(body)

    rule = VerbatimRule(
        rule_id=OpaqueId("combo"),
        target_field_pointer="/field",
        expected_text_sha256=text_hash,
        expected_text=body,
        source_path=RelativeArtifactPath("canon.txt"),
        source_sha256=source_hash,
    )
    report = run_lint({"field": body}, [rule], source_root=tmp_path)
    assert report.passed
    assert len(report.findings) == 2

    source.write_text("drifted", encoding="utf-8")
    report2 = run_lint({"field": body}, [rule], source_root=tmp_path)
    assert not report2.passed
    assert any("source drift" in f.message for f in report2.failing())


def test_load_rules_file_json(tmp_path: Path) -> None:
    expected = "hello lock"
    text_hash = _sha_text(expected)
    payload = {
        "ruleset_id": "channel-rules-1",
        "rules": [
            {
                "kind": "verbatim",
                "rule_id": "v1",
                "target_field_pointer": "/text",
                "expected_text": expected,
                "expected_text_sha256": text_hash,
            },
            {
                "kind": "source_lock",
                "rule_id": "s1",
                "source_path": "src.txt",
                "source_sha256": "a" * 64,
            },
        ],
    }
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    ruleset = load_lint_rules_file(path)
    assert ruleset.ruleset_id == "channel-rules-1"
    assert len(ruleset) == 2

    mapping = load_lint_rules_mapping(payload)
    assert len(mapping.rules) == 2


def test_no_channel_content_hardcoded_in_lint_module() -> None:
    """Core lint sources must not embed channel character text."""

    root = Path(__file__).resolve().parents[2] / "src" / "video_factory" / "lint"
    forbidden = ("BOSS", "subject bible", "character-bible", "location-bible")
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path} contains forbidden token {token!r}"
