# State and recovery

Persist state after material transitions. On interruption:

1. verify source/handoff digests;
2. compare state checkpoint with current HEAD/tree;
3. preserve incomplete evidence under `reports/autopilot/recovery/`;
4. continue if consistent, otherwise rollback to the last PASS checkpoint;
5. restart the current Wave with fresh discovery agents.

Do not ask the human to edit state or Git metadata.
