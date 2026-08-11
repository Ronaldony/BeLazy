# T90 Public Identifier Decision

## Decision

`T90-PUBLIC-IDENTIFIERS` / `MIG-002` is **not performed** by this program.

The W00-W07 implementation preserves the distribution name, package imports,
version 0.3.1, 17 CLI command identifiers, legacy workflow action identifiers,
artifact versions, and public mode/error re-export identity. The additive W07
selection coordinator and migration registry introduce no replacement public
identifier and carry no execution authority.

## Rationale

`AUTOPILOT_PROGRAM.yaml` explicitly defers T90 because irreversible public
compatibility migration requires a separate future mandate. Such a mandate must
define versioned coexistence, deprecation, converter/lockfile behavior,
telemetry, consumer inventory, rollback, and a production activation owner.
W07 only proves a reversible, production-disabled migration boundary.

## Future entry conditions

A future T90 program must begin from the W07 recovery checkpoint, enumerate
every public consumer, preserve a tested compatibility window, provide an exact
reverse mapping and rollback trigger, and obtain separate human authorization.
Until then the existing public identifiers remain authoritative.

