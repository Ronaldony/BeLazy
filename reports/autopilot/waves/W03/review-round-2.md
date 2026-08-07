# W03 Review Round 2 — Changes Requested

## Reviewed snapshot

- Commit: `110b450fdbedf8e359784c08fd42fb52a4d97fbc`
- Tree: `f5cf9ba7c7ab7598ed03e5e620344a64949c7f00`
- Parent: `f1ba96356243527a9424b993a99b87c4e740c8cb`
- Verdict: `CHANGES_REQUESTED`

## Consolidated findings

- The public coherent Blueprint construction path could accept self-rehashed
  caller provenance without the actual synthesis, charter, assessment, source,
  activation, and task evidence that it claimed to represent.
- Shadow normalization accepted caller-provided fields and labeled them as the
  result of a fixed normalizer, so the same bytes could produce contradictory
  comparison outcomes.
- Reference locks could bind one path to conflicting identities in root and
  per-shot fields.
- Channel, Concept, EpisodeIntent, and projection typed serializers did not
  fully enforce their registered schema invariants before emitting mappings.

The findings were repaired in `acefde0a42f2e4bf30702ee7532126f5ab69cc69`.

