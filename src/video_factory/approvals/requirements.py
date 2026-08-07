"""Approval requirement builder and evidence-binding checks (plan-only).

This module creates ``ApprovalRequirement`` documents bound to artifact
path+sha256 digests. It never creates ``ApprovalEvidence`` — evidence is a
human gate responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass
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

from .contracts import ApprovalEvidence, ApprovalRequirement, ApprovalState

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

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


def _opaque(value: object, label: str) -> OpaqueId:
    if not isinstance(value, str) or _OPAQUE.fullmatch(value) is None:
        raise ApprovalRequirementError(f"{label} must be a valid opaque identifier")
    return OpaqueId(value)


def _sha256(value: object, label: str) -> HashDigest:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ApprovalRequirementError(f"{label} must be a 64-char lowercase hex sha256")
    return HashDigest(value)


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
            "effective_config_sha256": str(effective_config_sha256),
        }
        req_id = OpaqueId(f"req-{str(canonical_sha256(identity))[:20]}")

    config_hash = _sha256(str(effective_config_sha256), "effective_config_sha256")
    return ApprovalRequirement(
        requirement_id=req_id,
        capability_id=cap,
        bound_artifacts=bound,
        effective_config_sha256=config_hash,
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

    return {
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

    return ApprovalRequirement(
        requirement_id=_opaque(str(req_id), "requirement_id"),
        capability_id=_opaque(str(cap_id), "capability_id"),
        bound_artifacts=tuple(_artifact_ref(item) for item in raw_artifacts),  # type: ignore[arg-type]
        effective_config_sha256=_sha256(
            data["effective_config_sha256"], "effective_config_sha256"
        ),
    )


def _requirements_equal(left: ApprovalRequirement, right: ApprovalRequirement) -> bool:
    if left.capability_id != right.capability_id:
        return False
    if left.effective_config_sha256 != right.effective_config_sha256:
        return False
    if left.bound_artifacts != right.bound_artifacts:
        return False
    return True


def validate_evidence_binding(
    requirement: ApprovalRequirement,
    evidence: ApprovalEvidence | None,
    *,
    require_granted: bool = True,
) -> EvidenceBindingResult:
    """Check that *evidence* is exactly bound to *requirement*.

    Aligns with PHASE 6 ``_validate_human_evidence`` checks:
    capability match, effective_config_sha256 match, bound_artifacts match.
    Does not create evidence. Returns a structured result (fail-closed).
    """

    req_id = str(requirement.requirement_id)
    if evidence is None:
        return EvidenceBindingResult(
            ok=False,
            message="required human evidence is missing",
            requirement_id=req_id,
            evidence_id=None,
        )
    if require_granted and evidence.state is not ApprovalState.GRANTED:
        return EvidenceBindingResult(
            ok=False,
            message="human evidence is not granted",
            requirement_id=req_id,
            evidence_id=str(evidence.evidence_id),
        )
    bound = evidence.requirement
    if bound.capability_id != requirement.capability_id:
        return EvidenceBindingResult(
            ok=False,
            message="human evidence capability does not match the requirement",
            requirement_id=req_id,
            evidence_id=str(evidence.evidence_id),
        )
    if bound.effective_config_sha256 != requirement.effective_config_sha256:
        return EvidenceBindingResult(
            ok=False,
            message="human evidence is bound to another effective config",
            requirement_id=req_id,
            evidence_id=str(evidence.evidence_id),
        )
    if bound.bound_artifacts != requirement.bound_artifacts:
        return EvidenceBindingResult(
            ok=False,
            message="human evidence is bound to different input artifacts",
            requirement_id=req_id,
            evidence_id=str(evidence.evidence_id),
        )
    # requirement_id on evidence may differ if re-issued; field equality above is authoritative.
    if not _requirements_equal(bound, requirement) and bound.requirement_id != requirement.requirement_id:
        # capability/config/artifacts already matched; id-only difference is acceptable.
        pass
    return EvidenceBindingResult(
        ok=True,
        message="evidence binding matches requirement",
        requirement_id=req_id,
        evidence_id=str(evidence.evidence_id),
    )


def assert_evidence_binding(
    requirement: ApprovalRequirement,
    evidence: ApprovalEvidence | None,
    *,
    require_granted: bool = True,
) -> str:
    """Like ``validate_evidence_binding`` but raises on failure; returns evidence_id."""

    result = validate_evidence_binding(
        requirement, evidence, require_granted=require_granted
    )
    if not result.ok:
        raise ApprovalRequirementError(result.message)
    assert result.evidence_id is not None
    return result.evidence_id
