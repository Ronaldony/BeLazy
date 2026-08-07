"""Pure workspace observation validation shared by all authority consumers."""

from __future__ import annotations

import re

from video_factory.config.canonical import canonical_sha256
from video_factory.domain import HashDigest, OpaqueId

from .contracts import (
    PathNodeKind,
    PathObservation,
    WorkspaceObservation,
    WorkspaceRevision,
    WorkspaceTrustState,
)
from .paths import (
    MutationPathError,
    observation_index,
    require_managed_path,
    require_no_link_or_reparse_ancestor,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class WorkspaceTrustError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def workspace_observation_mapping(
    observation: WorkspaceObservation,
) -> dict[str, object]:
    if not isinstance(observation, WorkspaceObservation):
        raise WorkspaceTrustError(
            "mutation.workspace.observation_type",
            "workspace observation has an invalid type",
        )
    if (
        not isinstance(observation.workspace_id, str)
        or not isinstance(observation.revision_id, str)
        or not str(observation.workspace_id)
        or not str(observation.revision_id)
    ):
        raise WorkspaceTrustError(
            "mutation.workspace.observation_id",
            "workspace and revision IDs must be non-empty",
        )
    if not isinstance(observation.complete, bool):
        raise WorkspaceTrustError(
            "mutation.workspace.observation_complete",
            "workspace observation completeness must be boolean",
        )
    if not isinstance(observation.trust_state, WorkspaceTrustState):
        raise WorkspaceTrustError(
            "mutation.workspace.trust_state",
            "workspace observation trust state is invalid",
        )
    if _SHA256.fullmatch(str(observation.manifest_sha256)) is None:
        raise WorkspaceTrustError(
            "mutation.workspace.manifest_digest",
            "workspace observation manifest digest is invalid",
        )
    if not isinstance(observation.entries, tuple) or any(
        not isinstance(item, PathObservation) for item in observation.entries
    ):
        raise WorkspaceTrustError(
            "mutation.workspace.entries_type",
            "workspace observation entries must be path observations",
        )
    if any(not isinstance(item.node_kind, PathNodeKind) for item in observation.entries):
        raise WorkspaceTrustError(
            "mutation.workspace.node_kind",
            "workspace observation contains an invalid node kind",
        )
    try:
        observation_index(observation.entries)
    except MutationPathError as error:
        raise WorkspaceTrustError(error.reason_code, str(error)) from error
    entries: list[dict[str, object]] = []
    for item in observation.entries:
        path = require_managed_path(item.path)
        if item.node_kind is PathNodeKind.FILE:
            if (
                item.exact_sha256 is None
                or _SHA256.fullmatch(str(item.exact_sha256)) is None
            ):
                raise WorkspaceTrustError(
                    "mutation.workspace.file_digest",
                    f"observed file digest is invalid: {path}",
                )
            if (
                not isinstance(item.byte_length, int)
                or isinstance(item.byte_length, bool)
                or item.byte_length < 0
            ):
                raise WorkspaceTrustError(
                    "mutation.workspace.file_length",
                    f"observed file length is invalid: {path}",
                )
        elif item.exact_sha256 is not None or item.byte_length is not None:
            raise WorkspaceTrustError(
                "mutation.workspace.nonfile_content",
                f"non-file observation carries content metadata: {path}",
            )
        entries.append(
            {
                "path": str(path),
                "node_kind": item.node_kind.value,
                "exact_sha256": (
                    str(item.exact_sha256) if item.exact_sha256 is not None else None
                ),
                "byte_length": item.byte_length,
            }
        )
    return {
        "workspace_id": str(observation.workspace_id),
        "revision_id": str(observation.revision_id),
        "manifest_sha256": str(observation.manifest_sha256),
        "trust_state": observation.trust_state.value,
        "complete": observation.complete,
        "entries": sorted(entries, key=lambda item: str(item["path"])),
    }


def workspace_observation_sha256(observation: WorkspaceObservation) -> HashDigest:
    return canonical_sha256(workspace_observation_mapping(observation))


def workspace_trust_blockers(
    observation: WorkspaceObservation | None,
    *,
    expected_workspace_id: OpaqueId | None = None,
    expected_revision_id: OpaqueId | None,
    expected_manifest_sha256: str | HashDigest | None,
    expected_revision: WorkspaceRevision | None = None,
    expected_revision_sha256: str | HashDigest | None = None,
) -> tuple[str, ...]:
    """Return stable blockers for generation, publish, and executor gates."""

    if observation is None:
        return ("mutation.workspace.observation_missing",)
    try:
        workspace_observation_mapping(observation)
    except WorkspaceTrustError as error:
        return (error.reason_code,)
    blockers: list[str] = []
    if observation.complete is not True:
        blockers.append("mutation.workspace.observation_incomplete")
    if observation.trust_state is not WorkspaceTrustState.TRUSTED:
        blockers.append("mutation.workspace.untrusted")
    if expected_workspace_id is None:
        blockers.append("mutation.workspace.expected_workspace_missing")
    elif observation.workspace_id != expected_workspace_id:
        blockers.append("mutation.workspace.workspace_mismatch")
    if expected_revision_id is None:
        blockers.append("mutation.workspace.expected_revision_missing")
    elif observation.revision_id != expected_revision_id:
        blockers.append("mutation.workspace.revision_mismatch")
    if expected_manifest_sha256 is None:
        blockers.append("mutation.workspace.expected_manifest_missing")
    elif str(observation.manifest_sha256) != str(expected_manifest_sha256):
        blockers.append("mutation.workspace.manifest_mismatch")
    if expected_revision is None:
        blockers.append("mutation.workspace.expected_revision_artifact_missing")
    else:
        try:
            from .planner import (
                validate_workspace_revision,
                workspace_revision_to_mapping,
            )

            validate_workspace_revision(expected_revision)
            revision_digest = canonical_sha256(
                workspace_revision_to_mapping(expected_revision)
            )
        except (AttributeError, TypeError, ValueError):
            blockers.append("mutation.workspace.expected_revision_invalid")
        else:
            if expected_revision_sha256 is None:
                blockers.append("mutation.workspace.expected_revision_digest_missing")
            elif str(revision_digest) != str(expected_revision_sha256):
                blockers.append("mutation.workspace.expected_revision_digest_mismatch")
            if expected_revision.trust_state is not WorkspaceTrustState.TRUSTED:
                blockers.append("mutation.workspace.expected_revision_untrusted")
            if expected_revision.workspace_id != observation.workspace_id:
                blockers.append("mutation.workspace.workspace_mismatch")
            if expected_revision.revision_id != observation.revision_id:
                blockers.append("mutation.workspace.revision_mismatch")
            if expected_revision.manifest_sha256 != observation.manifest_sha256:
                blockers.append("mutation.workspace.manifest_mismatch")

            latest: dict[str, object] = {}
            for entry in expected_revision.entries:
                path = str(entry.path)
                current = latest.get(path)
                if (
                    current is None
                    or entry.revision_ordinal > current.revision_ordinal  # type: ignore[attr-defined]
                ):
                    latest[path] = entry
            active = {
                path: entry
                for path, entry in latest.items()
                if not entry.tombstone  # type: ignore[attr-defined]
            }
            observed_files = {
                str(item.path): item
                for item in observation.entries
                if item.node_kind is PathNodeKind.FILE
            }
            if set(observed_files) != set(active):
                blockers.append("mutation.workspace.content_drift")
            else:
                for path, entry in active.items():
                    item = observed_files[path]
                    if (
                        item.exact_sha256 != entry.content_sha256  # type: ignore[attr-defined]
                        or item.byte_length != entry.byte_length  # type: ignore[attr-defined]
                    ):
                        blockers.append("mutation.workspace.content_drift")
                        break
            try:
                observed_index = observation_index(observation.entries)
                for path in active:
                    require_no_link_or_reparse_ancestor(path, observed_index)
            except MutationPathError as error:
                blockers.append(error.reason_code)
    if any(
        item.node_kind in {PathNodeKind.SYMLINK, PathNodeKind.REPARSE}
        for item in observation.entries
    ):
        blockers.append("mutation.workspace.link_or_reparse")
    return tuple(sorted(set(blockers)))
