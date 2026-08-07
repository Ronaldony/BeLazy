# be-lazy ExecPlan rules

The living program plan is `.agent/execplans/be-lazy-autopilot.md`.

- Keep the source archive and handoff package byte-identical and read-only.
- The main Supervisor is the only target writer.
- Record each material decision, exact command, actual result, reviewer finding,
  repair, and rollback point.
- Advance W00 through W07 without a human checkpoint.
- Create a local checkpoint only after the current Wave has zero unresolved
  Critical or High findings.
- Never treat a commit, prompt, AI review, or test result as application human
  approval.
- Never contact a remote, use the network, publish, deploy, call a provider, or
  migrate public identifiers in this program.

On recovery, verify both input digests, compare state with `HEAD`, preserve
incomplete evidence, and resume the first incomplete Wave.
