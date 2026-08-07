"""JSON Schema validation runner for production artifact documents.

Uses the already-available ``jsonschema`` package (Draft 2020-12) with a
local ``referencing`` registry so ``$ref`` never touches the network.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource

from .registry import (
    ArtifactSchemaError,
    ArtifactSchemaRegistry,
    get_default_registry,
)


class ArtifactValidationError(ValueError):
    """Raised for engine-level failures (missing package, bad JSON, etc.)."""


@dataclass(frozen=True, slots=True)
class FieldError:
    """One validation failure with a JSON-pointer-like path."""

    path: str
    validator: str
    message: str

    def as_text(self) -> str:
        return f"{self.path}: validator={self.validator}; {self.message}"


@dataclass(frozen=True, slots=True)
class ArtifactValidationResult:
    """Structured result for one document."""

    ok: bool
    artifact_version: str | None
    errors: tuple[FieldError, ...]
    source: str | None = None

    @property
    def error_texts(self) -> tuple[str, ...]:
        return tuple(error.as_text() for error in self.errors)


@dataclass(frozen=True, slots=True)
class FileValidationResult:
    """One file result inside a batch run."""

    path: str
    result: ArtifactValidationResult


@dataclass(frozen=True, slots=True)
class BatchValidationReport:
    """Aggregate for a directory of artifact JSON files."""

    total: int
    passed: int
    failed: int
    skipped: int
    results: tuple[FileValidationResult, ...]


def _json_pointer(path: Sequence[Any]) -> str:
    location = "$"
    for part in path:
        if isinstance(part, int):
            location += f"[{part}]"
        else:
            location += f".{part}"
    return location


def _build_referencing_registry(
    schemas: Mapping[str, Mapping[str, Any]],
) -> Registry:
    resources: list[tuple[str, Resource]] = []
    for schema in schemas.values():
        schema_id = schema.get("$id")
        if isinstance(schema_id, str) and schema_id:
            resources.append((schema_id, Resource.from_contents(dict(schema))))
    return Registry().with_resources(resources)


def _validator_for(
    entry_schema: Mapping[str, Any],
    all_schemas: Mapping[str, Mapping[str, Any]],
) -> Draft202012Validator:
    try:
        Draft202012Validator.check_schema(dict(entry_schema))
    except SchemaError as error:
        raise ArtifactValidationError(
            f"schema document is not a valid JSON Schema: {error.message}"
        ) from error
    registry = _build_referencing_registry(all_schemas)
    return Draft202012Validator(dict(entry_schema), registry=registry)


def _coerce_mapping(
    document: Mapping[str, object] | bytes | str | Path,
) -> tuple[Mapping[str, object], str | None]:
    source: str | None = None
    if isinstance(document, Path):
        source = str(document)
        try:
            raw = document.read_text(encoding="utf-8")
        except OSError as error:
            raise ArtifactValidationError(f"cannot read artifact file: {document}") from error
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ArtifactValidationError(f"invalid JSON in {document}: {error}") from error
    elif isinstance(document, (bytes, bytearray)):
        try:
            parsed = json.loads(document.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactValidationError(f"invalid JSON bytes: {error}") from error
    elif isinstance(document, str):
        # Ambiguous: treat as path if it exists, else JSON text.
        as_path = Path(document)
        if as_path.is_file():
            return _coerce_mapping(as_path)
        try:
            parsed = json.loads(document)
        except json.JSONDecodeError as error:
            raise ArtifactValidationError(f"invalid JSON text: {error}") from error
    elif isinstance(document, Mapping):
        parsed = document
    else:
        raise ArtifactValidationError(
            "document must be a mapping, JSON bytes/text, or filesystem path"
        )

    if not isinstance(parsed, Mapping) or not all(isinstance(key, str) for key in parsed):
        raise ArtifactValidationError("artifact document top-level must be a JSON object")
    return parsed, source


def validate_artifact(
    document: Mapping[str, object] | bytes | str | Path,
    *,
    artifact_version: str | None = None,
    registry: ArtifactSchemaRegistry | None = None,
) -> ArtifactValidationResult:
    """Validate one artifact document against its registered schema.

    Resolution order for the schema key:
    1. Explicit ``artifact_version`` argument when provided.
    2. Document field ``artifact_version``.

    An unregistered version fails closed with a clear error (never silent pass).
    """

    reg = registry or get_default_registry()
    try:
        payload, source = _coerce_mapping(document)
    except ArtifactValidationError as error:
        return ArtifactValidationResult(
            ok=False,
            artifact_version=artifact_version,
            errors=(
                FieldError(path="$", validator="engine", message=str(error)),
            ),
            source=source if isinstance(document, Path) else None,
        )

    version = artifact_version
    if version is None:
        raw_version = payload.get("artifact_version")
        if not isinstance(raw_version, str) or not raw_version:
            return ArtifactValidationResult(
                ok=False,
                artifact_version=None,
                errors=(
                    FieldError(
                        path="$.artifact_version",
                        validator="required",
                        message="artifact_version is required to select a schema",
                    ),
                ),
                source=source,
            )
        version = raw_version

    try:
        entry = reg.get(version)
    except ArtifactSchemaError as error:
        return ArtifactValidationResult(
            ok=False,
            artifact_version=version,
            errors=(
                FieldError(path="$.artifact_version", validator="registry", message=str(error)),
            ),
            source=source,
        )

    try:
        validator = _validator_for(entry.schema, reg.all_schemas())
    except ArtifactValidationError as error:
        return ArtifactValidationResult(
            ok=False,
            artifact_version=version,
            errors=(
                FieldError(path="$", validator="engine", message=str(error)),
            ),
            source=source,
        )

    field_errors: list[FieldError] = []
    for error in sorted(
        validator.iter_errors(dict(payload)),
        key=lambda item: (tuple(str(part) for part in item.absolute_path), item.validator or ""),
    ):
        field_errors.append(
            FieldError(
                path=_json_pointer(list(error.absolute_path)),
                validator=str(error.validator or "unknown"),
                message=error.message,
            )
        )

    return ArtifactValidationResult(
        ok=not field_errors,
        artifact_version=version,
        errors=tuple(field_errors),
        source=source,
    )


def validate_artifact_directory(
    directory: str | Path,
    *,
    registry: ArtifactSchemaRegistry | None = None,
    pattern: str = "*.json",
    recursive: bool = False,
) -> BatchValidationReport:
    """Validate every matching JSON file under ``directory`` and aggregate."""

    root = Path(directory)
    if not root.is_dir():
        raise ArtifactValidationError(f"not a directory: {root}")

    reg = registry or get_default_registry()
    paths = sorted(root.rglob(pattern) if recursive else root.glob(pattern))
    results: list[FileValidationResult] = []
    passed = 0
    failed = 0
    skipped = 0

    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
            payload = json.loads(text)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            failed += 1
            results.append(
                FileValidationResult(
                    path=str(path),
                    result=ArtifactValidationResult(
                        ok=False,
                        artifact_version=None,
                        errors=(
                            FieldError(
                                path="$",
                                validator="engine",
                                message=f"unreadable or invalid JSON: {error}",
                            ),
                        ),
                        source=str(path),
                    ),
                )
            )
            continue

        if not isinstance(payload, Mapping):
            failed += 1
            results.append(
                FileValidationResult(
                    path=str(path),
                    result=ArtifactValidationResult(
                        ok=False,
                        artifact_version=None,
                        errors=(
                            FieldError(
                                path="$",
                                validator="type",
                                message="top-level value must be a JSON object",
                            ),
                        ),
                        source=str(path),
                    ),
                )
            )
            continue

        if "artifact_version" not in payload:
            skipped += 1
            results.append(
                FileValidationResult(
                    path=str(path),
                    result=ArtifactValidationResult(
                        ok=False,
                        artifact_version=None,
                        errors=(
                            FieldError(
                                path="$.artifact_version",
                                validator="skipped",
                                message="no artifact_version; not treated as a registered artifact",
                            ),
                        ),
                        source=str(path),
                    ),
                )
            )
            continue

        outcome = validate_artifact(payload, registry=reg)
        if outcome.ok:
            passed += 1
        else:
            failed += 1
        results.append(FileValidationResult(path=str(path), result=outcome))

    total = len(results)
    return BatchValidationReport(
        total=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        results=tuple(results),
    )
