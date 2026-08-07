# W01 — P0 trust-boundary repair

## Task IDs

`P0-PKG-001`, `P0-JSON-001`, `P0-AUTH-001`

## Objective

Repair the production-blocking packaging, parsing and authority-binding defects before introducing the new architecture.

## Required work

### Schema packaging

- Move or expose all registered JSON Schemas as package resources.
- Load them with resource APIs independent of source checkout layout.
- Generate or validate a schema manifest.
- Build a wheel, inspect included resources, install it in isolation and load the full registry.

### Strict JSON boundary

- Provide explicit bytes, path and mapping validation entry points.
- Reject duplicate keys, `NaN`, `Infinity`, malformed UTF-8, invalid date-time and unsupported numeric values.
- Return stable domain errors for missing/unreadable path instead of incidental exceptions.
- Add size/depth defenses where appropriate.

### Approval context binding

- Make current effective-config digest an explicit readiness/evaluator input.
- Bind authorization to workflow, policy, rules, current manifest/evidence, exact artifacts, plan and effective config.
- Ensure stale, mismatched, expired or incomplete context fails closed at every entry point.

## Acceptance

- Isolated wheel installation loads all registered schemas.
- Negative parser regression corpus passes.
- Approval for a different effective config or context is rejected in all readiness paths.
- Legacy compatible success cases remain valid.
