"""core.lock document builder, parser, and installed-core verifier (plan/observe only).

``build_core_lock`` returns a deterministic UTF-8 TOML string. Callers (or a
human) write the file. ``verify_lock_against_installed`` only judges; it never
installs wheels or mutates the workspace.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
import re
import tomllib

from .contracts import (
    LOCK_FORMAT_VERSION,
    ArtifactRole,
    CoreLockDocument,
    LockArtifact,
    LockVerdict,
    LockVerdictStatus,
)
from .paths import (
    DistributionPathError,
    compare_semver,
    contract_major,
    require_contract_version,
    require_relative_artifact_path,
    require_semver,
    require_sha256_hex,
    require_source_commit,
    require_vendor_core_path,
)


class CoreLockError(ValueError):
    """Raised when a lock document cannot be built or parsed safely."""


_TOML_SIMPLE = re.compile(r'^[A-Za-z0-9_./+:@<>=, \-]+$')


def _escape_toml_string(value: str) -> str:
    """Emit a double-quoted TOML string with minimal escaping."""

    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _assert_printable_field(name: str, value: str) -> None:
    if not value or any(ord(ch) < 32 for ch in value):
        raise CoreLockError(f"{name} must be non-empty printable text")
    if not _TOML_SIMPLE.fullmatch(value):
        # Still allow via escaping, but reject control-like surprises for pins.
        if "\n" in value or "\r" in value:
            raise CoreLockError(f"{name} must not contain newlines")


def _normalize_artifact(artifact: LockArtifact) -> LockArtifact:
    role = artifact.role if isinstance(artifact.role, ArtifactRole) else ArtifactRole(str(artifact.role))
    name = artifact.name.strip()
    version = require_semver(artifact.version)
    # Core wheels live under vendor/core/<core-version>/. Dependency wheels for a
    # release may sit in the same core-version directory, so deps only require the
    # vendor/core/ prefix (ADR-001 wheelhouse layout).
    if role is ArtifactRole.CORE:
        path = require_vendor_core_path(artifact.path, version=version)
    else:
        path = require_vendor_core_path(artifact.path, version=None)
    sha256 = require_sha256_hex(artifact.sha256)
    _assert_printable_field("name", name)

    contract_version = artifact.contract_version
    source_commit = artifact.source_commit
    requires_python = artifact.requires_python

    if role is ArtifactRole.CORE:
        if not contract_version or not source_commit or not requires_python:
            raise CoreLockError(
                "core artifact requires contract_version, source_commit, and requires_python"
            )
        contract_version = require_contract_version(contract_version)
        source_commit = require_source_commit(source_commit)
        requires_python = requires_python.strip()
        _assert_printable_field("requires_python", requires_python)
    else:
        # dependency: ADR-001 example omits contract/source/requires_python
        if contract_version is not None or source_commit is not None or requires_python is not None:
            # Allow only if all three are None for deps — keep surface closed.
            raise CoreLockError(
                "dependency artifacts must omit contract_version, source_commit, requires_python"
            )

    return LockArtifact(
        role=role,
        name=name,
        version=version,
        path=path,
        sha256=sha256,
        contract_version=contract_version,
        source_commit=source_commit,
        requires_python=requires_python,
    )


def _artifact_sort_key(artifact: LockArtifact) -> tuple[int, str, str, str]:
    role_rank = 0 if artifact.role is ArtifactRole.CORE else 1
    return (role_rank, artifact.name, artifact.version, artifact.path)


def validate_lock_document(document: CoreLockDocument) -> CoreLockDocument:
    """Return a normalized copy or raise ``CoreLockError``."""

    if document.lock_format != LOCK_FORMAT_VERSION:
        raise CoreLockError(
            f"unsupported lock_format={document.lock_format!r}; only {LOCK_FORMAT_VERSION} is supported"
        )
    selected = document.selected_distribution.strip()
    _assert_printable_field("selected_distribution", selected)
    if not document.artifacts:
        raise CoreLockError("core.lock must contain at least one [[artifacts]] entry")

    try:
        normalized = tuple(
            sorted((_normalize_artifact(item) for item in document.artifacts), key=_artifact_sort_key)
        )
    except DistributionPathError as error:
        raise CoreLockError(str(error)) from error
    core_items = [item for item in normalized if item.role is ArtifactRole.CORE]
    if len(core_items) != 1:
        raise CoreLockError("core.lock must contain exactly one role=core artifact")
    core = core_items[0]
    if core.name != selected:
        raise CoreLockError(
            f"selected_distribution {selected!r} must match core artifact name {core.name!r}"
        )
    return CoreLockDocument(
        lock_format=LOCK_FORMAT_VERSION,
        selected_distribution=selected,
        artifacts=normalized,
    )


def build_core_lock(
    artifacts: Sequence[LockArtifact] | Iterable[LockArtifact],
    *,
    selected_distribution: str = "video-production-core",
    lock_format: int = LOCK_FORMAT_VERSION,
) -> str:
    """Build a deterministic ``core.lock`` TOML string (LF, sorted artifacts).

    Determinism rules (same inputs → identical bytes):
    1. UTF-8 text with LF line endings only (``\\n``); no BOM.
    2. Header keys in fixed order: ``lock_format``, ``selected_distribution``.
    3. ``[[artifacts]]`` sorted by role (core first), then name, version, path.
    4. Per-artifact keys in fixed order; dependency tables omit core-only keys.
    5. Exactly one trailing newline; no trailing spaces; blank line between tables.
    6. Artifacts are normalized (POSIX path, lowercase hex digests) before emit.
    """

    document = validate_lock_document(
        CoreLockDocument(
            lock_format=lock_format,
            selected_distribution=selected_distribution,
            artifacts=tuple(artifacts),
        )
    )
    lines: list[str] = [
        f"lock_format = {document.lock_format}",
        f"selected_distribution = {_escape_toml_string(document.selected_distribution)}",
        "",
    ]
    for index, artifact in enumerate(document.artifacts):
        if index > 0:
            lines.append("")
        lines.append("[[artifacts]]")
        lines.append(f"role = {_escape_toml_string(artifact.role.value)}")
        lines.append(f"name = {_escape_toml_string(artifact.name)}")
        lines.append(f"version = {_escape_toml_string(artifact.version)}")
        if artifact.role is ArtifactRole.CORE:
            assert artifact.contract_version is not None
            assert artifact.source_commit is not None
            assert artifact.requires_python is not None
            lines.append(f"contract_version = {_escape_toml_string(artifact.contract_version)}")
            lines.append(f"source_commit = {_escape_toml_string(artifact.source_commit)}")
            lines.append(f"path = {_escape_toml_string(artifact.path)}")
            lines.append(f"sha256 = {_escape_toml_string(artifact.sha256)}")
            lines.append(f"requires_python = {_escape_toml_string(artifact.requires_python)}")
        else:
            lines.append(f"path = {_escape_toml_string(artifact.path)}")
            lines.append(f"sha256 = {_escape_toml_string(artifact.sha256)}")
    return "\n".join(lines) + "\n"


def parse_core_lock(text: str) -> CoreLockDocument:
    """Parse TOML text into a validated ``CoreLockDocument`` (stdlib tomllib)."""

    if not isinstance(text, str):
        raise CoreLockError("core.lock text must be a str")
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise CoreLockError(f"invalid TOML: {error}") from error
    if not isinstance(raw, dict):
        raise CoreLockError("core.lock root must be a table")

    lock_format = raw.get("lock_format")
    selected = raw.get("selected_distribution")
    artifacts_raw = raw.get("artifacts")
    if not isinstance(lock_format, int):
        raise CoreLockError("lock_format must be an integer")
    if not isinstance(selected, str):
        raise CoreLockError("selected_distribution must be a string")
    if not isinstance(artifacts_raw, list) or not artifacts_raw:
        raise CoreLockError("artifacts must be a non-empty array of tables")

    artifacts: list[LockArtifact] = []
    for index, item in enumerate(artifacts_raw):
        if not isinstance(item, dict):
            raise CoreLockError(f"artifacts[{index}] must be a table")
        try:
            role = ArtifactRole(str(item.get("role", "")))
        except ValueError as error:
            raise CoreLockError(f"artifacts[{index}].role is invalid") from error
        name = item.get("name")
        version = item.get("version")
        path = item.get("path")
        sha256 = item.get("sha256")
        if not all(isinstance(value, str) for value in (name, version, path, sha256)):
            raise CoreLockError(f"artifacts[{index}] missing required string fields")
        contract_version = item.get("contract_version")
        source_commit = item.get("source_commit")
        requires_python = item.get("requires_python")
        for optional_name, optional_value in (
            ("contract_version", contract_version),
            ("source_commit", source_commit),
            ("requires_python", requires_python),
        ):
            if optional_value is not None and not isinstance(optional_value, str):
                raise CoreLockError(f"artifacts[{index}].{optional_name} must be a string when present")
        artifacts.append(
            LockArtifact(
                role=role,
                name=str(name),
                version=str(version),
                path=str(path),
                sha256=str(sha256),
                contract_version=contract_version if isinstance(contract_version, str) else None,
                source_commit=source_commit if isinstance(source_commit, str) else None,
                requires_python=requires_python if isinstance(requires_python, str) else None,
            )
        )
    return validate_lock_document(
        CoreLockDocument(
            lock_format=lock_format,
            selected_distribution=selected,
            artifacts=tuple(artifacts),
        )
    )


def _parse_python_version(version: str) -> tuple[int, int, int]:
    text = version.strip()
    if text.startswith("Python "):
        text = text[len("Python ") :]
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", text)
    if match is None:
        raise CoreLockError(f"cannot parse Python version: {version!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def python_version_satisfies(installed_python: str, requires_python: str) -> bool:
    """Evaluate a small subset of PEP 440 markers used by this project.

    Supported forms (comma = AND): ``>=3.12``, ``<3.13``, ``==3.12.*`` style is
    not required — OD-003 uses ``>=3.12,<3.13``.
    """

    version = _parse_python_version(installed_python)
    clauses = [part.strip() for part in requires_python.split(",") if part.strip()]
    if not clauses:
        raise CoreLockError("requires_python must not be empty")

    for clause in clauses:
        match = re.fullmatch(r"(>=|<=|>|<|==|!=)\s*(.+)", clause)
        if match is None:
            raise CoreLockError(f"unsupported requires_python clause: {clause!r}")
        op, rhs = match.group(1), match.group(2).strip()
        # strip trailing .* for equality checks if present
        rhs_clean = rhs[:-2] if rhs.endswith(".*") else rhs
        target = _parse_python_version(rhs_clean)
        # For two-component targets like 3.12, treat missing patch as 0 for
        # ordering, but for <3.13 compare against (3,13,0).
        if op == ">=":
            ok = version >= target
        elif op == ">":
            ok = version > target
        elif op == "<=":
            ok = version <= target
        elif op == "<":
            ok = version < target
        elif op == "==":
            if rhs.endswith(".*"):
                ok = version[:2] == target[:2]
            else:
                ok = version == target
        elif op == "!=":
            ok = version != target
        else:  # pragma: no cover - regex limits ops
            ok = False
        if not ok:
            return False
    return True


def verify_lock_against_installed(
    lock: CoreLockDocument | str,
    installed_version: str,
    installed_contract: str,
    *,
    installed_python: str | None = None,
) -> LockVerdict:
    """Judge installed core vs lock. Observation only — no install side effects.

    Breaking definition (data, ADR-001): ``contract_version`` **major** differs.
    Same major with distribution version drift is non-breaking and may surface as
    ``upgrade_available``. The lock path/version remain the pin for channel assets;
    a newer installed core never forces replacement of lock-pinned artifacts.
    """

    try:
        document = parse_core_lock(lock) if isinstance(lock, str) else validate_lock_document(lock)
        installed_version_n = require_semver(installed_version)
        installed_contract_n = require_contract_version(installed_contract)
    except (CoreLockError, DistributionPathError, ValueError) as error:
        return LockVerdict(
            status=LockVerdictStatus.INVALID_LOCK,
            reason=str(error),
            lock_core_version=None,
            lock_contract_version=None,
            lock_core_path=None,
            installed_version=installed_version,
            installed_contract=installed_contract,
            lock_pins_selected_version=False,
            forces_newer_install=False,
        )

    core = document.core_artifact()
    assert core is not None  # validate_lock_document guarantees one core
    assert core.contract_version is not None
    lock_major = contract_major(core.contract_version)
    installed_major = contract_major(installed_contract_n)

    # requires_python check first when caller supplies an interpreter version
    if installed_python is not None and core.requires_python:
        try:
            if not python_version_satisfies(installed_python, core.requires_python):
                return LockVerdict(
                    status=LockVerdictStatus.PYTHON_MISMATCH,
                    reason=(
                        f"installed Python {installed_python!r} does not satisfy "
                        f"requires_python={core.requires_python!r}"
                    ),
                    lock_core_version=core.version,
                    lock_contract_version=core.contract_version,
                    lock_core_path=core.path,
                    installed_version=installed_version_n,
                    installed_contract=installed_contract_n,
                    lock_pins_selected_version=True,
                    forces_newer_install=False,
                )
        except CoreLockError as error:
            return LockVerdict(
                status=LockVerdictStatus.INVALID_LOCK,
                reason=str(error),
                lock_core_version=core.version,
                lock_contract_version=core.contract_version,
                lock_core_path=core.path,
                installed_version=installed_version_n,
                installed_contract=installed_contract_n,
                lock_pins_selected_version=True,
                forces_newer_install=False,
            )

    if lock_major != installed_major:
        return LockVerdict(
            status=LockVerdictStatus.BREAKING,
            reason=(
                f"contract major mismatch: lock={core.contract_version!r} "
                f"installed={installed_contract_n!r}"
            ),
            lock_core_version=core.version,
            lock_contract_version=core.contract_version,
            lock_core_path=core.path,
            installed_version=installed_version_n,
            installed_contract=installed_contract_n,
            lock_pins_selected_version=True,
            forces_newer_install=False,
        )

    version_cmp = compare_semver(installed_version_n, core.version)
    if version_cmp == 0 and installed_contract_n == core.contract_version:
        return LockVerdict(
            status=LockVerdictStatus.COMPATIBLE,
            reason="installed distribution and contract match the lock pin",
            lock_core_version=core.version,
            lock_contract_version=core.contract_version,
            lock_core_path=core.path,
            installed_version=installed_version_n,
            installed_contract=installed_contract_n,
            lock_pins_selected_version=True,
            forces_newer_install=False,
        )

    # Same contract major, version or minor contract drift → non-breaking.
    # Channel assets stay on lock_core_path until a human rewrites core.lock.
    direction = "newer" if version_cmp > 0 else "older"
    return LockVerdict(
        status=LockVerdictStatus.UPGRADE_AVAILABLE,
        reason=(
            f"same contract major ({lock_major}); installed distribution is {direction} "
            f"than lock pin ({core.version} → {installed_version_n}); "
            f"lock still pins {core.path}"
        ),
        lock_core_version=core.version,
        lock_contract_version=core.contract_version,
        lock_core_path=core.path,
        installed_version=installed_version_n,
        installed_contract=installed_contract_n,
        lock_pins_selected_version=True,
        forces_newer_install=False,
    )


def lock_document_from_core(
    *,
    version: str,
    contract_version: str,
    source_commit: str,
    sha256: str,
    requires_python: str = ">=3.12,<3.13",
    name: str = "video-production-core",
    wheel_filename: str | None = None,
    dependencies: Sequence[LockArtifact] = (),
) -> CoreLockDocument:
    """Convenience constructor for synthetic fixtures and planning helpers."""

    filename = wheel_filename or default_wheel_filename(name, version)
    path = require_relative_artifact_path(f"vendor/core/{require_semver(version)}/{filename}")
    core = LockArtifact(
        role=ArtifactRole.CORE,
        name=name,
        version=version,
        path=path,
        sha256=sha256,
        contract_version=contract_version,
        source_commit=source_commit,
        requires_python=requires_python,
    )
    return validate_lock_document(
        CoreLockDocument(
            lock_format=LOCK_FORMAT_VERSION,
            selected_distribution=name,
            artifacts=(core, *dependencies),
        )
    )


def default_wheel_filename(package_name: str, version: str) -> str:
    """setuptools-style pure-Python wheel filename (py3-none-any)."""

    normalized = package_name.replace("-", "_")
    return f"{normalized}-{require_semver(version)}-py3-none-any.whl"
