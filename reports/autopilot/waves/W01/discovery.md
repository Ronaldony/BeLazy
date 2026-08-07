# W01 Discovery — Trust Boundary

## Subject

- Wave: `W01-TRUST-BOUNDARY`
- Tasks: `P0-PKG-001`, `P0-JSON-001`, `P0-AUTH-001`
- Baseline parent: `66d104339cbd46e26faf5ff571cc73e6427b38f8`
- Final implementation: `2ba74f80b62d5a550bb195d0ea4c0d69f0e72c21`
- Final implementation tree: `f023d90f7d8791f59741ff7193c4039ca0dc64cd`

## Packaging discovery

- The source contained 46 Schema files but only 42 artifact-version registrations.
- Four shared contract Schemas intentionally have no artifact version:
  `artifact-common`, `config-layer`, `encode-request`, and
  `encode-command-plan`.
- The original registry resolved a checkout-relative `schemas/` directory and
  package data was absent from `pyproject.toml`.
- The offline host has pytest/jsonschema but no build frontend, setuptools, or
  wheel package. No dependency was installed and no network was used.

## JSON discovery

- String input was ambiguously treated as either a path or JSON text.
- Missing paths could leak incidental exceptions.
- The prior decoder accepted duplicate keys and non-finite values and did not
  impose size, depth, node, token, or fixed-point expansion limits.
- A bare jsonschema format checker had no active RFC 3339 checker in this
  offline environment.

## Authority discovery

- Approval evidence was not bound to all material workflow inputs or an
  explicit validity window.
- Planning, rendered generation instructions, adapter dispatch, and external
  reconciliation required distinct non-authorizing versus authorizing
  contracts.
- Exact authorization now covers seven material context digests, the complete
  request envelope, explicit evaluation time, approval expiry, artifacts, and
  effective configuration. Authentic signatures, revocation, and a durable
  ledger remain explicitly assigned to W04/W06.

## Selected implementation

- Schemas are package resources with a digest-checked 46-entry manifest.
- A strict JSON boundary provides explicit bytes/path/mapping APIs and stable
  error codes.
- Legacy documents remain loadable; context-free planning remains
  non-authorizing; no context-free evidence, sheet, dispatch, or reconciliation
  path grants execution authority.
- A deterministic stdlib-only wheel tool is used solely as an offline PEP 427
  acceptance fallback. It does not claim setuptools-backend verification.
- The fallback binds the wheel filename, dist-info directory, unique
  METADATA/WHEEL identity headers, tags, and every RECORD digest/size row.
