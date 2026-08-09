"""Read-only legacy projection for W05 automatic decisions.

The projection intentionally emits no ``edit-manifest/1.0`` because that
legacy contract means a human made the selection.  The candidate ranking is a
diagnostic sort-only view and is never current/authorizing evidence.
"""

from __future__ import annotations

from video_factory.artifacts import validate_artifact_mapping
from video_factory.domain import ArtifactReference
from video_factory.quality.validation import reference_to_mapping, require_reference, require_token

from .contracts import CandidateDecision, LegacyCandidateSelectionProjection, SelectionContractError
from .decision import validate_candidate_decision_structure


def project_legacy_selection(
    decision: CandidateDecision,
    *,
    packet_ref: ArtifactReference,
    rules_version: str,
) -> LegacyCandidateSelectionProjection:
    validate_candidate_decision_structure(decision)
    require_reference(packet_ref, "packet_ref")
    require_token(rules_version, "rules_version")
    ranking = {
        "artifact_version": "candidate-ranking/1.0",
        "rules_version": rules_version,
        "episode_id": str(decision.episode_id),
        "generated_at": decision.evaluated_at,
        "packet_ref": reference_to_mapping(packet_ref),
        "ranking_policy": [
            "diagnostic-only W05 CandidateDecision projection",
            "does not create or represent human edit selection",
        ],
        "shots": [
            {
                "shot_id": str(shot.shot_id),
                "candidates": [
                    {
                        "rank": rank,
                        "file": str(candidate.subject.reference.path),
                        "adapter_id": str(candidate.adapter_id),
                        "qc_ref": {
                            "path": str(
                                candidate.subject.observation_receipt_ref.path
                            ),
                            "sha256": str(
                                candidate.subject.observation_receipt_ref.sha256
                            ),
                        },
                        "media_sha256": str(candidate.subject.reference.sha256),
                        "metrics": {
                            "quality_score_bps": candidate.quality_score_bps,
                            "confidence_bps": candidate.confidence_bps,
                        },
                        "rough_cut_candidate": False,
                        "notes": "shadow only; see candidate-decision/1.0",
                    }
                    for rank, candidate in enumerate(
                        shot.ranked_candidates,
                        start=1,
                    )
                ],
            }
            for shot in decision.shots
        ],
        "notes": (
            "read_only=true; current_eligible=false; authority_effect=none; "
            f"source_candidate_decision_sha256={decision.decision_sha256}"
        ),
    }
    result = validate_artifact_mapping(ranking)
    if not result.ok:
        raise SelectionContractError(
            "selection.legacy_projection.schema",
            "; ".join(result.error_texts),
        )
    return LegacyCandidateSelectionProjection(
        source_candidate_decision_sha256=decision.decision_sha256,
        candidate_ranking=ranking,
        edit_manifest=None,
        current_eligible=False,
        authority_effect="none",
    )


__all__ = ["project_legacy_selection"]
