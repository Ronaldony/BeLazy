"""Fixture-only filesystem boundary used by every concrete W06 adapter."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat

from video_factory.config.canonical import canonical_json_bytes, canonical_sha256
from video_factory.domain import HashDigest, OpaqueId


MARKER_NAME = ".be-lazy-runtime-fixture.json"
MARKER_VERSION = "runtime-fixture-boundary/1.0"
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class RuntimeBoundaryError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as error:
        raise RuntimeBoundaryError(
            "runtime.boundary.unreadable", f"cannot inspect runtime path: {path}"
        ) from error
    return path.is_symlink() or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_FLAG
    )


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class FixtureRuntimeBoundary:
    root: Path
    runtime_id: OpaqueId
    marker_sha256: HashDigest

    @classmethod
    def initialize(
        cls,
        root: Path,
        *,
        runtime_id: str,
        fixture_only: bool,
    ) -> "FixtureRuntimeBoundary":
        """Create a new explicitly test-only boundary.

        The explicit ``fixture_only=True`` acknowledgement and immutable marker
        are intentional friction.  Existing/non-empty directories are never
        adopted silently.
        """

        if fixture_only is not True:
            raise RuntimeBoundaryError(
                "runtime.boundary.fixture_required",
                "W06 concrete adapters are enabled only for isolated fixtures",
            )
        root = Path(root)
        if not root.is_absolute():
            raise RuntimeBoundaryError(
                "runtime.boundary.absolute_required", "runtime root must be absolute"
            )
        if root.exists():
            if _is_link_or_reparse(root) or not root.is_dir():
                raise RuntimeBoundaryError(
                    "runtime.boundary.root_type", "runtime root is not a regular directory"
                )
            if any(root.iterdir()):
                raise RuntimeBoundaryError(
                    "runtime.boundary.nonempty", "existing runtime root must be empty"
                )
        else:
            root.mkdir(parents=False)
        marker = {
            "artifact_version": MARKER_VERSION,
            "runtime_id": runtime_id,
            "fixture_only": True,
            "production_enabled": False,
        }
        payload = canonical_json_bytes(marker)
        marker_path = root / MARKER_NAME
        descriptor = os.open(
            marker_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_directory(root)
        return cls.open(root, expected_runtime_id=runtime_id)

    @classmethod
    def open(
        cls, root: Path, *, expected_runtime_id: str
    ) -> "FixtureRuntimeBoundary":
        root = Path(root)
        if not root.is_absolute() or not root.is_dir() or _is_link_or_reparse(root):
            raise RuntimeBoundaryError(
                "runtime.boundary.root", "runtime root is missing, relative, or reparse"
            )
        marker_path = root / MARKER_NAME
        if not marker_path.is_file() or _is_link_or_reparse(marker_path):
            raise RuntimeBoundaryError(
                "runtime.boundary.marker_missing", "fixture boundary marker is missing"
            )
        try:
            payload = marker_path.read_bytes()
            document = json.loads(payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeBoundaryError(
                "runtime.boundary.marker_invalid", "fixture boundary marker is invalid"
            ) from error
        expected = {
            "artifact_version": MARKER_VERSION,
            "runtime_id": expected_runtime_id,
            "fixture_only": True,
            "production_enabled": False,
        }
        if document != expected or payload != canonical_json_bytes(expected):
            raise RuntimeBoundaryError(
                "runtime.boundary.marker_rebound", "fixture boundary marker was changed"
            )
        return cls(
            root=root.resolve(strict=True),
            runtime_id=OpaqueId(expected_runtime_id),
            marker_sha256=canonical_sha256(expected),
        )

    def require_directory(self, relative_name: str, *, create: bool) -> Path:
        if (
            not relative_name
            or relative_name in {".", ".."}
            or "/" in relative_name
            or "\\" in relative_name
        ):
            raise RuntimeBoundaryError(
                "runtime.boundary.child", "runtime child name is invalid"
            )
        candidate = self.root / relative_name
        if candidate.exists():
            if _is_link_or_reparse(candidate) or not candidate.is_dir():
                raise RuntimeBoundaryError(
                    "runtime.boundary.child_type", "runtime child is not a regular directory"
                )
        elif create:
            candidate.mkdir()
            _fsync_directory(self.root)
        else:
            raise RuntimeBoundaryError(
                "runtime.boundary.child_missing", "runtime child directory is missing"
            )
        resolved = candidate.resolve(strict=True)
        if os.path.commonpath((str(self.root), str(resolved))) != str(self.root):
            raise RuntimeBoundaryError(
                "runtime.boundary.escape", "runtime child escaped the fixture boundary"
            )
        return resolved


__all__ = [
    "FixtureRuntimeBoundary",
    "MARKER_NAME",
    "MARKER_VERSION",
    "RuntimeBoundaryError",
]
