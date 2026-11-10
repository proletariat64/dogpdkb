# State, existing baseline and UTC+8

Type: task
Status: resolved
Blocked by: none

## Scope

Implement canonical .sync-state.json version 2, safe v1 migration, existing cleaned-corpus baseline initialization without raw-only preconditions or graph extraction, duplicate/source coverage reporting, and UTC+8 timestamp parsing/serialization/defaults. Own new state/time modules and new CLI initialization surface (coordinate cli.py with ticket 02). Do not wire legacy/apply/knowledge stage persistence yet: that is ticket 03. Keep dependencies minimal and expose a clear state API to ticket 03. Unknown exact baseline time remains unknown; observed current time cannot impersonate applied baseline.

Read [spec](../spec.md) and [approved design](../../../docs/sync-state-design.md).

## Acceptance

Use TDD through CLI seams. Commit scoped changes on own worktree/branch based on integration; merge current integration tip before reporting. Record tests and commit in Answer; mark resolved only after evidence.

## Answer

Implemented `dogpdkb init-state --config ... --book ...` with optional `--input` and `--graph-dir` overrides. Offline initialization scans only top-level corpus Markdown, matches existing graph sources, preserves the graph, reports duplicates and coverage, and records historical-enriched baseline limitations. Numeric book IDs remain unknown until later observation; stable config key and URL bind offline identity.

Canonical v2 state is stored in corpus `.sync-state.json`, with atomic private writes and safe v1 precision migration only for matching filenames. Unknown exact times remain null. State API exposes load/save, document access and per-step persistence for ticket 03. Fixed UTC+8 helpers retain original upstream value and timezone-default evidence; existing `utc` schema key serializes an equivalent `+08:00` timestamp for compatibility.

Validation: six CLI subprocess cases cover offline existing baseline, duplicate isolation/recovery, v1 exact timestamp migration including UTC date crossing and naive UTC+8 default, active-plan refusal and malformed legacy preservation. Focused suite plus existing platform suite: 36 passed. Ruff check/format and git diff --check passed. No production corpus, graph or remote service was modified. Workflow-wide persistence and filename selection integration remain ticket 03.
