from video_factory.artifacts import validate_artifact_mapping
from video_factory.domain import ArtifactReference, ArtifactVersion, HashDigest, RelativeArtifactPath
from video_factory.selection import project_legacy_selection
from tests.unit.test_candidate_decision import _decision


def test_automatic_decision_never_claims_legacy_human_selection() -> None:
    decision, *_ = _decision()
    packet = ArtifactReference(
        RelativeArtifactPath("generation/packet.json"),
        HashDigest("f" * 64),
        ArtifactVersion("generation-packet/2.1"),
    )
    projection = project_legacy_selection(
        decision,
        packet_ref=packet,
        rules_version="rules-v1",
    )
    assert projection.current_eligible is False
    assert projection.authority_effect == "none"
    assert projection.edit_manifest is None
    assert "selected_by_human" not in projection.candidate_ranking
    assert validate_artifact_mapping(projection.candidate_ranking).ok
    assert all(
        item["rough_cut_candidate"] is False
        for shot in projection.candidate_ranking["shots"]
        for item in shot["candidates"]
    )
