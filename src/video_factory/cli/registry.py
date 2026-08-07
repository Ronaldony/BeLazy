"""Canonical registry of general video-factory CLI commands.

The registry is the single source of command names. Workspace-local recovery
subcommands from legacy scripts are intentionally absent; migration docs map them.
"""

from __future__ import annotations

from .specs import CommandSpec, ImplementationStatus

# Ordered for stable help and acceptance checks. Names use hyphen form for the
# multi-word "new *" family so argparse and programmatic dispatch share one key.
COMMAND_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec(
        name="init",
        summary=(
            "Plan workspace materialization from a caller-supplied template "
            "(validates only; does not write files)."
        ),
        requires_workflow_mode=False,
        implementation_status=ImplementationStatus.IMPLEMENTED,
        backing_modules=("video_factory.storage.workspace_init",),
        expected_backing=None,
    ),
    CommandSpec(
        name="new-channel",
        summary=(
            "Return a validated channel-config document draft "
            "(plan-only; does not write files)."
        ),
        requires_workflow_mode=True,
        implementation_status=ImplementationStatus.IMPLEMENTED,
        backing_modules=("video_factory.config.drafts",),
        expected_backing=None,
    ),
    CommandSpec(
        name="new-concept",
        summary=(
            "Return a validated concept-config document draft "
            "(plan-only; does not write files)."
        ),
        requires_workflow_mode=True,
        implementation_status=ImplementationStatus.IMPLEMENTED,
        backing_modules=("video_factory.config.drafts",),
        expected_backing=None,
    ),
    CommandSpec(
        name="new-episode",
        summary=(
            "Return a validated episode-config document draft "
            "(plan-only; does not write files)."
        ),
        requires_workflow_mode=True,
        implementation_status=ImplementationStatus.IMPLEMENTED,
        backing_modules=("video_factory.config.drafts",),
        expected_backing=None,
    ),
    CommandSpec(
        name="run",
        summary=(
            "Return the next-step plan for injected episode artifacts "
            "(plan-only; does not transition or execute)."
        ),
        requires_workflow_mode=True,
        implementation_status=ImplementationStatus.IMPLEMENTED,
        backing_modules=("video_factory.engine.orchestration",),
        expected_backing=None,
    ),
    CommandSpec(
        name="status",
        summary=(
            "Return an observation report for injected episode artifacts "
            "(read-only; no workflow mutation)."
        ),
        requires_workflow_mode=True,
        implementation_status=ImplementationStatus.IMPLEMENTED,
        backing_modules=("video_factory.engine.orchestration",),
        expected_backing=None,
    ),
    CommandSpec(
        name="validate",
        summary=(
            "Validate a persisted config document or a production artifact "
            "against core schemas."
        ),
        requires_workflow_mode=False,
        implementation_status=ImplementationStatus.IMPLEMENTED,
        backing_modules=("video_factory.config", "video_factory.artifacts"),
        expected_backing=None,
    ),
    CommandSpec(
        name="review",
        summary=(
            "Build a ReviewRequest document for a subject "
            "(plan-only; does not perform the review)."
        ),
        requires_workflow_mode=True,
        implementation_status=ImplementationStatus.IMPLEMENTED,
        backing_modules=("video_factory.review",),
        expected_backing=None,
    ),
    CommandSpec(
        name="approve",
        summary=(
            "Build an ApprovalRequirement and optionally validate evidence binding "
            "(never creates ApprovalEvidence / never auto-approves)."
        ),
        requires_workflow_mode=True,
        implementation_status=ImplementationStatus.IMPLEMENTED,
        backing_modules=("video_factory.approvals",),
        expected_backing=None,
    ),
    CommandSpec(
        name="retry",
        summary="Request exactly one retry of a failed target (idempotent).",
        requires_workflow_mode=True,
        implementation_status=ImplementationStatus.CONTRACT_ONLY,
        backing_modules=("video_factory.policy", "video_factory.domain"),
        expected_backing="PHASE 9+ orchestrator dispatch + storage ledger",
    ),
    CommandSpec(
        name="resume",
        summary="Resume a stopped target from the last approved point (same stage).",
        requires_workflow_mode=True,
        implementation_status=ImplementationStatus.CONTRACT_ONLY,
        backing_modules=("video_factory.policy",),
        expected_backing="PHASE 9+ episode state store and orchestrator",
    ),
    CommandSpec(
        name="invalidate",
        summary="Describe invalidation intent for a target, optionally cascading.",
        requires_workflow_mode=True,
        implementation_status=ImplementationStatus.CONTRACT_ONLY,
        backing_modules=("video_factory.policy", "video_factory.storage"),
        expected_backing="PHASE 9+ storage invalidation engine (no silent delete)",
    ),
    CommandSpec(
        name="reopen",
        summary="Reopen a target by returning to an earlier stage, then continue.",
        requires_workflow_mode=True,
        implementation_status=ImplementationStatus.CONTRACT_ONLY,
        backing_modules=("video_factory.policy",),
        expected_backing="PHASE 9+ episode state store and orchestrator",
    ),
    CommandSpec(
        name="qc",
        summary=(
            "Build a QC plan from injected expectations and optionally judge "
            "injected measurements (never runs ffprobe/ffmpeg)."
        ),
        requires_workflow_mode=True,
        implementation_status=ImplementationStatus.IMPLEMENTED,
        backing_modules=("video_factory.qc",),
        expected_backing=None,
    ),
    CommandSpec(
        name="migrate",
        summary="Apply a declared artifact major migration to new output bytes.",
        requires_workflow_mode=True,
        implementation_status=ImplementationStatus.NOT_YET_BACKED,
        backing_modules=(),
        expected_backing="PHASE 21+ artifact migrator + no-overwrite storage",
    ),
    CommandSpec(
        name="export",
        summary=(
            "Plan a workspace export after sensitive-pattern scan "
            "(local only; does not write)."
        ),
        requires_workflow_mode=False,
        implementation_status=ImplementationStatus.IMPLEMENTED,
        backing_modules=("video_factory.storage.workspace_export",),
        expected_backing=None,
    ),
    CommandSpec(
        name="doctor",
        summary="Report core version, Python version, and repository purity scan.",
        requires_workflow_mode=False,
        implementation_status=ImplementationStatus.IMPLEMENTED,
        backing_modules=("video_factory",),
        expected_backing=None,
    ),
)


def _build_registry() -> dict[str, CommandSpec]:
    registry: dict[str, CommandSpec] = {}
    for spec in COMMAND_SPECS:
        if spec.name in registry:
            raise ValueError(f"duplicate CLI command name: {spec.name}")
        registry[spec.name] = spec
    return registry


COMMAND_REGISTRY: dict[str, CommandSpec] = _build_registry()


def get_command(name: str) -> CommandSpec:
    """Return a registered command or raise KeyError."""

    try:
        return COMMAND_REGISTRY[name]
    except KeyError as error:
        known = ", ".join(sorted(COMMAND_REGISTRY))
        raise KeyError(f"unknown CLI command {name!r}; known: {known}") from error


def list_commands() -> tuple[CommandSpec, ...]:
    """Return all registered commands in registration order."""

    return COMMAND_SPECS


def registered_names() -> frozenset[str]:
    return frozenset(COMMAND_REGISTRY)
