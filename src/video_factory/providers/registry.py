"""Opaque capability bindings supplied by a channel profile."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from video_factory.domain import CapabilityId, OpaqueId

from .adapters import ExecutorAdapter, ProviderAdapter, validate_descriptor
from .contracts import AdapterKind, CapabilityDescriptor, DescribedAdapter


class RegistryError(LookupError):
    """Raised when an opaque binding cannot satisfy capability and kind."""


@dataclass(frozen=True, slots=True)
class AdapterBinding:
    """Profile-owned binding; the core does not interpret the adapter ID."""

    capability_id: CapabilityId
    adapter_id: OpaqueId
    adapter_kind: AdapterKind


def _require_same(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise RegistryError(f"{label} does not match its binding")


class InMemoryCapabilityRegistry:
    """Generic registry mechanism; persistence and concrete bindings stay outside core."""

    def __init__(
        self,
        bindings: Iterable[AdapterBinding],
        adapters: Iterable[DescribedAdapter],
    ) -> None:
        binding_items = tuple(bindings)
        adapter_items = tuple(adapters)
        self._bindings = {binding.capability_id: binding for binding in binding_items}
        self._adapters = {adapter.descriptor.adapter_id: adapter for adapter in adapter_items}
        if len(self._bindings) != len(binding_items):
            raise RegistryError("a capability has more than one binding")
        if len(self._adapters) != len(adapter_items):
            raise RegistryError("an opaque adapter ID is registered more than once")
        for adapter in adapter_items:
            validate_descriptor(adapter.descriptor)

    def resolve(
        self,
        capability_id: CapabilityId,
        adapter_kind: AdapterKind,
    ) -> DescribedAdapter:
        try:
            binding = self._bindings[capability_id]
        except KeyError as error:
            raise RegistryError("capability has no profile binding") from error
        _require_same("requested adapter kind", binding.adapter_kind, adapter_kind)

        try:
            adapter = self._adapters[binding.adapter_id]
        except KeyError as error:
            raise RegistryError("bound opaque adapter is not registered") from error
        descriptor = adapter.descriptor
        _require_same("descriptor adapter ID", descriptor.adapter_id, binding.adapter_id)
        _require_same("descriptor adapter kind", descriptor.adapter_kind, binding.adapter_kind)
        if capability_id not in descriptor.capabilities:
            raise RegistryError("bound adapter does not declare the capability")
        if adapter_kind is AdapterKind.PROVIDER and not isinstance(adapter, ProviderAdapter):
            raise RegistryError("provider binding does not implement the provider port")
        if adapter_kind is AdapterKind.EXECUTOR and not isinstance(adapter, ExecutorAdapter):
            raise RegistryError("executor binding does not implement the executor port")
        return adapter

    def describe(
        self,
        capability_id: CapabilityId,
        adapter_kind: AdapterKind,
    ) -> CapabilityDescriptor:
        return self.resolve(capability_id, adapter_kind).descriptor
