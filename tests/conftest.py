"""Shared pytest configuration for the core suite.

Default policy (PHASE 9): every registered marker tier runs unless the caller
passes ``-m``. Directory layout applies a default tier so unmarked tests still
carry an explicit layer:

- ``tests/unit/**`` and ``tests/compatibility/**`` → ``unit``
- ``tests/integration/**`` → ``integration``

Tests may also declare ``live_health`` or ``windows`` explicitly. Those markers
are additive and do not remove the directory tier.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    for item in items:
        path = Path(str(item.fspath)).resolve()
        parts = set(path.parts)
        if "integration" in parts:
            item.add_marker(pytest.mark.integration)
        elif "unit" in parts or "compatibility" in parts:
            item.add_marker(pytest.mark.unit)
