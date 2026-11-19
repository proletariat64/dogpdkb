# phase-resume

Status: integrated

Blocked by: none

Implement five stop points, frozen phase handoffs and stale per-document publication checks plus incremental OCR catchup; preserve verified children and no replay.

Acceptance: applicable public CLI scenarios in [spec](../spec.md).

- Phase handoff and runner state are documented in
  [`orchestrator-core-contracts.md`](../../../docs/orchestrator-core-contracts.md#sync-phase-handoffs).
  Ticket 03 still owns native extraction, proof and graph publication; ticket 06
  consumes `completed_phase`, `checkpoints` and the existing run failures.
- Offline focused checks on the 03 integration: 5 phase/resume cases, 14 source
  selection/OCR replay cases, and 19 graph-input/native-proof and apply/replan
  regressions passed. New cases went red on the missing phase flag and on an
  unsent catchup replacing the existing fallback witness.
- If a batch is already bound to a stale document, automatically reopening it
  could admit stale work. A terminal report can still supply verified peers;
  there is no automatic split/retry of a running native batch. Graph-only
  recovery retains its explicit authorization and input validation.
- No installed end-to-end UAT, real provider calls or full-suite run here.
