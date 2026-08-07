"""Read-only filesystem tree inspection that never follows links.

These helpers support legacy workspace *planning* APIs.  They deliberately do
not claim to close the time-of-check/time-of-use window required by the managed
mutation runtime; that enforcement belongs at the W06 side-effect boundary.
"""

from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path
import stat


class SafeTreeError(RuntimeError):
    """Raised when a tree cannot be inspected without following unsafe nodes."""

    def __init__(self, code: str, path: Path, detail: str) -> None:
        super().__init__(f"{code}: {detail}: {path}")
        self.code = code
        self.path = path
        self.detail = detail


def _is_reparse_point(stat_result: object) -> bool:
    attributes = getattr(stat_result, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _lstat(path: Path) -> object:
    try:
        return path.lstat()
    except FileNotFoundError as error:
        raise SafeTreeError("tree.missing", path, "path disappeared") from error
    except OSError as error:
        raise SafeTreeError("tree.unreadable", path, str(error)) from error


def _require_safe_node(path: Path) -> object:
    result = _lstat(path)
    if stat.S_ISLNK(result.st_mode) or _is_reparse_point(result):
        raise SafeTreeError(
            "tree.link_or_reparse",
            path,
            "symbolic links and reparse points are not allowed",
        )
    return result


def require_safe_path_chain(path: Path, *, must_exist: bool) -> Path:
    """Reject a link/reparse at the leaf or any existing lexical ancestor."""

    absolute = Path(os.path.abspath(path))
    chain = tuple(reversed(absolute.parents)) + (absolute,)
    leaf_seen = False
    for index, candidate in enumerate(chain):
        try:
            result = candidate.lstat()
        except FileNotFoundError:
            if must_exist:
                raise SafeTreeError(
                    "tree.missing", absolute, "required path does not exist"
                )
            break
        except OSError as error:
            raise SafeTreeError("tree.unreadable", candidate, str(error)) from error
        if stat.S_ISLNK(result.st_mode) or _is_reparse_point(result):
            raise SafeTreeError(
                "tree.link_or_reparse",
                candidate,
                "path crosses a symbolic link or reparse point",
            )
        if index < len(chain) - 1 and not stat.S_ISDIR(result.st_mode):
            raise SafeTreeError(
                "tree.not_directory",
                candidate,
                "path ancestor is not a directory",
            )
        if index == len(chain) - 1:
            leaf_seen = True
    if must_exist and not leaf_seen:
        raise SafeTreeError("tree.missing", absolute, "required path does not exist")
    return absolute


def iter_regular_files_no_follow(
    root: Path,
    *,
    skip_directory_names: Iterable[str] = (),
) -> tuple[Path, ...]:
    """Return sorted regular files below *root* without following any link.

    Every encountered node is checked with ``lstat`` before it can be read or
    traversed.  Special files, symbolic links, junctions, and other Windows
    reparse points fail closed.  Directory names in ``skip_directory_names``
    are omitted only after the node itself has passed the no-link check.
    """

    root = Path(root)
    require_safe_path_chain(root, must_exist=True)
    root_stat = _require_safe_node(root)
    if not stat.S_ISDIR(root_stat.st_mode):
        raise SafeTreeError("tree.not_directory", root, "root is not a directory")

    skipped = frozenset(skip_directory_names)
    files: list[Path] = []

    def visit(directory: Path) -> None:
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as error:
            raise SafeTreeError("tree.unreadable", directory, str(error)) from error
        for child in children:
            child_stat = _require_safe_node(child)
            if stat.S_ISDIR(child_stat.st_mode):
                if child.name not in skipped:
                    visit(child)
                continue
            if stat.S_ISREG(child_stat.st_mode):
                files.append(child)
                continue
            raise SafeTreeError(
                "tree.special_node",
                child,
                "only regular files and directories are allowed",
            )

    visit(root)
    return tuple(files)
