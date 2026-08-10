"""Reversible coordinator joining W05 decisions to the production edit plane.

The legacy workflow remains the byte-stable compatibility oracle.  This
coordinator is an additive, non-authorizing cutover seam: only a cleanly
reverified AUTO_SELECTED decision may bypass the legacy human selection step.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from video_factory.domain import HashDigest, OpaqueId
from video_factory.quality import MediaSubject

from .contracts import (
    CandidateDecision,
    CandidateDecisionStatus,
    CandidateDecisionVerificationInputs,
    SelectionContractError,
)
from .decision import validate_candidate_decision_structure, verify_candidate_decision


class CandidateSelectionCutoverMode(StrEnum):
    LEGACY_HUMAN_FALLBACK = "legacy_human_fallback"
    VERIFIED_AUTOMATION = "verified_automation"


@dataclass(frozen=True, slots=True)
class CandidateSelectionRoute:
    source_candidate_decision_sha256: HashDigest
    mode: CandidateSelectionCutoverMode
    action_id: OpaqueId
    actor_role: OpaqueId
    selected_subjects: tuple[MediaSubject, ...]
    human_selection_required: bool
    reason_codes: tuple[str, ...]
    authority_effect: str = "none"


def route_candidate_selection(
    decision: CandidateDecision,
    *,
    mode: CandidateSelectionCutoverMode,
    verification: CandidateDecisionVerificationInputs | None = None,
) -> CandidateSelectionRoute:
    """Choose the edit-plane route without minting execution authority."""

    validate_candidate_decision_structure(decision)
    if not isinstance(mode, CandidateSelectionCutoverMode):
        raise SelectionContractError(
            "selection.cutover.mode", "candidate selection cutover mode is invalid"
        )
    if mode is CandidateSelectionCutoverMode.LEGACY_HUMAN_FALLBACK:
        return CandidateSelectionRoute(
            source_candidate_decision_sha256=decision.decision_sha256,
            mode=mode,
            action_id=OpaqueId("select_edit_inputs"),
            actor_role=OpaqueId("human-editor"),
            selected_subjects=(),
            human_selection_required=True,
            reason_codes=("selection.cutover.legacy_fallback",),
        )
    if verification is None:
        raise SelectionContractError(
            "selection.cutover.verification_missing",
            "verified automation requires complete current CandidateDecision evidence",
        )
    verified = verify_candidate_decision(decision, verification)
    if verified.status is CandidateDecisionStatus.AUTO_SELECTED:
        selected = tuple(item.selected_subject for item in verified.shots)
        if any(item is None for item in selected):
            raise SelectionContractError(
                "selection.cutover.selection_missing",
                "auto-selected decision is missing an exact selected subject",
            )
        return CandidateSelectionRoute(
            source_candidate_decision_sha256=verified.decision_sha256,
            mode=mode,
            action_id=OpaqueId("assemble_selected_candidates"),
            actor_role=OpaqueId("automation-coordinator"),
            selected_subjects=tuple(item for item in selected if item is not None),
            human_selection_required=False,
            reason_codes=("selection.cutover.verified_auto_selected",),
        )
    reason_codes = tuple(
        sorted(
            {
                *verified.reason_codes,
                (
                    "selection.cutover.denied"
                    if verified.status is CandidateDecisionStatus.DENIED
                    else "selection.cutover.escalation_required"
                ),
            }
        )
    )
    return CandidateSelectionRoute(
        source_candidate_decision_sha256=verified.decision_sha256,
        mode=mode,
        action_id=OpaqueId(
            "resolve_candidate_selection_exception"
            if verified.status is CandidateDecisionStatus.DENIED
            else "select_edit_inputs"
        ),
        actor_role=OpaqueId("human-editor"),
        selected_subjects=(),
        human_selection_required=True,
        reason_codes=reason_codes,
    )


__all__ = [
    "CandidateSelectionCutoverMode",
    "CandidateSelectionRoute",
    "route_candidate_selection",
]
