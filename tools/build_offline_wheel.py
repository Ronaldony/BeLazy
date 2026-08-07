"""Build a deterministic PEP 427 wheel with stdlib only.

This is an offline acceptance tool, not a claim that the setuptools backend in
``pyproject.toml`` was exercised.  Standard-backend verification remains a
separate environment/CI check whenever its declared build dependency exists.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tomllib
from typing import Iterable, Sequence
import zipfile


DIST_INFO_NAME = "video_production_core"
WHEEL_TAG = "py3-none-any"
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _record_digest(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
    return "sha256=" + encoded.rstrip(b"=").decode("ascii")


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _project_files(project_root: Path) -> dict[str, bytes]:
    package_root = project_root / "src" / "video_factory"
    if not package_root.is_dir():
        raise ValueError(f"package root is missing: {package_root}")
    output: dict[str, bytes] = {}
    for path in sorted(package_root.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        relative = path.relative_to(project_root / "src").as_posix()
        output[relative] = path.read_bytes()
    schema_prefix = "video_factory/resources/schemas/"
    schemas = [
        name
        for name in output
        if name.startswith(schema_prefix) and name.endswith(".schema.json")
    ]
    manifest_name = schema_prefix + "schema-manifest.json"
    if manifest_name not in output:
        raise ValueError("wheel input schema manifest is missing")
    manifest = json.loads(output[manifest_name])
    expected = manifest.get("schema_count") if isinstance(manifest, dict) else None
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
        raise ValueError("wheel input schema manifest count is invalid")
    if len(schemas) != expected:
        raise ValueError(
            f"wheel input schema count differs from manifest: "
            f"expected {expected}; found {len(schemas)}"
        )
    return output


def build_wheel(project_root: Path, output_dir: Path) -> tuple[Path, int]:
    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    version = str(project["version"])
    name = str(project["name"])
    if name != "video-production-core":
        raise ValueError(f"unexpected distribution name: {name}")
    dist_info = f"{DIST_INFO_NAME}-{version}.dist-info"
    files = _project_files(project_root)
    dependencies = "".join(
        f"Requires-Dist: {dependency}\n" for dependency in project.get("dependencies", [])
    )
    files[f"{dist_info}/METADATA"] = (
        "Metadata-Version: 2.1\n"
        f"Name: {name}\n"
        f"Version: {version}\n"
        f"Summary: {project['description']}\n"
        f"Requires-Python: {project['requires-python']}\n"
        f"{dependencies}"
        "\n"
    ).encode("utf-8")
    files[f"{dist_info}/WHEEL"] = (
        "Wheel-Version: 1.0\n"
        "Generator: be-lazy-offline-wheel/1.0\n"
        "Root-Is-Purelib: true\n"
        f"Tag: {WHEEL_TAG}\n"
    ).encode("utf-8")
    files[f"{dist_info}/entry_points.txt"] = (
        "[console_scripts]\nvideo-factory = video_factory.cli:main\n"
    ).encode("utf-8")
    files[f"{dist_info}/top_level.txt"] = b"video_factory\n"

    record_name = f"{dist_info}/RECORD"
    record_stream = io.StringIO(newline="")
    writer = csv.writer(record_stream, lineterminator="\n")
    for relative in sorted(files):
        payload = files[relative]
        writer.writerow((relative, _record_digest(payload), str(len(payload))))
    writer.writerow((record_name, "", ""))
    files[record_name] = record_stream.getvalue().encode("utf-8")

    output_dir.mkdir(parents=True, exist_ok=True)
    wheel_path = output_dir / f"{DIST_INFO_NAME}-{version}-{WHEEL_TAG}.whl"
    with zipfile.ZipFile(wheel_path, "w") as archive:
        for relative in sorted(files):
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts or "\\" in relative:
                raise ValueError(f"unsafe wheel member: {relative}")
            archive.writestr(_zip_info(relative), files[relative])
    return wheel_path, len(files)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    wheel, member_count = build_wheel(args.project_root.resolve(), args.output_dir.resolve())
    payload = wheel.read_bytes()
    print(
        "offline_wheel_build=PASS "
        f"path={wheel} members={member_count} bytes={len(payload)} "
        f"sha256={hashlib.sha256(payload).hexdigest()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
