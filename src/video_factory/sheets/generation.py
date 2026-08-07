"""Deterministic generation-packet → human order-sheet Markdown renderer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import hashlib
import json
from typing import Any

from video_factory.approvals import GateContext, gate_context_sha256
from video_factory.artifacts import validate_artifact_mapping
from video_factory.config import CanonicalizationError, canonical_json_bytes
from video_factory.engine.orchestration import GenerationReadinessPlan
from video_factory.json_boundary import parse_rfc3339_datetime


class GenerationSheetError(ValueError):
    """Raised when a packet document cannot be rendered without guessing."""


# Determinism contract (same input → byte-identical output):
# 1. UTF-8 text, LF newlines only (no CR).
# 2. No wall-clock timestamps.
# 3. Section order fixed: header → output → shots (document order) → footer.
# 4. Mapping keys sorted by Unicode when enumerating free maps.
# 5. Prompt body is copied verbatim (no summary, rewrite, or internal strip).
# 6. Trailing newline at end of document exactly once.


def packet_document_sha256(packet_doc: Mapping[str, Any]) -> str:
    """SHA-256 of the packet using canonical-json-v1 bytes."""

    try:
        payload = canonical_json_bytes(dict(packet_doc))
    except CanonicalizationError as exc:
        raise GenerationSheetError(f"packet is not canonicalizable: {exc}") from exc
    return hashlib.sha256(payload).hexdigest()


def render_generation_sheet(
    packet_doc: Mapping[str, Any],
    *,
    readiness: GenerationReadinessPlan | None = None,
    current_context: GateContext | None = None,
    evaluated_at: datetime | None = None,
) -> str:
    """Render a packet to deterministic human copy-paste Markdown.

    The prompt for each shot is emitted **byte-for-byte** inside a fenced block.
    Packet-local flags never authorize generation. Only a validated readiness
    plan bound to this exact packet content removes the preview warning.
    """

    if not isinstance(packet_doc, Mapping):
        raise GenerationSheetError("packet_doc must be a mapping")

    artifact_version = packet_doc.get("artifact_version")
    if artifact_version not in {
        "generation-packet/1.0",
        "generation-packet/2.0",
    }:
        raise GenerationSheetError(
            f"unsupported artifact_version for generation sheet: {artifact_version!r}"
        )
    validation = validate_artifact_mapping(packet_doc)
    if not validation.ok:
        raise GenerationSheetError(
            "generation packet schema validation failed: "
            + "; ".join(validation.error_texts)
        )

    episode_id = _require_str(packet_doc, "episode_id")
    title = packet_doc.get("title_working")
    title_text = title if isinstance(title, str) else ""
    rules_version = packet_doc.get("rules_version")
    rules_text = rules_version if isinstance(rules_version, str) else ""
    shots = packet_doc.get("shots")
    if not isinstance(shots, Sequence) or isinstance(shots, (str, bytes, bytearray)):
        raise GenerationSheetError("shots must be an array")
    if len(shots) < 1:
        raise GenerationSheetError("shots must contain at least one shot")

    packet_sha = packet_document_sha256(packet_doc)
    generation_authorized = False
    if readiness is not None:
        if readiness.packet_sha256 != packet_sha:
            raise GenerationSheetError(
                "generation readiness is bound to different packet content"
            )
        if readiness.authorization_ready:
            if current_context is None:
                raise GenerationSheetError(
                    "authorization-ready plan requires current gate context"
                )
            if str(gate_context_sha256(current_context)) != readiness.gate_context_sha256:
                raise GenerationSheetError(
                    "generation readiness is bound to another gate context"
                )
            if (
                evaluated_at is None
                or evaluated_at.tzinfo is None
                or evaluated_at.utcoffset() is None
            ):
                raise GenerationSheetError(
                    "authorization-ready plan requires timezone-aware evaluation time"
                )
            assert readiness.valid_from is not None
            assert readiness.valid_until is not None
            valid_from = parse_rfc3339_datetime(readiness.valid_from)
            valid_until = parse_rfc3339_datetime(readiness.valid_until)
            if evaluated_at < valid_from:
                raise GenerationSheetError("generation readiness is not yet valid")
            if evaluated_at >= valid_until:
                raise GenerationSheetError("generation readiness has expired")
            generation_authorized = True
    total_candidates = 0
    for index, shot in enumerate(shots):
        if not isinstance(shot, Mapping):
            raise GenerationSheetError(f"shots[{index}] must be an object")
        candidates = shot.get("candidates", 1)
        if not isinstance(candidates, int) or isinstance(candidates, bool) or candidates < 1:
            raise GenerationSheetError(f"shots[{index}].candidates must be a positive integer")
        total_candidates += candidates

    header_title = (
        "Generation Order Sheet"
        if generation_authorized
        else "Generation Order Sheet Preview — DO NOT GENERATE"
    )
    lines: list[str] = [
        f"# {header_title} — {episode_id}"
        + (f" ({title_text})" if title_text else ""),
        "",
        f"- packet_sha256: `{packet_sha}`",
        f"- artifact_version: `{artifact_version}`",
        f"- rules_version: `{rules_text}`" if rules_text else "- rules_version: (unset)",
        f"- packet_compatibility_flag: `{'true' if packet_doc.get('approved_by_human') is True else 'false'}`",
        f"- generation_readiness: `{'ready' if generation_authorized else 'blocked'}`",
        f"- total_candidates: **{total_candidates}**",
    ]
    if readiness is not None:
        if readiness.gate_context_sha256 is not None:
            lines.append(
                f"- gate_context_sha256: `{readiness.gate_context_sha256}`"
            )
        if readiness.valid_from is not None and readiness.valid_until is not None:
            lines.append(
                f"- authorization_window: `{readiness.valid_from}` to "
                f"`{readiness.valid_until}`"
            )
        if readiness.feasibility_review is not None:
            lines.append(
                "- feasibility_evidence: "
                f"`{readiness.feasibility_review.path}` "
                f"`{readiness.feasibility_review.sha256}`"
            )
        if readiness.approval_evidence is not None:
            lines.append(
                "- approval_evidence: "
                f"`{readiness.approval_evidence.path}` "
                f"`{readiness.approval_evidence.sha256}`"
            )
        for blocker in readiness.blockers:
            lines.append(f"- readiness_blocker: {blocker}")

    output = packet_doc.get("output")
    if isinstance(output, Mapping):
        out_dir = output.get("dir")
        pattern = output.get("filename_pattern")
        if isinstance(out_dir, str):
            lines.append(f"- output.dir: `{out_dir}`")
        if isinstance(pattern, str):
            lines.append(f"- output.filename_pattern: `{pattern}`")
        target = output.get("target")
        if isinstance(target, Mapping):
            for key in sorted(target):
                lines.append(f"- output.target.{key}: `{_format_scalar(target[key])}`")
        targets_by = output.get("targets_by_adapter")
        if isinstance(targets_by, Mapping):
            for adapter_id in sorted(targets_by):
                adapter_target = targets_by[adapter_id]
                if not isinstance(adapter_target, Mapping):
                    continue
                for key in sorted(adapter_target):
                    lines.append(
                        f"- output.targets_by_adapter.{adapter_id}.{key}: "
                        f"`{_format_scalar(adapter_target[key])}`"
                    )

    quota = packet_doc.get("quota_plan")
    if isinstance(quota, Mapping):
        date = quota.get("date")
        if isinstance(date, str) and date:
            lines.append(f"- quota_plan.date: `{date}`")
        budgets = quota.get("budgets")
        if isinstance(budgets, Mapping):
            for budget_key in sorted(budgets):
                budget = budgets[budget_key]
                lines.append(f"- quota_plan.budgets.{budget_key}: `{_format_json(budget)}`")

    lines.extend(
        [
            "",
            "> Order: copy prompt → generate → save with the listed name pattern → checklist → QC.",
            "",
        ]
    )

    for index, shot in enumerate(shots):
        assert isinstance(shot, Mapping)
        shot_id = _require_str(shot, "shot_id")
        duration = shot.get("duration_sec")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            raise GenerationSheetError(f"shots[{index}].duration_sec must be a number")
        tolerance = shot.get("duration_tolerance_sec")
        tol_text = ""
        if isinstance(tolerance, (int, float)) and not isinstance(tolerance, bool):
            tol_text = f" (±{tolerance}s)"
        candidates = shot.get("candidates", 1)
        mode = shot.get("mode")
        mode_text = mode if isinstance(mode, str) else "unset"
        prompt = shot.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            raise GenerationSheetError(f"shots[{index}].prompt must be a non-empty string")

        lines.extend(
            [
                "---",
                f"## {shot_id} — target {duration}s{tol_text} · candidates {candidates}",
                "",
                f"- mode: **{mode_text}**",
            ]
        )

        first_frame = shot.get("first_frame_note")
        if isinstance(first_frame, str) and first_frame:
            lines.append(f"- first_frame_note: {first_frame}")
        first_frame_record = shot.get("first_frame")
        if isinstance(first_frame_record, Mapping):
            if isinstance(first_frame_record.get("path"), str):
                lines.append(f"- first_frame.path: `{first_frame_record['path']}`")
            if isinstance(first_frame_record.get("state"), str):
                lines.append(f"- first_frame.state: `{first_frame_record['state']}`")

        plans = shot.get("provider_plans")
        if isinstance(plans, Sequence) and not isinstance(plans, (str, bytes, bytearray)):
            plan_bits: list[str] = []
            for plan in plans:
                if not isinstance(plan, Mapping):
                    continue
                adapter = plan.get("adapter_id")
                plan_cand = plan.get("candidates")
                if isinstance(adapter, str) and isinstance(plan_cand, int) and not isinstance(plan_cand, bool):
                    plan_bits.append(f"{adapter} x{plan_cand}")
            plan_bits_sorted = sorted(plan_bits)
            if plan_bits_sorted:
                lines.append(f"- provider_plans: {', '.join(plan_bits_sorted)}")

        refs = shot.get("reference_assets")
        if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes, bytearray)):
            ref_paths: list[str] = []
            for ref in refs:
                if isinstance(ref, Mapping) and isinstance(ref.get("path"), str):
                    ref_paths.append(str(ref["path"]))
            if ref_paths:
                lines.append(
                    "- reference_assets: "
                    + ", ".join(f"`{path}`" for path in ref_paths)
                )

        lines.extend(
            [
                "",
                "### Prompt (copy the entire block below as-is)",
                "",
                "```",
                prompt,
                "```",
                "",
            ]
        )

        checklist = shot.get("checklist")
        if isinstance(checklist, Sequence) and not isinstance(checklist, (str, bytes, bytearray)):
            lines.append("### Post-generation checklist")
            lines.append("")
            for item in checklist:
                if isinstance(item, str):
                    lines.append(f"- [ ] {item}")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"<!-- packet_sha256:{packet_sha} -->")
    if not generation_authorized:
        lines.append("<!-- generation_authorized:false -->")
    lines.append("")

    return "\n".join(lines)


def _require_str(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GenerationSheetError(f"{key} must be a non-empty string")
    return value


def _format_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def _format_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
