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
import re
import subprocess
import sys
from typing import Mapping, Sequence
import zipfile


SCHEMA_PREFIX = "video_factory/resources/schemas/"
DIRECTOR_PREFIX = "video_factory/resources/directors/"
WORKFLOW_AUTHORITY_PREFIX = "video_factory/resources/workflow_authority/"
QUALITY_RELEASE_PREFIX = "video_factory/resources/quality_release/"
RUNTIME_MIGRATION_PREFIX = "video_factory/resources/runtime_migration/"
EXPECTED_WHEEL_TAG = "py3-none-any"
DIRECTOR_RESOURCE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "video_factory"
    / "resources"
    / "directors"
)
WORKFLOW_AUTHORITY_RESOURCE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "video_factory"
    / "resources"
    / "workflow_authority"
)
QUALITY_RELEASE_RESOURCE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "video_factory"
    / "resources"
    / "quality_release"
)
RUNTIME_MIGRATION_RESOURCE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "video_factory"
    / "resources"
    / "runtime_migration"
)


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalized_distribution(value: str) -> str:
    return re.sub(r"[-_.]+", "_", value).lower()


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
    dist_info_leaf = PurePosixPath(dist_info).name
    if not dist_info_leaf.endswith(".dist-info"):
        raise ValueError("wheel RECORD is not inside a dist-info directory")
    try:
        dist_name, dist_version = dist_info_leaf.removesuffix(".dist-info").rsplit(
            "-", 1
        )
    except ValueError as error:
        raise ValueError("wheel dist-info name lacks distribution or version") from error
    expected_filename = f"{dist_name}-{dist_version}-{EXPECTED_WHEEL_TAG}.whl"
    if wheel_path.name != expected_filename:
        raise ValueError(
            "wheel filename does not match dist-info distribution/version/tag"
        )
    metadata_name = f"{dist_info}/METADATA"
    wheel_name = f"{dist_info}/WHEEL"
    top_level_name = f"{dist_info}/top_level.txt"
    for required in (metadata_name, wheel_name, top_level_name):
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
    if metadata.get_all("Metadata-Version", []) != ["2.1"]:
        raise ValueError("wheel METADATA version must be 2.1")
    metadata_names = metadata.get_all("Name", [])
    metadata_name_value = metadata_names[0] if len(metadata_names) == 1 else None
    if (
        not isinstance(metadata_name_value, str)
        or _normalized_distribution(metadata_name_value) != dist_name
        or metadata_name_value != "video-production-core"
    ):
        raise ValueError("wheel METADATA distribution name mismatch")
    if metadata.get_all("Version", []) != [dist_version]:
        raise ValueError("wheel METADATA version does not match dist-info")
    requires_python = metadata.get_all("Requires-Python", [])
    if len(requires_python) != 1 or not requires_python[0]:
        raise ValueError("wheel METADATA Requires-Python is missing")

    wheel = BytesParser(policy=policy.default).parsebytes(archive.read(wheel_name))
    if wheel.get_all("Wheel-Version", []) != ["1.0"]:
        raise ValueError("wheel contract version must be 1.0")
    if wheel.get_all("Root-Is-Purelib", []) != ["true"]:
        raise ValueError("wheel must declare Root-Is-Purelib: true")
    if wheel.get_all("Tag", []) != [EXPECTED_WHEEL_TAG]:
        raise ValueError("wheel compatibility tag mismatch")
    if archive.read(top_level_name) != b"video_factory\nvideo_factory_runtime\n":
        raise ValueError("wheel top-level package list is invalid")
    for required in (
        "video_factory/runtime/__init__.py",
        "video_factory_runtime/__init__.py",
        "video_factory_runtime/journal.py",
        "video_factory_runtime/publisher.py",
        "video_factory_runtime/migration.py",
    ):
        if required not in names:
            raise ValueError(f"wheel runtime boundary member is missing: {required}")


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


