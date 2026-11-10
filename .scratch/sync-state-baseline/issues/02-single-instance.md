# Single updater lock

Type: task
Status: resolved
Blocked by: none

## Scope

Implement one nonblocking POSIX instance lock per host/user independent of config/book/staging, covering installed CLI and legacy direct workflow entrypoints, including mutating discover and nested subprocesses. Reuse existing locks where appropriate; avoid deadlock on inherited workflow children. Parent death must not release protection while child updater continues. Read-only doctor/queries/standalone mdcompare stay available. Existing active plans prevent a different plan advancing shared state (coordinate state part with 03). Tests via real CLI processes, not internal-only mocks.

Read [spec](../spec.md) and [approved design](../../../docs/sync-state-design.md).

## Acceptance

Use TDD through CLI seams. Commit scoped changes on own worktree/branch based on integration; merge current integration tip before reporting. Record tests and commit in Answer; mark resolved only after evidence.

## Answer

Implemented user-wide POSIX updater exclusivity in `src/dogpdkb/instance_lock.py`.

- `locked_main` protects mutating entry points; help/version remain available. `updater_lock` uses a fixed user-state `dogpdkb/sync.lock` (XDG state directory or `~/.local/state`), with a new private directory (0700) and lock file (0600).
- CLI workflow forwarding plus all five direct workflow `main` functions are protected, including source `scripts/*.py` wrappers. `init-state`, update and uninstall are protected at the CLI dispatch boundary; doctor and standalone mdcompare remain available.
- `DOGPDKB_UPDATE_LOCK_FD` is only a descriptor hint. Inherited descriptors must name the same device/inode as the user lock file, be a regular file owned by the current user, and successfully acquire/reuse the real nonblocking flock. Invalid hints never bypass acquisition.
- `subprocess_lock_options()` supplies explicit `pass_fds` to CLI forwarding, sync stage runners, mdocr, Skylark proxy and knowledge Graphify execution. No explicit `LOCK_UN` is issued: cooperating descendants retain the shared open-file lock after parent termination until their own work ends. Existing publication locks remain independent.
- The existing test environment fixture now assigns a per-test XDG state directory, avoiding user-directory writes and interference with real processes. The runtime has no test bypass.

TDD red: CLI contention was not rejected before implementation; direct wrapper returned success on busy lock before fixing error exit propagation. Green: `tests/test_instance_lock_cli.py` plus platform, pipeline and sync tests: 83 passed. New process tests cover all five direct workflows, a forged descriptor environment, another config/book, readonly doctor/help/mdcompare, private permissions, and parent-killed/child-still-running protection followed by release. Ruff checks and `git diff --check` passed. Full repository checks run after integration, not independently repeated here.

Active-plan state conflict validation belongs to ticket 03; the instance lock ensures separate processes cannot simultaneously advance those plans.
