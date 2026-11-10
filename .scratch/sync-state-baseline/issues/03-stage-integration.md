# Per-step progress and mdcompare integration

Type: task
Status: ready-for-agent
Blocked by: 01-state-time, 02-single-instance

## Scope

Wire canonical state into discovery pages, per-document downloads, mdcompare plan rows, corpus publication, Graphify candidate/publication, Web completion and recovery. Use existing cleaned baseline as historical_enriched without requiring all prior raw; preserve raw/source limitations and approval gates. Separate observed/downloaded/compared/applied versions, preserve pending after remote timestamps stop changing, reuse valid downloaded raw, reject conflicting active plans, compare exact timestamps in UTC+8, correct filename/cutoff UTC+8 rules. Unify knowledge state with .sync-state.json through migration/compatibility, not independent competing writers. Do not auto-extract unknown or bypass approval.

Read [spec](../spec.md) and [approved design](../../../docs/sync-state-design.md).

## Acceptance

Use TDD through CLI seams. Commit scoped changes on own worktree/branch based on integration; merge current integration tip before reporting. Record tests and commit in Answer; mark resolved only after evidence.
