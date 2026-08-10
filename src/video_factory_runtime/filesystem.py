"""Fixture-only stable-byte observation and content-addressed storage."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import stat
from typing import Iterator

from video_factory.config.canonical import canonical_json_bytes, canonical_sha256
from video_factory.domain import ArtifactReference, ArtifactVersion, HashDigest, OpaqueId
from video_factory.mutation import (
    ContentObject,
    ContentObjectObservation,
    MutationKind,
    PathNodeKind,
    PathObservation,
    WorkspaceObservation,
    WorkspaceTrustState,
    require_managed_path,
    workspace_observation_mapping,
)

from .boundary import FixtureRuntimeBoundary


MANIFEST_VERSION = "fixture-workspace-manifest/1.0"
CONTENT_EVIDENCE_VERSION = "content-resolver-evidence/1.0"
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_BINARY = getattr(os, "O_BINARY", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class RuntimeFilesystemError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class StableFile:
    path: Path
    exact_sha256: HashDigest
    byte_length: int
    identity: tuple[int, int, int, int]


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return (int(info.st_dev), int(info.st_ino), int(info.st_size), int(info.st_mtime_ns))


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_FLAG)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_new(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def read_stable_regular_file(path: Path) -> StableFile:
    """Hash one regular file while proving its path identity stayed stable."""

    path = Path(path)
    try:
        before_path = path.lstat()
    except OSError as error:
        raise RuntimeFilesystemError(
            "runtime.filesystem.file_missing", f"cannot inspect file: {path}"
        ) from error
    if stat.S_ISLNK(before_path.st_mode) or _is_reparse(before_path):
        raise RuntimeFilesystemError(
            "runtime.filesystem.link_or_reparse", f"file is a link or reparse point: {path}"
        )
    if not stat.S_ISREG(before_path.st_mode):
        raise RuntimeFilesystemError(
            "runtime.filesystem.not_regular", f"path is not a regular file: {path}"
        )
    try:
        descriptor = os.open(path, os.O_RDONLY | _BINARY | _NOFOLLOW)
    except OSError as error:
        raise RuntimeFilesystemError(
            "runtime.filesystem.open", f"cannot open regular file: {path}"
        ) from error
    digest = hashlib.sha256()
    length = 0
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before_path):
            raise RuntimeFilesystemError(
                "runtime.filesystem.identity_changed", f"file identity changed before reading: {path}"
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            length += len(chunk)
        after_fd = os.fstat(descriptor)
        if _identity(after_fd) != _identity(opened):
            raise RuntimeFilesystemError(
                "runtime.filesystem.bytes_changed", f"file changed while being read: {path}"
            )
    finally:
        os.close(descriptor)
    try:
        after_path = path.lstat()
    except OSError as error:
        raise RuntimeFilesystemError(
            "runtime.filesystem.path_changed", f"file disappeared after reading: {path}"
        ) from error
    if _identity(after_path) != _identity(before_path):
        raise RuntimeFilesystemError(
            "runtime.filesystem.path_changed", f"file path changed after reading: {path}"
        )
    return StableFile(
        path=path,
        exact_sha256=HashDigest(digest.hexdigest()),
        byte_length=length,
        identity=_identity(after_path),
    )


def read_stable_regular_bytes(path: Path) -> tuple[StableFile, bytes]:
    """Read exact bytes through the same no-follow descriptor that is verified."""

    path = Path(path)
    try:
        before_path = path.lstat()
    except OSError as error:
        raise RuntimeFilesystemError(
            "runtime.filesystem.file_missing", f"cannot inspect file: {path}"
        ) from error
    if (
        stat.S_ISLNK(before_path.st_mode)
        or _is_reparse(before_path)
        or not stat.S_ISREG(before_path.st_mode)
    ):
        raise RuntimeFilesystemError(
            "runtime.filesystem.not_regular",
            f"path is not a no-follow regular file: {path}",
        )
    try:
        descriptor = os.open(path, os.O_RDONLY | _BINARY | _NOFOLLOW)
    except OSError as error:
        raise RuntimeFilesystemError(
            "runtime.filesystem.open", f"cannot open regular file: {path}"
        ) from error
    digest = hashlib.sha256()
    payload = bytearray()
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before_path):
            raise RuntimeFilesystemError(
                "runtime.filesystem.identity_changed",
                f"file identity changed before reading: {path}",
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            payload.extend(chunk)
        after_fd = os.fstat(descriptor)
        if _identity(after_fd) != _identity(opened):
            raise RuntimeFilesystemError(
                "runtime.filesystem.bytes_changed",
                f"file changed while being read: {path}",
            )
    finally:
        os.close(descriptor)
    try:
        after_path = path.lstat()
    except OSError as error:
        raise RuntimeFilesystemError(
            "runtime.filesystem.path_changed", f"file disappeared after reading: {path}"
        ) from error
    if _identity(after_path) != _identity(before_path):
        raise RuntimeFilesystemError(
            "runtime.filesystem.path_changed", f"file path changed after reading: {path}"
        )
    stable = StableFile(
        path=path,
        exact_sha256=HashDigest(digest.hexdigest()),
        byte_length=len(payload),
        identity=_identity(after_path),
    )
    return stable, bytes(payload)


@contextmanager
def _exclusive_fixture_lock(path: Path) -> Iterator[None]:
    """Cross-process lock used only inside a marker-bound fixture runtime."""

    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | _BINARY, 0o600)
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            yield
        finally:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


class _GuardedFixtureDirectories:
    """Exact directory capabilities held for one managed mutation."""

    def __init__(self, root: Path, descriptors: dict[Path, int] | None = None) -> None:
        self.root = Path(root)
        self._descriptors = descriptors or {}

    def descriptor(self, path: Path) -> int:
        if os.name == "nt":
            raise RuntimeFilesystemError(
                "runtime.mutation.directory_capability",
                "POSIX directory capabilities are unavailable on Windows",
            )
        try:
            return self._descriptors[Path(path)]
        except KeyError as error:
            raise RuntimeFilesystemError(
                "runtime.mutation.directory_capability",
                f"managed directory capability is missing: {path}",
            ) from error


@contextmanager
def _guard_fixture_directories(
    root: Path,
    directories: tuple[Path, ...],
) -> Iterator[_GuardedFixtureDirectories]:
    """Hold every managed ancestor open while a fixture mutation is in flight.

    On Windows the directory handles intentionally omit ``FILE_SHARE_DELETE``.
    That prevents another process from renaming an already-validated ancestor
    into a junction/reparse path between the final containment check and the
    filesystem syscall.  POSIX uses no-follow directory descriptors and keeps
    the complete ancestor chain alive for the same fixture boundary.
    """

    root = Path(root)
    ordered: list[Path] = [root]
    seen = {root}
    for directory in directories:
        directory = Path(directory)
        try:
            relative = directory.relative_to(root)
        except ValueError as error:
            raise RuntimeFilesystemError(
                "runtime.mutation.directory_escape",
                "mutation directory is outside the fixture workspace",
            ) from error
        current = root
        for segment in relative.parts:
            current = current / segment
            if current not in seen:
                seen.add(current)
                ordered.append(current)

    handles: list[int] = []
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class _ByHandleFileInformation(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME),
                ("ftLastWriteTime", wintypes.FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        get_information = kernel32.GetFileInformationByHandle
        get_information.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(_ByHandleFileInformation),
        )
        get_information.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        file_read_attributes = 0x0080
        synchronize = 0x00100000
        share_read_write = 0x00000001 | 0x00000002
        open_existing = 3
        backup_semantics = 0x02000000
        open_reparse_point = 0x00200000
        file_attribute_directory = 0x00000010
        invalid_handle = ctypes.c_void_p(-1).value
        try:
            for directory in ordered:
                handle = create_file(
                    str(directory),
                    file_read_attributes | synchronize,
                    share_read_write,
                    None,
                    open_existing,
                    backup_semantics | open_reparse_point,
                    None,
                )
                if handle == invalid_handle:
                    raise RuntimeFilesystemError(
                        "runtime.mutation.directory_lock",
                        f"cannot lock managed directory: {directory}",
                    )
                handles.append(handle)
                information = _ByHandleFileInformation()
                if not get_information(handle, ctypes.byref(information)):
                    raise RuntimeFilesystemError(
                        "runtime.mutation.directory_identity",
                        f"cannot inspect locked managed directory: {directory}",
                    )
                if (
                    not information.dwFileAttributes & file_attribute_directory
                    or information.dwFileAttributes & _REPARSE_FLAG
                ):
                    raise RuntimeFilesystemError(
                        "runtime.mutation.directory_reparse",
                        f"managed directory is not a no-follow directory: {directory}",
                    )
            yield _GuardedFixtureDirectories(root)
        finally:
            for handle in reversed(handles):
                close_handle(handle)
        return

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW
    descriptors: dict[Path, int] = {}
    try:
        for directory in ordered:
            try:
                if directory == root:
                    descriptor = os.open(directory, directory_flags)
                else:
                    descriptor = os.open(
                        directory.name,
                        directory_flags,
                        dir_fd=descriptors[directory.parent],
                    )
            except (KeyError, OSError) as error:
                raise RuntimeFilesystemError(
                    "runtime.mutation.directory_open",
                    f"cannot open managed directory capability: {directory}",
                ) from error
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode):
                os.close(descriptor)
                raise RuntimeFilesystemError(
                    "runtime.mutation.directory_type",
                    f"managed ancestor is not a directory: {directory}",
                )
            handles.append(descriptor)
            descriptors[directory] = descriptor
        yield _GuardedFixtureDirectories(root, descriptors)
    finally:
        for descriptor in reversed(handles):
            os.close(descriptor)


def _posix_entry_exists(directory_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as error:
        raise RuntimeFilesystemError(
            "runtime.mutation.path_stat",
            f"cannot inspect managed directory entry: {name}",
        ) from error
    return True


def _posix_require_absent(directory_fd: int, name: str) -> None:
    if _posix_entry_exists(directory_fd, name):
        raise RuntimeFilesystemError(
            "runtime.filesystem.target_exists",
            f"managed target already exists: {name}",
        )


def _posix_write_new(directory_fd: int, name: str, payload: bytes) -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY | _NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(directory_fd)


def _posix_stable_regular_file(
    directory_fd: int,
    name: str,
    *,
    display_path: Path,
) -> StableFile:
    try:
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        descriptor = os.open(
            name,
            os.O_RDONLY | _BINARY | _NOFOLLOW,
            dir_fd=directory_fd,
        )
    except OSError as error:
        raise RuntimeFilesystemError(
            "runtime.filesystem.open",
            f"cannot open exact managed file: {display_path}",
        ) from error
    digest = hashlib.sha256()
    length = 0
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or _identity(before) != _identity(opened)
        ):
            raise RuntimeFilesystemError(
                "runtime.filesystem.identity_changed",
                f"managed entry changed before reading: {display_path}",
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            length += len(chunk)
        after_fd = os.fstat(descriptor)
        if _identity(after_fd) != _identity(opened):
            raise RuntimeFilesystemError(
                "runtime.filesystem.bytes_changed",
                f"managed file changed while reading: {display_path}",
            )
    finally:
        os.close(descriptor)
    try:
        after_entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as error:
        raise RuntimeFilesystemError(
            "runtime.filesystem.path_changed",
            f"managed entry disappeared after reading: {display_path}",
        ) from error
    if _identity(after_entry) != _identity(before):
        raise RuntimeFilesystemError(
            "runtime.filesystem.path_changed",
            f"managed entry changed after reading: {display_path}",
        )
    return StableFile(
        path=display_path,
        exact_sha256=HashDigest(digest.hexdigest()),
        byte_length=length,
        identity=_identity(after_entry),
    )


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _WIN_GENERIC_READ = 0x80000000
    _WIN_DELETE = 0x00010000
    _WIN_FILE_READ_ATTRIBUTES = 0x0080
    _WIN_SYNCHRONIZE = 0x00100000
    _WIN_FILE_SHARE_READ_WRITE = 0x00000001 | 0x00000002
    _WIN_OPEN_EXISTING = 3
    _WIN_BACKUP_SEMANTICS = 0x02000000
    _WIN_OPEN_REPARSE_POINT = 0x00200000
    _WIN_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _WIN_FILE_BEGIN = 0
    _WIN_FILE_RENAME_INFO = 10
    _WIN_FILE_DISPOSITION_INFO = 4
    _WIN_FILE_LINK_INFO = 11
    _WIN_INVALID_HANDLE = ctypes.c_void_p(-1).value

    class _WinByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    class _WinFileNameInformation(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOLEAN),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    class _WinFileDispositionInformation(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

    class _WinIoStatusBlock(ctypes.Structure):
        _fields_ = [
            ("Status", ctypes.c_long),
            ("Information", ctypes.c_size_t),
        ]

    _WIN_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _WIN_CREATE_FILE = _WIN_KERNEL32.CreateFileW
    _WIN_CREATE_FILE.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _WIN_CREATE_FILE.restype = wintypes.HANDLE
    _WIN_CLOSE_HANDLE = _WIN_KERNEL32.CloseHandle
    _WIN_CLOSE_HANDLE.argtypes = (wintypes.HANDLE,)
    _WIN_CLOSE_HANDLE.restype = wintypes.BOOL
    _WIN_GET_INFORMATION = _WIN_KERNEL32.GetFileInformationByHandle
    _WIN_GET_INFORMATION.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_WinByHandleFileInformation),
    )
    _WIN_GET_INFORMATION.restype = wintypes.BOOL
    _WIN_GET_FINAL_PATH = _WIN_KERNEL32.GetFinalPathNameByHandleW
    _WIN_GET_FINAL_PATH.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    _WIN_GET_FINAL_PATH.restype = wintypes.DWORD
    _WIN_SET_INFORMATION = _WIN_KERNEL32.SetFileInformationByHandle
    _WIN_SET_INFORMATION.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    _WIN_SET_INFORMATION.restype = wintypes.BOOL
    _WIN_SET_POINTER = _WIN_KERNEL32.SetFilePointerEx
    _WIN_SET_POINTER.argtypes = (
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    )
    _WIN_SET_POINTER.restype = wintypes.BOOL
    _WIN_READ_FILE = _WIN_KERNEL32.ReadFile
    _WIN_READ_FILE.argtypes = (
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    _WIN_READ_FILE.restype = wintypes.BOOL
    _WIN_NTDLL = ctypes.WinDLL("ntdll", use_last_error=True)
    _WIN_NT_SET_INFORMATION = _WIN_NTDLL.NtSetInformationFile
    _WIN_NT_SET_INFORMATION.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_WinIoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        ctypes.c_int,
    )
    _WIN_NT_SET_INFORMATION.restype = ctypes.c_long
    _WIN_NTSTATUS_TO_DOS = _WIN_NTDLL.RtlNtStatusToDosError
    _WIN_NTSTATUS_TO_DOS.argtypes = (ctypes.c_long,)
    _WIN_NTSTATUS_TO_DOS.restype = wintypes.ULONG


    def _win_error(reason_code: str, message: str) -> RuntimeFilesystemError:
        return RuntimeFilesystemError(
            reason_code,
            f"{message} (winerror={ctypes.get_last_error()})",
        )


    def _win_final_path(handle: int) -> Path:
        required = _WIN_GET_FINAL_PATH(handle, None, 0, 0)
        if required == 0:
            raise _win_error(
                "runtime.mutation.handle_path",
                "cannot resolve a mutation handle",
            )
        buffer = ctypes.create_unicode_buffer(required + 1)
        written = _WIN_GET_FINAL_PATH(handle, buffer, len(buffer), 0)
        if written == 0 or written >= len(buffer):
            raise _win_error(
                "runtime.mutation.handle_path",
                "cannot read a mutation handle path",
            )
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return Path(value)


    def _win_require_inside(root: Path, path: Path) -> None:
        root_text = os.path.normcase(os.path.abspath(root))
        path_text = os.path.normcase(os.path.abspath(path))
        try:
            common = os.path.commonpath((root_text, path_text))
        except ValueError as error:
            raise RuntimeFilesystemError(
                "runtime.mutation.handle_escape",
                "mutation handle resolves outside the fixture volume",
            ) from error
        if common != root_text:
            raise RuntimeFilesystemError(
                "runtime.mutation.handle_escape",
                "mutation handle resolves outside the fixture boundary",
            )


    def _win_information(handle: int) -> _WinByHandleFileInformation:
        information = _WinByHandleFileInformation()
        if not _WIN_GET_INFORMATION(handle, ctypes.byref(information)):
            raise _win_error(
                "runtime.mutation.handle_identity",
                "cannot inspect a mutation handle",
            )
        return information


    def _win_identity(
        information: _WinByHandleFileInformation,
    ) -> tuple[int, int, int, int]:
        file_index = (information.nFileIndexHigh << 32) | information.nFileIndexLow
        size = (information.nFileSizeHigh << 32) | information.nFileSizeLow
        modified = (
            information.ftLastWriteTime.dwHighDateTime << 32
        ) | information.ftLastWriteTime.dwLowDateTime
        return (
            int(information.dwVolumeSerialNumber),
            int(file_index),
            int(size),
            int(modified),
        )


    class _WindowsDirectoryHandle:
        def __init__(self, path: Path, *, root: Path) -> None:
            self.path = Path(path)
            self._root = Path(root)
            self._expected_path = os.path.normcase(os.path.abspath(self.path))
            self.handle = _WIN_CREATE_FILE(
                str(self.path),
                _WIN_FILE_READ_ATTRIBUTES | _WIN_SYNCHRONIZE,
                _WIN_FILE_SHARE_READ_WRITE,
                None,
                _WIN_OPEN_EXISTING,
                _WIN_BACKUP_SEMANTICS | _WIN_OPEN_REPARSE_POINT,
                None,
            )
            if self.handle == _WIN_INVALID_HANDLE:
                raise _win_error(
                    "runtime.mutation.directory_open",
                    f"cannot open managed directory: {self.path}",
                )
            try:
                information = _win_information(self.handle)
                if (
                    not information.dwFileAttributes & _WIN_FILE_ATTRIBUTE_DIRECTORY
                    or information.dwFileAttributes & _REPARSE_FLAG
                ):
                    raise RuntimeFilesystemError(
                        "runtime.mutation.directory_reparse",
                        f"managed path is not a no-follow directory: {self.path}",
                    )
                self.require_current_path()
            except Exception:
                self.close()
                raise

        def require_current_path(self) -> None:
            final_path = _win_final_path(self.handle)
            _win_require_inside(self._root, final_path)
            if os.path.normcase(os.path.abspath(final_path)) != self._expected_path:
                raise RuntimeFilesystemError(
                    "runtime.mutation.directory_rebound",
                    "managed directory moved after its exact handle was opened",
                )

        def close(self) -> None:
            if self.handle is not None:
                _WIN_CLOSE_HANDLE(self.handle)
                self.handle = None

        def __enter__(self) -> "_WindowsDirectoryHandle":
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            self.close()


    class _WindowsMutationFile:
        def __init__(self, path: Path, *, root: Path) -> None:
            self.path = Path(path)
            self.handle = _WIN_CREATE_FILE(
                str(self.path),
                _WIN_GENERIC_READ
                | _WIN_DELETE
                | _WIN_FILE_READ_ATTRIBUTES
                | _WIN_SYNCHRONIZE,
                0x00000001,
                None,
                _WIN_OPEN_EXISTING,
                _WIN_OPEN_REPARSE_POINT,
                None,
            )
            if self.handle == _WIN_INVALID_HANDLE:
                raise _win_error(
                    "runtime.mutation.file_open",
                    f"cannot exclusively open managed file: {self.path}",
                )
            try:
                information = _win_information(self.handle)
                if (
                    information.dwFileAttributes & _WIN_FILE_ATTRIBUTE_DIRECTORY
                    or information.dwFileAttributes & _REPARSE_FLAG
                ):
                    raise RuntimeFilesystemError(
                        "runtime.mutation.file_reparse",
                        f"managed path is not a no-follow regular file: {self.path}",
                    )
                final_path = _win_final_path(self.handle)
                _win_require_inside(root, final_path)
            except Exception:
                self.close()
                raise

        def close(self) -> None:
            if self.handle is not None:
                _WIN_CLOSE_HANDLE(self.handle)
                self.handle = None

        def __enter__(self) -> "_WindowsMutationFile":
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            self.close()

        def stable(self) -> StableFile:
            before = _win_information(self.handle)
            if not _WIN_SET_POINTER(
                self.handle,
                0,
                None,
                _WIN_FILE_BEGIN,
            ):
                raise _win_error(
                    "runtime.mutation.file_seek",
                    "cannot seek the exact mutation file",
                )
            digest = hashlib.sha256()
            length = 0
            buffer = ctypes.create_string_buffer(1024 * 1024)
            while True:
                read = wintypes.DWORD()
                if not _WIN_READ_FILE(
                    self.handle,
                    buffer,
                    len(buffer),
                    ctypes.byref(read),
                    None,
                ):
                    raise _win_error(
                        "runtime.mutation.file_read",
                        "cannot read the exact mutation file",
                    )
                if read.value == 0:
                    break
                chunk = buffer.raw[: read.value]
                digest.update(chunk)
                length += read.value
            after = _win_information(self.handle)
            if _win_identity(before) != _win_identity(after):
                raise RuntimeFilesystemError(
                    "runtime.mutation.file_changed",
                    "managed file changed while its mutation handle was open",
                )
            return StableFile(
                path=self.path,
                exact_sha256=HashDigest(digest.hexdigest()),
                byte_length=length,
                identity=_win_identity(after),
            )

        def _set_name(
            self,
            information_class: int,
            directory: _WindowsDirectoryHandle,
            name: str,
        ) -> None:
            directory.require_current_path()
            encoded = name.encode("utf-16-le")
            offset = _WinFileNameInformation.FileName.offset
            buffer = ctypes.create_string_buffer(offset + len(encoded))
            information = ctypes.cast(
                buffer,
                ctypes.POINTER(_WinFileNameInformation),
            ).contents
            information.ReplaceIfExists = False
            information.RootDirectory = directory.handle
            information.FileNameLength = len(encoded)
            ctypes.memmove(ctypes.addressof(buffer) + offset, encoded, len(encoded))
            io_status = _WinIoStatusBlock()
            status = _WIN_NT_SET_INFORMATION(
                self.handle,
                ctypes.byref(io_status),
                buffer,
                len(buffer),
                information_class,
            )
            if status < 0:
                raise RuntimeFilesystemError(
                    "runtime.mutation.handle_name",
                    "cannot change the exact mutation file link "
                    f"(ntstatus=0x{status & 0xFFFFFFFF:08x}, "
                    f"winerror={_WIN_NTSTATUS_TO_DOS(status)})",
                )

        def rename_into(
            self,
            directory: _WindowsDirectoryHandle,
            name: str,
        ) -> None:
            self._set_name(_WIN_FILE_RENAME_INFO, directory, name)

        def link_into(
            self,
            directory: _WindowsDirectoryHandle,
            name: str,
        ) -> None:
            self._set_name(_WIN_FILE_LINK_INFO, directory, name)

        def delete_link(self) -> None:
            information = _WinFileDispositionInformation(True)
            if not _WIN_SET_INFORMATION(
                self.handle,
                _WIN_FILE_DISPOSITION_INFO,
                ctypes.byref(information),
                ctypes.sizeof(information),
            ):
                raise _win_error(
                    "runtime.mutation.handle_delete",
                    "cannot delete the exact staged mutation link",
                )


def fixture_manifest_sha256(entries: tuple[PathObservation, ...]) -> HashDigest:
    material = [
        {
            "path": str(require_managed_path(item.path)),
            "node_kind": item.node_kind.value,
            "exact_sha256": str(item.exact_sha256) if item.exact_sha256 is not None else None,
            "byte_length": item.byte_length,
        }
        for item in entries
    ]
    return canonical_sha256(
        {
            "artifact_version": MANIFEST_VERSION,
            "entries": sorted(material, key=lambda item: str(item["path"])),
        }
    )


class FixtureWorkspaceObserver:
    """Observe every managed node without following a link or reparse point."""

    def __init__(
        self,
        boundary: FixtureRuntimeBoundary,
        *,
        workspace_id: OpaqueId,
        directory_name: str = "workspace",
    ) -> None:
        self._boundary = boundary
        self.workspace_id = workspace_id
        self.root = boundary.require_directory(directory_name, create=True)

    def _scan(self, directory: Path, prefix: str = "") -> list[PathObservation]:
        observations: list[PathObservation] = []
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as error:
            raise RuntimeFilesystemError(
                "runtime.filesystem.scan", f"cannot scan workspace directory: {directory}"
            ) from error
        for child in children:
            relative_text = f"{prefix}/{child.name}" if prefix else child.name
            relative = require_managed_path(relative_text)
            try:
                info = child.stat(follow_symlinks=False)
            except OSError as error:
                raise RuntimeFilesystemError(
                    "runtime.filesystem.stat", f"cannot inspect workspace node: {relative}"
                ) from error
            if child.is_symlink():
                observations.append(PathObservation(relative, PathNodeKind.SYMLINK, None, None))
            elif _is_reparse(info):
                observations.append(PathObservation(relative, PathNodeKind.REPARSE, None, None))
            elif stat.S_ISDIR(info.st_mode):
                observations.append(PathObservation(relative, PathNodeKind.DIRECTORY, None, None))
                observations.extend(self._scan(Path(child.path), relative_text))
            elif stat.S_ISREG(info.st_mode):
                stable = read_stable_regular_file(Path(child.path))
                observations.append(
                    PathObservation(
                        relative,
                        PathNodeKind.FILE,
                        stable.exact_sha256,
                        stable.byte_length,
                    )
                )
            else:
                raise RuntimeFilesystemError(
                    "runtime.filesystem.unsupported_node",
                    f"workspace contains an unsupported node: {relative}",
                )
        return observations

    def observe(self, *, revision_id: OpaqueId) -> WorkspaceObservation:
        entries = tuple(self._scan(self.root))
        trust = (
            WorkspaceTrustState.UNTRUSTED
            if any(
                item.node_kind in {PathNodeKind.SYMLINK, PathNodeKind.REPARSE}
                for item in entries
            )
            else WorkspaceTrustState.TRUSTED
        )
        observation = WorkspaceObservation(
            workspace_id=self.workspace_id,
            revision_id=revision_id,
            manifest_sha256=fixture_manifest_sha256(entries),
            trust_state=trust,
            complete=True,
            entries=entries,
        )
        workspace_observation_mapping(observation)
        return observation

    def managed_path(self, value: str) -> Path:
        relative = require_managed_path(value)
        candidate = self.root.joinpath(*str(relative).split("/"))
        current = self.root
        for segment in candidate.parent.relative_to(self.root).parts:
            current = current / segment
            try:
                info = current.lstat()
            except OSError as error:
                raise RuntimeFilesystemError(
                    "runtime.filesystem.ancestor_missing", f"managed ancestor is missing: {relative}"
                ) from error
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                raise RuntimeFilesystemError(
                    "runtime.filesystem.ancestor_link",
                    f"managed ancestor is a link or reparse point: {relative}",
                )
            if not stat.S_ISDIR(info.st_mode):
                raise RuntimeFilesystemError(
                    "runtime.filesystem.ancestor_type",
                    f"managed ancestor is not a directory: {relative}",
                )
        return candidate

    def require_exact_file(
        self,
        value: str,
        *,
        expected_sha256: HashDigest,
        expected_length: int | None = None,
    ) -> StableFile:
        stable = read_stable_regular_file(self.managed_path(value))
        if stable.exact_sha256 != expected_sha256 or (
            expected_length is not None and stable.byte_length != expected_length
        ):
            raise RuntimeFilesystemError(
                "runtime.filesystem.exact_before",
                f"managed file no longer matches exact expected bytes: {value}",
            )
        return stable

    def require_absent(self, value: str) -> Path:
        candidate = self.managed_path(value)
        try:
            candidate.lstat()
        except FileNotFoundError:
            return candidate
        except OSError as error:
            raise RuntimeFilesystemError(
                "runtime.filesystem.target_stat", f"cannot inspect target: {value}"
            ) from error
        raise RuntimeFilesystemError(
            "runtime.filesystem.target_exists", f"managed target already exists: {value}"
        )


class ContentAddressedFixtureStore:
    """Immutable content resolver owned by an explicit fixture boundary."""

    def __init__(self, boundary: FixtureRuntimeBoundary, *, directory_name: str = "content") -> None:
        self._boundary = boundary
        self.root = boundary.require_directory(directory_name, create=True)
        self.objects = self.root / "objects"
        self.evidence = self.root / "evidence"
        self.objects.mkdir(exist_ok=True)
        self.evidence.mkdir(exist_ok=True)
        _fsync_directory(self.root)

    def _object_path(self, digest: HashDigest) -> Path:
        return self.objects / f"{digest}.bin"

    def _evidence_document(self, content: ContentObject) -> dict[str, object]:
        return {
            "artifact_version": CONTENT_EVIDENCE_VERSION,
            "runtime_marker_sha256": str(self._boundary.marker_sha256),
            "object_id": str(content.object_id),
            "exact_sha256": str(content.exact_sha256),
            "byte_length": content.byte_length,
        }

    def put(self, *, object_id: OpaqueId, payload: bytes) -> ContentObjectObservation:
        if not isinstance(payload, bytes):
            raise RuntimeFilesystemError(
                "runtime.content.payload", "fixture content payload must be exact bytes"
            )
        content = ContentObject(
            object_id=object_id,
            exact_sha256=HashDigest(hashlib.sha256(payload).hexdigest()),
            byte_length=len(payload),
        )
        object_path = self._object_path(content.exact_sha256)
        if object_path.exists():
            stable = read_stable_regular_file(object_path)
            if stable.exact_sha256 != content.exact_sha256 or stable.byte_length != content.byte_length:
                raise RuntimeFilesystemError(
                    "runtime.content.collision", "content-addressed object was changed"
                )
        else:
            _write_new(object_path, payload)
        return self._observation(content)

    def _observation(self, content: ContentObject) -> ContentObjectObservation:
        payload = canonical_json_bytes(self._evidence_document(content))
        evidence_sha = HashDigest(hashlib.sha256(payload).hexdigest())
        evidence_path = self.evidence / f"{evidence_sha}.json"
        if evidence_path.exists():
            stable = read_stable_regular_file(evidence_path)
            if stable.exact_sha256 != evidence_sha or stable.byte_length != len(payload):
                raise RuntimeFilesystemError(
                    "runtime.content.evidence_rebound", "content evidence was changed"
                )
        else:
            _write_new(evidence_path, payload)
        return ContentObjectObservation(
            object_id=content.object_id,
            exact_sha256=content.exact_sha256,
            byte_length=content.byte_length,
            resolver_evidence=ArtifactReference(
                path=f"runtime-content/evidence/{evidence_sha}.json",
                sha256=evidence_sha,
                artifact_version=ArtifactVersion(CONTENT_EVIDENCE_VERSION),
            ),
        )

    def resolve_current(
        self,
        content: ContentObject,
        *,
        evaluated_at: datetime,
    ) -> ContentObjectObservation | None:
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise RuntimeFilesystemError(
                "runtime.content.evaluation_time", "content lookup time must be aware"
            )
        stable = read_stable_regular_file(self._object_path(content.exact_sha256))
        if stable.exact_sha256 != content.exact_sha256 or stable.byte_length != content.byte_length:
            return None
        return self._observation(content)

    def read_current(self, content: ContentObject) -> bytes:
        path = self._object_path(content.exact_sha256)
        stable, payload = read_stable_regular_bytes(path)
        if stable.exact_sha256 != content.exact_sha256 or stable.byte_length != content.byte_length:
            raise RuntimeFilesystemError(
                "runtime.content.mismatch", "content object no longer matches planned bytes"
            )
        return payload


class FixtureAtomicMutationPort:
    """Reversible, staged filesystem CAS for an isolated fixture workspace.

    Existing bytes are atomically moved into a quarantine name and verified
    *after* that move.  Only verified bytes are then deleted or linked into a
    destination.  A concurrent path swap can therefore make the operation
    uncertain, but cannot cause unverified bytes to be destroyed as the
    approved object.
    """

    def __init__(
        self,
        boundary: FixtureRuntimeBoundary,
        *,
        observer: FixtureWorkspaceObserver,
        content_store: ContentAddressedFixtureStore,
    ) -> None:
        self._root = boundary.root
        self._observer = observer
        self._content = content_store
        self._staging = boundary.require_directory("mutation-staging", create=True)
        self._lock_path = self._staging / "mutation.lock"

    @staticmethod
    def _exists_no_follow(path: Path) -> bool:
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise RuntimeFilesystemError(
                "runtime.mutation.path_stat", f"cannot inspect mutation path: {path}"
            ) from error
        return True

    def _stage_paths(self, operation_id: OpaqueId) -> tuple[Path, Path]:
        suffix = hashlib.sha256(str(operation_id).encode("utf-8")).hexdigest()[:24]
        return (
            self._staging / f"{suffix}.before",
            self._staging / f"{suffix}.new",
        )

    @staticmethod
    def _posix_restore_staged(
        staging_fd: int,
        staged_name: str,
        target_fd: int,
        target_name: str,
    ) -> None:
        if not _posix_entry_exists(staging_fd, staged_name):
            return
        if _posix_entry_exists(target_fd, target_name):
            raise RuntimeFilesystemError(
                "runtime.mutation.restore_conflict",
                "concurrent target prevents safe restoration of staged bytes",
            )
        os.link(
            staged_name,
            target_name,
            src_dir_fd=staging_fd,
            dst_dir_fd=target_fd,
            follow_symlinks=False,
        )
        os.fsync(target_fd)
        os.unlink(staged_name, dir_fd=staging_fd)
        os.fsync(staging_fd)

    def _posix_stage_exact(
        self,
        target_fd: int,
        target_name: str,
        staging_fd: int,
        staged_name: str,
        *,
        expected_sha256: HashDigest,
        display_path: Path,
    ) -> StableFile:
        if _posix_entry_exists(staging_fd, staged_name):
            raise RuntimeFilesystemError(
                "runtime.mutation.staging_present",
                "a previous staged mutation requires reconciliation",
            )
        try:
            os.rename(
                target_name,
                staged_name,
                src_dir_fd=target_fd,
                dst_dir_fd=staging_fd,
            )
            os.fsync(target_fd)
            os.fsync(staging_fd)
        except OSError as error:
            raise RuntimeFilesystemError(
                "runtime.mutation.stage_failed",
                "could not atomically stage the exact source path",
            ) from error
        try:
            stable = _posix_stable_regular_file(
                staging_fd,
                staged_name,
                display_path=display_path,
            )
            if stable.exact_sha256 != expected_sha256:
                raise RuntimeFilesystemError(
                    "runtime.filesystem.exact_before",
                    "staged source bytes differ from the approved object",
                )
            return stable
        except Exception:
            self._posix_restore_staged(
                staging_fd,
                staged_name,
                target_fd,
                target_name,
            )
            raise

    def _apply_exact_posix(
        self,
        operation,
        target: Path,
        before_stage: Path,
        new_stage: Path,
        directories: _GuardedFixtureDirectories,
    ) -> int | None:
        """Apply through exact POSIX directory capabilities, never path lookup."""

        assert os.name != "nt"
        target_fd = directories.descriptor(target.parent)
        staging_fd = directories.descriptor(self._staging)
        destination = None
        destination_fd = target_fd
        if operation.kind is MutationKind.MOVE:
            assert operation.destination_path is not None
            destination = self._observer.managed_path(str(operation.destination_path))
            destination_fd = directories.descriptor(destination.parent)

        if _posix_entry_exists(staging_fd, before_stage.name) or _posix_entry_exists(
            staging_fd, new_stage.name
        ):
            raise RuntimeFilesystemError(
                "runtime.mutation.staging_present",
                "a previous staged mutation requires reconciliation",
            )

        if operation.kind is MutationKind.CREATE:
            assert operation.new_content is not None
            payload = self._content.read_current(operation.new_content)
            _posix_write_new(staging_fd, new_stage.name, payload)
            _posix_require_absent(target_fd, target.name)
            os.link(
                new_stage.name,
                target.name,
                src_dir_fd=staging_fd,
                dst_dir_fd=target_fd,
                follow_symlinks=False,
            )
            os.fsync(target_fd)
            created = _posix_stable_regular_file(
                target_fd,
                target.name,
                display_path=target,
            )
            if (
                created.exact_sha256 != operation.new_content.exact_sha256
                or created.byte_length != operation.new_content.byte_length
            ):
                raise RuntimeFilesystemError(
                    "runtime.mutation.create_rebound",
                    "created target differs from the exact content object",
                )
            os.unlink(new_stage.name, dir_fd=staging_fd)
            os.fsync(staging_fd)
            return None

        assert operation.expected_before.exact_sha256 is not None
        staged = self._posix_stage_exact(
            target_fd,
            target.name,
            staging_fd,
            before_stage.name,
            expected_sha256=operation.expected_before.exact_sha256,
            display_path=before_stage,
        )
        if operation.kind is MutationKind.DELETE:
            os.unlink(before_stage.name, dir_fd=staging_fd)
            os.fsync(staging_fd)
            return staged.byte_length

        if operation.kind is MutationKind.MOVE:
            assert destination is not None
            _posix_require_absent(destination_fd, destination.name)
            try:
                os.link(
                    before_stage.name,
                    destination.name,
                    src_dir_fd=staging_fd,
                    dst_dir_fd=destination_fd,
                    follow_symlinks=False,
                )
                os.fsync(destination_fd)
                moved = _posix_stable_regular_file(
                    destination_fd,
                    destination.name,
                    display_path=destination,
                )
                if (
                    moved.exact_sha256 != operation.expected_before.exact_sha256
                    or moved.byte_length != staged.byte_length
                ):
                    raise RuntimeFilesystemError(
                        "runtime.mutation.move_rebound",
                        "move destination differs from the staged source",
                    )
            except Exception:
                if not _posix_entry_exists(destination_fd, destination.name):
                    self._posix_restore_staged(
                        staging_fd,
                        before_stage.name,
                        target_fd,
                        target.name,
                    )
                raise
            os.unlink(before_stage.name, dir_fd=staging_fd)
            os.fsync(staging_fd)
            return staged.byte_length

        assert operation.kind is MutationKind.REPLACE
        assert operation.new_content is not None
        payload = self._content.read_current(operation.new_content)
        _posix_write_new(staging_fd, new_stage.name, payload)
        try:
            os.link(
                new_stage.name,
                target.name,
                src_dir_fd=staging_fd,
                dst_dir_fd=target_fd,
                follow_symlinks=False,
            )
            os.fsync(target_fd)
            replaced = _posix_stable_regular_file(
                target_fd,
                target.name,
                display_path=target,
            )
            if (
                replaced.exact_sha256 != operation.new_content.exact_sha256
                or replaced.byte_length != operation.new_content.byte_length
            ):
                raise RuntimeFilesystemError(
                    "runtime.mutation.replace_rebound",
                    "replacement target differs from the exact content object",
                )
        except Exception:
            if not _posix_entry_exists(target_fd, target.name):
                self._posix_restore_staged(
                    staging_fd,
                    before_stage.name,
                    target_fd,
                    target.name,
                )
            raise
        os.unlink(new_stage.name, dir_fd=staging_fd)
        os.unlink(before_stage.name, dir_fd=staging_fd)
        os.fsync(staging_fd)
        return staged.byte_length

    def _apply_exact_windows(
        self,
        operation,
        target: Path,
        before_stage: Path,
        new_stage: Path,
    ) -> int | None:
        """Use exact file and directory handles for the Windows effect."""

        assert os.name == "nt"
        if self._exists_no_follow(before_stage) or self._exists_no_follow(new_stage):
            raise RuntimeFilesystemError(
                "runtime.mutation.staging_present",
                "a previous staged mutation requires reconciliation",
            )
        destination_path = None
        if operation.kind is MutationKind.MOVE:
            assert operation.destination_path is not None
            destination_path = self._observer.managed_path(
                str(operation.destination_path)
            )
        with _WindowsDirectoryHandle(target.parent, root=self._root) as target_parent:
            with _WindowsDirectoryHandle(self._staging, root=self._root) as staging:
                if operation.kind is MutationKind.CREATE:
                    assert operation.new_content is not None
                    payload = self._content.read_current(operation.new_content)
                    _write_new(new_stage, payload)
                    with _WindowsMutationFile(new_stage, root=self._root) as new_file:
                        stable = new_file.stable()
                        if (
                            stable.exact_sha256 != operation.new_content.exact_sha256
                            or stable.byte_length != operation.new_content.byte_length
                        ):
                            raise RuntimeFilesystemError(
                                "runtime.mutation.create_rebound",
                                "staged create bytes differ from the exact content object",
                            )
                        self._observer.require_absent(str(operation.path))
                        new_file.link_into(target_parent, target.name)
                        new_file.delete_link()
                    return None

                assert operation.expected_before.exact_sha256 is not None
                with _WindowsMutationFile(target, root=self._root) as old_file:
                    staged = old_file.stable()
                    if staged.exact_sha256 != operation.expected_before.exact_sha256:
                        raise RuntimeFilesystemError(
                            "runtime.filesystem.exact_before",
                            "opened source bytes differ from the approved object",
                        )
                    old_file.rename_into(staging, before_stage.name)
                    try:
                        if operation.kind is MutationKind.DELETE:
                            old_file.delete_link()
                            return staged.byte_length

                        if operation.kind is MutationKind.MOVE:
                            assert destination_path is not None
                            self._observer.require_absent(
                                str(operation.destination_path)
                            )
                            with _WindowsDirectoryHandle(
                                destination_path.parent,
                                root=self._root,
                            ) as destination_parent:
                                old_file.link_into(
                                    destination_parent,
                                    destination_path.name,
                                )
                            old_file.delete_link()
                            return staged.byte_length

                        assert operation.kind is MutationKind.REPLACE
                        assert operation.new_content is not None
                        payload = self._content.read_current(operation.new_content)
                        _write_new(new_stage, payload)
                        with _WindowsMutationFile(
                            new_stage,
                            root=self._root,
                        ) as new_file:
                            replacement = new_file.stable()
                            if (
                                replacement.exact_sha256
                                != operation.new_content.exact_sha256
                                or replacement.byte_length
                                != operation.new_content.byte_length
                            ):
                                raise RuntimeFilesystemError(
                                    "runtime.mutation.replace_rebound",
                                    "staged replacement differs from the exact content object",
                                )
                            new_file.link_into(target_parent, target.name)
                            new_file.delete_link()
                        old_file.delete_link()
                        return staged.byte_length
                    except Exception:
                        if not self._exists_no_follow(target):
                            try:
                                old_file.link_into(target_parent, target.name)
                                old_file.delete_link()
                            except Exception:
                                # Keep the exact quarantine link for reconciliation.
                                pass
                        raise

    def apply_exact(self, operation) -> int | None:
        """Apply one operation under the fixture lock with reversible staging."""

        before_stage, new_stage = self._stage_paths(operation.operation_id)
        with _exclusive_fixture_lock(self._lock_path):
            target = self._observer.managed_path(str(operation.path))
            destination_parent = target.parent
            if operation.kind is MutationKind.MOVE:
                assert operation.destination_path is not None
                destination_parent = self._observer.managed_path(
                    str(operation.destination_path)
                ).parent
            if os.name == "nt":
                with _guard_fixture_directories(
                    self._root,
                    (target.parent, destination_parent, self._staging),
                ):
                    return self._apply_exact_windows(
                        operation,
                        target,
                        before_stage,
                        new_stage,
                    )
            with _guard_fixture_directories(
                self._root,
                (target.parent, destination_parent, self._staging),
            ) as directories:
                return self._apply_exact_posix(
                    operation,
                    target,
                    before_stage,
                    new_stage,
                    directories,
                )


__all__ = [
    "CONTENT_EVIDENCE_VERSION",
    "ContentAddressedFixtureStore",
    "FixtureAtomicMutationPort",
    "FixtureWorkspaceObserver",
    "MANIFEST_VERSION",
    "RuntimeFilesystemError",
    "StableFile",
    "fixture_manifest_sha256",
    "read_stable_regular_file",
    "read_stable_regular_bytes",
]
