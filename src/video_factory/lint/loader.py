"""Load channel-owned lint rule files (JSON; YAML extension only if JSON-compatible)."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from video_factory.json_boundary import (
    JsonInputError,
    parse_json_path,
    require_json_object,
)

from .rules import LintRuleSet, rules_from_mapping


class LintRuleLoadError(ValueError):
    """Raised when a rule file cannot be loaded without guessing."""


def load_lint_rules_file(path: Path | str) -> LintRuleSet:
    """Load a lint rule set from a file.

    Core is stdlib-only for parsing: ``.json`` is required for full fidelity.
    Files ending in ``.yaml`` / ``.yml`` are accepted **only** when their
    content is JSON-compatible (JSON is a subset). Full YAML requires the
    channel to convert to JSON before load — same stance as frozen-index.
    """

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in {".json", ".yaml", ".yml"}:
        raise LintRuleLoadError(
            f"unsupported lint rule extension {suffix!r}; use .json (or JSON-compatible .yaml)"
        )

    try:
        data = require_json_object(parse_json_path(source), source=str(source))
    except JsonInputError as error:
        if suffix in {".yaml", ".yml"}:
            raise LintRuleLoadError(
                f"YAML lint rules must be JSON-compatible (stdlib-only core); "
                f"convert to JSON before load: {source}: {error.code.value}: {error.detail}"
            ) from error
        raise LintRuleLoadError(
            f"lint rules JSON rejected [{error.code.value}]: {source}: {error.detail}"
        ) from error

    try:
        return rules_from_mapping(data)
    except ValueError as error:
        raise LintRuleLoadError(str(error)) from error


def load_lint_rules_mapping(payload: Mapping[str, object]) -> LintRuleSet:
    """Load a lint rule set from an in-memory mapping."""

    try:
        return rules_from_mapping(payload)
    except ValueError as error:
        raise LintRuleLoadError(str(error)) from error
