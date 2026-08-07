"""Regression coverage for the stdlib-only W01 wheel acceptance tools."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
from pathlib import Path
import zipfile

import pytest

from tools.build_offline_wheel import build_wheel
from tools.verify_schema_wheel import inspect_wheel


def _digest(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
    return "sha256=" + encoded.rstrip(b"=").decode("ascii")


def _rewrite(
    source: Path,
    destination: Path,
    updates: dict[str, bytes],
    *,
    refresh_record: bool,
) -> None:
    with zipfile.ZipFile(source) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files.update(updates)
    record_name = next(name for name in files if name.endswith(".dist-info/RECORD"))
    if refresh_record:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        for name in sorted(files):
            if name == record_name:
                continue
            payload = files[name]
            writer.writerow((name, _digest(payload), str(len(payload))))
        writer.writerow((record_name, "", ""))
        files[record_name] = stream.getvalue().encode("utf-8")
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])


def _built_wheel(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[2]
    wheel, members = build_wheel(root, tmp_path / "wheel")
    assert members == 172
    return wheel


def test_offline_wheel_record_metadata_and_tag_are_verified(tmp_path: Path) -> None:
    wheel = _built_wheel(tmp_path)
    members, schemas, digest = inspect_wheel(wheel)
    assert members == 172
    assert schemas == 61
    assert digest == hashlib.sha256(wheel.read_bytes()).hexdigest()


def test_wheel_rejects_record_digest_corruption(tmp_path: Path) -> None:
    wheel = _built_wheel(tmp_path)
    damaged = tmp_path / "damaged" / wheel.name
    damaged.parent.mkdir()
    target = "video_factory/resources/schemas/artifact-common.schema.json"
    _rewrite(wheel, damaged, {target: b"{}\n"}, refresh_record=False)
    with pytest.raises(ValueError, match="RECORD digest mismatch"):
        inspect_wheel(damaged)


def test_wheel_rejects_director_resource_digest_corruption(tmp_path: Path) -> None:
    wheel = _built_wheel(tmp_path)
    damaged = tmp_path / "director-damaged" / wheel.name
    damaged.parent.mkdir()
    target = "video_factory/resources/directors/director-registry.json"
    _rewrite(wheel, damaged, {target: b"{}\n"}, refresh_record=True)
    with pytest.raises(ValueError, match="Director resource digest mismatch"):
        inspect_wheel(damaged)


def test_wheel_rejects_nonempty_record_self_row(tmp_path: Path) -> None:
    wheel = _built_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        record_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/RECORD")
        )
        record = archive.read(record_name).decode("utf-8")
    damaged = tmp_path / "self-row" / wheel.name
    damaged.parent.mkdir()
    bad_record = record.replace(
        f"{record_name},,\n", f"{record_name},sha256=bad,1\n"
    ).encode("utf-8")
    _rewrite(wheel, damaged, {record_name: bad_record}, refresh_record=False)
    with pytest.raises(ValueError, match="self row"):
        inspect_wheel(damaged)


@pytest.mark.parametrize(
    ("member_suffix", "replacement", "message"),
    [
        (
            ".dist-info/WHEEL",
            b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py2-none-any\n",
            "compatibility tag",
        ),
        (
            ".dist-info/METADATA",
            b"Metadata-Version: 2.1\nName: wrong-name\nVersion: 0.3.1\nRequires-Python: >=3.12\n",
            "distribution name",
        ),
        (
            ".dist-info/METADATA",
            b"Metadata-Version: 2.1\nName: video-production-core\nVersion: 9.9.9\nRequires-Python: >=3.12\n",
            "version does not match",
        ),
        (
            ".dist-info/METADATA",
            b"Metadata-Version: 2.1\nName: video-production-core\nVersion: 0.3.1\nVersion: 9.9.9\nRequires-Python: >=3.12\n",
            "version does not match",
        ),
    ],
)
def test_wheel_rejects_semantically_invalid_metadata_with_valid_record(
    tmp_path: Path,
    member_suffix: str,
    replacement: bytes,
    message: str,
) -> None:
    wheel = _built_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        member = next(name for name in archive.namelist() if name.endswith(member_suffix))
    damaged = tmp_path / "semantic" / wheel.name
    damaged.parent.mkdir()
    _rewrite(wheel, damaged, {member: replacement}, refresh_record=True)
    with pytest.raises(ValueError, match=message):
        inspect_wheel(damaged)


def test_wheel_filename_version_must_match_dist_info(tmp_path: Path) -> None:
    wheel = _built_wheel(tmp_path)
    renamed = tmp_path / "video_production_core-9.9.9-py3-none-any.whl"
    renamed.write_bytes(wheel.read_bytes())
    with pytest.raises(ValueError, match="filename does not match"):
        inspect_wheel(renamed)