def _validate_director_resources(
    archive: zipfile.ZipFile, names: list[str]
) -> None:
    manifest_name = DIRECTOR_PREFIX + "director-resource-manifest.json"
    expected_documents = {
        "director-activation-policy.json",
        "director-registry.json",
    }
    expected_members = {
        DIRECTOR_PREFIX + "__init__.py",
        manifest_name,
        *(DIRECTOR_PREFIX + name for name in expected_documents),
    }
    if not expected_members.issubset(names):
        missing = sorted(expected_members - set(names))
        raise ValueError(f"wheel Director resources are incomplete: {missing}")
    actual_json = {
        name
        for name in names
        if name.startswith(DIRECTOR_PREFIX) and name.endswith(".json")
    }
    expected_json = expected_members - {DIRECTOR_PREFIX + "__init__.py"}
    if actual_json != expected_json:
        raise ValueError("wheel Director resource member set is invalid")
    manifest = _strict_object(archive.read(manifest_name), manifest_name)
    if set(manifest) != {"manifest_version", "resource_count", "resources"} or manifest.get(
        "manifest_version"
    ) != "director-resource-manifest/1.0":
        raise ValueError("wheel Director resource manifest shape/version is invalid")
    entries = manifest.get("resources")
    if manifest.get("resource_count") != 2 or not isinstance(entries, list):
        raise ValueError("wheel Director resource manifest count is invalid")
    filenames: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("wheel Director resource entry is not an object")
        filename = entry.get("filename")
        if set(entry) != {"filename", "sha256"} or not isinstance(filename, str):
            raise ValueError("wheel Director resource filename is invalid")
        filenames.append(filename)
        data = archive.read(DIRECTOR_PREFIX + filename)
        if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            raise ValueError(f"wheel Director resource digest mismatch: {filename}")
    if filenames != sorted(filenames) or set(filenames) != expected_documents:
        raise ValueError("wheel Director resource manifest set is invalid")
    registry = _strict_object(
        archive.read(DIRECTOR_PREFIX + "director-registry.json"),
        "wheel Director registry",
    )
    if set(registry) != {"registry_version", "registry_sha256", "charters"} or registry.get(
        "registry_version"
    ) != "director-registry/1.0":
        raise ValueError("wheel Director registry shape/version is invalid")
    charters = registry.get("charters")
    if not isinstance(charters, list) or len(charters) != 18:
        raise ValueError("wheel Director registry must contain 18 charters")
    expected_charter_keys = {
        "artifact_version",
        "charter_id",
        "charter_sha256",
        "director_id",
        "director_version",
        "kind",
        "owned_patterns",
        "verified_patterns",
        "activation_signals",
        "veto_patterns",
        "conflict_priority",
        "rules_version",
    }
    director_ids: list[str] = []
    for charter in charters:
        if not isinstance(charter, Mapping) or set(charter) != expected_charter_keys:
            raise ValueError("wheel Director charter shape is invalid")
        if charter.get("artifact_version") != "director-charter/1.0":
            raise ValueError("wheel Director charter artifact version is invalid")
        director_id = charter.get("director_id")
        if not isinstance(director_id, str):
            raise ValueError("wheel Director charter identity is invalid")
        director_ids.append(director_id)
        for field in (
            "owned_patterns",
            "verified_patterns",
            "activation_signals",
            "veto_patterns",
        ):
            values = charter.get(field)
            if (
                not isinstance(values, list)
                or values != sorted(set(values))
                or any(not isinstance(item, str) for item in values)
            ):
                raise ValueError("wheel Director charter list is not canonical")
        identity = {
            key: charter[key]
            for key in (
                "director_id",
                "director_version",
                "kind",
                "owned_patterns",
                "verified_patterns",
                "activation_signals",
                "veto_patterns",
                "conflict_priority",
                "rules_version",
            )
        }
        charter_sha = _canonical_sha256(identity)
        if charter.get("charter_sha256") != charter_sha or charter.get(
            "charter_id"
        ) != f"director-charter-{charter_sha[:20]}":
            raise ValueError("wheel Director charter identity digest is invalid")
    if director_ids != sorted(set(director_ids)) or "live-production-director" not in director_ids:
        raise ValueError("wheel Director registry identity set is invalid")
    expected_registry_sha = _canonical_sha256(
        {"registry_version": "director-registry/1.0", "charters": charters}
    )
    if registry.get("registry_sha256") != expected_registry_sha:
        raise ValueError("wheel Director registry digest is invalid")
    activation = _strict_object(
        archive.read(DIRECTOR_PREFIX + "director-activation-policy.json"),
        "wheel Director activation policy",
    )
    if set(activation) != {
        "activation_policy_version",
        "activation_policy_sha256",
        "sequential_stages_forbidden",
        "maximum_conflict_rounds",
        "conditional",
    } or activation.get("activation_policy_version") != "director-activation-policy/1.0":
        raise ValueError("wheel Director activation policy shape/version is invalid")
    conditional = activation.get("conditional")
    if (
        activation.get("sequential_stages_forbidden") is not True
        or activation.get("maximum_conflict_rounds") != 2
        or not isinstance(conditional, list)
        or len(conditional) != 6
    ):
        raise ValueError("wheel Director activation policy is invalid")
    expected_conditional = [
        {
            "director_id": charter["director_id"],
            "signals": charter["activation_signals"],
        }
        for charter in charters
        if charter["activation_signals"]
    ]
    if conditional != expected_conditional:
        raise ValueError("wheel Director activation policy does not match registry")
    expected_activation_sha = _canonical_sha256(
        {
            "activation_policy_version": "director-activation-policy/1.0",
            "sequential_stages_forbidden": True,
            "maximum_conflict_rounds": 2,
            "conditional": expected_conditional,
        }
    )
    if activation.get("activation_policy_sha256") != expected_activation_sha:
        raise ValueError("wheel Director activation policy digest is invalid")
    if not DIRECTOR_RESOURCE_ROOT.is_dir():
        raise ValueError("source Director resource projection is unavailable")
    for filename in (*sorted(expected_documents), "director-resource-manifest.json"):
        if archive.read(DIRECTOR_PREFIX + filename) != (
            DIRECTOR_RESOURCE_ROOT / filename
        ).read_bytes():
            raise ValueError(
                f"wheel Director resource does not match code projection: {filename}"
            )


