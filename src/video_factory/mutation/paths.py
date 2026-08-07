"""Cross-platform lexical and observed-node policy for managed paths."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
import re
import unicodedata

from video_factory.domain import RelativeArtifactPath

from .contracts import PathNodeKind, PathObservation


_DRIVE = re.compile(r"^[A-Za-z]:")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_FORBIDDEN = frozenset('<>:"|?*')
_RESERVED_BASENAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)


class MutationPathError(ValueError):
    """Stable path-policy failure raised before a plan or side effect."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def require_managed_path(
    value: str | RelativeArtifactPath,
    *,
    label: str = "path",
) -> RelativeArtifactPath:
    """Require one canonical relative POSIX path with no platform aliases."""

    if not isinstance(value, str) or not value:
        raise MutationPathError("mutation.path.empty", f"{label} must be non-empty")
    if value.startswith(("/", "//")) or _DRIVE.match(value):
        raise MutationPathError(
            "mutation.path.absolute",
            f"{label} must be relative: {value!r}",
        )
    if "\\" in value:
        raise MutationPathError(
            "mutation.path.backslash",
            f"{label} must use POSIX separators: {value!r}",
        )
    if _CONTROL.search(value):
        raise MutationPathError(
            "mutation.path.control_character",
            f"{label} contains a control character",
        )
    if unicodedata.normalize("NFC", value) != value:
        raise MutationPathError(
            "mutation.path.not_nfc",
            f"{label} must already be NFC-normalized",
        )
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        code = (
            "mutation.path.parent_traversal"
            if ".." in parts
            else "mutation.path.noncanonical_segment"
        )
        raise MutationPathError(code, f"{label} has an unsafe segment: {value!r}")
    for segment in parts:
        if segment[-1] in {" ", "."}:
            raise MutationPathError(
                "mutation.path.windows_alias",
                f"{label} has a trailing space or dot segment",
            )
        if any(character in _WINDOWS_FORBIDDEN for character in segment):
            raise MutationPathError(
                "mutation.path.platform_forbidden",
                f"{label} contains a platform-reserved character",
            )
        base = segment.split(".", 1)[0].casefold()
        if base in _RESERVED_BASENAMES:
            raise MutationPathError(
                "mutation.path.reserved_name",
                f"{label} contains a reserved device name",
            )
    canonical = PurePosixPath(*parts).as_posix()
    if canonical != value:
        raise MutationPathError(
            "mutation.path.noncanonical",
            f"{label} is not canonical: {value!r}",
        )
    return RelativeArtifactPath(canonical)


def path_collision_key(value: str | RelativeArtifactPath) -> str:
    path = require_managed_path(value)
    return unicodedata.normalize("NFC", str(path)).casefold()


def require_collision_free(
    paths: Iterable[str | RelativeArtifactPath],
    *,
    label: str = "paths",
) -> tuple[RelativeArtifactPath, ...]:
    """Reject exact, case-insensitive, and Unicode-normalized aliases."""

    normalized: list[RelativeArtifactPath] = []
    owners: dict[str, str] = {}
    for raw in paths:
        path = require_managed_path(raw, label=label)
        key = path_collision_key(path)
        existing = owners.get(key)
        if existing is not None:
            raise MutationPathError(
                "mutation.path.collision",
                f"{label} contains colliding paths {existing!r} and {str(path)!r}",
            )
        owners[key] = str(path)
        normalized.append(path)
    return tuple(normalized)


def observation_index(
    observations: Iterable[PathObservation],
) -> Mapping[str, PathObservation]:
    items = tuple(observations)
    require_collision_free((item.path for item in items), label="workspace entries")
    return {str(require_managed_path(item.path)): item for item in items}


def path_and_ancestors(value: str | RelativeArtifactPath) -> tuple[str, ...]:
    path = require_managed_path(value)
    parts = str(path).split("/")
    return tuple("/".join(parts[:index]) for index in range(1, len(parts) + 1))


def require_no_link_or_reparse_ancestor(
    value: str | RelativeArtifactPath,
    observations: Mapping[str, PathObservation],
) -> None:
    aliases = {
        path_collision_key(observed_path): (observed_path, observed)
        for observed_path, observed in observations.items()
    }
    for candidate in path_and_ancestors(value):
        owner = aliases.get(path_collision_key(candidate))
        if owner is None:
            continue
        observed_path, observed = owner
        if observed_path != candidate:
            raise MutationPathError(
                "mutation.path.observed_alias",
                f"managed path spelling aliases observed path {observed_path!r}",
            )
        if observed.node_kind is PathNodeKind.SYMLINK:
            raise MutationPathError(
                "mutation.path.symlink",
                f"managed path crosses a symlink at {candidate!r}",
            )
        if observed.node_kind is PathNodeKind.REPARSE:
            raise MutationPathError(
                "mutation.path.reparse",
                f"managed path crosses a reparse point at {candidate!r}",
            )
