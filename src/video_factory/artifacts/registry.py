"""Artifact schema registry: map ``artifact_version`` → JSON Schema document.

File naming convention
----------------------
Schemas live in the core repository ``schemas/`` directory as
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
import json
from pathlib import Path
from typing import Any, Mapping

import video_factory

_ARTIFACT_VERSION_SEP = "/"


class ArtifactSchemaError(ValueError):
    """Raised when a schema cannot be loaded or a version is unregistered."""


@dataclass(frozen=True, slots=True)
class SchemaEntry:
    """One registered document schema."""

    artifact_version: str
    family: str
    filename: str
    path: Path
    schema: Mapping[str, Any]


def default_schemas_dir() -> Path:
    """Locate the core ``schemas/`` directory from a source checkout layout."""

    package_file = Path(video_factory.__file__).resolve()
    # src/video_factory/__init__.py → parents[2] is the repository root.
    candidate = package_file.parents[2] / "schemas"
    if candidate.is_dir():
        return candidate
    for parent in package_file.parents:
        schemas = parent / "schemas"
        if schemas.is_dir() and any(schemas.glob("*.schema.json")):
            return schemas
    raise ArtifactSchemaError(
        "could not locate core schemas/ directory relative to video_factory package"
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


class ArtifactSchemaRegistry:
    """In-memory map of ``artifact_version`` → loaded JSON Schema."""

    def __init__(self, schemas_dir: Path | None = None) -> None:
        self._schemas_dir = Path(schemas_dir) if schemas_dir is not None else default_schemas_dir()
        if not self._schemas_dir.is_dir():
            raise ArtifactSchemaError(f"schemas directory does not exist: {self._schemas_dir}")
        self._by_version: dict[str, SchemaEntry] = {}
        self._by_filename: dict[str, Mapping[str, Any]] = {}
        self._load()

    @property
    def schemas_dir(self) -> Path:
        return self._schemas_dir

    def _load(self) -> None:
        for path in sorted(self._schemas_dir.glob("*.schema.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ArtifactSchemaError(f"failed to load schema file {path.name}") from error
            if not isinstance(raw, dict):
                raise ArtifactSchemaError(f"schema top-level must be an object: {path.name}")
            self._by_filename[path.name] = raw
            version = _artifact_version_const(raw)
            if version is None:
                continue
            if version in self._by_version:
                existing = self._by_version[version]
                raise ArtifactSchemaError(
                    f"duplicate artifact_version {version!r} in "
                    f"{existing.filename} and {path.name}"
                )
            self._by_version[version] = SchemaEntry(
                artifact_version=version,
                family=_family_of(version),
                filename=path.name,
                path=path,
                schema=raw,
            )

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
        """Filename → schema mapping (includes shared definition files)."""

        return dict(self._by_filename)

    def schema_for_version(self, artifact_version: str) -> Mapping[str, Any]:
        return self.get(artifact_version).schema


_DEFAULT_REGISTRY: ArtifactSchemaRegistry | None = None


def get_default_registry() -> ArtifactSchemaRegistry:
    """Process-wide default registry (lazy, schemas/ from source checkout)."""

    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = ArtifactSchemaRegistry()
    return _DEFAULT_REGISTRY


def clear_default_registry() -> None:
    """Test helper: drop the cached default registry."""

    global _DEFAULT_REGISTRY
    _DEFAULT_REGISTRY = None
