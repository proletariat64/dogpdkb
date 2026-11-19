# Publish an offline score-based routing stage

Status: resolved

Implement the public score-routing CLI and contract in the spec. Verify the policy through CLI tests.

## Answer

Implemented the independent offline `semantic-route` CLI; original deterministic report and orchestrator routes remain unchanged. Focused CLI checks cover default 3/4 boundary, custom threshold, ambiguity on scores either side, absent scores, sidecar integrity, and protected input paths. Installed and release verification is tracked by ticket 02.
