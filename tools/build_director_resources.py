"""Build deterministic target-owned Director registry resources."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Sequence

from video_factory.directors import (
    ACTIVATION_POLICY_VERSION,
    DIRECTOR_REGISTRY_VERSION,
    MAX_CONFLICT_ROUNDS,
    activation_policy_sha256,
    default_director_charters,
    director_charter_to_mapping,
    director_registry_sha256,
)


def _render(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def build_resources(output: Path) -> tuple[int, str]:
    output.mkdir(parents=True, exist_ok=True)
    allowed_json = {
        "director-activation-policy.json",
        "director-registry.json",
        "director-resource-manifest.json",
    }
    unexpected = sorted(
        path.name for path in output.glob("*.json") if path.name not in allowed_json
    )
    if unexpected:
        raise ValueError(f"unexpected Director resource files: {unexpected}")
    charters = default_director_charters()
    registry = {
        "registry_version": DIRECTOR_REGISTRY_VERSION,
        "registry_sha256": str(director_registry_sha256(charters)),
        "charters": [director_charter_to_mapping(item) for item in charters],
    }
    conditional = [
        {
            "director_id": str(item.director_id),
            "signals": list(item.activation_signals),
        }
        for item in charters
        if item.activation_signals
    ]
    policy = {
        "activation_policy_version": ACTIVATION_POLICY_VERSION,
        "activation_policy_sha256": str(activation_policy_sha256(charters)),
        "sequential_stages_forbidden": True,
        "maximum_conflict_rounds": MAX_CONFLICT_ROUNDS,
        "conditional": conditional,
    }
    documents = {
        "director-activation-policy.json": _render(policy),
        "director-registry.json": _render(registry),
    }
    for filename, payload in documents.items():
        (output / filename).write_bytes(payload)
    manifest = {
        "manifest_version": "director-resource-manifest/1.0",
        "resource_count": len(documents),
        "resources": [
            {
                "filename": filename,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for filename, payload in sorted(documents.items())
        ],
    }
    manifest_bytes = _render(manifest)
    (output / "director-resource-manifest.json").write_bytes(manifest_bytes)
    return len(charters), hashlib.sha256(manifest_bytes).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    charters, digest = build_resources(args.output)
    print(f"director_resources=PASS charters={charters} manifest_sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