def _validate_workflow_authority_resources(
    archive: zipfile.ZipFile, names: list[str]
) -> None:
    manifest_leaf = "workflow-authority-resource-manifest.json"
    manifest_name = WORKFLOW_AUTHORITY_PREFIX + manifest_leaf
    documents = {
        "authority-policy-v2.1.json",
        "episode-production-workflow.json",
        "legacy-parity-normalization.json",
    }
    expected = {
        WORKFLOW_AUTHORITY_PREFIX + "__init__.py",
        manifest_name,
        *(WORKFLOW_AUTHORITY_PREFIX + value for value in documents),
    }
    actual = {name for name in names if name.startswith(WORKFLOW_AUTHORITY_PREFIX)}
    if actual != expected:
        raise ValueError("wheel workflow authority resource member set is invalid")
    manifest = _strict_object(archive.read(manifest_name), manifest_name)
    if (
        set(manifest) != {"manifest_version", "resource_count", "resources"}
        or manifest.get("manifest_version")
        != "workflow-authority-resource-manifest/1.0"
        or manifest.get("resource_count") != 3
    ):
        raise ValueError("wheel workflow authority manifest shape/version is invalid")
    entries = manifest.get("resources")
    if not isinstance(entries, list) or len(entries) != 3:
        raise ValueError("wheel workflow authority manifest entries are invalid")
    filenames: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {"filename", "sha256"}:
            raise ValueError("wheel workflow authority manifest entry is invalid")
        filename = entry.get("filename")
        if not isinstance(filename, str):
            raise ValueError("wheel workflow authority filename is invalid")
        filenames.append(filename)
        payload = archive.read(WORKFLOW_AUTHORITY_PREFIX + filename)
        if hashlib.sha256(payload).hexdigest() != entry.get("sha256"):
            raise ValueError(f"wheel workflow authority digest mismatch: {filename}")
        if payload != (WORKFLOW_AUTHORITY_RESOURCE_ROOT / filename).read_bytes():
            raise ValueError(
                f"wheel workflow authority resource does not match code projection: {filename}"
            )
    if filenames != sorted(filenames) or set(filenames) != documents:
        raise ValueError("wheel workflow authority manifest set is invalid")
    if archive.read(manifest_name) != (
        WORKFLOW_AUTHORITY_RESOURCE_ROOT / manifest_leaf
    ).read_bytes():
        raise ValueError("wheel workflow authority manifest does not match code projection")


