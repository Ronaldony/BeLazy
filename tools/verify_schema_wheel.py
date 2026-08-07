"""Inspect, install and probe a schema-bearing wheel without network access."""

from __future__ import annotations

import argparse
import base64
import csv
from email import policy
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Mapping, Sequence
import zipfile


SCHEMA_PREFIX = "video_factory/resources/schemas/"
EXPECTED_WHEEL_TAG = "py3-none-any"


def _record_digest(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
    return "sha256=" + encoded.rstrip(b"=").decode("ascii")


def _validate_dist_info(
    archive: zipfile.ZipFile,
    names: list[str],
    wheel_path: Path,
) -> None:
    records = [name for name in names if name.endswith(".dist-info/RECORD")]
    if len(records) != 1:
        raise ValueError(f"wheel must contain exactly one RECORD; found {len(records)}")
    record_name = records[0]
    dist_info = record_name.removesuffix("/RECORD")
    metadata_name = f"{dist_info}/METADATA"
    wheel_name = f"{dist_info}/WHEEL"
    for required in (metadata_name, wheel_name):
        if required not in names:
            raise ValueError(f"wheel metadata member is missing: {required}")

    rows: dict[str, tuple[str, str]] = {}
    reader = csv.reader(io.StringIO(archive.read(record_name).decode("utf-8")))
    for row in reader:
        if len(row) != 3:
            raise ValueError("wheel RECORD row must have exactly three fields")
        member, digest, size = row
        if member in rows:
            raise ValueError(f"wheel RECORD contains duplicate row: {member}")
        rows[member] = (digest, size)
    if set(rows) != set(names):
        missing = sorted(set(names) - set(rows))
        extra = sorted(set(rows) - set(names))
        raise ValueError(f"wheel RECORD member set mismatch: missing={missing} extra={extra}")
    if rows[record_name] != ("", ""):
        raise ValueError("wheel RECORD self row must have empty digest and size")
    for name in names:
        if name == record_name:
            continue
        payload = archive.read(name)
        digest, size = rows[name]
        if digest != _record_digest(payload):
            raise ValueError(f"wheel RECORD digest mismatch: {name}")
        if size != str(len(payload)):
            raise ValueError(f"wheel RECORD size mismatch: {name}")

    metadata = BytesParser(policy=policy.default).parsebytes(
        archive.read(metadata_name)
    )
    if metadata.get("Metadata-Version") != "2.1":
        raise ValueError("wheel METADATA version must be 2.1")
    if metadata.get("Name") != "video-production-core":
        raise ValueError("wheel METADATA distribution name mismatch")
    if not metadata.get("Version") or not metadata.get("Requires-Python"):
        raise ValueError("wheel METADATA version or Requires-Python is missing")

    wheel = BytesParser(policy=policy.default).parsebytes(archive.read(wheel_name))
    if wheel.get("Wheel-Version") != "1.0":
        raise ValueError("wheel contract version must be 1.0")
    if wheel.get("Root-Is-Purelib") != "true":
        raise ValueError("wheel must declare Root-Is-Purelib: true")
    if wheel.get_all("Tag", []) != [EXPECTED_WHEEL_TAG]:
        raise ValueError("wheel compatibility tag mismatch")
    if not wheel_path.name.endswith(f"-{EXPECTED_WHEEL_TAG}.whl"):
        raise ValueError("wheel filename compatibility tag mismatch")


def _strict_object(payload: bytes, label: str) -> Mapping[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        output: dict[str, object] = {}
        for key, value in pairs:
            if key in output:
                raise ValueError(f"duplicate key in {label}: {key}")
            output[key] = value
        return output

    def reject(value: str) -> object:
        raise ValueError(f"non-finite number in {label}: {value}")

    value = json.loads(
        payload.decode("utf-8"),
        object_pairs_hook=unique,
        parse_constant=reject,
    )
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} top-level must be an object")
    return value


def inspect_wheel(path: Path) -> tuple[int, int, str]:
    payload = path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("wheel contains duplicate member names")
        for name in names:
            member = PurePosixPath(name)
            if member.is_absolute() or ".." in member.parts or "\\" in name:
                raise ValueError(f"unsafe wheel member: {name}")
        _validate_dist_info(archive, names, path)
        schema_names = sorted(
            name
            for name in names
            if name.startswith(SCHEMA_PREFIX) and name.endswith(".schema.json")
        )
        if len(schema_names) != 46:
            raise ValueError(f"wheel must contain 46 schemas; found {len(schema_names)}")
        manifest_name = SCHEMA_PREFIX + "schema-manifest.json"
        manifest = _strict_object(archive.read(manifest_name), manifest_name)
        entries = manifest.get("schemas")
        if not isinstance(entries, list) or len(entries) != 46:
            raise ValueError("wheel schema manifest must contain 46 entries")
        versions = 0
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise ValueError("wheel schema manifest entry is not an object")
            filename = entry.get("filename")
            if not isinstance(filename, str):
                raise ValueError("wheel schema manifest filename is invalid")
            data = archive.read(SCHEMA_PREFIX + filename)
            if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
                raise ValueError(f"wheel schema digest mismatch: {filename}")
            if entry.get("artifact_version") is not None:
                versions += 1
        if versions != 42:
            raise ValueError(f"wheel manifest must register 42 versions; found {versions}")
    return len(names), len(schema_names), hashlib.sha256(payload).hexdigest()


def install_and_probe(wheel: Path, python: Path, work_dir: Path) -> str:
    if work_dir.exists():
        raise ValueError(f"verification work directory must not exist: {work_dir}")
    install_dir = work_dir / "installed"
    install_dir.mkdir(parents=True)
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    pip_command = [
        str(python),
        "-B",
        "-m",
        "pip",
        "install",
        "--no-index",
        "--no-deps",
        "--no-compile",
        "--target",
        str(install_dir),
        str(wheel),
    ]
    installed = subprocess.run(
        pip_command,
        cwd=work_dir,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if installed.returncode != 0:
        raise RuntimeError(
            "offline pip install failed: " + installed.stdout + installed.stderr
        )
    probe = """
import json
from pathlib import Path
import sys
install = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(install))
import video_factory
from video_factory.artifacts import ArtifactSchemaRegistry, validate_artifact_mapping
package_file = Path(video_factory.__file__).resolve()
if not package_file.is_relative_to(install):
    raise SystemExit(f'package escaped isolated install: {package_file}')
registry = ArtifactSchemaRegistry()
if len(registry.all_schemas()) != 46 or len(registry.list_versions()) != 42:
    raise SystemExit('installed registry counts mismatch')
valid = {'artifact_version': 'approval-requirement/1.0', 'rules_version': 'rules', 'episode_id': 'ep', 'requirement_id': 'req', 'capability_id': 'cap', 'bound_artifacts': [{'path': 'a.json', 'sha256': 'a'*64, 'artifact_version': 'brief/1.0'}], 'effective_config_sha256': 'b'*64, 'kind': 'packet', 'creates_evidence': False}
if not validate_artifact_mapping(valid, registry=registry).ok:
    raise SystemExit('installed registry could not validate a valid artifact')
bad = dict(valid); bad['effective_config_sha256'] = 'bad'
if validate_artifact_mapping(bad, registry=registry).ok:
    raise SystemExit('installed registry accepted an invalid artifact')
print(json.dumps({'package_file': str(package_file), 'schemas': 46, 'versions': 42, 'manifest_sha256': registry.manifest_sha256}, sort_keys=True))
"""
    probed = subprocess.run(
        [str(python), "-I", "-B", "-c", probe, str(install_dir)],
        cwd=work_dir,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if probed.returncode != 0:
        raise RuntimeError("isolated schema probe failed: " + probed.stdout + probed.stderr)
    return probed.stdout.strip()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    members, schemas, digest = inspect_wheel(args.wheel.resolve())
    probe = install_and_probe(
        args.wheel.resolve(), args.python.resolve(), args.work_dir.resolve()
    )
    print(
        "schema_wheel_verify=PASS "
        f"members={members} schemas={schemas} sha256={digest} probe={probe}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
