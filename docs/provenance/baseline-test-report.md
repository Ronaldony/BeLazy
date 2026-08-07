# W00 source-to-target baseline test report

## Summary

The immutable archive was tested only through a verified temporary extraction.
The source archive and handoff package remained byte-identical. Source behavior
passed all 329 available tests. After the explicit target-only purity scope and
W00 review repairs, target commit `5e335507cf33b1f8d637a8a975f629c4243fb3af`
passed the 329 imported tests plus 14 target-specific regression tests.

## Environments

- Supported runtime: Python 3.12.10
- Default host Python packages: no pytest, jsonschema, setuptools, build, or wheel
- Read-only local test tool environment: pytest 9.1.1 and jsonschema 4.26.0
- Test runner: CPython 3.12.10 AMD64, executable SHA-256
  `0b471133e110cfb53a061cad528ce8e517d7b9ac41a0a396c39ad795a487fc14`
- Test cache: disabled
- Bytecode writes: disabled
- Basetemp: outside source, handoff, and target
- Network/package installation: none

The read-only test tool environment is test infrastructure, not a target
runtime dependency.

## Source results

| Check | Actual result |
|---|---|
| Full pytest | 329 passed in 15.41s |
| Core purity | PASS, 180 scanned files, 0 violations |
| Side-effect free | PASS, 78 scanned files, 0 violations |
| Repository isolation | PASS, 125 scanned files, 0 violations |
| Generated cache/bytecode in source extraction | 0 |

## Target results

The first target run reproduced a bootstrap-specific incompatibility: the
legacy neutrality scanner inspected required engineering controls and immutable
provenance. It reported agent-control names and canonical local paths as product
identity violations. That run was recorded, not hidden:

- Initial target run: 327 passed, 2 failed.
- Failing tests: doctor exit status and repository purity gate.
- Cause: scanner scope, not a legacy product behavior change.

The scanner was narrowed only for explicit non-product control/provenance paths,
and two regression tests prove that product paths remain scanned. Review repair
also added provenance binding/locality tests and a separate no-follow repository
boundary gate with synthetic negative cases.

| Check | Actual result |
|---|---|
| Full target pytest | 343 passed in 16.90s |
| Core purity | PASS, 191 scanned files, 0 violations |
| Side-effect free | PASS, 78 scanned files, 0 violations |
| Repository isolation | PASS, 128 scanned files, 0 violations |
| Target boundary | PASS, 239 files and 55 directories scanned, 0 violations |

The target total is 329 imported tests plus 14 target-specific tests: two purity
scope tests, two provenance/locality tests, and ten target-boundary tests.

## Evidence binding

- Tested target commit: `5e335507cf33b1f8d637a8a975f629c4243fb3af`
- Tested target tree: `d91e7affe3fc025c987d227f8e7f76973f89d509`
- Exact environment-variable mapping, cwd roles, argv, results, source inventory,
  handoff, import-map, and import-plan digests:
  `reports/autopilot/waves/W00/test-receipt.json`
- Exact host-local paths are deliberately retained only in ignored local state;
  tracked evidence uses stable roles and path fingerprints.

## Packaging baseline

The standard wheel build command was not executed in W00 because no offline
build frontend, setuptools, or wheel package exists on the host, and downloads
are forbidden.

The handoff's trusted assessment of this exact source digest records the known
baseline defect: the source wheel had 83 entries, zero JSON Schema resources,
and the isolated installed registry failed. This is reference evidence, not a
newly executed W00 result. W01 owns the packaging repair and fresh offline
verification.

## Parity decision

- No imported characterization behavior regressed.
- The repository identity is now `be-lazy`.
- Legacy distribution, import, CLI, Schema, artifact, and serialized identifiers
  remain unchanged.
- `README.md` and `src/video_factory/security/purity.py` are the two documented
  `PORT_WITH_FIX` source members.
- Every other source file remains an exact verified copy.

## Input postcondition

- Source archive post SHA-256:
  `954325b77028bcf7d36a88136d8e1ed0ec2348ad15014623710614cced94034a`
- Handoff manifest post SHA-256:
  `1bd3aa1687299c15f81539641afcd1563097b0ff8cebc9c24a793e132386244a`
- Both inputs unchanged: yes
