"""Command handlers: real implementations, contract-only, and honest stubs.

Handlers never invent a workflow mode default (OD-004) and never perform paid
external generation or publish actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import platform
import shutil
from typing import Mapping, Sequence

import video_factory
from video_factory.approvals import (
    ApprovalEvidence,
    ApprovalRequirementError,
    GateContext,
    build_approval_requirement,
    requirement_to_mapping,
    validate_evidence_binding,
)
from video_factory.artifacts import (
    ArtifactValidationResult,
    validate_artifact_bytes,
    validate_artifact_directory,
    validate_artifact_mapping,
    validate_artifact_path,
)
from video_factory.artifacts.registry import get_default_registry
from video_factory.config import (
    CONFIG_CONTRACT_VERSION,
    ConfigDraftError,
    ConfigLayer,
    ConfigValidationError,
    LayerConfig,
    config_document_from_mapping,
    draft_channel_config,
    draft_concept_config,
    draft_episode_config,
    parse_config_document,
)
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
    RoleId,
)
from video_factory.json_boundary import (
    JsonInputError,
    parse_json_path,
    require_json_object,
)
from video_factory.engine import (
    ArtifactSnapshot,
    OrchestrationPlanError,
    next_step_to_mapping,
    observation_to_mapping,
    observe_episode_state,
    plan_next_step,
)
from video_factory.policy import WorkflowPolicyError, resolve_workflow_policy
from video_factory.qc import (
    Constraint,
    Measurement,
    QCPlanError,
    build_qc_plan,
    judge_measurements,
    judgment_to_mapping,
    qc_plan_to_mapping,
)
from video_factory.review import ReviewRequest
from video_factory.security.purity import format_purity_report, scan_repository
from video_factory.storage import (
    WorkspaceExportPlanStatus,
    WorkspaceInitPlanStatus,
    plan_export as plan_export_workspace,
    plan_materialize as plan_materialize_workspace,
)

from .recovery import (
    RecoveryContractResult,
    RecoveryPolicyError,
    RetryIdempotencyLedger,
    request_invalidate,
    request_reopen,
    request_resume,
    request_retry,
)
from .registry import get_command
from .specs import CommandSpec, ImplementationStatus


class CommandNotYetBackedError(RuntimeError):
    """Raised when a registered command has no engine backing yet."""

    def __init__(self, spec: CommandSpec) -> None:
        phase = spec.expected_backing or "a later phase"
        message = (
            f"command {spec.name!r} is registered but not yet backed "
            f"(implementation_status={spec.implementation_status.value}; "
            f"expected backing: {phase})"
        )
        super().__init__(message)
        self.spec = spec


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Uniform CLI result envelope for both real and placeholder commands."""

    command: str
    exit_code: int
    status: str
    message: str
    payload: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Environment observation report.

    Existing fields are stable. Newer diagnostic fields are additive only and
    never assert that optional tools must be present (live_health principle).
    """

    core_version: str
    core_contract: str
    python_version: str
    purity_exit_code: int
    purity_summary: str
    purity_available: bool
    schema_registry: Mapping[str, object]
    optional_tools: Mapping[str, object]
    package_contracts: Mapping[str, object]


def _observe_schema_registry() -> dict[str, object]:
    """Load the manifest-verified package registry without checkout assumptions."""

    try:
        registry = get_default_registry()
    except Exception as error:  # noqa: BLE001 — observation must not raise to callers
        return {
            "available": False,
            "schemas_dir": None,
            "schema_files": 0,
            "registered_count": 0,
            "registered_versions": [],
            "load_failures": [f"schemas directory unavailable: {error}"],
        }

    unique_versions = list(registry.list_versions())
    return {
        "available": True,
        "schemas_dir": (
            str(registry.schemas_dir)
            if registry.schemas_dir is not None
            else f"package-resource:{registry.resource_root}"
        ),
        "schema_files": len(registry.all_schemas()),
        "registered_count": len(unique_versions),
        "registered_versions": unique_versions,
        "manifest_validated": registry.manifest_validated,
        "manifest_sha256": registry.manifest_sha256,
        "load_failures": [],
    }


def _observe_optional_tools() -> dict[str, object]:
    """Observe PATH presence of optional media tools — never required for doctor success."""

    tools: dict[str, object] = {}
    for name in ("ffmpeg", "ffprobe"):
        found = shutil.which(name)
        tools[name] = {
            "status": "present" if found else "absent",
            "path": found,
        }
    return tools


def _package_contract_summary() -> dict[str, object]:
    return {
        "core_version": video_factory.__version__,
        "core_contract": video_factory.CORE_CONTRACT_VERSION,
        "config_contract": CONFIG_CONTRACT_VERSION,
    }


@dataclass(frozen=True, slots=True)
class ValidateReport:
    ok: bool
    layer: str
    errors: tuple[str, ...]
    scope_id: str | None = None
    artifact_version: str | None = None
    kind: str = "config"
    batch: Mapping[str, object] | None = None


def _repository_root() -> Path | None:
    """Locate the core repository root when running from a source checkout."""

    package_file = Path(video_factory.__file__).resolve()
    # src/video_factory/__init__.py -> parents[0]=video_factory, [1]=src, [2]=repo
    candidate = package_file.parents[2]
    if (candidate / "tools" / "check_core_purity.py").is_file():
        return candidate
    # Editable installs may place the package differently; walk parents briefly.
    for parent in package_file.parents:
        if (parent / "tools" / "check_core_purity.py").is_file():
            return parent
    return None


def run_doctor(*, python_executable: str | None = None) -> DoctorReport:
    """Collect environment facts and run the purity scanner in-process.

    Schema-registry, optional-tool, and purity observations are all in-process
    (no child process launches). Optional tools may be absent; doctor still
    returns a report. ``python_executable`` is accepted for API stability but
    unused after the in-process purity migration.
    """

    del python_executable  # retained for call-site compatibility only
    root = _repository_root()
    schema_registry = _observe_schema_registry()
    optional_tools = _observe_optional_tools()
    package_contracts = _package_contract_summary()

    if root is None:
        return DoctorReport(
            core_version=video_factory.__version__,
            core_contract=video_factory.CORE_CONTRACT_VERSION,
            python_version=platform.python_version(),
            purity_exit_code=2,
            purity_summary="repository root with purity scanner not found relative to package",
            purity_available=False,
            schema_registry=schema_registry,
            optional_tools=optional_tools,
            package_contracts=package_contracts,
        )

    violations, scanned_files = scan_repository(root)
    exit_code, compact, _full = format_purity_report(violations, scanned_files)
    return DoctorReport(
        core_version=video_factory.__version__,
        core_contract=video_factory.CORE_CONTRACT_VERSION,
        python_version=platform.python_version(),
        purity_exit_code=exit_code,
        purity_summary=compact,
        purity_available=True,
        schema_registry=schema_registry,
        optional_tools=optional_tools,
        package_contracts=package_contracts,
    )


def _layer_from_value(layer: ConfigLayer | str) -> ConfigLayer:
    if isinstance(layer, ConfigLayer):
        return layer
    try:
        return ConfigLayer(str(layer).strip().lower())
    except ValueError as error:
        allowed = ("workspace", "channel", "concept", "episode")
        raise ConfigValidationError(
            f"unknown config layer {layer!r}; expected one of {', '.join(allowed)}"
        ) from error


def _scope_id(document: LayerConfig) -> str:
    for attr in ("workspace_id", "channel_id", "concept_id", "episode_id"):
        if hasattr(document, attr):
            return str(getattr(document, attr))
    return ""


def _artifact_result_to_report(result: ArtifactValidationResult) -> ValidateReport:
    return ValidateReport(
        ok=result.ok,
        layer="artifact",
        errors=result.error_texts,
        scope_id=None,
        artifact_version=result.artifact_version,
        kind="artifact",
    )


def run_validate_artifact(
    *,
    document: Mapping[str, object] | bytes | str | None = None,
    path: str | Path | None = None,
    artifact_version: str | None = None,
) -> ValidateReport:
    """Validate one production artifact document via the schema registry."""

    if path is not None:
        outcome = validate_artifact_path(path, artifact_version=artifact_version)
    elif isinstance(document, Mapping):
        outcome = validate_artifact_mapping(
            document, artifact_version=artifact_version
        )
    elif isinstance(document, bytes):
        outcome = validate_artifact_bytes(
            document, artifact_version=artifact_version
        )
    elif isinstance(document, str):
        outcome = validate_artifact_bytes(
            document.encode("utf-8"), artifact_version=artifact_version
        )
    else:
        return ValidateReport(
            ok=False,
            layer="artifact",
            errors=("validate artifact requires a document mapping, bytes, or path",),
            kind="artifact",
        )
    return _artifact_result_to_report(outcome)


def run_validate_artifact_directory(directory: str | Path) -> ValidateReport:
    """Validate every JSON artifact under a directory and return aggregate status."""

    try:
        report = validate_artifact_directory(directory)
    except Exception as error:  # engine-level (missing dir, etc.)
        return ValidateReport(
            ok=False,
            layer="artifact-batch",
            errors=(str(error),),
            kind="artifact-batch",
        )
    errors: list[str] = []
    for item in report.results:
        if item.result.ok:
            continue
        for text in item.result.error_texts:
            errors.append(f"{item.path}: {text}")
    batch = {
        "total": report.total,
        "passed": report.passed,
        "failed": report.failed,
        "skipped": report.skipped,
    }
    return ValidateReport(
        ok=report.failed == 0 and report.total > 0 and report.skipped == 0,
        layer="artifact-batch",
        errors=tuple(errors),
        kind="artifact-batch",
        batch=batch,
    )


def run_validate(
    *,
    layer: ConfigLayer | str | None = None,
    document: Mapping[str, object] | bytes | str | None = None,
    path: str | Path | None = None,
    artifact_version: str | None = None,
    directory: str | Path | None = None,
) -> ValidateReport:
    """Validate a config layer document or a production artifact document.

    Backward compatible: when ``layer`` is provided, behavior matches the
    previous config-only validator. When ``layer`` is omitted, the call is
    treated as artifact validation (single path/document or directory batch).
    """

    if directory is not None:
        return run_validate_artifact_directory(directory)

    if layer is None:
        return run_validate_artifact(
            document=document,
            path=path,
            artifact_version=artifact_version,
        )

    try:
        resolved_layer = _layer_from_value(layer)
        if resolved_layer not in {
            ConfigLayer.WORKSPACE,
            ConfigLayer.CHANNEL,
            ConfigLayer.CONCEPT,
            ConfigLayer.EPISODE,
        }:
            raise ConfigValidationError(
                f"{resolved_layer.value} is not a persisted configuration layer"
            )

        if path is not None:
            mapping = require_json_object(
                parse_json_path(path, decimal_numbers=True), source=str(path)
            )
            parsed = config_document_from_mapping(mapping, resolved_layer)
        elif isinstance(document, (bytes, bytearray)):
            parsed = parse_config_document(bytes(document), resolved_layer)
        elif isinstance(document, str):
            parsed = parse_config_document(document.encode("utf-8"), resolved_layer)
        elif isinstance(document, Mapping):
            parsed = config_document_from_mapping(document, resolved_layer)
        else:
            raise ConfigValidationError("validate requires a document mapping, bytes, or path")

        return ValidateReport(
            ok=True,
            layer=resolved_layer.value,
            errors=(),
            scope_id=_scope_id(parsed),
            artifact_version=str(parsed.artifact_version),
            kind="config",
        )
    except (ConfigValidationError, JsonInputError) as error:
        return ValidateReport(
            ok=False,
            layer=str(layer),
            errors=(str(error),),
            kind="config",
        )


def require_workflow_mode(mode: str | None) -> None:
    """Fail closed when a mode-required command is invoked without mode."""

    resolve_workflow_policy(mode)


def not_yet_backed_result(name: str) -> CommandResult:
    """Build an explicit not-yet-backed response (never silent)."""

    spec = get_command(name)
    error = CommandNotYetBackedError(spec)
    return CommandResult(
        command=name,
        exit_code=2,
        status=ImplementationStatus.NOT_YET_BACKED.value,
        message=str(error),
        payload={
            "implementation_status": spec.implementation_status.value,
            "expected_backing": spec.expected_backing,
            "requires_workflow_mode": spec.requires_workflow_mode,
        },
    )


def handle_doctor() -> CommandResult:
    report = run_doctor()
    exit_code = 0 if report.purity_available and report.purity_exit_code == 0 else 1
    if not report.purity_available:
        exit_code = 2
    # Schema load failures degrade the message but optional tool absence does not.
    registry_failures = report.schema_registry.get("load_failures") or []
    if exit_code == 0 and isinstance(registry_failures, list) and registry_failures:
        exit_code = 1
    message = (
        f"core={report.core_version} contract={report.core_contract} "
        f"python={report.python_version} purity={report.purity_summary} "
        f"schemas={report.schema_registry.get('registered_count')}"
    )
    return CommandResult(
        command="doctor",
        exit_code=exit_code,
        status="ok" if exit_code == 0 else "degraded",
        message=message,
        payload={
            "core_version": report.core_version,
            "core_contract": report.core_contract,
            "python_version": report.python_version,
            "purity_exit_code": report.purity_exit_code,
            "purity_summary": report.purity_summary,
            "purity_available": report.purity_available,
            "schema_registry": dict(report.schema_registry),
            "optional_tools": dict(report.optional_tools),
            "package_contracts": dict(report.package_contracts),
        },
    )


def handle_validate(
    *,
    layer: ConfigLayer | str | None = None,
    document: Mapping[str, object] | bytes | str | None = None,
    path: str | Path | None = None,
    artifact_version: str | None = None,
    directory: str | Path | None = None,
) -> CommandResult:
    report = run_validate(
        layer=layer,
        document=document,
        path=path,
        artifact_version=artifact_version,
        directory=directory,
    )
    if report.ok:
        if report.kind == "artifact-batch":
            message = (
                f"valid artifact directory batch "
                f"total={report.batch and report.batch.get('total')} "
                f"passed={report.batch and report.batch.get('passed')}"
            )
        elif report.kind == "artifact":
            message = f"valid artifact document artifact_version={report.artifact_version}"
        else:
            message = (
                f"valid {report.layer} document "
                f"scope_id={report.scope_id} artifact_version={report.artifact_version}"
            )
        return CommandResult(
            command="validate",
            exit_code=0,
            status="ok",
            message=message,
            payload={
                "ok": True,
                "kind": report.kind,
                "layer": report.layer,
                "scope_id": report.scope_id,
                "artifact_version": report.artifact_version,
                "errors": [],
                "batch": dict(report.batch) if report.batch is not None else None,
            },
        )
    joined = "; ".join(report.errors) if report.errors else "validation failed"
    return CommandResult(
        command="validate",
        exit_code=1,
        status="validation_failed",
        message=joined,
        payload={
            "ok": False,
            "kind": report.kind,
            "layer": report.layer,
            "artifact_version": report.artifact_version,
            "errors": list(report.errors),
            "batch": dict(report.batch) if report.batch is not None else None,
        },
    )


def _recovery_to_result(name: str, outcome: RecoveryContractResult) -> CommandResult:
    exit_code = 0
    status = (
        "duplicate"
        if outcome.acceptance.value == "duplicate"
        else ImplementationStatus.CONTRACT_ONLY.value
    )
    return CommandResult(
        command=name,
        exit_code=exit_code,
        status=status,
        message=outcome.message,
        payload={
            "acceptance": outcome.acceptance.value,
            "command": outcome.intent.command,
            "target_id": str(outcome.intent.target_id),
            "workflow_mode": outcome.intent.workflow_mode.value,
            "stage": outcome.intent.stage,
            "cascade": outcome.intent.cascade,
            "idempotency_key": (
                str(outcome.intent.idempotency_key)
                if outcome.intent.idempotency_key is not None
                else None
            ),
            "dispatched_new": outcome.dispatched_new,
            "implementation_status": ImplementationStatus.CONTRACT_ONLY.value,
        },
    )


def handle_retry(
    target_id: str,
    mode: str | None,
    *,
    idempotency_key: str,
    ledger: RetryIdempotencyLedger | None = None,
) -> CommandResult:
    try:
        outcome = request_retry(
            target_id,
            mode,
            idempotency_key=idempotency_key,
            ledger=ledger,
        )
    except (WorkflowPolicyError, RecoveryPolicyError) as error:
        return CommandResult(
            command="retry",
            exit_code=2,
            status="policy_error",
            message=str(error),
        )
    return _recovery_to_result("retry", outcome)


def handle_resume(
    target_id: str,
    mode: str | None,
    *,
    stage: str | None = None,
) -> CommandResult:
    try:
        outcome = request_resume(target_id, mode, stage=stage)
    except (WorkflowPolicyError, RecoveryPolicyError) as error:
        return CommandResult(
            command="resume",
            exit_code=2,
            status="policy_error",
            message=str(error),
        )
    return _recovery_to_result("resume", outcome)


def handle_invalidate(
    target_id: str,
    mode: str | None,
    *,
    cascade: bool = False,
) -> CommandResult:
    try:
        outcome = request_invalidate(target_id, mode, cascade=cascade)
    except (WorkflowPolicyError, RecoveryPolicyError) as error:
        return CommandResult(
            command="invalidate",
            exit_code=2,
            status="policy_error",
            message=str(error),
        )
    return _recovery_to_result("invalidate", outcome)


def handle_reopen(
    target_id: str,
    mode: str | None,
    *,
    stage: str,
) -> CommandResult:
    try:
        outcome = request_reopen(target_id, mode, stage=stage)
    except (WorkflowPolicyError, RecoveryPolicyError) as error:
        return CommandResult(
            command="reopen",
            exit_code=2,
            status="policy_error",
            message=str(error),
        )
    return _recovery_to_result("reopen", outcome)


def handle_not_yet_backed(name: str, mode: str | None = None) -> CommandResult:
    """Dispatch path for NOT_YET_BACKED commands with mode fail-closed."""

    spec = get_command(name)
    if spec.implementation_status is not ImplementationStatus.NOT_YET_BACKED:
        raise ValueError(f"{name} is not a not_yet_backed command")
    if spec.requires_workflow_mode:
        try:
            require_workflow_mode(mode)
        except WorkflowPolicyError as error:
            return CommandResult(
                command=name,
                exit_code=2,
                status="policy_error",
                message=str(error),
            )
    return not_yet_backed_result(name)


def handle_init(*, template_dir: str | Path, target_dir: str | Path) -> CommandResult:
    """Plan workspace materialization from a template (no workflow mode; no writes)."""

    plan = plan_materialize_workspace(Path(template_dir), Path(target_dir))
    if plan.status is not WorkspaceInitPlanStatus.READY:
        return CommandResult(
            command="init",
            exit_code=1,
            status="init_rejected",
            message=plan.rejection_reason or f"init plan rejected: {plan.status.value}",
            payload={
                "ok": False,
                "status": plan.status.value,
                "executed": plan.executed,
                "error": plan.rejection_reason,
                "operations": [],
                "configs_validated": plan.configs_validated,
            },
        )
    return CommandResult(
        command="init",
        exit_code=0,
        status="ok",
        message=(
            f"workspace init plan ready operations={len(plan.operations)} "
            f"configs_validated={plan.configs_validated} "
            f"executed={plan.executed} target={plan.target_dir}"
        ),
        payload={
            "ok": True,
            "status": plan.status.value,
            "executed": plan.executed,
            "template_dir": str(plan.template_dir),
            "target_dir": str(plan.target_dir),
            "operations": [
                {
                    "source_relative": item.source_relative,
                    "destination_relative": item.destination_relative,
                    "source_sha256": item.source_sha256,
                }
                for item in plan.operations
            ],
            "operation_count": len(plan.operations),
            "configs_validated": plan.configs_validated,
        },
    )


def handle_export(
    *,
    source_dir: str | Path,
    target: str | Path,
) -> CommandResult:
    """Plan a workspace export after sensitive-pattern scan (no writes; no publish)."""

    plan = plan_export_workspace(Path(source_dir), Path(target))
    findings = [
        {
            "relative_path": item.relative_path,
            "line_number": item.line_number,
            "pattern_id": item.pattern_id,
        }
        for item in plan.findings
    ]
    if plan.status is not WorkspaceExportPlanStatus.READY:
        status = (
            "export_refused"
            if plan.status is WorkspaceExportPlanStatus.REJECTED_SENSITIVE
            else "export_rejected"
        )
        return CommandResult(
            command="export",
            exit_code=1,
            status=status,
            message=plan.rejection_reason or f"export plan rejected: {plan.status.value}",
            payload={
                "ok": False,
                "status": plan.status.value,
                "executed": plan.executed,
                "error": plan.rejection_reason,
                "violations": findings,
                "violation_count": len(findings),
                "files": [],
                "files_scanned": plan.files_scanned,
            },
        )
    return CommandResult(
        command="export",
        exit_code=0,
        status="ok",
        message=(
            f"export plan ready output_kind={plan.output_kind} "
            f"files={len(plan.files)} "
            f"files_scanned={plan.files_scanned} "
            f"executed={plan.executed} target={plan.target}"
        ),
        payload={
            "ok": True,
            "status": plan.status.value,
            "executed": plan.executed,
            "source_dir": str(plan.source_dir),
            "target": str(plan.target),
            "files_scanned": plan.files_scanned,
            "files": list(plan.files),
            "file_count": len(plan.files),
            "pattern_kinds_checked": list(plan.pattern_kinds_checked),
            "output_kind": plan.output_kind,
        },
    )


def _mode_or_error(command: str, mode: str | None) -> CommandResult | None:
    try:
        require_workflow_mode(mode)
    except WorkflowPolicyError as error:
        return CommandResult(
            command=command,
            exit_code=2,
            status="policy_error",
            message=str(error),
        )
    return None


def handle_qc(
    mode: str | None,
    *,
    expectations: Sequence[Mapping[str, object]] | None = None,
    constraints: Sequence[Constraint] | None = None,
    measurements: Sequence[Measurement | Mapping[str, object]] | None = None,
    artifact: ArtifactReference | Mapping[str, object] | None = None,
) -> CommandResult:
    """Build a QC plan and optionally judge injected measurements (no media tools)."""

    blocked = _mode_or_error("qc", mode)
    if blocked is not None:
        return blocked

    try:
        plan = build_qc_plan(
            constraints=constraints or (),
            expectations=expectations or (),
        )
    except QCPlanError as error:
        return CommandResult(
            command="qc",
            exit_code=1,
            status="qc_plan_error",
            message=str(error),
            payload={"ok": False, "executed": False, "error": str(error)},
        )

    payload: dict[str, object] = {
        "ok": True,
        "executed": False,
        "workflow_mode": str(mode).strip().lower() if mode else None,
        "plan": qc_plan_to_mapping(plan),
        "judgment": None,
    }
    if measurements is None:
        return CommandResult(
            command="qc",
            exit_code=0,
            status="ok",
            message=(
                f"qc plan ready checks={len(plan.checks)} executed=false "
                f"(inject measurements to judge)"
            ),
            payload=payload,
        )

    parsed_measurements: list[Measurement] = []
    for index, item in enumerate(measurements):
        if isinstance(item, Measurement):
            parsed_measurements.append(item)
            continue
        if not isinstance(item, Mapping):
            return CommandResult(
                command="qc",
                exit_code=1,
                status="qc_plan_error",
                message=f"measurements[{index}] must be Measurement or mapping",
            )
        mid = item.get("measurement_id")
        if not isinstance(mid, str) or not mid:
            return CommandResult(
                command="qc",
                exit_code=1,
                status="qc_plan_error",
                message=f"measurements[{index}] missing measurement_id",
            )
        if "value" not in item:
            return CommandResult(
                command="qc",
                exit_code=1,
                status="qc_plan_error",
                message=f"measurements[{index}] missing value",
            )
        unit = item.get("unit")
        parsed_measurements.append(
            Measurement(
                measurement_id=OpaqueId(mid),
                value=item["value"],  # type: ignore[arg-type]
                unit=unit if isinstance(unit, str) else None,
            )
        )

    subject: ArtifactReference | None = None
    if isinstance(artifact, ArtifactReference):
        subject = artifact
    elif isinstance(artifact, Mapping):
        try:
            subject = ArtifactReference(
                path=RelativeArtifactPath(str(artifact["path"])),
                sha256=HashDigest(str(artifact["sha256"])),
                artifact_version=ArtifactVersion(str(artifact["artifact_version"])),
            )
        except (KeyError, TypeError, ValueError) as error:
            return CommandResult(
                command="qc",
                exit_code=1,
                status="qc_plan_error",
                message=f"invalid artifact reference: {error}",
            )

    try:
        judgment = judge_measurements(
            plan, parsed_measurements, artifact=subject
        )
    except QCPlanError as error:
        return CommandResult(
            command="qc",
            exit_code=1,
            status="qc_plan_error",
            message=str(error),
            payload={"ok": False, "executed": False, "error": str(error)},
        )

    payload["judgment"] = judgment_to_mapping(judgment)
    exit_code = 0 if judgment.fail_count == 0 else 1
    return CommandResult(
        command="qc",
        exit_code=exit_code,
        status="ok" if exit_code == 0 else "qc_failed",
        message=(
            f"qc judgment overall={judgment.overall.value} "
            f"pass={judgment.pass_count} warn={judgment.warn_count} "
            f"fail={judgment.fail_count} "
            f"inconclusive={judgment.inconclusive_count} "
            f"not_applicable={judgment.not_applicable_count} executed=false"
        ),
        payload=payload,
    )


def handle_approve(
    mode: str | None,
    *,
    kind: str,
    artifacts: Sequence[Mapping[str, object] | ArtifactReference],
    effective_config_sha256: str,
    requirement_id: str | None = None,
    capability_id: str | None = None,
    evidence: ApprovalEvidence | None = None,
    episode_id: str | None = None,
    rules_version: str | None = None,
    gate_context: GateContext | Mapping[str, object] | None = None,
    evaluated_at: datetime | str | None = None,
) -> CommandResult:
    """Build an ApprovalRequirement; optionally validate evidence (never create it)."""

    blocked = _mode_or_error("approve", mode)
    if blocked is not None:
        return blocked

    try:
        requirement = build_approval_requirement(
            kind,
            artifacts,
            effective_config_sha256,
            requirement_id=requirement_id,
            capability_id=capability_id,
            gate_context=gate_context,
        )
    except ApprovalRequirementError as error:
        return CommandResult(
            command="approve",
            exit_code=1,
            status="approval_error",
            message=str(error),
            payload={"ok": False, "creates_evidence": False, "error": str(error)},
        )

    document = requirement_to_mapping(
        requirement,
        kind=kind,
        episode_id=episode_id,
        rules_version=rules_version,
        include_schema_envelope=(
            episode_id is not None and rules_version is not None
        ),
    )
    payload: dict[str, object] = {
        "ok": True,
        "creates_evidence": False,
        "executed": False,
        "workflow_mode": str(mode).strip().lower() if mode else None,
        "requirement": document,
        "evidence_binding": None,
        "note": (
            "ApprovalRequirement only — ApprovalEvidence must be supplied by a human; "
            "core never auto-approves"
        ),
    }

    if evidence is not None:
        binding = validate_evidence_binding(
            requirement,
            evidence,
            current_context=gate_context,
            evaluated_at=evaluated_at,
        )
        payload["evidence_binding"] = {
            "ok": binding.ok,
            "message": binding.message,
            "evidence_id": binding.evidence_id,
            "reason_code": binding.reason_code,
        }
        if not binding.ok:
            return CommandResult(
                command="approve",
                exit_code=1,
                status="evidence_rejected",
                message=binding.message,
                payload=payload,
            )
        return CommandResult(
            command="approve",
            exit_code=0,
            status="ok",
            message=(
                f"requirement ready and evidence binding ok "
                f"requirement_id={requirement.requirement_id} creates_evidence=false"
            ),
            payload=payload,
        )

    return CommandResult(
        command="approve",
        exit_code=0,
        status="ok",
        message=(
            f"approval requirement ready requirement_id={requirement.requirement_id} "
            f"creates_evidence=false executed=false"
        ),
        payload=payload,
    )


def handle_status(
    mode: str | None,
    *,
    artifact_docs: Sequence[ArtifactSnapshot | Mapping[str, object]] = (),
) -> CommandResult:
    """Observe episode artifact state (report only; no mutation)."""

    blocked = _mode_or_error("status", mode)
    if blocked is not None:
        return blocked

    try:
        observation = observe_episode_state(artifact_docs)
    except OrchestrationPlanError as error:
        return CommandResult(
            command="status",
            exit_code=1,
            status="status_error",
            message=str(error),
        )

    body = observation_to_mapping(observation)
    return CommandResult(
        command="status",
        exit_code=0,
        status="ok",
        message=(
            f"status observation kinds={len(observation.present_kinds)} "
            f"mode={str(mode).strip().lower()} mutated=false"
        ),
        payload={
            "ok": True,
            "mutated": False,
            "executed": False,
            "workflow_mode": str(mode).strip().lower() if mode else None,
            "observation": body,
        },
    )


def handle_run(
    mode: str | None,
    *,
    artifact_docs: Sequence[ArtifactSnapshot | Mapping[str, object]] = (),
    current_context: GateContext | None = None,
    evaluated_at: datetime | None = None,
) -> CommandResult:
    """Return next-step plan only (never transitions or executes stages)."""

    blocked = _mode_or_error("run", mode)
    if blocked is not None:
        return blocked

    try:
        observation = observe_episode_state(artifact_docs)
        plan = plan_next_step(
            observation,
            mode,
            current_context=current_context,
            evaluated_at=evaluated_at,
        )
        document = next_step_to_mapping(plan)
    except OrchestrationPlanError as error:
        return CommandResult(
            command="run",
            exit_code=1,
            status="run_plan_error",
            message=str(error),
            payload={"ok": False, "transition_applied": False, "error": str(error)},
        )

    return CommandResult(
        command="run",
        exit_code=0,
        status="ok",
        message=(
            f"next-step plan action_type={plan.action_type} "
            f"transition_applied={plan.transition_applied} "
            f"auto_execution={plan.auto_execution}"
        ),
        payload={
            "ok": True,
            "executed": False,
            "transition_applied": plan.transition_applied,
            "auto_execution": plan.auto_execution,
            "workflow_mode": plan.workflow_mode.value,
            "next_step": document,
            "observation": observation_to_mapping(observation),
        },
    )


def handle_new_channel(
    mode: str | None,
    *,
    channel_id: str,
    settings: Mapping[str, object] | None = None,
    extensions: Mapping[str, object] | None = None,
) -> CommandResult:
    """Return a validated channel-config draft mapping (no disk write)."""

    blocked = _mode_or_error("new-channel", mode)
    if blocked is not None:
        return blocked
    try:
        document = draft_channel_config(
            channel_id, settings=settings, extensions=extensions
        )
    except ConfigDraftError as error:
        return CommandResult(
            command="new-channel",
            exit_code=1,
            status="draft_error",
            message=str(error),
        )
    return CommandResult(
        command="new-channel",
        exit_code=0,
        status="ok",
        message=f"channel-config draft ready channel_id={channel_id} written=false",
        payload={
            "ok": True,
            "written": False,
            "executed": False,
            "workflow_mode": str(mode).strip().lower() if mode else None,
            "document": document,
        },
    )


def handle_new_concept(
    mode: str | None,
    *,
    concept_id: str,
    settings: Mapping[str, object] | None = None,
    extensions: Mapping[str, object] | None = None,
) -> CommandResult:
    """Return a validated concept-config draft mapping (no disk write)."""

    blocked = _mode_or_error("new-concept", mode)
    if blocked is not None:
        return blocked
    try:
        document = draft_concept_config(
            concept_id, settings=settings, extensions=extensions
        )
    except ConfigDraftError as error:
        return CommandResult(
            command="new-concept",
            exit_code=1,
            status="draft_error",
            message=str(error),
        )
    return CommandResult(
        command="new-concept",
        exit_code=0,
        status="ok",
        message=f"concept-config draft ready concept_id={concept_id} written=false",
        payload={
            "ok": True,
            "written": False,
            "executed": False,
            "workflow_mode": str(mode).strip().lower() if mode else None,
            "document": document,
        },
    )


def handle_new_episode(
    mode: str | None,
    *,
    episode_id: str,
    settings: Mapping[str, object] | None = None,
    extensions: Mapping[str, object] | None = None,
) -> CommandResult:
    """Return a validated episode-config draft mapping (no disk write)."""

    blocked = _mode_or_error("new-episode", mode)
    if blocked is not None:
        return blocked
    try:
        document = draft_episode_config(
            episode_id, settings=settings, extensions=extensions
        )
    except ConfigDraftError as error:
        return CommandResult(
            command="new-episode",
            exit_code=1,
            status="draft_error",
            message=str(error),
        )
    return CommandResult(
        command="new-episode",
        exit_code=0,
        status="ok",
        message=f"episode-config draft ready episode_id={episode_id} written=false",
        payload={
            "ok": True,
            "written": False,
            "executed": False,
            "workflow_mode": str(mode).strip().lower() if mode else None,
            "document": document,
        },
    )


def handle_review(
    mode: str | None,
    *,
    review_id: str,
    subject: Mapping[str, object] | ArtifactReference,
    creator_role: str,
    reviewer_role: str,
    effective_config_sha256: str,
    output_contract: str = "review-result/1.0",
) -> CommandResult:
    """Build a ReviewRequest document (does not perform the review)."""

    blocked = _mode_or_error("review", mode)
    if blocked is not None:
        return blocked

    try:
        if isinstance(subject, ArtifactReference):
            subject_ref = subject
        else:
            subject_ref = ArtifactReference(
                path=RelativeArtifactPath(str(subject["path"])),
                sha256=HashDigest(str(subject["sha256"])),
                artifact_version=ArtifactVersion(str(subject["artifact_version"])),
            )
        request = ReviewRequest(
            review_id=OpaqueId(review_id),
            subject=subject_ref,
            creator_role=RoleId(creator_role),
            reviewer_role=RoleId(reviewer_role),
            effective_config_sha256=HashDigest(effective_config_sha256),
            output_contract=output_contract,
        )
    except (KeyError, TypeError, ValueError) as error:
        return CommandResult(
            command="review",
            exit_code=1,
            status="review_error",
            message=f"invalid review request inputs: {error}",
        )

    document = {
        "review_id": str(request.review_id),
        "subject": {
            "path": str(request.subject.path),
            "sha256": str(request.subject.sha256),
            "artifact_version": str(request.subject.artifact_version),
        },
        "creator_role": str(request.creator_role),
        "reviewer_role": str(request.reviewer_role),
        "effective_config_sha256": str(request.effective_config_sha256),
        "output_contract": request.output_contract,
        "performed": False,
    }
    return CommandResult(
        command="review",
        exit_code=0,
        status="ok",
        message=(
            f"review request ready review_id={request.review_id} "
            f"performed=false executed=false"
        ),
        payload={
            "ok": True,
            "performed": False,
            "executed": False,
            "workflow_mode": str(mode).strip().lower() if mode else None,
            "request": document,
        },
    )
