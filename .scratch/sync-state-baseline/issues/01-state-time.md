# State, existing baseline and UTC+8

Type: task
Status: ready-for-agent
Blocked by: none

## Scope

Implement canonical .sync-state.json version 2, safe v1 migration, existing cleaned-corpus baseline initialization without raw-only preconditions or graph extraction, duplicate/source coverage reporting, and UTC+8 timestamp parsing/serialization/defaults. Own new state/time modules and new CLI initialization surface (coordinate cli.py with ticket 02). Do not wire legacy/apply/knowledge stage persistence yet: that is ticket 03. Keep dependencies minimal and expose a clear state API to ticket 03. Unknown exact baseline time remains unknown; observed current time cannot impersonate applied baseline.

Read [spec](../spec.md) and [approved design](../../../docs/sync-state-design.md).

## Acceptance

Use TDD through CLI seams. Commit scoped changes on own worktree/branch based on integration; merge current integration tip before reporting. Record tests and commit in Answer; mark resolved only after evidence.
