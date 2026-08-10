"""Write the deterministic packaged W06 runtime/migration resources."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Sequence

from video_factory.runtime.resources import (
    runtime_resource_bytes,
    runtime_resource_manifest_bytes,
)


def build_resources(output: Path) -> dict[str, bytes]:
    output.mkdir(parents=True, exist_ok=True)
    payloads = runtime_resource_bytes()
    payloads["runtime-migration-resource-manifest.json"] = (
        runtime_resource_manifest_bytes()
    )
    for name, payload in payloads.items():
        (output / name).write_bytes(payload)
    return payloads


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    payloads = build_resources(args.output)
    manifest = payloads["runtime-migration-resource-manifest.json"]
    print(
        "runtime_resources=PASS "
        f"resources={len(payloads) - 1} "
        f"sha256={hashlib.sha256(manifest).hexdigest()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
