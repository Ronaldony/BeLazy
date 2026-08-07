"""Encode-stage contracts and deterministic command planning (no execution)."""

from .builder import EncodeBuildError, build_encode_command
from .contracts import (
    EncodeCommandPlan,
    EncodeInputClip,
    EncodeInputRole,
    EncodeOutputSpec,
    EncodePlanStatus,
    EncodeProfile,
    EncodeProfileKind,
    EncodeRequest,
    OverwritePolicy,
    PostEncodeCheck,
    default_encode_profiles,
)

__all__ = [
    "EncodeBuildError",
    "EncodeCommandPlan",
    "EncodeInputClip",
    "EncodeInputRole",
    "EncodeOutputSpec",
    "EncodePlanStatus",
    "EncodeProfile",
    "EncodeProfileKind",
    "EncodeRequest",
    "OverwritePolicy",
    "PostEncodeCheck",
    "build_encode_command",
    "default_encode_profiles",
]
