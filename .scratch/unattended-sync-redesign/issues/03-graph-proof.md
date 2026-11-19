# graph-proof

Status: ready-for-integration

Progress: implemented in `work/sync-redesign-03`; not integrated.

Blocked by: none

Implement native extraction completeness and preflight against actual patched CLI, candidate integrity, durable failure suppression and baseline guards; OAuth parallel options. Never weaken coverage or modify external tool.

Acceptance: applicable public CLI scenarios in [spec](../spec.md).

- Focused release-venv verification: 204 graph, scope, state, wash, apply and
  semantic CLI cases passed. After the final guards, six focused graph cases
  and two sync/wash cases passed again. The installed patched CLI was exercised
  only with loopback SSE and synthetic OAuth, including five native chunks and
  an observed peak of three in flight.
- The supported contract is pinned to the observed Graphify 0.9.111 diagnostics
  and manifest. Startup probing cannot establish the provenance of every build
  reporting that version or validate real OAuth. No inference, production graph,
  refresh behavior or Graphify rate-limit policy was tested. Unknown proof,
  nonportable/unstamped historical input or unavailable atomic exchange keeps
  the last graph rather than treating a corpus publication as graph success.
