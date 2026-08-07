"""Fail when repository text or paths contain concrete workspace identities.

Thin CLI wrapper over :func:`video_factory.security.purity.scan_repository`.
Output format is stable for automation::

    core_purity=PASS scanned_files=N violations=0
"""

from __future__ import annotations

from pathlib import Path
import sys


def _ensure_src_on_path() -> Path:
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    src_text = str(src)
    if src_text not in sys.path:
        sys.path.insert(0, src_text)
    return root


def main() -> int:
    root = _ensure_src_on_path()
    from video_factory.security.purity import format_purity_report, scan_repository

    violations, scanned_files = scan_repository(root)
    exit_code, _compact, full_text = format_purity_report(violations, scanned_files)
    if exit_code != 0:
        # Print each FAIL line then the summary (format_purity_report full text).
        print(full_text)
        return exit_code
    print(full_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
