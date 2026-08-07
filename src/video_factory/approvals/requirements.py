"""Approval requirement builder and evidence-binding checks (plan-only).

This module creates ``ApprovalRequirement`` documents bound to artifact
path+sha256 digests. It never creates ``ApprovalEvidence`` — evidence is a
human gate responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Mapping, Sequence

from video_factory.config import canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.json_boundary import parse_rfc3339_datetime

from .contracts import (
    ApprovalEvidence,
    ApprovalRequirement,
    ApprovalState,
    GateContext,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_GATE_CONTEXT_FIELDS = (
    "workflow_definition_sha256",
    "policy_bundle_sha256",
    "rules_bundle_sha256",
    "effective_config_sha256",
    "current_manifest_sha256",
    "evidence_graph_sha256",
    "executable_plan_sha256",
)

# kind → capability_id default + schema artifact_version for document mapping
_KIND_CAPABILITY: Mapping[str, str] = {
    "generation_approval": "generation_approval",
    "publish_approval": "publish_approval",
    "destructive_action_approval": "destructive_action_approval",
    "policy_exception_approval": "policy_exception_approval",
    "packet": "generation_approval",
    "packet-approval": "generation_approval",
    "storyboard": "storyboard_approval",
    "storyboard-approval": "storyboard_approval",
    "publish": "publish_approval",
    "publish-approval": "publish_approval",
}

_KIND_SCHEMA_VERSION: Mapping[str, str] = {
    "packet": "packet-approval/2.0",
    "packet-approval": "packet-approval/2.0",
    "generation_approval": "packet-approval/2.0",
    "storyboard": "storyboard-approval/2.0",
    "storyboard-approval": "storyboard-approval/2.0",
    "publish": "publish-approval/1.0",
    "publish-approval": "publish-approval/1.0",
    "publish_approval": "publish-approval/1.0",
}


class ApprovalRequirementError(ValueError):
    """Raised when a requirement cannot be built or evidence fails binding."""


@dataclass(frozen=True, slots=True)
class EvidenceBindingResult:
    """Outcome of comparing evidence to a requirement (never invents evidence)."""

    ok: bool
    message: str
    requirement_id: str
    evidence_id: str | None
    reason_code: str = "approval.binding_unknown"


def _opaque(value: object, label: str) -> OpaqueId:
    if not isinstance(value, str) or _OPAQUE.fullmatch(value) is None:
        raise ApprovalRequirementError(f"{label} must be a valid opaque identifier")
    return OpaqueId(value)


def _sha256(value: object, label: str) -> HashDigest:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ApprovalRequirementError(f"{label} must be a 64-char lowercase hex sha256")
    return HashDigest(value)


def gate_context_from_mapping(data: Mapping[str, object]) -> GateContext:
    """Parse exactly seven required material digests; unknown fields fail closed."""

    if not isinstance(data, Mapping):
        raise ApprovalRequirementError("gate_context must be an object")
    missing = sorted(set(_GATE_CONTEXT_FIELDS) - set(data))
    extra = sorted(set(data) - set(_GATE_CONTEXT_FIELDS))
    if missing or extra:
        raise ApprovalRequirementError(
            f"gate_context fields mismatch; missing={missing}, extra={extra}"
        )
    values = {
        field: _sha256(data[field], f"gate_context.{field}")
        for field in _GATE_CONTEXT_FIELDS
    }
    return GateContext(**values)


def gate_context_to_mapping(context: GateContext) -> dict[str, str]:
    if not isinstance(context, GateContext):
        raise ApprovalRequirementError("gate_context must be GateContext")
    return {
        field: str(getattr(context, field))
        for field in _GATE_CONTEXT_FIELDS
    }


def gate_context_sha256(context: GateContext) -> HashDigest:
    return canonical_sha256(gate_context_to_mapping(context))


def _gate_context(
    value: GateContext | Mapping[str, object],
) -> GateContext:
    if isinstance(value, GateContext):
        # Round-trip through the strict parser so manually-constructed values
        # receive the same digest validation as document mappings.
        return gate_context_from_mapping(gate_context_to_mapping(value))
    return gate_context_from_mapping(value)


def _artifact_ref(item: ArtifactReference | Mapping[str, object]) -> ArtifactReference:
    if isinstance(item, ArtifactReference):
        path = str(item.path)
        if not path or path.startswith("/") or "\\" in path or ".." in path.split("/"):
            raise ApprovalRequirementError(f"invalid artifact path: {path!r}")
        _sha256(str(item.sha256), "artifact.sha256")
        return item
    if not isinstance(item, Mapping):
        raise ApprovalRequirementError(
            f"artifact must be ArtifactReference or mapping; got {type(item).__name__}"
        )
    for key in ("path", "sha256", "artifact_version"):
        if key not in item:
            raise ApprovalRequirementError(f"artifact mapping missing {key!r}")
    path = item["path"]
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
        raise ApprovalRequirementError(f"invalid artifact path: {path!r}")
    if ".." in path.split("/"):
        raise ApprovalRequirementError(f"artifact path must not contain '..': {path!r}")
    return ArtifactReference(
        path=RelativeArtifactPath(path),
        sha256=_sha256(item["sha256"], "artifact.sha256"),
        artifact_version=ArtifactVersion(str(item["artifact_version"])),
    )


def build_approval_requirement(
    kind: str,
    artifacts: Sequence[ArtifactReference | Mapping[str, object]],
    effective_config_sha256: str | HashDigest,
    *,
    requirement_id: str | OpaqueId | None = None,
    capability_id: str | OpaqueId | None = None,
    gate_context: GateContext | Mapping[str, object] | None = None,
) -> ApprovalRequirement:
    """Build a hash-bound approval requirement (no evidence, no auto-approve).

    Parameters
    ----------
    kind:
        Approval kind or schema family key (e.g. ``generation_approval``,
        ``packet``, ``storyboard-approval``). Used to default ``capability_id``.
    artifacts:
        Bound artifact references (path + sha256 + artifact_version).
    effective_config_sha256:
        Digest of the effective configuration this gate is bound to.
    """

    if not isinstance(kind, str) or not kind.strip():
        raise ApprovalRequirementError("kind must be a non-empty string")
    kind_key = kind.strip().lower()
    if not artifacts:
        raise ApprovalRequirementError("artifacts must be non-empty")

    bound = tuple(_artifact_ref(item) for item in artifacts)
    # Stable order for deterministic equality checks.
    bound = tuple(sorted(bound, key=lambda a: (str(a.path), str(a.sha256), str(a.artifact_version))))
    identities = {
        (str(item.path), str(item.sha256), str(item.artifact_version)) for item in bound
    }
    if len(identities) != len(bound):
        raise ApprovalRequirementError("bound_artifacts must not contain duplicates")

    config_hash = _sha256(str(effective_config_sha256), "effective_config_sha256")
    parsed_context = _gate_context(gate_context) if gate_context is not None else None
    if (
        parsed_context is not None
        and parsed_context.effective_config_sha256 != config_hash
    ):
        raise ApprovalRequirementError(
            "gate_context effective config does not match effective_config_sha256"
        )

    if capability_id is not None:
        cap = _opaque(str(capability_id), "capability_id")
    else:
        default_cap = _KIND_CAPABILITY.get(kind_key)
        if default_cap is None:
            # Treat unknown kind as opaque capability id itself when well-formed.
            cap = _opaque(kind_key, "capability_id")
        else:
            cap = OpaqueId(default_cap)

    if requirement_id is not None:
        req_id = _opaque(str(requirement_id), "requirement_id")
    else:
        identity = {
            "kind": kind_key,
            "capability_id": str(cap),
            "bound_artifacts": [
                {
                    "path": str(item.path),
                    "sha256": str(item.sha256),
                    "artifact_version": str(item.artifact_version),
                }
                for item in bound
            ],
            "effective_config_sha256": str(config_hash),
        }
        if parsed_context is not None:
            identity["gate_context"] = gate_context_to_mapping(parsed_context)
        req_id = OpaqueId(f"req-{str(canonical_sha256(identity))[:20]}")

    return ApprovalRequirement(
        requirement_id=req_id,
        capability_id=cap,
        bound_artifacts=bound,
        effective_config_sha256=config_hash,
        gate_context=parsed_context,
    )


def requirement_to_mapping(
    requirement: ApprovalRequirement,
    *,
    kind: str = "packet-approval",
    episode_id: str | None = None,
    rules_version: str | None = None,
    include_schema_envelope: bool = True,
) -> dict[str, object]:
    """Serialize a pending requirement without impersonating evidence."""

    bound = [
        {
            "path": str(item.path),
            "sha256": str(item.sha256),
            "artifact_version": str(item.artifact_version),
        }
        for item in requirement.bound_artifacts
    ]
    document: dict[str, object] = {
        "requirement_id": str(requirement.requirement_id),
        "capability_id": str(requirement.capability_id),
        "bound_artifacts": bound,
        "effective_config_sha256": str(requirement.effective_config_sha256),
        "kind": kind,
        "creates_evidence": False,
    }
    if requirement.gate_context is not None:
        document["gate_context"] = gate_context_to_mapping(
            requirement.gate_context
        )
    if episode_id is not None:
        document["episode_id"] = episode_id
    if rules_version is not None:
        document["rules_version"] = rules_version

    if include_schema_envelope:
        if episode_id is None or rules_version is None:
            raise ApprovalRequirementError(
                "episode_id and rules_version are required for a schema envelope"
            )
        document["artifact_version"] = "approval-requirement/1.0"
    return document


def approval_evidence_to_mapping(
    evidence: ApprovalEvidence,
    *,
    kind: str,
    episode_id: str,
    rules_version: str,
) -> dict[str, object]:
    """Serialize caller-supplied granted evidence to an approval artifact.

    Core never creates ``ApprovalEvidence``. This function only preserves an
    already supplied human record and fails closed for rejected evidence.
    """

    if evidence.state is not ApprovalState.GRANTED:
        raise ApprovalRequirementError(
            "only granted human evidence can become an approval artifact"
        )
    kind_key = kind.strip().lower()
    artifact_version = _KIND_SCHEMA_VERSION.get(kind_key)
    if artifact_version is None:
        raise ApprovalRequirementError(f"unsupported approval evidence kind: {kind!r}")

    expected_capability = _KIND_CAPABILITY.get(kind_key)
    if (
        expected_capability is not None
        and str(evidence.requirement.capability_id) != expected_capability
    ):
        raise ApprovalRequirementError(
            "evidence capability does not match the requested approval kind"
        )

    document: dict[str, object] = {
        "artifact_version": artifact_version,
        "rules_version": rules_version,
        "episode_id": episode_id,
        "requirement_id": str(evidence.requirement.requirement_id),
        "capability_id": str(evidence.requirement.capability_id),
        "evidence_id": str(evidence.evidence_id),
        "state": evidence.state.value,
        "approved_by_human": True,
        "approver_role": str(evidence.approver_role),
        "approved_at": evidence.created_at,
        "record_sha256": str(evidence.record_sha256),
        "bound_artifacts": [
            {
                "path": str(item.path),
                "sha256": str(item.sha256),
                "artifact_version": str(item.artifact_version),
            }
            for item in evidence.requirement.bound_artifacts
        ],
        "effective_config_sha256": str(
            evidence.requirement.effective_config_sha256
        ),
    }
    if evidence.requirement.gate_context is not None:
        document["gate_context"] = gate_context_to_mapping(
            evidence.requirement.gate_context
        )
    if evidence.expires_at is not None:
        document["expires_at"] = evidence.expires_at
    return document


def requirement_from_mapping(data: Mapping[str, object]) -> ApprovalRequirement:
    """Parse an ApprovalRequirement from a mapping (round-trip with to_mapping)."""

    if "bound_artifacts" not in data:
        raise ApprovalRequirementError("mapping missing bound_artifacts")
    if "effective_config_sha256" not in data:
        raise ApprovalRequirementError("mapping missing effective_config_sha256")

    req_id = data.get("requirement_id")
    cap_id = data.get("capability_id")
    if req_id is None:
        raise ApprovalRequirementError("mapping missing requirement_id")
    if cap_id is None:
        # Fall back to kind → capability for older documents.
        kind = data.get("kind") or data.get("artifact_version")
        if isinstance(kind, str) and kind:
            kind_key = kind.split("/")[0].replace("_", "-")
            cap_default = _KIND_CAPABILITY.get(kind_key) or _KIND_CAPABILITY.get(
                kind.replace("_", "-")
            )
            if cap_default is None:
                raise ApprovalRequirementError("mapping missing capability_id")
            cap_id = cap_default
        else:
            raise ApprovalRequirementError("mapping missing capability_id")

    raw_artifacts = data["bound_artifacts"]
    if not isinstance(raw_artifacts, Sequence) or isinstance(
        raw_artifacts, (str, bytes, bytearray)
    ):
        raise ApprovalRequirementError("bound_artifacts must be an array")

    bound = tuple(_artifact_ref(item) for item in raw_artifacts)  # type: ignore[arg-type]
    identities = {
        (str(item.path), str(item.sha256), str(item.artifact_version))
        for item in bound
    }
    if len(identities) != len(bound):
        raise ApprovalRequirementError("bound_artifacts must not contain duplicates")
    bound = tuple(
        sorted(
            bound,
            key=lambda item: (
                str(item.path),
                str(item.sha256),
                str(item.artifact_version),
            ),
        )
    )
    config_hash = _sha256(
        data["effective_config_sha256"], "effective_config_sha256"
    )
    raw_context = data.get("gate_context")
    parsed_context = None
    if raw_context is not None:
        if not isinstance(raw_context, Mapping):
            raise ApprovalRequirementError("gate_context must be an object")
        parsed_context = gate_context_from_mapping(raw_context)
        if parsed_context.effective_config_sha256 != config_hash:
            raise ApprovalRequirementError(
                "gate_context effective config does not match effective_config_sha256"
            )

    return ApprovalRequirement(
        requirement_id=_opaque(str(req_id), "requirement_id"),
        capability_id=_opaque(str(cap_id), "capability_id"),
        bound_artifacts=bound,
        effective_config_sha256=config_hash,
        gate_context=parsed_context,
    )


def _requirements_equal(left: ApprovalRequirement, right: ApprovalRequirement) -> bool:
    if left.capability_id != right.capability_id:
        return False
    if left.effective_config_sha256 != right.effective_config_sha256:
        return False
    if left.gate_context != right.gate_context:
        return False
    if _artifact_identities(left.bound_artifacts) != _artifact_identities(
        right.bound_artifacts
    ):
        return False
    return True


def _binding_result(
    requirement: ApprovalRequirement,
    evidence: ApprovalEvidence | None,
    *,
    ok: bool,
    message: str,
    reason_code: str,
) -> EvidenceBindingResult:
    return EvidenceBindingResult(
        ok=ok,
        message=message,
        requirement_id=str(requirement.requirement_id),
        evidence_id=(str(evidence.evidence_id) if evidence is not None else None),
        reason_code=reason_code,
    )


def _artifact_identities(
    artifacts: Sequence[ArtifactReference],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        sorted(
            (
                str(item.path),
                str(item.sha256),
                str(item.artifact_version),
            )
            for item in artifacts
        )
    )


def _evaluation_time(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return parse_rfc3339_datetime(value)
        except ValueError as error:
            raise ApprovalRequirementError(
                "evaluated_at must be a timezone-aware RFC 3339 date-time"
            ) from error
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ApprovalRequirementError("evaluated_at must be timezone-aware")
    return value


def validate_evidence_binding(
    requirement: ApprovalRequirement,
    evidence: ApprovalEvidence | None,
    *,
    require_granted: bool = True,
    current_context: GateContext | Mapping[str, object] | None,
    evaluated_at: datetime | str | None,
) -> EvidenceBindingResult:
    """Authorize only evidence bound to an explicit current context and time.

    Legacy documents remain schema-loadable, but this authority API never
    treats a context-free or timeless document as authorization.
    """

    if evidence is None:
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message="required human evidence is missing",
            reason_code="approval.evidence_missing",
        )
    if require_granted and evidence.state is not ApprovalState.GRANTED:
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message="human evidence is not granted",
            reason_code="approval.evidence_not_granted",
        )
    bound = evidence.requirement
    if bound.capability_id != requirement.capability_id:
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message="human evidence capability does not match the requirement",
            reason_code="approval.capability_mismatch",
        )
    if bound.effective_config_sha256 != requirement.effective_config_sha256:
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message="human evidence is bound to another effective config",
            reason_code="approval.effective_config_mismatch",
        )

    required_identities = _artifact_identities(requirement.bound_artifacts)
    evidence_identities = _artifact_identities(bound.bound_artifacts)
    if len(set(required_identities)) != len(required_identities) or len(
        set(evidence_identities)
    ) != len(evidence_identities):
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message="bound artifacts contain duplicate identities",
            reason_code="approval.artifact_duplicate",
        )
    if evidence_identities != required_identities:
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message="human evidence is bound to different input artifacts",
            reason_code="approval.artifact_mismatch",
        )

    if current_context is None:
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message="current gate context is missing",
            reason_code="approval.current_context_missing",
        )
    try:
        current = _gate_context(current_context)
    except ApprovalRequirementError as error:
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message=str(error),
            reason_code="approval.current_context_invalid",
        )
    if requirement.gate_context is None or bound.gate_context is None:
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message="approval gate context is missing",
            reason_code="approval.bound_context_missing",
        )
    if requirement.gate_context != current or bound.gate_context != current:
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message="human evidence is bound to another material context",
            reason_code="approval.context_mismatch",
        )
    if current.effective_config_sha256 != requirement.effective_config_sha256:
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message="current context effective config is inconsistent",
            reason_code="approval.context_config_inconsistent",
        )
    try:
        evaluation = _evaluation_time(evaluated_at)
    except ApprovalRequirementError as error:
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message=str(error),
            reason_code="approval.evaluation_time_invalid",
        )
    if evaluation is None:
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message="approval evaluation time is missing",
            reason_code="approval.evaluation_time_missing",
        )
    if evidence.expires_at is None:
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message="human evidence expiry is missing",
            reason_code="approval.expiry_missing",
        )
    try:
        approved = parse_rfc3339_datetime(evidence.created_at)
        expires = parse_rfc3339_datetime(evidence.expires_at)
    except ValueError:
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message="approval validity window is not valid RFC 3339",
            reason_code="approval.validity_window_invalid",
        )
    if approved >= expires:
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message="approval expiry must be after approval time",
            reason_code="approval.validity_window_invalid",
        )
    if approved > evaluation:
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message="approval was issued after the evaluation time",
            reason_code="approval.not_yet_valid",
        )
    if evaluation >= expires:
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message="human evidence has expired",
            reason_code="approval.expired",
        )

    if not _requirements_equal(bound, requirement):
        return _binding_result(
            requirement,
            evidence,
            ok=False,
            message="human evidence requirement fields do not match",
            reason_code="approval.requirement_mismatch",
        )
    return _binding_result(
        requirement,
        evidence,
        ok=True,
        message="evidence binding matches requirement and current context",
        reason_code="approval.binding_match",
    )


def assert_evidence_binding(
    requirement: ApprovalRequirement,
    evidence: ApprovalEvidence | None,
    *,
    require_granted: bool = True,
    current_context: GateContext | Mapping[str, object] | None,
    evaluated_at: datetime | str | None,
) -> str:
    """Like ``validate_evidence_binding`` but raises on failure; returns evidence_id."""

    result = validate_evidence_binding(
        requirement,
        evidence,
        require_granted=require_granted,
        current_context=current_context,
        evaluated_at=evaluated_at,
    )
    if not result.ok:
        raise ApprovalRequirementError(result.message)
    assert result.evidence_id is not None
    return result.evidence_id
