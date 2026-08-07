"""Cross-platform relative path helpers for distribution contracts.

Reuses the same POSIX-normalization spirit as ``normalize_frozen_path`` while
adding lock-specific containment rules (``vendor/core/`` only, no ``..``).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
import re

from .contracts import VENDOR_CORE_PREFIX


class DistributionPathError(ValueError):
    """Raised when a path violates relative / vendor containment rules."""


_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_HEX = re.compile(r"^[0-9a-f]{40}$")
_SEMVER_LIKE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_CONTRACT_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def normalize_relative_posix(path: str | Path) -> str:
    """Normalize separators to POSIX forward slashes without casefolding.

    Unlike frozen-index keys, lock paths preserve the declared case so SHA and
    vendor layout remain byte-stable. Collapse only separators and strip a
    single leading ``./``.
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
    if text.endswith("/") and text not in {"", "/"}:
        text = text.rstrip("/")
    return text


def reject_parent_escape(path: str) -> None:
    """Reject any ``..`` segment (POSIX or Windows-style input already normalized)."""

    parts = [part for part in normalize_relative_posix(path).split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise DistributionPathError(f"path must not contain parent segments: {path!r}")


def require_relative_artifact_path(path: str | Path) -> str:
    """Validate a repository-relative artifact path (no absolute, no ``..``).

    Accepts mixed separators. Returns the POSIX form.
    """

    text = normalize_relative_posix(path)
    if not text or text == "/":
        raise DistributionPathError("relative path must be non-empty")
    if text.startswith("/"):
        raise DistributionPathError(f"absolute POSIX path is forbidden: {path!r}")
    # Windows drive or UNC after strip of leading slash checks
    if re.match(r"^[A-Za-z]:", text) or text.startswith("//"):
        raise DistributionPathError(f"absolute Windows path is forbidden: {path!r}")
    # also catch residual backslash-only absolute forms that normalize oddly
    original = str(path).strip()
    if re.match(r"^[A-Za-z]:[\\/]", original) or original.startswith("\\\\"):
        raise DistributionPathError(f"absolute Windows path is forbidden: {path!r}")
    reject_parent_escape(text)
    return text


def require_vendor_core_path(path: str | Path, *, version: str | None = None) -> str:
    """Require path under ``vendor/core/`` and optionally under a version dir."""

    text = require_relative_artifact_path(path)
    if not text.startswith(VENDOR_CORE_PREFIX):
        raise DistributionPathError(
            f"lock path must stay under {VENDOR_CORE_PREFIX!r}: {path!r}"
        )
    remainder = text[len(VENDOR_CORE_PREFIX) :]
    if not remainder or remainder.startswith("/") or ".." in remainder.split("/"):
        raise DistributionPathError(f"lock path escapes vendor/core: {path!r}")
    if version is not None:
        expected_prefix = f"{VENDOR_CORE_PREFIX}{version}/"
        if not text.startswith(expected_prefix):
            raise DistributionPathError(
                f"lock path must be under {expected_prefix!r}: {path!r}"
            )
    return text


def map_archive_member_to_extract_relative(
    archive_member: str,
    *,
    extract_root_style: str = "posix",
) -> str:
    """Map a ZIP member path (always POSIX inside archives) to a relative extract path.

    ZIP stores forward-slash paths. On Windows the extractor joins them under a
    local root; the *relative* result must still be POSIX-relative without
    ``..`` or absolute prefixes (zip-slip defense).
    """

    # Archives use POSIX; PurePosixPath ignores OS.
    member = str(archive_member).replace("\\", "/")
    if member.startswith("/") or re.match(r"^[A-Za-z]:", member):
        raise DistributionPathError(f"archive member must be relative: {archive_member!r}")
    pure = PurePosixPath(member)
    if pure.is_absolute() or ".." in pure.parts:
        raise DistributionPathError(f"unsafe archive member path: {archive_member!r}")
    relative = pure.as_posix()
    if extract_root_style == "windows":
        # Prove Windows path objects still yield a safe relative POSIX key.
        win = PureWindowsPath(*pure.parts)
        # PureWindowsPath.as_posix() keeps forward slashes for portable keys.
        relative = win.as_posix()
    return require_relative_artifact_path(relative)


def require_sha256_hex(value: str) -> str:
    text = value.strip().lower()
    if _SHA256_HEX.fullmatch(text) is None:
        raise DistributionPathError(f"sha256 must be 64 lowercase hex digits: {value!r}")
    return text


def require_source_commit(value: str) -> str:
    text = value.strip().lower()
    if _COMMIT_HEX.fullmatch(text) is None:
        raise DistributionPathError(f"source_commit must be 40 lowercase hex digits: {value!r}")
    return text


def require_semver(value: str) -> str:
    text = value.strip()
    if _SEMVER_LIKE.fullmatch(text) is None:
        raise DistributionPathError(f"version must be semantic version: {value!r}")
    return text


def require_contract_version(value: str) -> str:
    text = value.strip()
    if _CONTRACT_VERSION.fullmatch(text) is None:
        raise DistributionPathError(
            f"contract_version must be major.minor (non-negative integers): {value!r}"
        )
    return text


def contract_major(contract_version: str) -> int:
    require_contract_version(contract_version)
    return int(contract_version.split(".", 1)[0])


def parse_version_tuple(version: str) -> tuple[int, int, int]:
    """Parse a semantic version core (ignores pre/build for ordering)."""

    require_semver(version)
    core = version.split("-", 1)[0].split("+", 1)[0]
    major_s, minor_s, patch_s = core.split(".", 2)
    return int(major_s), int(minor_s), int(patch_s)


def compare_semver(left: str, right: str) -> int:
    """Return -1/0/1 comparing release cores (pre-release not ordered specially)."""

    a = parse_version_tuple(left)
    b = parse_version_tuple(right)
    if a < b:
        return -1
    if a > b:
        return 1
    return 0
