"""W05 confidence-bound automatic candidate decisions."""

from .contracts import (
    CandidateDecision,
    CandidateDecisionStatus,
    CandidateDecisionVerificationInputs,
    CandidateOption,
    LegacyCandidateSelectionProjection,
    RankedCandidate,
    SelectionContractError,
    ShotCandidateDecision,
    ShotCandidateSet,
)
from .decision import (
    CANDIDATE_DECISION_VERSION,
    build_candidate_decision,
    candidate_decision_bytes_sha256,
    candidate_decision_to_mapping,
    validate_candidate_decision_structure,
    verify_candidate_decision,
)
from .projection import project_legacy_selection
from .serialization import (
    SelectionSerializationError,
    candidate_decision_from_bytes,
    candidate_decision_from_mapping,
    candidate_decision_to_bytes,
)

__all__ = [
    "CANDIDATE_DECISION_VERSION",
    "CandidateDecision",
    "CandidateDecisionStatus",
    "CandidateDecisionVerificationInputs",
    "CandidateOption",
    "LegacyCandidateSelectionProjection",
    "RankedCandidate",
    "SelectionContractError",
    "ShotCandidateDecision",
    "ShotCandidateSet",
    "build_candidate_decision",
    "candidate_decision_bytes_sha256",
    "candidate_decision_to_mapping",
    "validate_candidate_decision_structure",
    "verify_candidate_decision",
    "project_legacy_selection",
    "SelectionSerializationError",
    "candidate_decision_from_bytes",
    "candidate_decision_from_mapping",
    "candidate_decision_to_bytes",
]
