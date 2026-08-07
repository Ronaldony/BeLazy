"""Packaged schema manifest and checkout-projection parity."""

from __future__ import annotations

import hashlib
from importlib.resources import files
from pathlib import Path
import shutil

import pytest

from video_factory.artifacts import ArtifactSchemaError, ArtifactSchemaRegistry
from video_factory.json_boundary import parse_json_bytes, require_json_object


RESOURCE_PACKAGE = "video_factory.resources.schemas"
NON_REGISTERED = {
    "artifact-common.schema.json",
    "config-layer.schema.json",
    "encode-command-plan.schema.json",
    "encode-request.schema.json",
}


def _resource_files() -> dict[str, bytes]:
    root = files(RESOURCE_PACKAGE)
    return {
        item.name: item.read_bytes()
        for item in root.iterdir()
        if item.is_file() and item.name.endswith(".schema.json")
    }


def test_packaged_manifest_loads_all_schemas_and_versions() -> None:
    registry = ArtifactSchemaRegistry()
    assert registry.manifest_validated is True
    assert registry.manifest_sha256 is not None
    assert len(registry.all_schemas()) == 52
    assert len(registry.list_versions()) == 48
    assert set(registry.all_schemas()) - {
        registry.get(version).filename for version in registry.list_versions()
    } == NON_REGISTERED
    manifest = registry.manifest
    assert manifest is not None
    assert manifest["schema_count"] == 52
    assert manifest["registered_version_count"] == 48


def test_root_schema_projection_matches_packaged_resources_byte_for_byte() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    packaged = _resource_files()
    root_paths = {
        path.name: path for path in (repository_root / "schemas").glob("*.schema.json")
    }
    assert set(packaged) == set(root_paths)
    for filename, payload in packaged.items():
        assert root_paths[filename].read_bytes() == payload


def test_manifest_entries_are_sorted_unique_and_digest_exact() -> None:
    resource_root = files(RESOURCE_PACKAGE)
    manifest_bytes = resource_root.joinpath("schema-manifest.json").read_bytes()
    manifest = require_json_object(parse_json_bytes(manifest_bytes))
    entries = manifest["schemas"]
    assert isinstance(entries, list)
    filenames = [entry["filename"] for entry in entries]
    assert filenames == sorted(filenames)
    assert len(filenames) == len(set(filenames)) == 52
    schema_ids = [entry["schema_id"] for entry in entries]
    assert len(schema_ids) == len(set(schema_ids)) == 52
    versions = [entry["artifact_version"] for entry in entries if entry["artifact_version"]]
    assert len(versions) == len(set(versions)) == 48
    for entry in entries:
        payload = resource_root.joinpath(entry["filename"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"]


def test_explicit_legacy_directory_without_manifest_remains_supported(
    tmp_path: Path,
) -> None:
    (tmp_path / "simple.schema.json").write_text(
        """{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://schemas.invalid/simple.schema.json",
  "type": "object",
  "properties": {"artifact_version": {"const": "simple/1.0"}}
}\n""",
        encoding="utf-8",
        newline="\n",
    )
    registry = ArtifactSchemaRegistry(tmp_path)
    assert registry.manifest_validated is False
    assert registry.list_versions() == ("simple/1.0",)
    assert registry.get("simple/1.0").path == tmp_path / "simple.schema.json"


def test_manifest_digest_tamper_fails_closed(tmp_path: Path) -> None:
    resource_root = files(RESOURCE_PACKAGE)
    for item in resource_root.iterdir():
        if item.is_file() and (
            item.name.endswith(".schema.json") or item.name == "schema-manifest.json"
        ):
            (tmp_path / item.name).write_bytes(item.read_bytes())
    target = tmp_path / "brief.schema.json"
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(ArtifactSchemaError, match="digest"):
        ArtifactSchemaRegistry(tmp_path)


def test_manifest_extra_resource_fails_closed(tmp_path: Path) -> None:
    resource_root = files(RESOURCE_PACKAGE)
    for item in resource_root.iterdir():
        if item.is_file() and (
            item.name.endswith(".schema.json") or item.name == "schema-manifest.json"
        ):
            shutil.copyfile(item, tmp_path / item.name)
    (tmp_path / "unexpected.schema.json").write_text(
        '{"$id":"https://schemas.invalid/unexpected.schema.json"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ArtifactSchemaError, match="mismatch"):
        ArtifactSchemaRegistry(tmp_path)
