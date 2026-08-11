# Rollback and Commit Range

## Recorded range

- Initial target commit: `b7aaaf2ac43ca99970c317a7d505316a07d320e9`
- W06 sealed recovery ancestor: `df05c406f26303d3216486c12c2a9a7d192d6ef7`
- W07 implementation range:
  `df05c406f26303d3216486c12c2a9a7d192d6ef7..2acd77c0b1b80d6449b38a0e79e859b4cfc9f4e5`
- W07 implementation commits:
  `e494350a24b762620f4ec55ea0e95c6bde828fbe`,
  `d07b34c9cacb713f28e6d69c89899016fd458da3`,
  `fd858ff22a16bbf0eaffa9d813a160ebda9dcce0`, and
  `2acd77c0b1b80d6449b38a0e79e859b4cfc9f4e5`
- Final local checkpoint subject:
  `autopilot(W07): complete final audit and delivery report`

## Safe rollback

Do not rewrite a shared worktree. Create a separate local recovery branch or
worktree at the desired immutable checkpoint, verify its recorded recovery
anchor, then switch consumers only after validation.

To return to the fully sealed W06 state, branch from
`df05c406f26303d3216486c12c2a9a7d192d6ef7` and run the generic plus W01-W06
recovery tools. To preserve W07 evidence while disabling its additive cutover,
leave the code in place and select the legacy `select_edit_inputs` fallback;
production migration consumers are already unregistered.

If commit reversal is required, use a new branch and revert the W07 range in
reverse order. Never blind-retry durable runtime entries in `dispatching`,
`dispatched`, `partial`, `reconciling`, or `uncertain`; follow the journal
recovery runbook and reconcile them instead.

No remote or tag was created by the program. The tracked W07 recovery anchor
is the authoritative local checkpoint binding.