def _validate_quality_release_resources(
    archive: zipfile.ZipFile, names: list[str]
) -> None:
    manifest_leaf = "quality-release-resource-manifest.json"
    manifest_name = QUALITY_RELEASE_PREFIX + manifest_leaf
    documents = {"automation-quality-policy-v1.json"}
    expected = {
        QUALITY_RELEASE_PREFIX + "__init__.py",
        manifest_name,
        *(QUALITY_RELEASE_PREFIX + value for value in documents),
    }
    actual = {name for name in names if name.startswith(QUALITY_RELEASE_PREFIX)}
    if actual != expected:
        raise ValueError("wheel quality/release resource member set is invalid")
    manifest = _strict_object(archive.read(manifest_name), manifest_name)
    if (
        set(manifest) != {"manifest_version", "resource_count", "resources"}
        or manifest.get("manifest_version")
        != "quality-release-resource-manifest/1.0"
        or manifest.get("resource_count") != 1
    ):
        raise ValueError("wheel quality/release manifest shape/version is invalid")
    entries = manifest.get("resources")
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError("wheel quality/release manifest entries are invalid")
    filenames: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {"filename", "sha256"}:
            raise ValueError("wheel quality/release manifest entry is invalid")
        filename = entry.get("filename")
        if not isinstance(filename, str):
            raise ValueError("wheel quality/release filename is invalid")
        filenames.append(filename)
        payload = archive.read(QUALITY_RELEASE_PREFIX + filename)
        if hashlib.sha256(payload).hexdigest() != entry.get("sha256"):
            raise ValueError(f"wheel quality/release digest mismatch: {filename}")
        if payload != (QUALITY_RELEASE_RESOURCE_ROOT / filename).read_bytes():
            raise ValueError(
                f"wheel quality/release resource does not match code projection: {filename}"
            )
    if filenames != sorted(filenames) or set(filenames) != documents:
        raise ValueError("wheel quality/release manifest set is invalid")
    if archive.read(manifest_name) != (
        QUALITY_RELEASE_RESOURCE_ROOT / manifest_leaf
    ).read_bytes():
        raise ValueError("wheel quality/release manifest does not match code projection")


