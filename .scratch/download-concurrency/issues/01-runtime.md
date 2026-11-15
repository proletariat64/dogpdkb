# 01: Bounded export runtime and CLI option

Status: ready-for-agent
Type: task

Implement the approved runtime/CLI contract in spec.md, including public CLI regressions for bounds, checkpoint/resume, failures, stage ordering and interruption. Own Python runtime and tests. Keep state coordination on the main thread and preserve subprocess locks and signal semantics.

## Acceptance

Focused public-boundary tests pass; no real service calls or local full regression. Merge the integration tip before reporting the implementation commit.
