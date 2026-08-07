"""Canonical relative paths shared by Blueprint and Director references."""

from __future__ import annotations

import re
import unicodedata


_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class ReferencePathError(ValueError):
    """Raised when an immutable reference path is not already canonical."""


def require_canonical_reference_path(value: object) -> str:
    """Require one relative POSIX NFC path without aliasing segments."""

    if not isinstance(value, str) or not value:
        raise ReferencePathError("reference path must be a non-empty string")
    if unicodedata.normalize("NFC", value) != value:
        raise ReferencePathError("reference path must already be NFC-normalized")
    if value.startswith(("/", "\\")) or _WINDOWS_DRIVE.match(value):
        raise ReferencePathError("reference path must be relative")
    if "\\" in value or any(ord(character) < 32 for character in value):
        raise ReferencePathError("reference path contains forbidden characters")
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ReferencePathError("reference path contains an aliasing segment")
    return value


def reference_path_collision_key(value: object) -> str:
    """Return the cross-platform alias key for a validated reference path."""

    return require_canonical_reference_path(value).casefold()


__all__ = [
    "ReferencePathError",
    "reference_path_collision_key",
    "require_canonical_reference_path",
]