def _validate_runtime_migration_resources(
    archive: zipfile.ZipFile, names: list[str]
) -> None:
    manifest_leaf = "runtime-migration-resource-manifest.json"
    manifest_name = RUNTIME_MIGRATION_PREFIX + manifest_leaf
    documents = {"migration-registry-v1.json", "runtime-policy-v1.json"}
    expected = {
        RUNTIME_MIGRATION_PREFIX + "__init__.py",
        manifest_name,
        *(RUNTIME_MIGRATION_PREFIX + value for value in documents),
    }
    actual = {name for name in names if name.startswith(RUNTIME_MIGRATION_PREFIX)}
    if actual != expected:
        raise ValueError("wheel runtime/migration resource member set is invalid")
    manifest = _strict_object(archive.read(manifest_name), manifest_name)
    if (
        set(manifest) != {"manifest_version", "resource_count", "resources"}
        or manifest.get("manifest_version")
        != "runtime-migration-resource-manifest/1.0"
        or manifest.get("resource_count") != 2
    ):
        raise ValueError("wheel runtime/migration manifest shape/version is invalid")
    entries = manifest.get("resources")
    if not isinstance(entries, list) or len(entries) != 2:
        raise ValueError("wheel runtime/migration manifest entries are invalid")
    filenames: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {"filename", "sha256"}:
            raise ValueError("wheel runtime/migration manifest entry is invalid")
        filename = entry.get("filename")
        if not isinstance(filename, str):
            raise ValueError("wheel runtime/migration filename is invalid")
        filenames.append(filename)
        payload = archive.read(RUNTIME_MIGRATION_PREFIX + filename)
        if hashlib.sha256(payload).hexdigest() != entry.get("sha256"):
            raise ValueError(f"wheel runtime/migration digest mismatch: {filename}")
        if payload != (RUNTIME_MIGRATION_RESOURCE_ROOT / filename).read_bytes():
            raise ValueError(
                f"wheel runtime/migration resource differs from code projection: {filename}"
            )
    if filenames != sorted(filenames) or set(filenames) != documents:
        raise ValueError("wheel runtime/migration manifest set is invalid")
    if archive.read(manifest_name) != (
        RUNTIME_MIGRATION_RESOURCE_ROOT / manifest_leaf
    ).read_bytes():
        raise ValueError("wheel runtime/migration manifest differs from code projection")
    policy_document = _strict_object(
        archive.read(RUNTIME_MIGRATION_PREFIX + "runtime-policy-v1.json"),
        "wheel runtime policy",
    )
    policy_identity = dict(policy_document)
    policy_sha = policy_identity.pop("policy_sha256", None)
    if (
        policy_identity.get("policy_version") != "runtime-policy/1.0"
        or policy_identity.get("fixture_only") is not True
        or policy_identity.get("production_enabled") is not False
        or policy_sha != _canonical_sha256(policy_identity)
    ):
        raise ValueError("wheel runtime policy identity or safety mode is invalid")
    migration = policy_identity.get("migration")
    if not isinstance(migration, Mapping) or (
        migration.get("production_activation_enabled") is not False
        or migration.get("projections_are_authority") is not False
        or migration.get("projection_read_only") is not True
    ):
        raise ValueError("wheel runtime migration safety policy is invalid")
    registry = _strict_object(
        archive.read(RUNTIME_MIGRATION_PREFIX + "migration-registry-v1.json"),
        "wheel migration registry",
    )
    registry_identity = dict(registry)
    registry_sha = registry_identity.pop("registry_sha256", None)
    views = registry_identity.get("views")
    shadow_only_views = registry_identity.get("shadow_only_views")
    expected_views = [
        {
            "view_kind": "brief",
            "legacy_artifact_version": "brief/1.0",
            "consumer_status": "unregistered",
            "fixture_pinned_parity_required": True,
        },
        {
            "view_kind": "edit",
            "legacy_artifact_version": "edit-manifest/1.0",
            "consumer_status": "unregistered",
            "fixture_pinned_parity_required": True,
        },
        {
            "view_kind": "generation",
            "legacy_artifact_version": "generation-packet/2.1",
            "consumer_status": "unregistered",
            "fixture_pinned_parity_required": True,
        },
        {
            "view_kind": "storyboard",
            "legacy_artifact_version": "storyboard/1.0",
            "consumer_status": "unregistered",
            "fixture_pinned_parity_required": True,
        },
    ]
    expected_shadow_only_views = [
        {
            "view_kind": "publish",
            "migration_status": "no_registered_legacy_contract",
        },
        {
            "view_kind": "sound",
            "migration_status": "no_registered_legacy_contract",
        },
    ]
    if (
        registry_identity.get("registry_version")
        != "projection-migration-registry/1.1"
        or registry_identity.get("fixture_only") is not True
        or registry_identity.get("production_activation_enabled") is not False
        or registry_sha != _canonical_sha256(registry_identity)
        or views != expected_views
        or shadow_only_views != expected_shadow_only_views
    ):
        raise ValueError("wheel migration registry identity or safety mode is invalid")


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
        _validate_director_resources(archive, names)
        _validate_workflow_authority_resources(archive, names)
        _validate_quality_release_resources(archive, names)
        _validate_runtime_migration_resources(archive, names)
        schema_names = sorted(
            name
            for name in names
            if name.startswith(SCHEMA_PREFIX) and name.endswith(".schema.json")
        )
        manifest_name = SCHEMA_PREFIX + "schema-manifest.json"
        manifest = _strict_object(archive.read(manifest_name), manifest_name)
        entries = manifest.get("schemas")
        schema_count = manifest.get("schema_count")
        version_count = manifest.get("registered_version_count")
        if (
            not isinstance(schema_count, int)
            or isinstance(schema_count, bool)
            or schema_count < 1
            or len(schema_names) != schema_count
        ):
            raise ValueError("wheel schema count differs from its manifest")
        if not isinstance(entries, list) or len(entries) != schema_count:
            raise ValueError("wheel schema manifest entry count is invalid")
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
        if (
            not isinstance(version_count, int)
            or isinstance(version_count, bool)
            or versions != version_count
        ):
            raise ValueError(
                "wheel registered version count differs from its manifest"
            )
    return len(names), len(schema_names), hashlib.sha256(payload).hexdigest()


