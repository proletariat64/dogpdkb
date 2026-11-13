# fix: Purge owned wash after execution targets change

Type: task
Status: resolved

## Scope

Review Issue 2 is valid: purge uses execution-only Run.load validation, so changed
corpus/graph targets block deletion of otherwise owned wash. Validate saved schema,
book and direct wash ownership for deletion without comparing execution targets.
Preserve protected paths, symlink checks, locks, live-process checks, force policy
and all-candidate validation. Resume must continue rejecting changed targets.

## Acceptance

Public CLI regressions for changed corpus/graph targets, single and all selection,
unfinished force policy, mixed target histories and invalid ownership. Use focused
local checks only; full make check and make smoke belong to existing PR CI.

## Answer

Confirmed through six failing public CLI cases before the fix. Purge now reads
saved ownership directly instead of using the execution loader. Saved schema,
book key/source URL and run ID must match the selected wash. Changed corpus/graph
targets permit deletion; paused work still requires force. Single/all selection
and mixed target histories pass, with current/previous corpus, graph, durable
inputs and cache bytes preserved. Four invalid-ownership cases verify all runs
remain untouched on preflight failure. Existing protected-path, symlink, lock and
live-process tests pass, and execution resume still rejects changed targets.
Nearest affected suite: 113 passed; focused Ruff and format checks passed.
