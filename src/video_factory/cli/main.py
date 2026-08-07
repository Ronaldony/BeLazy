"""CLI entry point wired to the command registry.

This module replaces the empty scaffold. It registers only the general command
set, dispatches real handlers for implemented commands, evaluates recovery
contracts, and returns explicit not-yet-backed results for the rest.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .handlers import (
    CommandResult,
    handle_approve,
    handle_doctor,
    handle_export,
    handle_init,
    handle_invalidate,
    handle_new_channel,
    handle_new_concept,
    handle_new_episode,
    handle_not_yet_backed,
    handle_qc,
    handle_reopen,
    handle_resume,
    handle_retry,
    handle_review,
    handle_run,
    handle_status,
    handle_validate,
)
from .registry import COMMAND_SPECS, get_command
from .specs import ImplementationStatus


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-factory",
        description="Channel-neutral video production core CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for spec in COMMAND_SPECS:
        help_text = f"[{spec.implementation_status.value}] {spec.summary}"
        command_parser = sub.add_parser(spec.name, help=help_text)

        if spec.requires_workflow_mode:
            command_parser.add_argument(
                "--mode",
                required=False,
                default=None,
                help="Explicit workflow mode (rapid|standard|controlled). Required; no default.",
            )

        if spec.name == "validate":
            command_parser.add_argument(
                "--layer",
                required=False,
                default=None,
                choices=("workspace", "channel", "concept", "episode"),
                help=(
                    "Persisted configuration layer of the document. "
                    "Omit to validate a production artifact by its artifact_version."
                ),
            )
            command_parser.add_argument(
                "--artifact-version",
                required=False,
                default=None,
                help="Optional explicit artifact_version override for artifact validation",
            )
            command_parser.add_argument(
                "--directory",
                required=False,
                default=None,
                help="Validate every *.json artifact under this directory (batch)",
            )
            command_parser.add_argument(
                "path",
                nargs="?",
                default=None,
                help="Path to a UTF-8 JSON configuration or artifact document",
            )
        elif spec.name == "init":
            command_parser.add_argument(
                "--template",
                required=True,
                help="Path to a template directory to plan materialization for",
            )
            command_parser.add_argument(
                "--target",
                required=True,
                help="Empty or non-existent destination directory (never overwritten)",
            )
        elif spec.name == "export":
            command_parser.add_argument(
                "--source",
                required=True,
                help="Workspace directory to plan export for (local scan only)",
            )
            command_parser.add_argument(
                "--target",
                required=True,
                help="Destination directory or .zip archive path (no publish)",
            )
        elif spec.name in {"retry", "resume", "invalidate", "reopen"}:
            command_parser.add_argument(
                "target_id",
                help="Opaque target identifier (run, episode, or artifact id)",
            )
            if spec.name == "retry":
                command_parser.add_argument(
                    "--idempotency-key",
                    required=True,
                    help="Caller-supplied idempotency key (exactly one new dispatch per key)",
                )
            if spec.name in {"resume", "reopen"}:
                command_parser.add_argument(
                    "--from",
                    dest="stage",
                    required=(spec.name == "reopen"),
                    default=None,
                    help=(
                        "Stage name. Required for reopen (return to stage). "
                        "Optional for resume (continue same-stage from last approved point)."
                    ),
                )
            if spec.name == "invalidate":
                command_parser.add_argument(
                    "--cascade",
                    action="store_true",
                    help="Request cascade invalidation of dependent artifacts",
                )
        elif spec.name in {"new-channel", "new-concept", "new-episode"}:
            command_parser.add_argument(
                "scope_id",
                help="Opaque scope id for the draft config document",
            )
        elif spec.name in {"run", "status", "qc", "approve", "review"}:
            # Library-first commands: CLI entry accepts mode; documents are
            # injected via the programmatic handlers (plan-only, no file I/O).
            command_parser.add_argument(
                "args",
                nargs="*",
                help="Reserved; prefer library handlers with injected documents",
            )
        elif spec.implementation_status is ImplementationStatus.NOT_YET_BACKED:
            # Accept leftover positional tokens only as opaque context for future engines.
            command_parser.add_argument(
                "args",
                nargs="*",
                help="Reserved for future engine arguments (currently unused)",
            )

    return parser


def dispatch(args: argparse.Namespace) -> CommandResult:
    name = args.command
    spec = get_command(name)

    if name == "doctor":
        return handle_doctor()
    if name == "validate":
        if getattr(args, "directory", None):
            return handle_validate(directory=args.directory)
        if args.path is None:
            return CommandResult(
                command="validate",
                exit_code=2,
                status="usage_error",
                message="validate requires a path or --directory",
            )
        return handle_validate(
            layer=args.layer,
            path=args.path,
            artifact_version=getattr(args, "artifact_version", None),
        )
    if name == "init":
        return handle_init(template_dir=args.template, target_dir=args.target)
    if name == "export":
        return handle_export(source_dir=args.source, target=args.target)
    if name == "retry":
        return handle_retry(
            args.target_id,
            getattr(args, "mode", None),
            idempotency_key=args.idempotency_key,
        )
    if name == "resume":
        return handle_resume(
            args.target_id,
            getattr(args, "mode", None),
            stage=getattr(args, "stage", None),
        )
    if name == "invalidate":
        return handle_invalidate(
            args.target_id,
            getattr(args, "mode", None),
            cascade=bool(getattr(args, "cascade", False)),
        )
    if name == "reopen":
        return handle_reopen(
            args.target_id,
            getattr(args, "mode", None),
            stage=args.stage,
        )
    if name == "new-channel":
        return handle_new_channel(getattr(args, "mode", None), channel_id=args.scope_id)
    if name == "new-concept":
        return handle_new_concept(getattr(args, "mode", None), concept_id=args.scope_id)
    if name == "new-episode":
        return handle_new_episode(getattr(args, "mode", None), episode_id=args.scope_id)
    if name == "status":
        return handle_status(getattr(args, "mode", None), artifact_docs=())
    if name == "run":
        return handle_run(getattr(args, "mode", None), artifact_docs=())
    if name == "qc":
        # CLI without injected expectations returns a usage-style plan error;
        # programmatic handle_qc is the primary surface.
        return handle_qc(getattr(args, "mode", None), expectations=None)
    if name == "approve":
        return CommandResult(
            command="approve",
            exit_code=2,
            status="usage_error",
            message=(
                "approve requires programmatic handle_approve(kind, artifacts, "
                "effective_config_sha256); CLI shell does not invent evidence"
            ),
        )
    if name == "review":
        return CommandResult(
            command="review",
            exit_code=2,
            status="usage_error",
            message=(
                "review requires programmatic handle_review(...); "
                "CLI shell does not invent subjects"
            ),
        )
    if spec.implementation_status is ImplementationStatus.NOT_YET_BACKED:
        return handle_not_yet_backed(name, getattr(args, "mode", None))

    return CommandResult(
        command=name,
        exit_code=2,
        status="error",
        message=f"no dispatch path registered for command {name!r}",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = dispatch(args)
    stream = sys.stdout if result.exit_code == 0 else sys.stderr
    print(result.message, file=stream)
    if result.payload is not None and "--json" in (argv or ()):
        print(json.dumps(dict(result.payload), sort_keys=True), file=stream)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
