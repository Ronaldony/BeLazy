"""Idempotent workflow transition and execution mode shapes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Protocol

from video_factory.domain import HashDigest, IdempotencyKey, OpaqueId


class WorkflowMode(StrEnum):
    RAPID = "rapid"
    STANDARD = "standard"
    CONTROLLED = "controlled"


class ExecutionMode(StrEnum):
    PREVIEW_ONLY = "preview_only"
    HUMAN_ONLY = "human_only"
    AUTOMATED = "automated"


@dataclass(frozen=True, slots=True)
class WorkflowState:
    state_id: OpaqueId
    state_version: str
    data: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class TransitionRequest:
    transition_id: OpaqueId
    current_state: WorkflowState
    input_sha256: HashDigest
    effective_config_sha256: HashDigest
    idempotency_key: IdempotencyKey


@dataclass(frozen=True, slots=True)
class TransitionResult:
    previous_state: WorkflowState
    next_state: WorkflowState
    event_sha256: HashDigest
    replayed: bool


class WorkflowEngine(Protocol):
    def transition(self, request: TransitionRequest) -> TransitionResult: ...

