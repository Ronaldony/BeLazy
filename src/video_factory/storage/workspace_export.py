"""Workspace export *planning* with personal-data pattern scanning.

Scans a source directory for the same *kinds* of sensitive patterns documented
in the migration baseline (Windows absolute paths, email addresses, session or
run UUIDs, API-key shapes). Violations refuse the export entirely
(all-or-nothing): the plan is rejected and lists no files to write.

Patterns are general regular expressions. They intentionally do **not** hard-code
any host username or machine path so the engine works for any operator.

Core never writes a directory tree or archive. Callers execute a READY plan
outside this package. This module performs no publish or upload side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import re
from typing import Iterator, Pattern

from video_factory.storage.frozen_index import (
    FrozenIndex,
    FrozenIndexViolation,
    assert_write_allowed,
    assert_write_paths_allowed,
)
from video_factory.storage.tree_safety import (
    SafeTreeError,
    iter_regular_files_no_follow,
    require_safe_path_chain,
)


# Pattern kinds mirror migration/baseline/sensitive_patterns.md categories
# (Windows absolute path, email, session/run UUID, API key shapes). Usernames
# are *not* hard-coded; baseline used an environment-specific username regex
# which is host-dependent and therefore omitted here.
_PATTERN_SPECS: tuple[tuple[str, Pattern[str]], ...] = (
    (
        "windows_absolute_path",
        re.compile(r"(?i)\b[A-Za-z]:[\\/][^\s\"'<>|]+"),
    ),
    (
        "email_address",
        re.compile(
            r"(?i)(?<![A-Z0-9._%+\-])[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}"
            r"(?![A-Z0-9._%+\-])"
        ),
    ),
    (
        "session_or_run_uuid",
        re.compile(
            r"(?i)(?<![0-9a-f])(?:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}|[0-9a-f]{32})(?![0-9a-f])"
        ),
    ),
    (
        "api_key_shape",
        re.compile(
            r"(?<![A-Za-z0-9])(?:sk-[A-Za-z0-9_-]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
            r"gh[pousr]_[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{20,}|"
            r"xox[baprs]-[A-Za-z0-9-]{15,}|AKIA[0-9A-Z]{16})(?![A-Za-z0-9])"
        ),
    ),
    (
        "api_key_assignment",
        re.compile(
            r"(?i)(\b(?:api[_-]?key|access[_-]?token|secret[_-]?key)\b\s*[:=]\s*[\"']?)"
            r"([^\s\"',;}]{4,})"
        ),
    ),
)

_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "__pycache__",
        ".venv",
        "node_modules",
    }
)

_TEXT_SUFFIXES = frozenset(
    {
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".py",
        ".csv",
        ".ini",
        ".cfg",
        ".xml",
        ".html",
        ".css",
        ".js",
        ".ts",
        ".sh",
        ".ps1",
        ".bat",
        ".cmd",
        ".env",
        ".rst",
        ".svg",
    }
)


class WorkspaceExportError(RuntimeError):
    """Raised by low-level scan helpers when the source tree cannot be read."""

    def __init__(
        self,
        message: str,
        *,
        violations: tuple["SensitiveFinding", ...] = (),
    ) -> None:
        super().__init__(message)
        self.violations = violations


class WorkspaceExportPlanStatus(StrEnum):
    READY = "ready"
    REJECTED_INVALID_SOURCE = "rejected_invalid_source"
    REJECTED_SENSITIVE = "rejected_sensitive"
    REJECTED_FROZEN_INDEX = "rejected_frozen_index"
    REJECTED_TARGET_EXISTS = "rejected_target_exists"
    REJECTED_UNSAFE_TREE = "rejected_unsafe_tree"


@dataclass(frozen=True, slots=True)
class SensitiveFinding:
    relative_path: str
    line_number: int
    pattern_id: str


@dataclass(frozen=True, slots=True)
class WorkspaceExportPlan:
    """Structured export plan — never implies execution (``executed`` is always false)."""

    status: WorkspaceExportPlanStatus
    source_dir: Path
    target: Path
    output_kind: str  # "directory" | "zip" | ""
    files: tuple[str, ...]
    files_scanned: int
    pattern_kinds_checked: tuple[str, ...]
    findings: tuple[SensitiveFinding, ...]
    executed: bool = False
    planning_only: bool = True
    authorization_ready: bool = False
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        if self.executed is not False:
            object.__setattr__(self, "executed", False)
        if self.planning_only is not True:
            object.__setattr__(self, "planning_only", True)
        if self.authorization_ready is not False:
            object.__setattr__(self, "authorization_ready", False)


def pattern_kind_ids() -> tuple[str, ...]:
    """Stable list of pattern kinds the scanner evaluates."""

    return tuple(kind for kind, _pattern in _PATTERN_SPECS)


def _should_skip_dir(name: str) -> bool:
    return name in _SKIP_DIR_NAMES


def _iter_source_files(source_dir: Path) -> Iterator[Path]:
    yield from iter_regular_files_no_follow(
        source_dir,
        skip_directory_names=_SKIP_DIR_NAMES,
    )


def _looks_like_text(path: Path, payload: bytes) -> bool:
    if path.suffix.lower() in _TEXT_SUFFIXES:
        return True
    if path.name.lower() in {".env", "dockerfile", "makefile", "license", "readme"}:
        return True
    # Heuristic: UTF-8 without NUL in the first 8 KiB.
    sample = payload[:8192]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def scan_for_sensitive_content(
    source_dir: Path,
    *,
    _files: tuple[Path, ...] | None = None,
) -> tuple[list[SensitiveFinding], int]:
    """Scan *source_dir* text files; return findings and files-scanned count."""

    findings: list[SensitiveFinding] = []
    scanned = 0
    try:
        files = tuple(_iter_source_files(source_dir)) if _files is None else _files
    except SafeTreeError as error:
        raise WorkspaceExportError(str(error)) from error
    for path in files:
        payload = path.read_bytes()
        if not _looks_like_text(path, payload):
            continue
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        scanned += 1
        relative = path.relative_to(source_dir).as_posix()
        for line_number, line in enumerate(text.splitlines(), start=1):
            for pattern_id, pattern in _PATTERN_SPECS:
                if pattern.search(line):
                    findings.append(
                        SensitiveFinding(
                            relative_path=relative,
                            line_number=line_number,
                            pattern_id=pattern_id,
                        )
                    )
    return findings, scanned


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


def _plan_output_kind(target: Path) -> str:
    return "zip" if target.suffix.lower() == ".zip" else "directory"


def _rejected(
    status: WorkspaceExportPlanStatus,
    source: Path,
    target: Path,
    reason: str,
    *,
    files_scanned: int = 0,
    findings: tuple[SensitiveFinding, ...] = (),
    output_kind: str = "",
) -> WorkspaceExportPlan:
    return WorkspaceExportPlan(
        status=status,
        source_dir=source,
        target=target,
        output_kind=output_kind,
        files=(),
        files_scanned=files_scanned,
        pattern_kinds_checked=pattern_kind_ids(),
        findings=findings,
        executed=False,
        rejection_reason=reason,
    )


def plan_export(
    source_dir: Path,
    target: Path,
    *,
    frozen_index: FrozenIndex | None = None,
) -> WorkspaceExportPlan:
    """Scan *source_dir* and return an export plan for *target*.

    If any sensitive pattern matches, returns ``REJECTED_SENSITIVE`` with
    findings and an empty file list. When *frozen_index* is provided,
    destination-relative paths listed in the index are also refused.
    Existing nonempty targets (or an existing ``.zip`` path) yield
    ``REJECTED_TARGET_EXISTS``. Never writes.
    """

    source = Path(source_dir)
    destination = Path(target)
    output_kind = _plan_output_kind(destination)

    try:
        source_files = tuple(_iter_source_files(source))
    except SafeTreeError as error:
        status = (
            WorkspaceExportPlanStatus.REJECTED_INVALID_SOURCE
            if error.code in {"tree.missing", "tree.not_directory"}
            else WorkspaceExportPlanStatus.REJECTED_UNSAFE_TREE
        )
        return _rejected(
            status,
            source,
            destination,
            str(error),
            output_kind=output_kind,
        )

    try:
        require_safe_path_chain(destination, must_exist=False)
    except SafeTreeError as error:
        return _rejected(
            WorkspaceExportPlanStatus.REJECTED_UNSAFE_TREE,
            source,
            destination,
            str(error),
            output_kind=output_kind,
        )

    try:
        findings, scanned = scan_for_sensitive_content(source, _files=source_files)
    except WorkspaceExportError as error:
        return _rejected(
            WorkspaceExportPlanStatus.REJECTED_INVALID_SOURCE,
            source,
            destination,
            str(error),
            output_kind=output_kind,
        )

    if findings:
        summary = "; ".join(
            f"{item.relative_path}:{item.line_number}:{item.pattern_id}"
            for item in findings[:12]
        )
        more = "" if len(findings) <= 12 else f" (+{len(findings) - 12} more)"
        return _rejected(
            WorkspaceExportPlanStatus.REJECTED_SENSITIVE,
            source,
            destination,
            f"export refused: {len(findings)} sensitive finding(s): {summary}{more}",
            files_scanned=scanned,
            findings=tuple(findings),
            output_kind=output_kind,
        )

    relative_files = [path.relative_to(source).as_posix() for path in source_files]
    try:
        assert_write_paths_allowed(relative_files, frozen_index)
        if not destination.is_absolute():
            assert_write_allowed(destination, frozen_index)
    except FrozenIndexViolation as error:
        return _rejected(
            WorkspaceExportPlanStatus.REJECTED_FROZEN_INDEX,
            source,
            destination,
            str(error),
            files_scanned=scanned,
            output_kind=output_kind,
        )

    if output_kind == "zip":
        if destination.exists():
            return _rejected(
                WorkspaceExportPlanStatus.REJECTED_TARGET_EXISTS,
                source,
                destination,
                f"export archive already exists (refusing overwrite): {destination}",
                files_scanned=scanned,
                output_kind=output_kind,
            )
    elif _is_nonempty_path(destination):
        return _rejected(
            WorkspaceExportPlanStatus.REJECTED_TARGET_EXISTS,
            source,
            destination,
            f"export target already exists and is not empty (refusing overwrite): {destination}",
            files_scanned=scanned,
            output_kind=output_kind,
        )

    return WorkspaceExportPlan(
        status=WorkspaceExportPlanStatus.READY,
        source_dir=source.resolve(),
        target=destination,
        output_kind=output_kind,
        files=tuple(relative_files),
        files_scanned=scanned,
        pattern_kinds_checked=pattern_kind_ids(),
        findings=(),
        executed=False,
        rejection_reason=None,
    )


class WorkspaceExportEngine:
    """Thin object façade over :func:`plan_export` for registry/backing clarity."""

    def plan_export(
        self,
        source_dir: Path,
        target_archive_or_dir: Path,
        *,
        frozen_index: FrozenIndex | None = None,
    ) -> WorkspaceExportPlan:
        return plan_export(
            source_dir, target_archive_or_dir, frozen_index=frozen_index
        )

    def scan(
        self, source_dir: Path
    ) -> tuple[list[SensitiveFinding], int]:
        return scan_for_sensitive_content(source_dir)
