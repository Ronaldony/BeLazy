# W03 Review Round 1 — Changes Requested

## Reviewed snapshot

- Commit: `f1ba96356243527a9424b993a99b87c4e740c8cb`
- Tree: `76336e6a1954b1d20fe6daaa44e5c56062c8188f`
- Parent: `789756c24c9e501bb7f1708694638884b7459338`
- Verdict: `CHANGES_REQUESTED`

## Consolidated findings

- Synthesis trusted caller-mutated activation and task topology instead of
  reconstructing the exact registry, ownership, activation, and task scope.
- Conditional Director coverage and per-field ownership could be reduced as a
  self-consistent set, and cross-episode source/intent context was insufficiently
  rebound at consumers.
- A structurally empty/null detailed Blueprint could be marked coherent.
- Round lineage was caller-asserted rather than bound to an immutable blocked
  predecessor.
- Shadow comparison fields were not derived from exact legacy bytes, several
  public typed serializers could emit schema-invalid mappings, and the wheel
  verifier did not recompute Director resource semantics after coherent tamper.

The findings were repaired in `110b450fdbedf8e359784c08fd42fb52a4d97fbc`.

