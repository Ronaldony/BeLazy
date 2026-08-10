# W06 Discovery - Durable Runtime and Reversible Migration

## Subject

- Wave: `W06-RUNTIME-MIGRATION`
- Tasks: `RUN-001`, `RUN-002`, `MIG-001`, `REL-002`
- Approved W05 recovery ancestor: `9cca6ff6bb0955c9fa7dafd0a1daf46a4a42ce10`
- Final implementation candidate: `376cca4e2113e9d730dfbf130fc6ce5216717400`
- Final implementation tree: `c6644afcc90985b37de422c4cf71a823f6af6486`

## Runtime discovery

- Pure contracts remain in `video_factory.runtime`; all concrete filesystem,
  SQLite, credential, executor, publication, and migration adapters live in
  the additive `video_factory_runtime` package. The packaged runtime policy is
  fixture-only and explicitly disables production effects.
- The SQLite execution journal atomically claims an exact action and
  idempotency scope, preserves an append-only hash chain and immutable receipt
  history, rejects same-key/different-intent reuse, and uses stable reservation
  identity independently from fresh timestamp-bound W04 verification receipts.
- A durable pre-dispatch marker distinguishes work proven not to have started
  from work that may have started. Dispatching, dispatched, partial,
  reconciling, and uncertain states never blind-redispatch; terminal replay is
  read-only.
- Credential material is represented only by a current broker-issued,
  destination- and purpose-bound lease. Raw secret values are never accepted
  as handles and are not serializable into artifacts, journal rows, receipts,
  or logs.

## Effect and recovery discovery

- Managed fixture mutation holds a root-to-parent no-follow capability chain
  through actual use. POSIX effects are `dir_fd` relative; Windows effects use
  exact directory/file handles and reject directory-path rebound. Every
  operation revalidates exact source bytes, containment, workspace manifest,
  W04 authority, identity, and kill switch before the effect.
- Synthetic external execution validates exact request, output references and
  bytes, cost/currency, workspace changes, and uncertainty semantics. Restart
  reconciliation consumes a durable external reference and cannot redispatch.
- Publication remains separate from W05 readiness. The fake publisher requires
  a fresh release verification, exact destination and workspace, identity,
  credential lease, kill switch, and a distinct W04 side-effect reservation.
  Production publication remains disabled.
- Reservation and settlement use stable journal claim identity and append-only
  fresh verification history. Interrupted claims remain discoverable and
  idempotent across restart.

## Migration discovery

- Migration is read-only and reversible across legacy-only, dual-read,
  projection-read-only, and rolled-back states. Every generation binds the
  exact original legacy artifact.
- Projection activation consumes a fixture-pinned exact-byte parity receipt
  that was registered by the trusted verifier. Projection artifacts remain
  non-current and non-authorizing.
- Projection rollback is bound to the immediately preceding activation
  receipts; dual-read rollback needs no projection receipt. Historical-cycle
  receipts and foreign legacy references are rejected.
- Deployment, migration, rollback, incident, drift, break-glass, journal
  recovery, and release runbooks preserve exact commands and no-redispatch
  conditions. Public identifier migration remains deferred to T90.

## Packaging and compatibility

- Seven additive artifact versions extend the manifest to 87 schemas and 82
  registered versions. Root and packaged copies are byte-identical.
- Two independent 255-member offline wheels are byte-identical and load both
  `video_factory` and `video_factory_runtime` in source-free isolation.
- Distribution version `0.3.1`, all 17 CLI commands, prior public identifiers,
  and all W00-W05 recovery anchors remain unchanged.
- Real providers, production credentials, production publication/deployment,
  authoritative migration cutover, and public identifier migration are not
  claimed.

