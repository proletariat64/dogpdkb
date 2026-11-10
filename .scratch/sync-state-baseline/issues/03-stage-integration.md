# Per-step progress and mdcompare integration

Type: task
Status: resolved
Blocked by: 01-state-time, 02-single-instance

## Scope

Wire canonical state into discovery pages, per-document downloads, mdcompare plan rows, corpus publication, Graphify candidate/publication, Web completion and recovery. Use existing cleaned baseline as historical_enriched without requiring all prior raw; preserve raw/source limitations and approval gates. Separate observed/downloaded/compared/applied versions, preserve pending after remote timestamps stop changing, reuse valid downloaded raw, reject conflicting active plans, compare exact timestamps in UTC+8, correct filename/cutoff UTC+8 rules. Unify knowledge state with .sync-state.json through migration/compatibility, not independent competing writers. Do not auto-extract unknown or bypass approval.

Read [spec](../spec.md) and [approved design](../../../docs/sync-state-design.md).

## Acceptance

Use TDD through CLI seams. Commit scoped changes on own worktree/branch based on integration; merge current integration tip before reporting. Record tests and commit in Answer; mark resolved only after evidence.

## Answer

Implemented canonical state integration across the snapshot/knowledge flow and merged the legacy per-step subtask (`f52ff7b`). New knowledge commands accept configured corpus/book defaults; existing explicit standalone legacy state commands remain compatible, while explicit migration publishes a canonical state and redirects the retired legacy path. Imported relative source references are rebased; plans made before migration require regeneration because their source bindings changed.

Existing initialized cleaned Markdown enters comparison as historical_enriched. Incoming title/path metadata is transformed using the existing filename encoding for comparison with historical filename metadata; the historical value is not replaced by a guessed new title. Unmatched graph sources remain uncovered/HOLD. Downloads, page observations, comparison outputs, Graphify candidates/publication and Web completion record distinct outcomes; no download or candidate advances the production graph version. Complete comparison reports live in existing staging, not repeatedly embedded in canonical state. Frozen input bytes and source versions bind reuse. Single-snapshot knowledge run identity permits capture/plan/execute continuation; another snapshot or planning directory cannot advance the active run. Legacy root-staging inheritance remains supported.

UTC+8 applies to observations, default naive times, filenames and cutoff handling. Date-only remote metadata cannot become an exact midnight version. Canonical books resolve by existing key/URL and do not acquire shadow adhoc entries. Knowledge child Graphify processes cannot create a second state in their isolated execution corpus.

Validation: a combined 183-test run across sync-progress CLI, knowledge capture/planning/execution, discovery, apply recovery, pipeline, graph update and Web publication passed. Subsequent focused knowledge/CLI runs passed 39 tests after adding unmatched-source coverage and date-only checks. Ruff check, all 186 file format checks and git diff --check passed. Red/green evidence includes initialized same-day capture previously selecting zero, and COMPARISON_PENDING preventing comparison completion from clearing the transient pending reason. New CLI scenarios cover partial export failure and raw reuse on resume, exact timezone handling, old state migration plus approved candidate publication, and conflicting plans leaving state unchanged.

No production corpus, graph, server deployment or model calls were used. Graphify behavior in execution tests uses the existing external substitute. Full repository and installed smoke acceptance belong to ticket 04.
