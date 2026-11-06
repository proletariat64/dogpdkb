# Publication gate and checkpoint recovery

Type: task
Status: resolved
Blocked by: none

Spec: [spec.md](../spec.md)

Implement spec decisions 1, 4 and 5 in apply: retain successful staged exports/OCR, reject missing artifacts, record failures, never publish/delete/update state with required failures; direct and no-merge apply must fail. Preserve empty-body and plan identity contracts. Tests at apply/OCR seams, including resume, cancellation, publication failure. Own apply_sync_updates.py and apply tests.

Use TDD, approved seams in spec Testing Decisions. No live calls. Integration branch: feat/enhance-sync-break, initial base 5d06e4c.

## Answer

Implemented in 59ed843. Required export/OCR failures now stop before publication/deletions/state updates, including direct and no-merge apply. Checkpoints retain successful work; missing artifacts and incompatible configuration are handled safely, with legacy compatibility. Diagnostic manifests and direct retry commands retained. Implementer verified 68 focused tests plus Ruff/format/diff checks. No live calls.

## Comments

Review follow-up 1410008 reads formal sync state before publication, records its nonzero SystemExit failure, and preserves failure/retry diagnostics when the failed manifest cannot be persisted. Invalid-state and diagnostic-storage regressions pass in the final 516-test suite.

PR #10 follow-up: the export-cancellation review finding was valid. Direct apply now records a failed export manifest with the cancelled document ID, prints the same-staging recovery command, and re-raises KeyboardInterrupt without starting OCR or publication. Completed exports remain reusable. Regressions replay the emitted command in normal and no-merge modes and preserve diagnostics when manifest storage is unavailable. Final `make check` passed 518 tests plus lint, formatting and documentation checks. The first full run hit an unrelated 100 ms proxy timing timeout; that test passed in isolation and the unchanged full rerun passed. Greptile was not retriggered.
