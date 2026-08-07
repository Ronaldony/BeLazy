from __future__ import annotations

from pathlib import Path
import tomllib

import video_factory
from video_factory import (
    analytics,
    approvals,
    artifacts,
    blueprint,
    brand,
    cli,
    config,
    continuity,
    distribution,
    domain,
    directors,
    encode,
    engine,
    feasibility,
    lint,
    media,
    mutation,
    policy,
    providers,
    qc,
    review,
    security,
    sheets,
    storage,
)

DOCUMENTED_PUBLIC_PACKAGES = {
    "video_factory.analytics",
    "video_factory.approvals",
    "video_factory.artifacts",
    "video_factory.blueprint",
    "video_factory.brand",
    "video_factory.cli",
    "video_factory.config",
    "video_factory.continuity",
    "video_factory.distribution",
    "video_factory.domain",
    "video_factory.directors",
    "video_factory.encode",
    "video_factory.engine",
    "video_factory.feasibility",
    "video_factory.lint",
    "video_factory.media",
    "video_factory.mutation",
    "video_factory.policy",
    "video_factory.providers",
    "video_factory.qc",
    "video_factory.review",
    "video_factory.security",
    "video_factory.sheets",
    "video_factory.storage",
}

ROOT = Path(__file__).resolve().parents[2]


def test_current_contract_identity_and_public_packages() -> None:
    assert video_factory.__version__ == "0.3.1"
    assert video_factory.CORE_CONTRACT_VERSION == "0.3"
    imported_public_packages = {
        module.__name__
        for module in (
            analytics,
            approvals,
            artifacts,
            blueprint,
            brand,
            cli,
            config,
            continuity,
            distribution,
            domain,
            directors,
            encode,
            engine,
            feasibility,
            lint,
            media,
            mutation,
            policy,
            providers,
            qc,
            review,
            security,
            sheets,
            storage,
        )
    }
    assert imported_public_packages == DOCUMENTED_PUBLIC_PACKAGES
    assert all(
        name.startswith("video_factory.")
        for name in imported_public_packages
    )
    assert len(cli.list_commands()) == 17


def test_patch_release_metadata_and_severity_documentation_are_aligned() -> None:
    pyproject = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert pyproject["project"]["version"] == "0.3.1"

    contracts = (ROOT / "CONTRACTS.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for document in (contracts, readme):
        assert "INFO" in document
        assert "does not lower" in document
        assert "WARNING" in document
        assert "WARN" in document
        assert "ERROR" in document
        assert "FAIL" in document

    assert "video-production-core==0.3.1" in readme
    assert "## 0.3.1 - 2026-07-30" in changelog
    assert "WARN" in changelog
    assert "PASS" in changelog
