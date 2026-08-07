from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
BASELINE_COMMIT = "b7aaaf2ac43ca99970c317a7d505316a07d320e9"
BASELINE_TREE = "0f9f3cf065261a0b0f24faea72fa116687bb59d2"
SOURCE_SHA256 = "954325b77028bcf7d36a88136d8e1ed0ec2348ad15014623710614cced94034a"
TRACKED_LOCALITY_RECORDS = (
    ".agent/execplans/be-lazy-autopilot.md",
    "docs/provenance/baseline-test-report.md",
    "docs/provenance/source-baseline.json",
    "reports/autopilot/input-verification.json",
    "reports/autopilot/waves/W00/test-receipt.json",
)


def _sha256(relative_path: str) -> str:
    return hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()


def test_w00_provenance_binds_baseline_snapshot_and_test_evidence() -> None:
    provenance = json.loads(
        (ROOT / "docs/provenance/source-baseline.json").read_text(encoding="utf-8")
    )

    assert provenance["expected_sha256"] == SOURCE_SHA256
    assert provenance["observed_pre_sha256"] == SOURCE_SHA256
    assert provenance["observed_post_sha256"] == SOURCE_SHA256

    target = provenance["target"]
    assert target["initial_commit"] == BASELINE_COMMIT
    assert target["baseline_snapshot_commit"] == BASELINE_COMMIT
    assert target["baseline_snapshot_tree"] == BASELINE_TREE
    assert target["remote_count_at_bootstrap"] == 0
    assert target["remote_contact_count"] == 0

    evidence = provenance["test_evidence"]
    for evidence_id in ("baseline_report", "w00_test_receipt"):
        item = evidence[evidence_id]
        assert item["sha256"] == _sha256(item["path"])

    receipt = json.loads(
        (ROOT / evidence["w00_test_receipt"]["path"]).read_text(encoding="utf-8")
    )
    tested_target = evidence["tested_target"]
    assert re.fullmatch(r"[0-9a-f]{40}", tested_target["commit"])
    assert re.fullmatch(r"[0-9a-f]{40}", tested_target["tree"])
    assert receipt["subject"]["target"]["tested_commit"] == tested_target["commit"]
    assert receipt["subject"]["target"]["tested_tree"] == tested_target["tree"]

    assert evidence["source_baseline"]["pytest"].startswith("329 passed")
    assert evidence["target_baseline"]["pytest"].startswith("343 passed")
    assert evidence["wheel_build"]["status"] == "ENVIRONMENT_BLOCKED"

    receipt_text = json.dumps(receipt, sort_keys=True)
    assert "<external-temp>" not in receipt_text
    for command in receipt["commands"]:
        assert command["cwd_role"]
        assert command["invocation"]
        assert command["argv"]


def test_w00_tracked_evidence_omits_host_local_paths() -> None:
    windows_absolute_path = re.compile(r"(?i)\b[A-Z]:[\\/]")
    local_user_marker = "wo" + "tmd"

    for relative_path in TRACKED_LOCALITY_RECORDS:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert windows_absolute_path.search(text) is None, relative_path
        assert local_user_marker not in text.casefold(), relative_path
