# Incremental sync state and existing corpus baseline

Status: implemented

Implement [the approved design](../../docs/sync-state-design.md), including UTC+8 defaults. This design is authoritative over older raw-only baseline requirements. Scope: existing-corpus initialization, unified incremental state, single updater process, exact timestamp selection, mdcompare knowledge integration and recovery. Preserve current explicit execution approval/quality gates. No production sync, paid extraction, deployment or corpus writes are authorized by implementation work.

Public test seams: existing dogpdkb CLI subprocesses and observable state/plan/artifacts; use boundary adapters for remote calls and Graphify. This repeats the user-confirmed seams in daily-knowledge-sync/spec.md. TDD per vertical slice. Acceptance: focused CLI tests, make check, isolated installed make smoke, read-only real-corpus initialization exercise in task-owned copies/state output, code review. Keep production artifacts unchanged.

Task graph: 01 and 02 independent; 03 depends on both; 04 depends on 03. All commits merge to feat/sync-state-baseline. Resolve local tickets by Status: resolved plus Answer evidence. No PR requested.

## Implementation result

All four tickets are resolved on the integration history. Final review fixes cover historical-download selection, completed execution/journal recovery, conflicting plan ownership, actual detail timestamp metadata, preflight validation before claims, legacy state import preservation and quoted-home identity handling. Final source check passes 1,021 tests; isolated installed smoke passes. See [acceptance](../../docs/sync-state-acceptance.md). Production deployment and data migration remain outside this implementation. Standards/specification review is complete after fixes; the separate OCR CLI review remains pending as of this record.
