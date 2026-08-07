"""Generic write-path denial against an injected frozen path index.

The index is pure data supplied by the caller. This module does not embed any
repository-specific path names; it only normalizes paths and tests membership.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence


class FrozenIndexError(ValueError):
    """Raised when a frozen index document cannot be loaded or is invalid."""


class FrozenIndexViolation(RuntimeError):
    """Raised when a write target path is present in the frozen index."""

    def __init__(self, path: str, *, normalized_path: str) -> None:
        super().__init__(
            f"write refused: path is frozen by index: {path!r} "
            f"(normalized={normalized_path!r})"
        )
        self.path = path
        self.normalized_path = normalized_path


@dataclass(frozen=True, slots=True)
class FrozenIndexEntry:
    """One frozen path record.

    ``sha256`` and ``status`` are optional metadata retained for callers; the
    write-guard only uses ``path``.
    """

    path: str
    sha256: str | None = None
    status: str | None = None


@dataclass(frozen=True, slots=True)
class FrozenIndex:
    """Immutable set of frozen paths with O(1) membership after load."""

    entries: tuple[FrozenIndexEntry, ...]
    _by_key: Mapping[str, FrozenIndexEntry]

    def __len__(self) -> int:
        return len(self.entries)

    def contains(self, path: str | Path) -> bool:
        """Return True when *path* matches an indexed path after normalization."""

        return normalize_frozen_path(path) in self._by_key

    def get(self, path: str | Path) -> FrozenIndexEntry | None:
        return self._by_key.get(normalize_frozen_path(path))


def normalize_frozen_path(path: str | Path) -> str:
    """Normalize a path for stable index comparison.

    Rules:
    - Accept ``pathlib.Path`` or ``str``
    - Convert backslashes to forward slashes
    - Strip surrounding whitespace
    - Drop a leading ``./``
    - Collapse repeated ``/``
    - Strip a single leading ``/`` (index keys are relative)
    - Casefold for case-insensitive equality (Windows-safe)
    """

    if isinstance(path, Path):
        raw = path.as_posix()
    else:
        raw = str(path)
    text = raw.strip().replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    if text.startswith("./"):
        text = text[2:]
    if text.startswith("/"):
        text = text[1:]
    if text.endswith("/") and text != "/":
        text = text.rstrip("/")
    return text.casefold()


def frozen_index_from_entries(entries: Iterable[FrozenIndexEntry]) -> FrozenIndex:
    """Build a ``FrozenIndex`` from entry objects (synthetic fixtures / tests)."""

    collected: list[FrozenIndexEntry] = []
    by_key: dict[str, FrozenIndexEntry] = {}
    for entry in entries:
        if not isinstance(entry, FrozenIndexEntry):
            raise FrozenIndexError(f"entry must be FrozenIndexEntry, got {type(entry)!r}")
        key = normalize_frozen_path(entry.path)
        if not key:
            raise FrozenIndexError("frozen index entry path must be non-empty")
        if key in by_key:
            raise FrozenIndexError(f"duplicate frozen path after normalization: {entry.path!r}")
        # Preserve the caller's path string on the entry; lookup uses key only.
        collected.append(entry)
        by_key[key] = entry
    return FrozenIndex(entries=tuple(collected), _by_key=by_key)


def load_frozen_index(path: str | Path) -> FrozenIndex:
    """Load a frozen index document from JSON (stdlib only).

    Expected shape::

        {
          "entries": [
            {"path": "relative/posix/path", "sha256": "...", "status": "..."}
          ]
        }

    Unknown top-level keys are ignored. YAML files are not parsed here; callers
    that store YAML should convert to this JSON shape before loading into core.
    """

    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as error:
        raise FrozenIndexError(f"cannot read frozen index: {source}") from error

    suffix = source.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        raise FrozenIndexError(
            "YAML frozen indexes are not loaded by core (stdlib-only); "
            "provide JSON or convert before load"
        )

    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise FrozenIndexError(f"invalid frozen index JSON: {source}: {error}") from error

    if not isinstance(data, dict):
        raise FrozenIndexError("frozen index root must be a JSON object")

    raw_entries = data.get("entries")
    if raw_entries is None:
        raise FrozenIndexError("frozen index missing 'entries' array")
    if not isinstance(raw_entries, list):
        raise FrozenIndexError("frozen index 'entries' must be an array")

    built: list[FrozenIndexEntry] = []
    for index, item in enumerate(raw_entries):
        if not isinstance(item, Mapping):
            raise FrozenIndexError(f"entries[{index}] must be an object")
        path_value = item.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            raise FrozenIndexError(f"entries[{index}].path must be a non-empty string")
        sha_value = item.get("sha256")
        if sha_value is not None and not isinstance(sha_value, str):
            raise FrozenIndexError(f"entries[{index}].sha256 must be a string when present")
        status_value = item.get("status")
        if status_value is not None and not isinstance(status_value, str):
            raise FrozenIndexError(f"entries[{index}].status must be a string when present")
        built.append(
            FrozenIndexEntry(
                path=path_value,
                sha256=sha_value,
                status=status_value,
            )
        )
    return frozen_index_from_entries(built)


def assert_write_allowed(
    path: str | Path,
    frozen_index: FrozenIndex | None,
) -> None:
    """Refuse writes when *path* is listed in *frozen_index*.

    ``frozen_index is None`` or an empty index allows every path.
    """

    if frozen_index is None or len(frozen_index) == 0:
        return
    key = normalize_frozen_path(path)
    if not key:
        return
    if frozen_index.contains(path):
        raise FrozenIndexViolation(str(path), normalized_path=key)


def assert_write_paths_allowed(
    paths: Sequence[str | Path],
    frozen_index: FrozenIndex | None,
) -> None:
    """Apply :func:`assert_write_allowed` to each path (first violation wins)."""

    if frozen_index is None or len(frozen_index) == 0:
        return
    for path in paths:
        assert_write_allowed(path, frozen_index)
