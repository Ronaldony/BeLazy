"""Render exact W05 quality/release policy package resources."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from video_factory.quality.resources import (
    quality_resource_bytes,
    quality_resource_manifest_bytes,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, payload in quality_resource_bytes().items():
        (args.output / name).write_bytes(payload)
    manifest = quality_resource_manifest_bytes()
    (args.output / "quality-release-resource-manifest.json").write_bytes(manifest)
    print(
        "quality_release_resources=PASS "
        f"resources={len(quality_resource_bytes())} bytes={len(manifest)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