def install_and_probe(wheel: Path, python: Path, work_dir: Path) -> str:
    if work_dir.exists():
        raise ValueError(f"verification work directory must not exist: {work_dir}")
    install_dir = work_dir / "installed"
    install_dir.mkdir(parents=True)
    with zipfile.ZipFile(wheel) as archive:
        manifest = _strict_object(
            archive.read(SCHEMA_PREFIX + "schema-manifest.json"),
            "installed probe schema manifest",
        )
    expected_schemas = manifest.get("schema_count")
    expected_versions = manifest.get("registered_version_count")
    if not isinstance(expected_schemas, int) or not isinstance(
        expected_versions, int
    ):
        raise ValueError("installed probe schema counts are invalid")
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
from importlib.resources import files
from pathlib import Path
import sys
install = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(install))
import video_factory
import video_factory_runtime
from video_factory import authority, blueprint, directors, quality, release, runtime, selection, workflow
from video_factory.artifacts import ArtifactSchemaRegistry, validate_artifact_mapping
from video_factory.workflow.resources import validate_packaged_workflow_resources
from video_factory.quality.resources import validate_packaged_quality_resources
from video_factory.runtime.resources import validate_packaged_runtime_resources
package_file = Path(video_factory.__file__).resolve()
runtime_package_file = Path(video_factory_runtime.__file__).resolve()
if not package_file.is_relative_to(install):
    raise SystemExit(f'package escaped isolated install: {package_file}')
if not runtime_package_file.is_relative_to(install):
    raise SystemExit(f'runtime package escaped isolated install: {runtime_package_file}')
registry = ArtifactSchemaRegistry()
expected_schemas = int(sys.argv[2])
expected_versions = int(sys.argv[3])
if len(registry.all_schemas()) != expected_schemas or len(registry.list_versions()) != expected_versions:
    raise SystemExit('installed registry counts mismatch')
if len(directors.default_director_charters()) != 18:
    raise SystemExit('installed Director registry count mismatch')
director_resources = files('video_factory.resources.directors')
for name in ('director-registry.json', 'director-activation-policy.json', 'director-resource-manifest.json'):
    if not director_resources.joinpath(name).is_file():
        raise SystemExit(f'installed Director resource missing: {name}')
if not hasattr(blueprint, 'ProductionBlueprint'):
    raise SystemExit('installed Blueprint public contract missing')
if workflow.default_workflow_definition().workflow_version != 'episode-production-workflow/1.0':
    raise SystemExit('installed workflow definition missing')
if authority.target_policy_bundle().policy_version != 'authority-policy/2.1':
    raise SystemExit('installed authority policy missing')
authority_policy_resource = json.loads(
    files('video_factory.resources.workflow_authority')
    .joinpath('authority-policy-v2.1.json')
    .read_text(encoding='utf-8')
)
if authority_policy_resource != authority.policy_bundle_to_mapping(authority.target_policy_bundle()):
    raise SystemExit('installed authority policy resource/code projection mismatch')
validate_packaged_workflow_resources()
validate_packaged_quality_resources()
validate_packaged_runtime_resources()
if quality.target_quality_policy().artifact_version != 'quality-policy/1.0':
    raise SystemExit('installed quality policy missing')
if not hasattr(selection, 'CandidateDecision') or not hasattr(release, 'ReleaseAssessment'):
    raise SystemExit('installed W05 public contracts missing')
if not hasattr(runtime, 'ExecutionIntent') or not hasattr(video_factory_runtime, 'SQLiteExecutionJournal'):
    raise SystemExit('installed W06 runtime contracts missing')
valid = {'artifact_version': 'approval-requirement/1.0', 'rules_version': 'rules', 'episode_id': 'ep', 'requirement_id': 'req', 'capability_id': 'cap', 'bound_artifacts': [{'path': 'a.json', 'sha256': 'a'*64, 'artifact_version': 'brief/1.0'}], 'effective_config_sha256': 'b'*64, 'kind': 'packet', 'creates_evidence': False}
if not validate_artifact_mapping(valid, registry=registry).ok:
    raise SystemExit('installed registry could not validate a valid artifact')
bad = dict(valid); bad['effective_config_sha256'] = 'bad'
if validate_artifact_mapping(bad, registry=registry).ok:
    raise SystemExit('installed registry accepted an invalid artifact')
print(json.dumps({'package_file': str(package_file), 'schemas': expected_schemas, 'versions': expected_versions, 'manifest_sha256': registry.manifest_sha256}, sort_keys=True))
"""
    probed = subprocess.run(
        [
            str(python),
            "-I",
            "-B",
            "-c",
            probe,
            str(install_dir),
            str(expected_schemas),
            str(expected_versions),
        ],
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
