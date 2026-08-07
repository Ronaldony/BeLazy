# Public identifier compatibility

The repository identity is `be-lazy`. During the initial parity and shadow
migration phases, the following source identifiers remain public compatibility
contracts:

| Kind | Preserved identifier | Classification |
|---|---|---|
| Python distribution | `video-production-core` | `LEGACY_DISTRIBUTION_CONTRACT` |
| Python import namespace | `video_factory` | `LEGACY_IMPORT_API` |
| CLI entry point | `video-factory` | `LEGACY_CLI_CONTRACT` |
| JSON Schema identifiers | existing `$id` values | `LEGACY_SCHEMA_OR_SERIALIZED_ID` |
| Artifact versions | existing version strings | `LEGACY_SCHEMA_OR_SERIALIZED_ID` |
| Serialized markers | existing protocol strings | `LEGACY_SCHEMA_OR_SERIALIZED_ID` |

These identifiers do not rename the target repository back to the source.
`T90-PUBLIC-IDENTIFIERS` / `MIG-002` is explicitly deferred. Any future change
requires a separate compatibility mandate, consumer inventory, coexistence and
deprecation window, converters, rollback evidence, and no implicit publication.
