"""Artifact schema registry: map ``artifact_version`` → JSON Schema document.

File naming convention
----------------------
Runtime schemas live in the installed ``video_factory.resources.schemas``
package and are verified against a deterministic manifest. The repository-root
``schemas/`` directory is a byte-identical compatibility projection. Files use
``{family}.schema.json`` where ``family`` is the left side of
``artifact_version`` (e.g. ``storyboard-review/1.0`` →
``storyboard-review.schema.json``). Shared ``$defs`` may live in
``artifact-common.schema.json`` (no ``artifact_version`` const; not
registered as a document kind).

Discovery
---------
Every ``*.schema.json`` whose top-level ``properties.artifact_version.const``
is a string of the form ``family/major.minor`` is registered under that
version key. Config-layer schemas are included when they follow the same
convention, so one registry covers config and production artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
import re
from typing import Any, Mapping

from video_factory.json_boundary import (
    JsonInputError,
    parse_json_bytes,
    require_json_object,
)

_ARTIFACT_VERSION_SEP = "/"
_RESOURCE_PACKAGE = "video_factory.resources.schemas"
_MANIFEST_FILENAME = "schema-manifest.json"
_MANIFEST_VERSION = "video-factory-schema-manifest/1.0"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ArtifactSchemaError(ValueError):
    """Raised when a schema cannot be loaded or a version is unregistered."""


@dataclass(frozen=True, slots=True)
class SchemaEntry:
    """One registered document schema."""

    artifact_version: str
    family: str
    filename: str
    path: Path | None
    schema: Mapping[str, Any]
    resource: Traversable | None = None


def default_schemas_resource() -> Traversable:
    """Return the import-system resource root containing packaged schemas."""

    try:
        root = files(_RESOURCE_PACKAGE)
    except (ImportError, ModuleNotFoundError) as error:
        raise ArtifactSchemaError("packaged schema resources are unavailable") from error
    if not root.is_dir():
        raise ArtifactSchemaError("packaged schema resource root is not a directory")
    return root


def default_schemas_dir() -> Path:
    """Compatibility path for unpacked installs; runtime uses resources directly."""

    root = default_schemas_resource()
    if isinstance(root, Path):
        return root
    raise ArtifactSchemaError(
        "packaged schemas are not backed by a physical directory; use resource APIs"
    )


def _artifact_version_const(schema: Mapping[str, Any]) -> str | None:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return None
    version_node = properties.get("artifact_version")
    if not isinstance(version_node, Mapping):
        return None
    const = version_node.get("const")
    if isinstance(const, str) and _ARTIFACT_VERSION_SEP in const:
        return const
    return None


def _family_of(artifact_version: str) -> str:
    family, _sep, rest = artifact_version.partition(_ARTIFACT_VERSION_SEP)
    if not family or not rest:
        raise ArtifactSchemaError(
            f"invalid artifact_version {artifact_version!r}; expected family/major.minor"
        )
    return family


def _read_bytes(resource: Traversable, label: str) -> bytes:
    try:
        return resource.read_bytes()
    except OSError as error:
        raise ArtifactSchemaError(f"failed to read schema resource {label}") from error


class ArtifactSchemaRegistry:
    """Manifest-verified map of ``artifact_version`` to packaged JSON Schema."""

    def __init__(self, schemas_dir: Path | None = None) -> None:
        if schemas_dir is None:
            self._resource_root = default_schemas_resource()
            self._schemas_dir = (
                self._resource_root if isinstance(self._resource_root, Path) else None
            )
            self._manifest_required = True
        else:
            explicit = Path(schemas_dir)
            if not explicit.is_dir():
                raise ArtifactSchemaError(f"schemas directory does not exist: {explicit}")
            self._resource_root = explicit
            self._schemas_dir = explicit
            self._manifest_required = False
        self._by_version: dict[str, SchemaEntry] = {}
        self._by_filename: dict[str, Mapping[str, Any]] = {}
        self._manifest: Mapping[str, Any] | None = None
        self._manifest_sha256: str | None = None
        self._load()

    @property
    def schemas_dir(self) -> Path | None:
        return self._schemas_dir

    @property
    def resource_root(self) -> Traversable:
        return self._resource_root

    @property
    def manifest(self) -> Mapping[str, Any] | None:
        return self._manifest

    @property
    def manifest_sha256(self) -> str | None:
        return self._manifest_sha256

    @property
    def manifest_validated(self) -> bool:
        return self._manifest is not None

    def _schema_resources(self) -> dict[str, Traversable]:
        resources = {
            child.name: child
            for child in self._resource_root.iterdir()
            if child.is_file() and child.name.endswith(".schema.json")
        }
        if not resources:
            raise ArtifactSchemaError("schema resource root contains no schemas")
        return resources

    def _load_manifest(
        self,
        resources: Mapping[str, Traversable],
    ) -> tuple[Mapping[str, object], ...] | None:
        manifest_resource = self._resource_root.joinpath(_MANIFEST_FILENAME)
        if not manifest_resource.is_file():
            if self._manifest_required:
                raise ArtifactSchemaError("packaged schema manifest is missing")
            return None
        manifest_bytes = _read_bytes(manifest_resource, _MANIFEST_FILENAME)
        try:
            manifest = require_json_object(
                parse_json_bytes(manifest_bytes, source=_MANIFEST_FILENAME),
                source=_MANIFEST_FILENAME,
            )
        except JsonInputError as error:
            raise ArtifactSchemaError(f"invalid schema manifest: {error}") from error
        if manifest.get("manifest_version") != _MANIFEST_VERSION:
            raise ArtifactSchemaError("schema manifest version is unsupported")
        raw_entries = manifest.get("schemas")
        if not isinstance(raw_entries, list) or not all(
            isinstance(entry, Mapping) for entry in raw_entries
        ):
            raise ArtifactSchemaError("schema manifest schemas must be an array of objects")
        entries = tuple(raw_entries)
        if manifest.get("schema_count") != len(entries):
            raise ArtifactSchemaError("schema manifest schema_count mismatch")
        filenames: list[str] = []
        schema_ids: list[str] = []
        versions: list[str] = []
        for entry in entries:
            if set(entry) != {"filename", "sha256", "schema_id", "artifact_version"}:
                raise ArtifactSchemaError("schema manifest entry has unexpected fields")
            filename = entry.get("filename")
            digest = entry.get("sha256")
            schema_id = entry.get("schema_id")
            version = entry.get("artifact_version")
            if (
                not isinstance(filename, str)
                or Path(filename).name != filename
                or not filename.endswith(".schema.json")
            ):
                raise ArtifactSchemaError("schema manifest contains an unsafe filename")
            if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
                raise ArtifactSchemaError(f"schema manifest digest is invalid: {filename}")
            if not isinstance(schema_id, str) or not schema_id:
                raise ArtifactSchemaError(f"schema manifest $id is invalid: {filename}")
            if version is not None and not isinstance(version, str):
                raise ArtifactSchemaError(
                    f"schema manifest artifact_version is invalid: {filename}"
                )
            filenames.append(filename)
            schema_ids.append(schema_id)
            if isinstance(version, str):
                versions.append(version)
        if len(filenames) != len(set(filenames)):
            raise ArtifactSchemaError("schema manifest contains duplicate filenames")
        if len(schema_ids) != len(set(schema_ids)):
            raise ArtifactSchemaError("schema manifest contains duplicate schema IDs")
        if len(versions) != len(set(versions)):
            raise ArtifactSchemaError("schema manifest contains duplicate artifact versions")
        if set(filenames) != set(resources):
            missing = sorted(set(filenames) - set(resources))
            extra = sorted(set(resources) - set(filenames))
            raise ArtifactSchemaError(
                f"schema manifest/resource mismatch; missing={missing}, extra={extra}"
            )
        if manifest.get("registered_version_count") != len(versions):
            raise ArtifactSchemaError("schema manifest registered_version_count mismatch")
        self._manifest = manifest
        self._manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        return entries

    def _add_schema(
        self,
        resource: Traversable,
        *,
        expected: Mapping[str, object] | None,
    ) -> None:
        filename = resource.name
        payload = _read_bytes(resource, filename)
        digest = hashlib.sha256(payload).hexdigest()
        try:
            raw = require_json_object(
                parse_json_bytes(payload, source=filename), source=filename
            )
        except JsonInputError as error:
            raise ArtifactSchemaError(f"failed to load schema file {filename}: {error}") from error
        version = _artifact_version_const(raw)
        schema_id = raw.get("$id")
        if not isinstance(schema_id, str) or not schema_id:
            raise ArtifactSchemaError(f"schema has no non-empty $id: {filename}")
        if expected is not None:
            if digest != expected["sha256"]:
                raise ArtifactSchemaError(f"schema digest does not match manifest: {filename}")
            if schema_id != expected["schema_id"]:
                raise ArtifactSchemaError(f"schema $id does not match manifest: {filename}")
            if version != expected["artifact_version"]:
                raise ArtifactSchemaError(
                    f"schema artifact_version does not match manifest: {filename}"
                )
        self._by_filename[filename] = raw
        if version is None:
            return
        if version in self._by_version:
            existing = self._by_version[version]
            raise ArtifactSchemaError(
                f"duplicate artifact_version {version!r} in "
                f"{existing.filename} and {filename}"
            )
        self._by_version[version] = SchemaEntry(
            artifact_version=version,
            family=_family_of(version),
            filename=filename,
            path=(self._schemas_dir / filename if self._schemas_dir is not None else None),
            schema=raw,
            resource=resource,
        )

    def _load(self) -> None:
        resources = self._schema_resources()
        manifest_entries = self._load_manifest(resources)
        expected_by_filename = (
            {str(entry["filename"]): entry for entry in manifest_entries}
            if manifest_entries is not None
            else {}
        )
        for filename in sorted(resources):
            self._add_schema(
                resources[filename],
                expected=expected_by_filename.get(filename),
            )
        ids = [schema["$id"] for schema in self._by_filename.values()]
        if len(ids) != len(set(ids)):
            raise ArtifactSchemaError("schema resources contain duplicate $id values")

    def list_versions(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_version))

    def get(self, artifact_version: str) -> SchemaEntry:
        try:
            return self._by_version[artifact_version]
        except KeyError as error:
            known = ", ".join(self.list_versions()) or "(none)"
            raise ArtifactSchemaError(
                f"unregistered artifact_version {artifact_version!r}; "
                f"registered versions: {known}"
            ) from error

    def has(self, artifact_version: str) -> bool:
        return artifact_version in self._by_version

    def all_schemas(self) -> Mapping[str, Mapping[str, Any]]:
        """Filename to schema mapping (includes shared definition files)."""

        return dict(self._by_filename)

    def schema_for_version(self, artifact_version: str) -> Mapping[str, Any]:
        return self.get(artifact_version).schema


_DEFAULT_REGISTRY: ArtifactSchemaRegistry | None = None


def get_default_registry() -> ArtifactSchemaRegistry:
    """Process-wide lazy registry backed by installed package resources."""

    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = ArtifactSchemaRegistry()
    return _DEFAULT_REGISTRY


def clear_default_registry() -> None:
    """Test helper: drop the cached default registry."""

    global _DEFAULT_REGISTRY
    _DEFAULT_REGISTRY = None
