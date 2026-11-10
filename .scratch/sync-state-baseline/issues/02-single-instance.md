# Single updater lock

Type: task
Status: ready-for-agent
Blocked by: none

## Scope

Implement one nonblocking POSIX instance lock per host/user independent of config/book/staging, covering installed CLI and legacy direct workflow entrypoints, including mutating discover and nested subprocesses. Reuse existing locks where appropriate; avoid deadlock on inherited workflow children. Parent death must not release protection while child updater continues. Read-only doctor/queries/standalone mdcompare stay available. Existing active plans prevent a different plan advancing shared state (coordinate state part with 03). Tests via real CLI processes, not internal-only mocks.

Read [spec](../spec.md) and [approved design](../../../docs/sync-state-design.md).

## Acceptance

Use TDD through CLI seams. Commit scoped changes on own worktree/branch based on integration; merge current integration tip before reporting. Record tests and commit in Answer; mark resolved only after evidence.
