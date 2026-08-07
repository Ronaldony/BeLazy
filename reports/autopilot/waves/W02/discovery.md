# W02 Discovery — Managed Mutation Plane

## Subject

- Wave: `W02-MANAGED-MUTATION`
- Tasks: `MUT-001`, `MUT-002`, `MUT-003`, `MUT-004`
- Approved W01 ancestor: `b93c44ff58a892f608c534f1ce68d876cdfd6fed`
- Final implementation: `0fa62c81bcd2fa9537aa93db47403a73b388073a`
- Final implementation tree: `cc7e398710addb03a5febdd86d435a0d9a037aad`

## Contract discovery

- The approved baseline had no managed-mutation package or the six required
  artifact versions. Existing storage and executor protocols could not be
  extended without breaking structural implementations.
- W02 therefore adds an independent `video_factory.mutation` package containing
  immutable contracts, strict serialization, a deterministic pure planner,
  workspace revision and drift logic, and narrow runtime protocols. It does not
  add a concrete filesystem executor or durable journal; those remain W06 work.
- The six required artifact versions are byte-identical root/package resources
  behind the existing digest-checked schema manifest. The manifest now records
  52 schemas and 48 registered versions.
- Managed paths require canonical NFC POSIX-relative spelling and reject
  absolute, drive, UNC, traversal, backslash, ADS, reserved-device, trailing
  dot/space, case-fold, Unicode, symlink, reparse, and non-directory-ancestor
  ambiguity.

## Authority and state discovery

- Requester-declared risk is not authoritative. Until W04 supplies a trusted
  versioned classifier, every W02 mutation has an R4 floor and therefore
  requires the complete narrow break-glass contract.
- Every mutation also requires a trusted authority decision, current complete
  workspace observation, compare-and-swap revision/manifest state, atomic
  idempotency reservation, exact content resolution, service identity, and
  explicit evaluation time.
- Each of two distinct authenticated human approvals binds the exact canonical
  break-glass request. Snapshot, incident, and audit records are verified as
  current references; the core cannot issue or synthesize approvals.
- Each unique create/replace content object retains its exact
  object-to-resolver-observation pair in the execution authorization. Plan-aware
  consumers require exact set coverage before a receipt can promote a trusted
  workspace revision.
- Drift compares workspace identity, canonical revision, complete active file
  set, digests, lengths, path/link state, and manifest. A mismatch yields an
  untrusted report that blocks dependent generation and publication.

## Selected scope

- W02 implements pure contracts, planners, validators, drift/revision logic,
  pre-side-effect decision logic, and test-only runtime fakes.
- W04 remains responsible for durable authority/classifier/ledger semantics.
- W06 remains responsible for concrete filesystem execution, journal durability,
  crash recovery, and real compare-and-swap application.
- No network, provider, publication, deployment, credential, or human-identity
  side effect was performed.
