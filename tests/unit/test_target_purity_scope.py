from __future__ import annotations

from pathlib import Path

from video_factory.security.purity import scan_repository


def test_repository_controls_and_provenance_are_outside_product_scan(tmp_path: Path) -> None:
    control_paths = (
        "AGENTS.md",
        ".agent/execplans/plan.md",
        ".agents/skills/local/SKILL.md",
        ".be-lazy/autopilot/state.json",
        "." + "co" + "dex/agents/reviewer.toml",
        "docs/program/acceptance.md",
        "docs/provenance/source-baseline.json",
        "docs/governance/source-baseline-policy-v1.yaml",
        "reports/autopilot/waves/W00/receipt.md",
        "waves/W00.md",
    )
    local_agent_name = "Co" + "dex"
    local_path = "C" + ":\\" + "Users\\local\\source.zip"
    source_instruction_name = "CLA" + "UDE.md"
    for relative in control_paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"agent={local_agent_name}\npath={local_path}\ninstruction={source_instruction_name}\n",
            encoding="utf-8",
        )
    product_file = tmp_path / "src" / "neutral.py"
    product_file.parent.mkdir(parents=True)
    product_file.write_text("VALUE = 'channel-neutral'\n", encoding="utf-8")

    violations, scanned_files = scan_repository(tmp_path)

    assert violations == []
    assert scanned_files == 1


def test_repository_product_files_remain_subject_to_neutrality_scan(tmp_path: Path) -> None:
    product_file = tmp_path / "src" / "product.py"
    product_file.parent.mkdir(parents=True)
    concrete_channel = "Boss" + "Kimu"
    product_file.write_text(f"CHANNEL = {concrete_channel!r}\n", encoding="utf-8")

    violations, scanned_files = scan_repository(tmp_path)

    assert scanned_files == 1
    assert {finding.rule_id for finding in violations} == {"channel_name_variant"}
