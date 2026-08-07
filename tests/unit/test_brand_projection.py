from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from video_factory.brand import (
    CatalogSourceFormat,
    EntityKind,
    FixedSentenceObservation,
    MarkdownBrandCatalog,
    MarkdownCatalogEntry,
    MarkdownProjectionRenderer,
    NonProductionEntityError,
    ProjectionSnapshot,
    RoundTripChecker,
    entity_from_mapping,
    entity_snapshot,
    rendered_snapshot,
    require_production_eligible,
)
from video_factory.domain import HashDigest, OpaqueId, RelativeArtifactPath


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _character():
    data = json.loads((FIXTURES / "brand_character_shadow.json").read_text(encoding="utf-8"))
    return entity_from_mapping(data)


def test_two_fixed_sentences_remain_separate_and_role_ordered() -> None:
    entity = _character()

    assert tuple(item.role for item in entity.fixed_sentences) == (
        "review-lock",
        "prompt-subject",
    )
    assert entity.fixed_sentences[0].consumers == ("documentation", "visual-review")
    assert entity.fixed_sentences[1].consumers == ("generation-prompt.subject",)
    assert entity.fixed_sentences[0].text != entity.fixed_sentences[1].text


def test_renderer_preserves_each_fixed_sentence_as_identical_utf8_bytes() -> None:
    entity = _character()
    rendered = MarkdownProjectionRenderer().render(entity)
    parsed = rendered_snapshot(rendered)

    assert len(parsed.fixed_sentences) == 2
    for original, projected in zip(entity.fixed_sentences, parsed.fixed_sentences, strict=True):
        assert projected.role == original.role
        assert projected.text.encode("utf-8") == original.text.encode("utf-8")
        assert projected.sha256 == original.sha256


def test_round_trip_reports_missing_order_and_byte_differences() -> None:
    expected = entity_snapshot(_character())
    shadow_with_missing_field = ProjectionSnapshot(
        fields=expected.fields[:-1],
        fixed_sentences=expected.fixed_sentences,
    )
    changed_fixed = replace(
        expected.fixed_sentences[0],
        text=expected.fixed_sentences[0].text + "!",
    )
    rendered_with_order_and_byte_changes = ProjectionSnapshot(
        fields=(expected.fields[1], expected.fields[0], *expected.fields[2:]),
        fixed_sentences=(changed_fixed, *expected.fixed_sentences[1:]),
    )

    report = RoundTripChecker().compare(
        expected,
        shadow_with_missing_field,
        rendered_with_order_and_byte_changes,
    )

    assert not report.passed
    assert any("missing_in_shadow" in item.issues for item in report.fields)
    assert not report.order.source_shadow_fields_match
    assert not report.order.shadow_rendered_fields_match
    assert "shadow_rendered_text_bytes_mismatch" in report.fixed_sentences[0].issues
    assert report.as_mapping()["passed"] is False


def test_shadow_is_not_production_input_and_markdown_catalog_reads_one_exact_source(
    tmp_path: Path,
) -> None:
    entity = _character()
    with pytest.raises(NonProductionEntityError):
        require_production_eligible(entity)

    payload = b"# Identity A\n\nSynthetic source.\n"
    source = tmp_path / "catalog" / "identity-a.md"
    source.parent.mkdir()
    source.write_bytes(payload)
    seen: list[bytes] = []

    def extract(markdown: bytes):
        seen.append(markdown)
        return entity.fixed_sentences

    entry = MarkdownCatalogEntry(
        entity_id=OpaqueId("identity-a"),
        entity_kind=EntityKind.CHARACTER,
        path=RelativeArtifactPath("catalog/identity-a.md"),
        sha256=HashDigest(hashlib.sha256(payload).hexdigest()),
        extractor=extract,
    )
    catalog = MarkdownBrandCatalog(tmp_path, [entry])

    loaded = catalog.get(OpaqueId("identity-a"))
    assert loaded.source_format is CatalogSourceFormat.MARKDOWN
    assert loaded.payload == payload
    assert loaded.fixed_sentences == entity.fixed_sentences
    assert seen == [payload]
    with pytest.raises(KeyError):
        catalog.get(OpaqueId("identity-b"))
