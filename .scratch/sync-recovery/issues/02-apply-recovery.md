# Publication gate and checkpoint recovery

Type: task
Status: resolved
Blocked by: none

Spec: [spec.md](../spec.md)

Implement spec decisions 1, 4 and 5 in apply: retain successful staged exports/OCR, reject missing artifacts, record failures, never publish/delete/update state with required failures; direct and no-merge apply must fail. Preserve empty-body and plan identity contracts. Tests at apply/OCR seams, including resume, cancellation, publication failure. Own apply_sync_updates.py and apply tests.

Use TDD, approved seams in spec Testing Decisions. No live calls. Integration branch: feat/enhance-sync-break, initial base 5d06e4c.

## Answer

Implemented in 59ed843. Required export/OCR failures now stop before publication/deletions/state updates, including direct and no-merge apply. Checkpoints retain successful work; missing artifacts and incompatible configuration are handled safely, with legacy compatibility. Diagnostic manifests and direct retry commands retained. Implementer verified 68 focused tests plus Ruff/format/diff checks. No live calls.
