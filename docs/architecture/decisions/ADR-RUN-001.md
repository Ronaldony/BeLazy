# ADR-RUN-001: Durable fixture runtime and effect reconciliation

- Status: Accepted for W06 fixture execution; production activation disabled
- Date: 2026-08-10
- Wave: W06 (`RUN-001`, `RUN-002`, `REL-002`)

## Context

The pure core can describe an exact action and can obtain current W04
authority, but an actual effect needs a durable idempotency claim, a crash-safe
state machine, current workspace and service evidence, reservation settlement,
and a reconciliation path. Process-local retry dictionaries cannot prove that
an effect did not start, and a W05 `ready` ReleaseAssessment is not publication
authority.

## Decision

Keep `video_factory` pure and add concrete adapters in the separate
`video_factory_runtime` package. The shipped implementation is deliberately
fixture-only. `FixtureRuntimeBoundary` requires a newly initialized,
marker-bound isolated root and rejects production activation, adoption of a
non-empty tree, links, reparse points, and paths outside that root.

`SQLiteExecutionJournal` atomically claims `(action kind, action id,
idempotency key)` with the exact request and intent digests. It uses
`BEGIN IMMEDIATE`, `synchronous=FULL`, immutable receipts, and an append-only
SHA-256 event chain. Mutation journals additionally bind the ordered operation
IDs inside the canonical `ExecutionIntent`; the SQLite effect-plan column is a
redundant index that is compared with that hash-bound sequence both when the
journal is loaded and inside the atomic dispatch claim. Only the exact next
operation can acquire a dispatch marker. The state machine is:

`planned -> authorized -> reserved -> dispatching -> dispatched -> terminal`.

`succeeded`, `failed`, and `reconciled` are terminal. A durable `planned`,
`authorized`, or `reserved` record proves that no dispatch marker exists and
may resume only after every current check is repeated. `dispatching`,
`dispatched`, `partial`, `reconciling`, and `uncertain` mean the effect may have
started; they are reconcile-only and can never be blindly redispatched.
Reconciliation has a distinct W04 purpose and reservation receipt.
Terminal success, failure, and reconciliation are written only by the atomic
finish operation that appends the event and its exact receipt in one SQLite
transaction; the generic state API cannot terminalize unresolved work.

Before a trusted ledger is called, the journal durably records a stable
reservation claim `(journal, purpose, effect, request)`. Ledger retries under
that claim are idempotent, while every invocation returns a fresh
timestamp-bound verification receipt. The stable reservation identity excludes
receipt time and ledger-head fields; the journal preserves the first
reservation plus an append-only history of every fresh receipt. Settlement
binds every claim and stable reservation to one final state. Before each new effect the runtime verifies exact
request/plan/context, workspace bytes and manifest, service identity, a
broker-issued destination- and purpose-bound credential lease, kill switch,
W04 decision, fresh purpose-bound reservation, idempotency, and destination
scope. These checks are repeated after the durable dispatch marker and
immediately before the effect port. The fresh receipt and reservation are
durably recorded and settled or held with the final receipt. A terminal
exact-idempotency replay is
read-only: it validates the durable input binding and returns the existing
receipt without minting new authority or invoking the effect port.

Managed mutation moves existing bytes into a fixture-owned quarantine name,
verifies the staged no-follow regular file, and only then deletes, replaces, or
links it. On POSIX the root and complete managed ancestor chain are opened
component-by-component with `O_NOFOLLOW`; every rename, link, unlink, create,
stat, read, and directory sync uses those exact `dir_fd` capabilities rather
than re-resolving a path. On Windows the complete managed ancestor chain remains open, file and
directory identities stay live through the effect, the directory handle's
current path is rechecked immediately before each handle-relative rename or
link, and deletion targets the opened file rather than a re-resolved path. A
path swap can therefore force uncertainty but cannot redirect approved bytes
as the requested effect. External executor ports receive an immutable
in-memory bundle of the exact descriptor-verified input bytes. Result,
mutation-receipt, and publication-receipt documents are parsed from one stable
descriptor read and checked against exact outputs, bytes, workspace change,
uncertainty, cost, and request identity.

Publication is a separate fixture runtime action. It first freshly reverifies
the W05 candidate and assessment, then requires a distinct R3 W04
`ready_for_human_publish`/`publish` decision, current workspace evidence,
identity, opaque credential handle, journal, and settlement. Before and after
workspace verification records are persisted. The W05 assessment remains
non-authorizing and `authority_effect=none` remains invariant.

## Consequences

- Restart, duplicate request, crash-window, partial mutation, uncertain
  external state, and publication reconciliation can be tested locally.
- A changed request under the same idempotency key is a hard conflict.
- No credential value is accepted or serialized. A stable broker registration
  attestation is journaled; a non-serializable current lease crosses only the
  effect boundary.
- The wheel contains runtime contracts and fixture adapters, but the packaged
  policy fixes `fixture_only=true` and `production_enabled=false`.
- Real provider, production filesystem, credential broker, signature service,
  and production publisher implementations remain outside this program.

## Rejected alternatives

- Process-local retry tracking: cannot survive restart or competing processes.
- Retry every nonterminal record: can duplicate an effect after a crash.
- Treat a deterministic ID as authority: integrity is not current permission.
- Publish directly from W05 `ready`: handoff eligibility is not side-effect
  authority.
