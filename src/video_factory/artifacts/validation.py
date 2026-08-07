"""JSON Schema validation runner for production artifact documents.

Uses the already-available ``jsonschema`` package (Draft 2020-12) with a
local ``referencing`` registry so ``$ref`` never touches the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urljoin

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource

from video_factory.json_boundary import (
    JsonErrorCode,
    JsonInputError,
    is_rfc3339_datetime,
    parse_json_bytes,
    parse_json_path,
    require_json_object,
    validate_json_mapping,
)

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
    reason_code: str | None = None

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
    by_uri: dict[str, Resource] = {}
    bases = {
        schema_id.rsplit("/", 1)[0] + "/"
        for schema in schemas.values()
        if isinstance((schema_id := schema.get("$id")), str) and schema_id
    }
    for filename, schema in schemas.items():
        schema_id = schema.get("$id")
        if isinstance(schema_id, str) and schema_id:
            resource = Resource.from_contents(dict(schema))
            by_uri[schema_id] = resource
            # Existing schemas use filename-relative refs from more than one
            # $id directory. Register deterministic local aliases so resolution
            # remains offline and never falls through to network retrieval.
            for base in bases:
                by_uri.setdefault(urljoin(base, filename), resource)
    return Registry().with_resources(sorted(by_uri.items()))


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
    return Draft202012Validator(
        dict(entry_schema),
        registry=registry,
        format_checker=_FORMAT_CHECKER,
    )


_FORMAT_CHECKER = FormatChecker()
_FORMAT_CHECKER.checks("date-time")(is_rfc3339_datetime)


def _coerce_mapping(
    document: Mapping[str, object] | bytes | str | Path,
) -> tuple[Mapping[str, object], str | None]:
    """Compatibility facade; internal callers use the explicit public APIs."""

    if isinstance(document, Path):
        source = str(document)
        parsed = require_json_object(parse_json_path(document), source=source)
        return parsed, source
    if isinstance(document, bytes):
        parsed = require_json_object(parse_json_bytes(document))
        return parsed, None
    elif isinstance(document, str):
        # Deprecated legacy behavior only. New code must call the explicit path
        # or bytes API and never infer an input kind from filesystem existence.
        as_path = Path(document)
        if as_path.is_file():
            return _coerce_mapping(as_path)
        parsed = require_json_object(
            parse_json_bytes(document.encode("utf-8"), source="<legacy-json-text>"),
            source="<legacy-json-text>",
        )
        return parsed, "<legacy-json-text>"
    elif isinstance(document, Mapping):
        return validate_json_mapping(document), None
    else:
        raise JsonInputError(
            code=JsonErrorCode.UNSUPPORTED_VALUE,
            detail="document must be a mapping, bytes, string, or pathlib.Path",
        )


def _input_failure(
    error: JsonInputError,
    *,
    artifact_version: str | None,
    source: str | None,
) -> ArtifactValidationResult:
    return ArtifactValidationResult(
        ok=False,
        artifact_version=artifact_version,
        errors=(
            FieldError(
                path=error.path,
                validator="json-boundary",
                message=error.detail,
                reason_code=error.code.value,
            ),
        ),
        source=source or error.source,
    )


def _validate_mapping_document(
    payload: Mapping[str, object],
    *,
    source: str | None,
    artifact_version: str | None,
    registry: ArtifactSchemaRegistry | None,
) -> ArtifactValidationResult:
    reg = registry or get_default_registry()

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


def validate_artifact_mapping(
    document: Mapping[str, object],
    *,
    artifact_version: str | None = None,
    registry: ArtifactSchemaRegistry | None = None,
) -> ArtifactValidationResult:
    """Validate an explicit, already-decoded JSON object mapping."""

    try:
        payload = validate_json_mapping(document)
    except JsonInputError as error:
        return _input_failure(
            error, artifact_version=artifact_version, source=None
        )
    return _validate_mapping_document(
        payload,
        source=None,
        artifact_version=artifact_version,
        registry=registry,
    )


def validate_artifact_bytes(
    document: bytes,
    *,
    artifact_version: str | None = None,
    registry: ArtifactSchemaRegistry | None = None,
) -> ArtifactValidationResult:
    """Validate explicit exact UTF-8 JSON bytes."""

    try:
        payload = require_json_object(parse_json_bytes(document))
    except JsonInputError as error:
        return _input_failure(
            error, artifact_version=artifact_version, source=None
        )
    return _validate_mapping_document(
        payload,
        source=None,
        artifact_version=artifact_version,
        registry=registry,
    )


def validate_artifact_path(
    document: str | Path,
    *,
    artifact_version: str | None = None,
    registry: ArtifactSchemaRegistry | None = None,
) -> ArtifactValidationResult:
    """Validate one explicit filesystem path with stable boundary errors."""

    source = str(document)
    try:
        payload = require_json_object(parse_json_path(document), source=source)
    except JsonInputError as error:
        return _input_failure(
            error, artifact_version=artifact_version, source=source
        )
    return _validate_mapping_document(
        payload,
        source=source,
        artifact_version=artifact_version,
        registry=registry,
    )


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

    try:
        payload, source = _coerce_mapping(document)
    except JsonInputError as error:
        return _input_failure(
            error,
            artifact_version=artifact_version,
            source=str(document) if isinstance(document, Path) else None,
        )
    return _validate_mapping_document(
        payload,
        source=source,
        artifact_version=artifact_version,
        registry=registry,
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
            payload = require_json_object(parse_json_path(path), source=str(path))
        except JsonInputError as error:
            failed += 1
            results.append(
                FileValidationResult(
                    path=str(path),
                    result=ArtifactValidationResult(
                        ok=False,
                        artifact_version=None,
                        errors=(
                            FieldError(
                                path=error.path,
                                validator="json-boundary",
                                message=error.detail,
                                reason_code=error.code.value,
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

        outcome = _validate_mapping_document(
            payload,
            source=str(path),
            artifact_version=None,
            registry=reg,
        )
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
