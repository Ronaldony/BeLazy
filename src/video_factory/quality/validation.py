"""Shared strict validation helpers for W05 immutable references."""

from __future__ import annotations

from collections.abc import Iterable
import re

from video_factory.approvals import GateContext, gate_context_from_mapping, gate_context_to_mapping
from video_factory.blueprint.reference_paths import (
    ReferencePathError,
    reference_path_collision_key,
    require_canonical_reference_path,
)
from video_factory.domain import ArtifactReference

from .contracts import MediaSubject, QualityContractError


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise QualityContractError("quality.sha256", f"{label} must be lowercase sha256")
    return value


def require_token(value: object, label: str) -> str:
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise QualityContractError("quality.token", f"{label} must be a canonical token")
    return value


def require_bps(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 10_000:
        raise QualityContractError("quality.bps", f"{label} must be an integer from 0 through 10000")
    return value


def require_gate_context(context: GateContext) -> GateContext:
    try:
        return gate_context_from_mapping(gate_context_to_mapping(context))
    except (TypeError, ValueError) as error:
        raise QualityContractError("quality.context", "GateContext is incomplete or malformed") from error


def reference_to_mapping(reference: ArtifactReference) -> dict[str, str]:
    require_reference(reference, "artifact reference")
    return {
        "path": str(reference.path),
        "sha256": str(reference.sha256),
        "artifact_version": str(reference.artifact_version),
    }


def require_reference(reference: ArtifactReference, label: str) -> ArtifactReference:
    if not isinstance(reference, ArtifactReference):
        raise QualityContractError("quality.reference", f"{label} must be ArtifactReference")
    try:
        require_canonical_reference_path(str(reference.path))
    except ReferencePathError as error:
        raise QualityContractError("quality.reference.path", f"{label}: {error}") from error
    require_sha256(str(reference.sha256), f"{label}.sha256")
    version = str(reference.artifact_version)
    if version.count("/") != 1 or any(not part for part in version.split("/")):
        raise QualityContractError("quality.reference.version", f"{label}.artifact_version is malformed")
    return reference


def media_subject_to_mapping(subject: MediaSubject) -> dict[str, object]:
    require_media_subject(subject, "media subject")
    return {
        "shot_id": str(subject.shot_id),
        "component_id": str(subject.component_id),
        "reference": reference_to_mapping(subject.reference),
        "byte_length": subject.byte_length,
        "workspace_observation_sha256": str(subject.workspace_observation_sha256),
        "observation_receipt_ref": reference_to_mapping(subject.observation_receipt_ref),
    }


def require_media_subject(subject: MediaSubject, label: str) -> MediaSubject:
    if not isinstance(subject, MediaSubject):
        raise QualityContractError("quality.subject", f"{label} must be MediaSubject")
    require_token(str(subject.shot_id), f"{label}.shot_id")
    require_token(str(subject.component_id), f"{label}.component_id")
    require_reference(subject.reference, f"{label}.reference")
    if (
        not isinstance(subject.byte_length, int)
        or isinstance(subject.byte_length, bool)
        or subject.byte_length < 0
    ):
        raise QualityContractError(
            "quality.subject.byte_length",
            f"{label}.byte_length must be a non-negative integer",
        )
    require_sha256(
        str(subject.workspace_observation_sha256),
        f"{label}.workspace_observation_sha256",
    )
    require_reference(
        subject.observation_receipt_ref,
        f"{label}.observation_receipt_ref",
    )
    if subject.observation_receipt_ref.artifact_version != "quality-observation-receipt/1.0":
        raise QualityContractError(
            "quality.subject.receipt_version",
            "media observation receipt has an unsupported artifact version",
        )
    require_reference_consistency(
        (subject.reference, subject.observation_receipt_ref),
        allow_exact_reuse=False,
    )
    return subject


def require_reference_consistency(
    references: Iterable[ArtifactReference],
    *,
    allow_exact_reuse: bool,
) -> tuple[ArtifactReference, ...]:
    values = tuple(references)
    by_collision: dict[str, tuple[str, str, str]] = {}
    seen_exact: set[tuple[str, str, str]] = set()
    for index, reference in enumerate(values):
        require_reference(reference, f"references[{index}]")
        identity = (
            str(reference.path),
            str(reference.sha256),
            str(reference.artifact_version),
        )
        if identity in seen_exact and not allow_exact_reuse:
            raise QualityContractError("quality.reference.duplicate", "reference identity is duplicated")
        seen_exact.add(identity)
        key = reference_path_collision_key(str(reference.path))
        existing = by_collision.get(key)
        if existing is not None and existing != identity:
            raise QualityContractError("quality.reference.collision", "case or Unicode path alias has conflicting identity")
        by_collision[key] = identity
    return values


def require_reason_codes(values: tuple[str, ...], label: str, *, allow_empty: bool) -> tuple[str, ...]:
    if (not allow_empty and not values) or len(values) != len(set(values)):
        raise QualityContractError("quality.reasons", f"{label} must be canonical and unique")
    for value in values:
        require_token(value, label)
    if values != tuple(sorted(values)):
        raise QualityContractError("quality.reasons", f"{label} must be sorted")
    return values


__all__ = [
    "media_subject_to_mapping",
    "reference_to_mapping",
    "require_bps",
    "require_gate_context",
    "require_media_subject",
    "require_reason_codes",
    "require_reference",
    "require_reference_consistency",
    "require_sha256",
    "require_token",
]
