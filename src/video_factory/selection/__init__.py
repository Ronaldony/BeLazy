"""W05 confidence-bound automatic candidate decisions."""

from .contracts import (
    CandidateDecision,
    CandidateDecisionStatus,
    CandidateDecisionVerificationInputs,
    CandidateOption,
    CurrentCandidateConfidenceVerifier,
    LegacyCandidateSelectionProjection,
    RankedCandidate,
    SelectionContractError,
    ShotCandidateDecision,
    ShotCandidateSet,
)
from .decision import (
    AUTO_SELECT_CANDIDATES_ACTION_ID,
    AUTO_SELECT_CANDIDATES_CAPABILITY_ID,
    CANDIDATE_CONFIDENCE_RECEIPT_VERSION,
    CANDIDATE_DECISION_VERSION,
    build_candidate_decision,
    candidate_decision_bytes_sha256,
    candidate_decision_to_mapping,
    candidate_selection_input_sha256,
    validate_candidate_decision_structure,
    verify_candidate_decision,
)
from .projection import project_legacy_selection
from .cutover import (
    CandidateSelectionCutoverMode,
    CandidateSelectionRoute,
    route_candidate_selection,
)
from .serialization import (
    SelectionSerializationError,
    candidate_decision_from_bytes,
    candidate_decision_from_mapping,
    candidate_decision_to_bytes,
)

__all__ = [
    "AUTO_SELECT_CANDIDATES_ACTION_ID",
    "AUTO_SELECT_CANDIDATES_CAPABILITY_ID",
    "CANDIDATE_CONFIDENCE_RECEIPT_VERSION",
    "CANDIDATE_DECISION_VERSION",
    "CandidateDecision",
    "CandidateDecisionStatus",
    "CandidateDecisionVerificationInputs",
    "CandidateOption",
    "CandidateSelectionCutoverMode",
    "CandidateSelectionRoute",
    "CurrentCandidateConfidenceVerifier",
    "LegacyCandidateSelectionProjection",
    "RankedCandidate",
    "SelectionContractError",
    "ShotCandidateDecision",
    "ShotCandidateSet",
    "build_candidate_decision",
    "candidate_decision_bytes_sha256",
    "candidate_decision_to_mapping",
    "candidate_selection_input_sha256",
    "validate_candidate_decision_structure",
    "verify_candidate_decision",
    "project_legacy_selection",
    "route_candidate_selection",
    "SelectionSerializationError",
    "candidate_decision_from_bytes",
    "candidate_decision_from_mapping",
    "candidate_decision_to_bytes",
]
