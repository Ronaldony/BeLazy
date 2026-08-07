"""Build the deterministic packaged-schema manifest using only stdlib."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


MANIFEST_VERSION = "video-factory-schema-manifest/1.0"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"duplicate JSON key {key!r}")
        output[key] = value
    return output


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number {value!r}")


def _artifact_version(schema: Mapping[str, object]) -> str | None:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return None
    node = properties.get("artifact_version")
    if not isinstance(node, Mapping):
        return None
    value = node.get("const")
    return value if isinstance(value, str) and "/" in value else None


def build_manifest(schemas_dir: Path) -> dict[str, object]:
    paths = sorted(schemas_dir.glob("*.schema.json"), key=lambda item: item.name)
    if not paths:
        raise ValueError(f"no schema files found in {schemas_dir}")
    entries: list[dict[str, object]] = []
    schema_ids: set[str] = set()
    versions: set[str] = set()
    for path in paths:
        payload = path.read_bytes()
        try:
            parsed = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError(f"invalid strict JSON schema {path.name}: {error}") from error
        if not isinstance(parsed, Mapping):
            raise ValueError(f"schema top-level is not an object: {path.name}")
        schema_id = parsed.get("$id")
        if not isinstance(schema_id, str) or not schema_id:
            raise ValueError(f"schema has no non-empty $id: {path.name}")
        if schema_id in schema_ids:
            raise ValueError(f"duplicate schema $id: {schema_id}")
        schema_ids.add(schema_id)
        version = _artifact_version(parsed)
        if version is not None:
            if version in versions:
                raise ValueError(f"duplicate artifact_version: {version}")
            versions.add(version)
        entries.append(
            {
                "filename": path.name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "schema_id": schema_id,
                "artifact_version": version,
            }
        )
    return {
        "manifest_version": MANIFEST_VERSION,
        "schema_count": len(entries),
        "registered_version_count": len(versions),
        "schemas": entries,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schemas", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = build_manifest(args.schemas)
    rendered = json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
        allow_nan=False,
    ) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(
        "schema_manifest=PASS "
        f"schemas={manifest['schema_count']} "
        f"registered_versions={manifest['registered_version_count']} "
        f"sha256={hashlib.sha256(rendered.encode('utf-8')).hexdigest()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
