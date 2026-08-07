"""Write deterministic W04 resource projections using only project code/stdlib."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Sequence

from video_factory.workflow.resources import (
    workflow_resource_bytes,
    workflow_resource_manifest,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    payloads = workflow_resource_bytes()
    for name, payload in payloads.items():
        (args.output / name).write_bytes(payload)
    import json

    manifest_bytes = (
        json.dumps(
            workflow_resource_manifest(),
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    (args.output / "workflow-authority-resource-manifest.json").write_bytes(
        manifest_bytes
    )
    print(
        "workflow_resources=PASS "
        f"resources={len(payloads)} "
        f"manifest_sha256={hashlib.sha256(manifest_bytes).hexdigest()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
