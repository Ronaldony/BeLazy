from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path

import pytest

from tests.unit.test_artifact_schemas import _valid_brief
from tests.unit.test_director_blueprint import _blueprint_fixture
from video_factory.blueprint import (
    BlueprintProjectionKind,
    blueprint_artifact_to_bytes,
    blueprint_projection_artifact_sha256,
    production_blueprint_artifact_sha256,
    project_all_blueprint_views,
)
from video_factory.config.canonical import canonical_json_bytes, canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.runtime import (
    MigrationMode,
    build_projection_parity_receipt,
    projection_parity_receipt_to_mapping,
)
from video_factory_runtime import (
    MIGRATION_ACTIVATION_RECORD_VERSION,
    MIGRATION_ROLLBACK_RECORD_VERSION,
    DurableReadOnlyMigrationRuntime,
    FixturePinnedParityVerifier,
    FixtureRuntimeBoundary,
    MigrationApprovalVerification,
    MigrationPolicy,
    MigrationRuntimeError,
    PinnedParityCase,
)


NOW = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
POLICY_SHA = HashDigest("7" * 64)
FEATURE_SHA = HashDigest("8" * 64)
CONSUMER = OpaqueId("fixture-brief-reader")
VIEW = OpaqueId("brief")
MIGRATION_ID = OpaqueId("migration-fixture-brief")


class ApprovalVerifier:
    def __init__(self) -> None:
        self.approved = True
        self.calls = []

    def verify_current(self, request):
        self.calls.append(request)
        if not self.approved:
            return None
        version = (
            MIGRATION_ROLLBACK_RECORD_VERSION
            if request.target_mode is MigrationMode.ROLLED_BACK
            else MIGRATION_ACTIVATION_RECORD_VERSION
        )
        return MigrationApprovalVerification(
            request_sha256=request.request_sha256,
            record=ArtifactReference(
                RelativeArtifactPath(
                    f"runtime-migration/approval-{request.generation}.json"
                ),
                request.request_sha256,
                ArtifactVersion(version),
            ),
            approved=True,
        )


def _fixture(tmp_path: Path):
    boundary = FixtureRuntimeBoundary.initialize(
        tmp_path / "runtime",
        runtime_id="migration-runtime-test",
        fixture_only=True,
    )
    _, _, _, _, _, _, blueprint = _blueprint_fixture()
    source_ref = ArtifactReference(
        RelativeArtifactPath("artifacts/production-blueprint.json"),
        production_blueprint_artifact_sha256(blueprint),
        ArtifactVersion("production-blueprint/1.0"),
    )
    projection = next(
        item
        for item in project_all_blueprint_views(
            blueprint, source_blueprint_ref=source_ref
        )
        if item.view_kind is BlueprintProjectionKind.BRIEF
    )
    projection_document = blueprint_artifact_to_bytes(projection)
    projection_ref = ArtifactReference(
        RelativeArtifactPath("shadow/brief.json"),
        blueprint_projection_artifact_sha256(projection),
        ArtifactVersion("blueprint-projection/1.0"),
    )
    legacy_mapping = _valid_brief()
    legacy_mapping["episode_id"] = "episode-demo"
    legacy_document = canonical_json_bytes(legacy_mapping)
    legacy_ref = ArtifactReference(
        RelativeArtifactPath("legacy/brief.json"),
        HashDigest(hashlib.sha256(legacy_document).hexdigest()),
        ArtifactVersion("brief/1.0"),
    )
    normalized = canonical_sha256(
        {
            "fixture_review": "brief-semantic-parity",
            "legacy_sha256": str(legacy_ref.sha256),
            "projection_sha256": str(projection_ref.sha256),
        }
    )
    parity_verifier = FixturePinnedParityVerifier(
        boundary,
        cases=(
            PinnedParityCase(
                consumer_id=CONSUMER,
                view_kind=VIEW,
                legacy_artifact_sha256=legacy_ref.sha256,
                projection_artifact_sha256=projection_ref.sha256,
                policy_bundle_sha256=POLICY_SHA,
                legacy_normalized_sha256=normalized,
                projection_normalized_sha256=normalized,
                difference_count=0,
            ),
        ),
    )
    approval = ApprovalVerifier()
    policy = MigrationPolicy(
        policy_bundle_sha256=POLICY_SHA,
        max_parity_age=timedelta(hours=1),
        allowed_consumers=((CONSUMER, VIEW),),
    )
    runtime = DurableReadOnlyMigrationRuntime(
        boundary,
        parity_verifier=parity_verifier,
        approval_verifier=approval,
        policy=policy,
    )
    return (
        boundary,
        runtime,
        approval,
        policy,
        projection,
        projection_document,
        projection_ref,
        legacy_document,
        legacy_ref,
        parity_verifier,
    )


