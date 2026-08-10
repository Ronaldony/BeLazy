# Fixture publication runbook

## Eligibility is not authority

A W05 `ready` ReleaseAssessment is a non-publishing handoff with
`authority_effect=none`. It cannot call a publisher. W06 publication requires
a new exact `publication-intent/1.0` and separate R3 W04 side-effect request.

## Pre-effect checks

1. Structurally validate and freshly reverify the ReleaseCandidate,
   ReleaseAssessment, destination, quality, selection, and all exact references.
2. Verify workspace/channel/concept/episode, current manifest, workspace bytes,
   publisher/variant, destination, cost/currency, retry, and allowed outputs.
3. Require current service identity, clear kill switch, and a broker-issued
   destination- and purpose-bound credential lease. Persist only its stable
   registration attestation; never persist the handle or credential material.
4. Persist the stable reservation claim before calling the trusted W04 ledger,
   then bind the returned receipt and reservation to that claim.
5. After the publication marker, repeat identity, kill-switch, lease, workspace,
   and same-claim authority checks immediately before the effect call.
6. Reverify workspace bytes immediately after the call and durably settle every
   reservation claim, including claims recovered after a crash.

## Result handling

A settled success requires the expected request identity, external reference,
known cost/currency, and unchanged managed workspace. Failure after the marker,
timeout, invalid result, missing settlement, or missing post-publication
workspace proof is `uncertain`. Persist before/after workspace verifier records;
the after record is absent when it could not be proven.

Never blindly publish again. Use the publisher reconcile port with a fresh
purpose=`reconcile` receipt and the durable external reference. The supplied
publisher is a fake fixture only; real publication remains disabled.
