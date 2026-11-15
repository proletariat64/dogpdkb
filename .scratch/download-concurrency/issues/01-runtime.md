# 01: Bounded export runtime and CLI option

Status: resolved
Type: task

Implement the approved runtime/CLI contract in spec.md, including public CLI regressions for bounds, checkpoint/resume, failures, stage ordering and interruption. Own Python runtime and tests. Keep state coordination on the main thread and preserve subprocess locks and signal semantics.

## Acceptance

Focused public-boundary tests pass; no real service calls or local full regression. Merge the integration tip before reporting the implementation commit.


## Answer

Implemented --concurrency on sync/apply with default two and integer choices one through three. A main-thread selector scheduler starts independent public Skylark processes, inherits existing locks, streams diagnostics, checkpoints each settled export and completes the download batch before comparison. SIGINT/SIGTERM terminate every active process group with escalation and preserve unknown outcomes. Coordinator I/O errors also kill peers and record unfinished attempts as unknown; missing individual export reports remain isolated unknown results. No foreign tool imports, new retry layer or connection reuse.

Validation: 21 public CLI concurrency tests pass in 11.06 seconds. Coverage includes exact bounds/default, invalid arguments without state/tool side effects, sync propagation and comparison gating, mixed failures/version drift, missing reports, settled checkpoint reuse with changed resume concurrency, both interruption signals with descendants, coordinator checkpoint failure and inherited write-lock protection after parent death. Earlier nearest apply/state/help run passed 101 of 102; the one serial-default assumption was updated to explicit concurrency one and passed in a subsequent 24-case focused run. Changed Python Ruff check and format pass. No full local regression, local OCR review, real gateway or model calls.
