# Acceptance, documentation and installed proof

Type: task
Status: ready-for-agent
Blocked by: 03-stage-integration

## Scope

Update user docs and installer smoke for actual implemented CLI behavior; focused recovery/concurrency/same-day timezone cases, full make check/make smoke, read-only real corpus verification in isolated task data. Resolve design/implementation gaps; record exact proof boundaries. No production graph write, rebuild or paid extraction. Final code review and fixes required before completion.

Read [spec](../spec.md) and [approved design](../../../docs/sync-state-design.md).

## Acceptance

Use TDD through CLI seams. Commit scoped changes on own worktree/branch based on integration; merge current integration tip before reporting. Record tests and commit in Answer; mark resolved only after evidence.
