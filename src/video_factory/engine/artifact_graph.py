"""Validated, hash-bound artifact snapshots for plan-only orchestration.

The channel-side caller observes files and supplies immutable snapshots.  Core
validates and relates those snapshots but never discovers files or chooses a
"latest" artifact from timestamps or filenames.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from video_factory.artifacts import ArtifactSchemaRegistry, validate_artifact
from video_factory.config import canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    RelativeArtifactPath,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ArtifactGraphError(ValueError):
    """Raised when immutable artifact snapshots cannot form a safe graph."""


@dataclass(frozen=True, slots=True)
class ArtifactSnapshot:
    """One caller-observed artifact document plus its immutable file identity."""

    path: RelativeArtifactPath
    sha256: HashDigest
    document: Mapping[str, object]
    is_current: bool = True

    @property
    def artifact_version(self) -> str:
        value = self.document.get("artifact_version")
        return value if isinstance(value, str) else ""

    @property
    def family(self) -> str:
        version = self.artifact_version
        return version.split("/", 1)[0] if "/" in version else ""

    @property
    def episode_id(self) -> str | None:
        value = self.document.get("episode_id")
        return value if isinstance(value, str) and value else None

    @property
    def rules_version(self) -> str | None:
        value = self.document.get("rules_version")
        return value if isinstance(value, str) and value else None

    @property
    def reference(self) -> ArtifactReference:
        return ArtifactReference(
            path=self.path,
            sha256=self.sha256,
            artifact_version=ArtifactVersion(self.artifact_version),
        )


@dataclass(frozen=True, slots=True)
class ArtifactGraphFinding:
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactGraph:
    """Validated current-artifact graph; findings are fail-closed blockers."""

    snapshots: tuple[ArtifactSnapshot, ...]
    current_snapshots: tuple[ArtifactSnapshot, ...]
    episode_id: str | None
    rules_version: str | None
    findings: tuple[ArtifactGraphFinding, ...]

    @property
    def ok(self) -> bool:
        return not self.findings

    def family(self, family: str) -> tuple[ArtifactSnapshot, ...]:
        return tuple(
            snapshot
            for snapshot in self.current_snapshots
            if snapshot.family == family
        )

    def one(self, family: str) -> ArtifactSnapshot | None:
        items = self.family(family)
        return items[0] if len(items) == 1 else None

    def contains_reference(self, value: object) -> bool:
        parsed = bound_reference_from_mapping(value)
        if parsed is None:
            return False
        return any(snapshot.reference == parsed for snapshot in self.current_snapshots)

    def matches(self, snapshot: ArtifactSnapshot, value: object) -> bool:
        parsed = bound_reference_from_mapping(value)
        return parsed == snapshot.reference if parsed is not None else False


def _relative_path(value: object) -> RelativeArtifactPath:
    if not isinstance(value, str) or not value:
        raise ArtifactGraphError("artifact snapshot path must be a non-empty string")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if (
        value.startswith(("/", "\\"))
        or re.match(r"^[A-Za-z]:", value)
        or "\\" in value
        or ".." in parts
        or "\r" in value
        or "\n" in value
    ):
        raise ArtifactGraphError(
            "artifact snapshot path must be a contained relative POSIX path"
        )
    return RelativeArtifactPath(value)


def _hash(value: object) -> HashDigest:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ArtifactGraphError(
            "artifact snapshot sha256 must be 64 lowercase hexadecimal characters"
        )
    return HashDigest(value)


def make_artifact_snapshot(
    path: str,
    document: Mapping[str, object],
    *,
    sha256: str | None = None,
    is_current: bool = True,
) -> ArtifactSnapshot:
    """Build a snapshot; omitted digest means canonical document bytes.

    Production callers should normally provide the digest of the exact observed
    file bytes.  Canonical hashing is useful for in-memory plans and tests.
    """

    if not isinstance(document, Mapping):
        raise ArtifactGraphError("artifact snapshot document must be a mapping")
    digest = (
        _hash(sha256)
        if sha256 is not None
        else HashDigest(str(canonical_sha256(dict(document))))
    )
    return ArtifactSnapshot(
        path=_relative_path(path),
        sha256=digest,
        document=dict(document),
        is_current=bool(is_current),
    )


def make_artifact_snapshot_from_json_bytes(
    path: str,
    data: bytes,
    *,
    expected_sha256: str | None = None,
    is_current: bool = True,
) -> ArtifactSnapshot:
    """Parse exact observed bytes and bind the snapshot to their real digest."""

    if not isinstance(data, bytes):
        raise ArtifactGraphError("artifact snapshot data must be bytes")
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ArtifactGraphError(
            "artifact snapshot bytes do not match expected_sha256"
        )
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArtifactGraphError(f"artifact snapshot is not valid UTF-8 JSON: {error}") from error
    if not isinstance(document, Mapping):
        raise ArtifactGraphError("artifact snapshot JSON top-level must be an object")
    return make_artifact_snapshot(
        path,
        document,
        sha256=digest,
        is_current=is_current,
    )


def snapshot_from_mapping(value: Mapping[str, object]) -> ArtifactSnapshot:
    """Parse a caller envelope: path, sha256, document, and optional is_current."""

    document = value.get("document")
    if not isinstance(document, Mapping):
        raise ArtifactGraphError(
            "artifact snapshot envelope requires a mapping field named 'document'"
        )
    current = value.get("is_current", True)
    if not isinstance(current, bool):
        raise ArtifactGraphError("artifact snapshot is_current must be boolean")
    return make_artifact_snapshot(
        str(value.get("path", "")),
        document,
        sha256=str(value.get("sha256", "")),
        is_current=current,
    )


def bound_reference_from_mapping(value: object) -> ArtifactReference | None:
    if not isinstance(value, Mapping):
        return None
    path = value.get("path")
    digest = value.get("sha256")
    version = value.get("artifact_version")
    if (
        not isinstance(path, str)
        or not isinstance(digest, str)
        or not isinstance(version, str)
    ):
        return None
    try:
        return ArtifactReference(
            path=_relative_path(path),
            sha256=_hash(digest),
            artifact_version=ArtifactVersion(version),
        )
    except ArtifactGraphError:
        return None


def _coerce_snapshots(
    values: Sequence[ArtifactSnapshot | Mapping[str, object]],
) -> tuple[ArtifactSnapshot, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise ArtifactGraphError("artifact snapshots must be a sequence")
    snapshots: list[ArtifactSnapshot] = []
    for index, value in enumerate(values):
        if isinstance(value, ArtifactSnapshot):
            snapshots.append(value)
        elif isinstance(value, Mapping):
            try:
                snapshots.append(snapshot_from_mapping(value))
            except ArtifactGraphError as error:
                raise ArtifactGraphError(f"snapshots[{index}]: {error}") from error
        else:
            raise ArtifactGraphError(
                f"snapshots[{index}] must be ArtifactSnapshot or snapshot envelope"
            )
    return tuple(snapshots)


_MULTI_CURRENT_FAMILIES = frozenset(
    {
        "storyboard-review",
        "packet-review",
        "shot-qc",
        "final-review",
    }
)


def build_artifact_graph(
    values: Sequence[ArtifactSnapshot | Mapping[str, object]],
    *,
    registry: ArtifactSchemaRegistry | None = None,
) -> ArtifactGraph:
    """Validate snapshots and resolve only explicitly current artifacts."""

    snapshots = _coerce_snapshots(values)
    findings: list[ArtifactGraphFinding] = []

    for snapshot in snapshots:
        outcome = validate_artifact(snapshot.document, registry=registry)
        if not outcome.ok:
            detail = "; ".join(outcome.error_texts)
            findings.append(
                ArtifactGraphFinding(
                    code="invalid_artifact",
                    message=f"{snapshot.path}: {detail}",
                    path=str(snapshot.path),
                )
            )

    current = tuple(snapshot for snapshot in snapshots if snapshot.is_current)
    episode_ids = sorted(
        {
            episode_id
            for snapshot in current
            if (episode_id := snapshot.episode_id) is not None
        }
    )
    rules_versions = sorted(
        {
            rules_version
            for snapshot in current
            if (rules_version := snapshot.rules_version) is not None
        }
    )
    if len(episode_ids) > 1:
        findings.append(
            ArtifactGraphFinding(
                code="mixed_episode_ids",
                message=f"current artifacts mix episode ids: {', '.join(episode_ids)}",
            )
        )
    if len(rules_versions) > 1:
        findings.append(
            ArtifactGraphFinding(
                code="mixed_rules_versions",
                message=(
                    "current artifacts mix rules versions: "
                    + ", ".join(rules_versions)
                ),
            )
        )

    by_family: dict[str, list[ArtifactSnapshot]] = {}
    by_identity: dict[tuple[str, str, str], ArtifactSnapshot] = {}
    for snapshot in current:
        by_family.setdefault(snapshot.family, []).append(snapshot)
        identity = (
            str(snapshot.path),
            str(snapshot.sha256),
            snapshot.artifact_version,
        )
        if identity in by_identity:
            findings.append(
                ArtifactGraphFinding(
                    code="duplicate_snapshot",
                    message=f"duplicate current artifact snapshot: {snapshot.path}",
                    path=str(snapshot.path),
                )
            )
        by_identity[identity] = snapshot

    for family, members in sorted(by_family.items()):
        if family not in _MULTI_CURRENT_FAMILIES and len(members) > 1:
            findings.append(
                ArtifactGraphFinding(
                    code="ambiguous_current_artifact",
                    message=(
                        f"family {family!r} has {len(members)} current artifacts; "
                        "the caller must mark exactly one current"
                    ),
                )
            )

    for snapshot in current:
        family = snapshot.family
        if family.endswith("-review"):
            creator = snapshot.document.get("creator_role")
            reviewer = snapshot.document.get("reviewer_role")
            if (
                isinstance(creator, str)
                and isinstance(reviewer, str)
                and creator == reviewer
            ):
                findings.append(
                    ArtifactGraphFinding(
                        code="creator_reviewer_not_separated",
                        message=(
                            f"{snapshot.path}: creator_role and reviewer_role must differ"
                        ),
                        path=str(snapshot.path),
                    )
                )

    return ArtifactGraph(
        snapshots=snapshots,
        current_snapshots=current,
        episode_id=episode_ids[0] if len(episode_ids) == 1 else None,
        rules_version=rules_versions[0] if len(rules_versions) == 1 else None,
        findings=tuple(findings),
    )


def artifact_reference_to_mapping(reference: ArtifactReference) -> dict[str, Any]:
    return {
        "path": str(reference.path),
        "sha256": str(reference.sha256),
        "artifact_version": str(reference.artifact_version),
    }