def test_exact_pinned_pair_enables_only_a_read_only_reversible_projection(
    tmp_path: Path,
) -> None:
    (
        boundary,
        runtime,
        approval,
        policy,
        projection,
        projection_document,
        projection_ref,
        legacy_document,
        legacy_ref,
        parity_verifier,
    ) = _fixture(tmp_path)
    receipt, receipt_ref = runtime.verify_pair(
        consumer_id=CONSUMER,
        view_kind=VIEW,
        legacy_artifact=legacy_ref,
        legacy_document=legacy_document,
        projection=projection,
        projection_artifact=projection_ref,
        projection_document=projection_document,
        evaluated_at=NOW,
    )
    legacy = runtime.initialize_legacy(
        migration_id=MIGRATION_ID,
        consumer_id=CONSUMER,
        view_kind=VIEW,
        feature_flag_sha256=FEATURE_SHA,
        evaluated_at=NOW,
    )
    dual = runtime.transition(
        migration_id=MIGRATION_ID,
        target_mode=MigrationMode.DUAL_READ_COMPARE,
        feature_flag_sha256=FEATURE_SHA,
        evaluated_at=NOW + timedelta(seconds=1),
    )
    projected = runtime.transition(
        migration_id=MIGRATION_ID,
        target_mode=MigrationMode.PROJECTION_READ_ONLY,
        feature_flag_sha256=FEATURE_SHA,
        parity_receipts=((receipt, receipt_ref),),
        evaluated_at=NOW + timedelta(seconds=2),
    )

    selection = runtime.select_read_only(
        projected,
        legacy_artifact=legacy_ref,
        projection_artifact=projection_ref,
        parity_receipt=receipt,
        parity_receipt_reference=receipt_ref,
    )
    assert selection.selected_artifact == projection_ref
    assert selection.selected_source == "projection_read_only"
    assert selection.read_only is True
    assert selection.authority_effect == "none"
    assert projected.projections_are_authority is False
    assert [state.mode for state in runtime.history(MIGRATION_ID)] == [
        MigrationMode.LEGACY_ONLY,
        MigrationMode.DUAL_READ_COMPARE,
        MigrationMode.PROJECTION_READ_ONLY,
    ]

    reopened = DurableReadOnlyMigrationRuntime(
        FixtureRuntimeBoundary.open(
            boundary.root, expected_runtime_id="migration-runtime-test"
        ),
        parity_verifier=parity_verifier,
        approval_verifier=approval,
        policy=policy,
    )
    assert reopened.load(MIGRATION_ID) == projected
    rolled_back = reopened.transition(
        migration_id=MIGRATION_ID,
        target_mode=MigrationMode.ROLLED_BACK,
        feature_flag_sha256=HashDigest("9" * 64),
        evaluated_at=NOW + timedelta(seconds=3),
    )
    fallback = reopened.select_read_only(
        rolled_back,
        legacy_artifact=legacy_ref,
        projection_artifact=projection_ref,
        parity_receipt=receipt,
        parity_receipt_reference=receipt_ref,
    )
    assert fallback.selected_artifact == legacy_ref
    assert fallback.selected_source == "legacy_rollback"
    with pytest.raises(MigrationRuntimeError) as rebound:
        reopened.select_read_only(
            rolled_back,
            legacy_artifact=replace(legacy_ref, sha256=HashDigest("f" * 64)),
            projection_artifact=projection_ref,
            parity_receipt=receipt,
            parity_receipt_reference=receipt_ref,
        )
    assert rebound.value.reason_code == "runtime.migration.rollback_target_rebound"
    assert rolled_back.rollback_record is not None
    with pytest.raises(MigrationRuntimeError) as caught:
        reopened.select_read_only(
            projected,
            legacy_artifact=legacy_ref,
            projection_artifact=projection_ref,
            parity_receipt=receipt,
            parity_receipt_reference=receipt_ref,
        )
    assert caught.value.reason_code == "runtime.migration.state_stale"
    assert {call.target_mode for call in approval.calls} >= {
        MigrationMode.LEGACY_ONLY,
        MigrationMode.DUAL_READ_COMPARE,
        MigrationMode.PROJECTION_READ_ONLY,
        MigrationMode.ROLLED_BACK,
    }
    assert legacy.authority_effect == dual.authority_effect == "none"


