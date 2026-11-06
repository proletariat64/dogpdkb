# Sync recovery

## Notes

Implementation graph derived from spec; original checkout had no tickets.

## Decisions-so-far

Integration branch: feat/enhance-sync-break. Tickets 01 and 02 independent; 03 depends on both.

- [01 MCP retries](issues/01-mcp-retries.md): integrated e9605ae; bounded read-only retry and diagnostics.
- [02 Apply recovery](issues/02-apply-recovery.md): integrated 59ed843; pre-publication failure gate and safe checkpoint reuse.

- [03 Pipeline diagnostics](issues/03-pipeline-diagnostics.md): integrated e1c9590; manifest validation before completion and accurate recovery guidance.
- Review fixes: 1f2c06f (threaded cancellation and shared manifest rule), 499b81f (malformed MCP content and dead helper), 1410008 (sync-state publication preflight and best-effort failure diagnostics).
- Final gate at 1410008: 516 tests, Ruff, formatting, document guard and diff checks passed. All tickets and the spec are resolved. OCR covered 3/3 selected source files; its findings were fixed and final code was tested, without another OCR run. Production and corpus artifacts were untouched.

## Fog

None.
