"""Workspace init *planning*: validate a template and describe copy operations.

Core never materializes files. Callers (channel workspace tools or a human)
receive a :class:`WorkspaceInitPlan` and execute the listed operations outside
the core package.

Validation is preserved all-or-nothing: schema failures, nonempty targets, and
frozen-index collisions produce a rejected plan with an empty operation list
and ``executed=False``. No path is written by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
from pathlib import Path
from typing import Mapping

from video_factory.config import ConfigLayer, ConfigValidationError, parse_config_document
from video_factory.json_boundary import JsonInputError, parse_json_bytes
from video_factory.storage.frozen_index import (
    FrozenIndex,
    FrozenIndexViolation,
    assert_write_allowed,
    assert_write_paths_allowed,
)


# Map artifact_version string values to persisted layers. Built without
# hard-coding a channel-specific template name.
_ARTIFACT_VERSION_TO_LAYER: Mapping[str, ConfigLayer] = {
    "workspace-config/1.0": ConfigLayer.WORKSPACE,
    "channel-config/1.0": ConfigLayer.CHANNEL,
    "concept-config/1.0": ConfigLayer.CONCEPT,
    "episode-config/1.0": ConfigLayer.EPISODE,
}


class WorkspaceInitError(RuntimeError):
    """Raised by low-level validation helpers (not by :func:`plan_materialize`)."""


class WorkspaceInitPlanStatus(StrEnum):
    READY = "ready"
    REJECTED_INVALID_TEMPLATE = "rejected_invalid_template"
    REJECTED_TARGET_NONEMPTY = "rejected_target_nonempty"
    REJECTED_EMPTY_TEMPLATE = "rejected_empty_template"
    REJECTED_SCHEMA = "rejected_schema"
    REJECTED_FROZEN_INDEX = "rejected_frozen_index"


@dataclass(frozen=True, slots=True)
class CopyOperation:
    """One planned file copy (source → destination), with source content digest."""

    source_relative: str
    destination_relative: str
    source_sha256: str


@dataclass(frozen=True, slots=True)
class WorkspaceInitPlan:
    """Structured init plan — never implies execution (``executed`` is always false)."""

    status: WorkspaceInitPlanStatus
    template_dir: Path
    target_dir: Path
    operations: tuple[CopyOperation, ...]
    configs_validated: int
    executed: bool = False
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        if self.executed is not False:
            object.__setattr__(self, "executed", False)


def _is_nonempty_path(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_file():
        return True
    try:
        next(path.iterdir())
    except StopIteration:
        return False
    return True


def _iter_template_files(template_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(template_dir.rglob("*")):
        if path.is_file():
            files.append(path)
    return files


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def detect_config_layer(payload: bytes) -> ConfigLayer | None:
    """Return a config layer when *payload* looks like a core config document.

    Non-config JSON (for example provider binding lists owned by a channel
    repository) returns ``None`` and is planned without core schema validation.
    """

    try:
        data = parse_json_bytes(payload)
    except JsonInputError as error:
        raise WorkspaceInitError(
            f"template JSON rejected [{error.code.value}]: {error.detail}"
        ) from error
    if not isinstance(data, dict):
        return None
    artifact_version = data.get("artifact_version")
    if not isinstance(artifact_version, str):
        return None
    return _ARTIFACT_VERSION_TO_LAYER.get(artifact_version)


def validate_template_configs(template_dir: Path) -> int:
    """Validate every core config JSON under *template_dir*.

    Returns the number of documents validated. Raises ``WorkspaceInitError`` on
    the first schema or encoding failure without planning any write.
    """

    if not template_dir.is_dir():
        raise WorkspaceInitError(f"template_dir is not a directory: {template_dir}")

    validated = 0
    for path in _iter_template_files(template_dir):
        if path.suffix.lower() != ".json":
            continue
        payload = path.read_bytes()
        layer = detect_config_layer(payload)
        if layer is None:
            continue
        relative = path.relative_to(template_dir).as_posix()
        try:
            parse_config_document(payload, layer)
        except ConfigValidationError as error:
            raise WorkspaceInitError(
                f"template config validation failed for {relative} "
                f"(layer={layer.value}): {error}"
            ) from error
        validated += 1
    return validated


def _rejected(
    status: WorkspaceInitPlanStatus,
    template: Path,
    target: Path,
    reason: str,
    *,
    configs_validated: int = 0,
) -> WorkspaceInitPlan:
    return WorkspaceInitPlan(
        status=status,
        template_dir=template,
        target_dir=target,
        operations=(),
        configs_validated=configs_validated,
        executed=False,
        rejection_reason=reason,
    )


def plan_materialize(
    template_dir: Path,
    target_dir: Path,
    *,
    frozen_index: FrozenIndex | None = None,
) -> WorkspaceInitPlan:
    """Validate *template_dir* and return a copy plan for *target_dir*.

    Performs the same checks as the former materialize engine:

    - *template_dir* must be a directory
    - *target_dir* must not already exist as a nonempty path
    - every core config JSON must pass ``parse_config_document``
    - optional *frozen_index* must not list any destination-relative path

    On success returns ``status=READY`` with one :class:`CopyOperation` per
    template file (relative source/destination + source sha256). On failure
    returns a ``REJECTED_*`` plan with an empty operation list. Never writes.
    """

    template = Path(template_dir)
    target = Path(target_dir)

    if not template.is_dir():
        return _rejected(
            WorkspaceInitPlanStatus.REJECTED_INVALID_TEMPLATE,
            template,
            target,
            f"template_dir is not a directory: {template}",
        )

    if _is_nonempty_path(target):
        return _rejected(
            WorkspaceInitPlanStatus.REJECTED_TARGET_NONEMPTY,
            template,
            target,
            f"target_dir already exists and is not empty (refusing overwrite): {target}",
        )

    try:
        configs_validated = validate_template_configs(template)
    except WorkspaceInitError as error:
        return _rejected(
            WorkspaceInitPlanStatus.REJECTED_SCHEMA,
            template,
            target,
            str(error),
        )

    files = _iter_template_files(template)
    if not files:
        return _rejected(
            WorkspaceInitPlanStatus.REJECTED_EMPTY_TEMPLATE,
            template,
            target,
            f"template_dir contains no files: {template}",
            configs_validated=configs_validated,
        )

    relative_destinations = [source.relative_to(template).as_posix() for source in files]
    try:
        assert_write_paths_allowed(relative_destinations, frozen_index)
        if not target.is_absolute():
            assert_write_allowed(target, frozen_index)
    except FrozenIndexViolation as error:
        return _rejected(
            WorkspaceInitPlanStatus.REJECTED_FROZEN_INDEX,
            template,
            target,
            str(error),
            configs_validated=configs_validated,
        )

    operations: list[CopyOperation] = []
    for source in files:
        relative = source.relative_to(template).as_posix()
        payload = source.read_bytes()
        operations.append(
            CopyOperation(
                source_relative=relative,
                destination_relative=relative,
                source_sha256=_sha256_bytes(payload),
            )
        )

    return WorkspaceInitPlan(
        status=WorkspaceInitPlanStatus.READY,
        template_dir=template.resolve(),
        target_dir=target,
        operations=tuple(operations),
        configs_validated=configs_validated,
        executed=False,
        rejection_reason=None,
    )


class WorkspaceInitEngine:
    """Thin object façade over :func:`plan_materialize` for registry/backing clarity."""

    def plan_materialize(
        self,
        template_dir: Path,
        target_dir: Path,
        *,
        frozen_index: FrozenIndex | None = None,
    ) -> WorkspaceInitPlan:
        return plan_materialize(template_dir, target_dir, frozen_index=frozen_index)
