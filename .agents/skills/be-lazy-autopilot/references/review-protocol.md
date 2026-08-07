# Review protocol

Each reviewer is fresh and read-only. Return concrete findings with severity, file/symbol, evidence, impact, and required fix.

- Critical: authority bypass, source mutation, destructive data risk, approval fabrication, arbitrary workspace escape, package unusable.
- High: incorrect public behavior, missing material digest, broken migration/parity, severe test gap, core side effect.
- Medium: maintainability or incomplete edge behavior that can become a defect.
- Low: non-blocking polish.

Wave PASS requires zero substantiated Critical or High findings. Reviewer consensus is quality evidence, never human approval.