def test_unseen_or_rebound_pair_never_creates_parity_evidence(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    runtime = fixture[1]
    projection = fixture[4]
    projection_document = fixture[5]
    projection_ref = fixture[6]
    legacy_document = fixture[7]
    legacy_ref = fixture[8]

    with pytest.raises(MigrationRuntimeError) as caught:
        runtime.verify_pair(
            consumer_id=CONSUMER,
            view_kind=VIEW,
            legacy_artifact=replace(legacy_ref, sha256=HashDigest("a" * 64)),
            legacy_document=legacy_document,
            projection=projection,
            projection_artifact=projection_ref,
            projection_document=projection_document,
            evaluated_at=NOW,
        )
    assert caught.value.reason_code == "runtime.migration.legacy_rebound"

    changed = dict(_valid_brief())
    changed["episode_id"] = "episode-demo"
    changed["summary"] = "not the pinned semantic pair"
    changed_bytes = canonical_json_bytes(changed)
    changed_ref = replace(
        legacy_ref,
        sha256=HashDigest(hashlib.sha256(changed_bytes).hexdigest()),
    )
    with pytest.raises(MigrationRuntimeError) as caught:
        runtime.verify_pair(
            consumer_id=CONSUMER,
            view_kind=VIEW,
            legacy_artifact=changed_ref,
            legacy_document=changed_bytes,
            projection=projection,
            projection_artifact=projection_ref,
            projection_document=projection_document,
            evaluated_at=NOW,
        )
    assert caught.value.reason_code == "runtime.migration.parity_unverified"


def test_activation_requires_fresh_exact_parity_and_separate_approval(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    runtime, approval = fixture[1], fixture[2]
    projection, projection_document, projection_ref = fixture[4:7]
    legacy_document, legacy_ref = fixture[7:9]
    receipt, receipt_ref = runtime.verify_pair(
        consumer_id=CONSUMER,
        view_kind=VIEW,
        legacy_artifact=legacy_ref,
        legacy_document=legacy_document,
        projection=projection,
        projection_artifact=projection_ref,
        projection_document=projection_document,
        evaluated_at=NOW,
    )
    runtime.initialize_legacy(
        migration_id=MIGRATION_ID,
        consumer_id=CONSUMER,
        view_kind=VIEW,
        feature_flag_sha256=FEATURE_SHA,
        evaluated_at=NOW,
    )
    runtime.transition(
        migration_id=MIGRATION_ID,
        target_mode=MigrationMode.DUAL_READ_COMPARE,
        feature_flag_sha256=FEATURE_SHA,
        evaluated_at=NOW,
    )

    with pytest.raises(MigrationRuntimeError) as caught:
        runtime.transition(
            migration_id=MIGRATION_ID,
            target_mode=MigrationMode.PROJECTION_READ_ONLY,
            feature_flag_sha256=FEATURE_SHA,
            evaluated_at=NOW + timedelta(seconds=1),
        )
    assert caught.value.reason_code == "runtime.migration.parity_missing"

    with pytest.raises(MigrationRuntimeError) as caught:
        runtime.transition(
            migration_id=MIGRATION_ID,
            target_mode=MigrationMode.PROJECTION_READ_ONLY,
            feature_flag_sha256=FEATURE_SHA,
            parity_receipts=((receipt, receipt_ref),),
            evaluated_at=NOW + timedelta(hours=2),
        )
    assert caught.value.reason_code == "runtime.migration.parity_stale"

    approval.approved = False
    with pytest.raises(MigrationRuntimeError) as caught:
        runtime.transition(
            migration_id=MIGRATION_ID,
            target_mode=MigrationMode.PROJECTION_READ_ONLY,
            feature_flag_sha256=FEATURE_SHA,
            parity_receipts=((receipt, receipt_ref),),
            evaluated_at=NOW + timedelta(seconds=1),
        )
    assert caught.value.reason_code == "runtime.migration.approval"
    with pytest.raises(MigrationRuntimeError) as caught:
        runtime.load(MIGRATION_ID)
    assert caught.value.reason_code == "runtime.migration.approval"
    approval.approved = True
    assert runtime.load(MIGRATION_ID).mode is MigrationMode.DUAL_READ_COMPARE


def test_activation_rejects_caller_minted_unregistered_parity_receipt(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    runtime = fixture[1]
    projection, _, projection_ref = fixture[4:7]
    _, legacy_ref = fixture[7:9]
    minted = build_projection_parity_receipt(
        consumer_id=CONSUMER,
        view_kind=VIEW,
        legacy_artifact=legacy_ref,
        projection_artifact=projection_ref,
        source_blueprint_sha256=projection.source_blueprint_sha256,
        projection_sha256=projection.projection_sha256,
        legacy_normalized_sha256=HashDigest("a" * 64),
        projection_normalized_sha256=HashDigest("a" * 64),
        normalizer_id=OpaqueId("caller-normalizer"),
        normalizer_version="1.0",
        normalizer_sha256=HashDigest("b" * 64),
        verifier_record=ArtifactReference(
            RelativeArtifactPath("runtime-migration/caller-verifier.json"),
            HashDigest("c" * 64),
            ArtifactVersion("projection-parity-verification/1.0"),
        ),
        policy_bundle_sha256=POLICY_SHA,
        evaluated_at=NOW.isoformat(),
        parity_pass=True,
        difference_count=0,
        authority_effect="none",
    )
    minted_ref = ArtifactReference(
        RelativeArtifactPath(f"runtime-migration/{minted.receipt_id}.json"),
        HashDigest(
            hashlib.sha256(
                canonical_json_bytes(projection_parity_receipt_to_mapping(minted))
            ).hexdigest()
        ),
        ArtifactVersion(minted.artifact_version),
    )
    runtime.initialize_legacy(
        migration_id=MIGRATION_ID,
        consumer_id=CONSUMER,
        view_kind=VIEW,
        feature_flag_sha256=FEATURE_SHA,
        evaluated_at=NOW,
    )
    runtime.transition(
        migration_id=MIGRATION_ID,
        target_mode=MigrationMode.DUAL_READ_COMPARE,
        feature_flag_sha256=FEATURE_SHA,
        evaluated_at=NOW,
    )

    with pytest.raises(MigrationRuntimeError) as caught:
        runtime.transition(
            migration_id=MIGRATION_ID,
            target_mode=MigrationMode.PROJECTION_READ_ONLY,
            feature_flag_sha256=FEATURE_SHA,
            parity_receipts=((minted, minted_ref),),
            evaluated_at=NOW + timedelta(seconds=1),
        )

    assert caught.value.reason_code == "runtime.migration.parity_unregistered"


def test_projection_selection_rejects_receipt_rebound(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    runtime = fixture[1]
    projection, projection_document, projection_ref = fixture[4:7]
    legacy_document, legacy_ref = fixture[7:9]
    receipt, receipt_ref = runtime.verify_pair(
        consumer_id=CONSUMER,
        view_kind=VIEW,
        legacy_artifact=legacy_ref,
        legacy_document=legacy_document,
        projection=projection,
        projection_artifact=projection_ref,
        projection_document=projection_document,
        evaluated_at=NOW,
    )
    runtime.initialize_legacy(
        migration_id=MIGRATION_ID,
        consumer_id=CONSUMER,
        view_kind=VIEW,
        feature_flag_sha256=FEATURE_SHA,
        evaluated_at=NOW,
    )
    runtime.transition(
        migration_id=MIGRATION_ID,
        target_mode=MigrationMode.DUAL_READ_COMPARE,
        feature_flag_sha256=FEATURE_SHA,
        evaluated_at=NOW,
    )
    state = runtime.transition(
        migration_id=MIGRATION_ID,
        target_mode=MigrationMode.PROJECTION_READ_ONLY,
        feature_flag_sha256=FEATURE_SHA,
        parity_receipts=((receipt, receipt_ref),),
        evaluated_at=NOW + timedelta(seconds=1),
    )

    with pytest.raises(MigrationRuntimeError) as caught:
        runtime.select_read_only(
            state,
            legacy_artifact=legacy_ref,
            projection_artifact=projection_ref,
            parity_receipt=receipt,
            parity_receipt_reference=replace(
                receipt_ref, sha256=HashDigest("f" * 64)
            ),
        )
    assert caught.value.reason_code == "runtime.migration.selection_rebound"
