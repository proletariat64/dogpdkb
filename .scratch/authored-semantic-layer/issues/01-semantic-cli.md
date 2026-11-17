# Authored semantic CLI and complete diff

Status: resolved
Type: task
Blocked by: none

Implement the independent mdcompare contract in ../spec.md. Own semantic comparison, explicit marker-recovery input validation and replay, full durable diff, CLI normal/full help, focused CLI tests and tool documentation. Keep existing knowledge/compare contracts. No orchestrator changes. No model/network calls. Public CLI red/green seam; maintain original-source location evidence for repairs. Resolve with passing focused tests and a commit on a ticket worktree based on the integration branch.

Verification: 438 mdcompare tests passed; Ruff check/format passed. Full 3293-pair offline semantic replay completed in 109 seconds with max RSS 86,180KB: SKIP1968 / COMPILE1298 / DEFER27. All36 new documents compile; all708 approved repair witnesses replay and are available; final26 remain deferred (one additional independent invalid scope is deferred). Complete evidence is streamed to private per-document artifacts and indexed by durable references. No Graphify/OCR/model requests. Subsequent public CLI regressions fixed inserted-EOF coordinates and reserved occurrence ownership (04c9990/47d121f); all32 semantic CLI cases are included in the final1202-test regression. Ticket02 integration and complete public plan-only replay now pass; user/real compilation/review/release acceptance remains ticket03.
