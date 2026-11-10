# Acceptance, documentation and installed proof

Type: task
Status: resolved
Blocked by: 03-stage-integration

## Scope

Update user docs and installer smoke for actual implemented CLI behavior; focused recovery/concurrency/same-day timezone cases, full make check/make smoke, read-only real corpus verification in isolated task data. Resolve design/implementation gaps; record exact proof boundaries. No production graph write, rebuild or paid extraction. Final code review and fixes required before completion.

Read [spec](../spec.md) and [approved design](../../../docs/sync-state-design.md).

## Acceptance

Use TDD through CLI seams. Commit scoped changes on own worktree/branch based on integration; merge current integration tip before reporting. Record tests and commit in Answer; mark resolved only after evidence.

## Answer

Completed initializer inventory reporting, user documentation and isolated installed acceptance. Root Markdown files without a standard ID are counted and listed in book `unmatched_files`; no remote identity is invented. The new real CLI regression failed before the inventory fix and now passes. The installed smoke now covers canonical initialization, same-day raw capture, UTC+8 normalization, mdcompare KEEP state and updater-lock refusal, in addition to the existing explicit legacy execution/quality checks.

Installed smoke found and fixed a separate-runtime regression: `update_graph.py` imported new dogpdkb state/lock modules before establishing its existing isolated-interpreter package fallback. Moving those imports after the fallback restored `python -I` operation without adding dependencies.

Evidence: full `make check` passed with 1,004 tests in 100.73 seconds; after the import-order correction, 66 final graph/knowledge execution/initializer tests passed in 27.99 seconds. Final `make smoke` passed from built wheel/sdist in disposable environments. Last doc/lint checks passed for 73 documents and 187 formatted files; `git diff --check` passed.

Real-file initialization used the task-owned independent copy `/tmp/dogpdkb-sync-state-real-uat-ahcz9q86`. Final inventory: 4,782 root Markdown files, 4,781 recognized IDs, 1 unmatched file, 4,778 recognized graph-source matches, 3 unknown source matches and 4,781 day-only versions. All 4,782 Markdown copies plus graph and manifest remained byte-identical to the production source. The production source has no state file; no production state or graph was changed.

See `docs/sync-state-acceptance.md` for private evidence paths, exact proof boundaries and the distinction between deterministic installed Graphify stubs and production knowledge validation. No remote listing, paid model call, production deployment or real graph update occurred. Final integration code/OCR review remains the integrating agent's gate; this ticket does not declare those reviews complete.
