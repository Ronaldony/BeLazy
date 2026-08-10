# Fixture runtime deployment runbook

## Scope

W06 is not a production deployment. The only concrete package is
`video_factory_runtime`, and it refuses any root that is not newly initialized
with its fixture marker. Packaged policy must report `fixture_only=true` and
`production_enabled=false`.

## Preconditions

1. Verify the approved wheel SHA-256 out of band.
2. With no source checkout on `PYTHONPATH`, run
   `python tools/verify_schema_wheel.py --wheel "<approved-wheel.whl>" --python "<trusted-python>" --work-dir "<empty-external-work-dir>"`.
   The work directory must exist, be empty, and be outside the checkout.
3. Confirm 87 packaged schemas, 82 registered versions, and exact
   runtime/migration resource manifests.
4. Run `python tools/check_core_purity.py`,
   `python tools/check_side_effect_free.py`,
   `python tools/check_runtime_boundary.py`, and
   `python tools/check_repo_isolation.py`.
5. Confirm no real credential, provider, channel workspace, or publisher is
   configured.

## Activation

Initialize only a disposable empty fixture directory through
`FixtureRuntimeBoundary.initialize(..., fixture_only=True)`. Never adopt an
existing directory. Preserve the marker, SQLite journal, evidence directories,
and exact wheel digest as one deployment record.

## Rollback

Stop the fixture process, preserve the entire fixture directory read-only, and
revert to the previous verified wheel. Do not delete or edit a journal to make
rollback appear clean. Any may-have-started entry follows the journal recovery
runbook.

## Required evidence

- wheel SHA-256 and verifier output;
- policy and migration manifest digests;
- boundary-check outputs;
- fixture marker/runtime ID;
- start and stop times;
- preserved journal head digests.
