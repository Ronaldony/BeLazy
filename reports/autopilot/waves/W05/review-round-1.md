# W05 Review Round 1 - Initial Candidate

## Reviewed snapshot

- Commit: `1b9b413b16719b7c865e3804c8d9fc27c512dbaa`
- Tree: `f2a972a7b125a146bdee73a95a3841298db25266`
- Parent: `d10d5b83c297acac8030411e20980cedf06fcd71`
- Verdict: `CHANGES_REQUESTED`

## Consolidated findings

The initial candidate established the additive quality, selection, and release
contracts. Review found that only the first of nine evaluator receipts was
verified for a shared subject, caller confidence was not bound to the selection
authority request, release assessment consumed only structural self-hashes,
and past authority verification could be reused at a later release time.
Public serializers also admitted a small set of schema-invalid dataclass
shapes. No Critical finding was recorded, but unresolved High findings required
a new candidate.

