# Sync recovery

## Notes

Implementation graph derived from spec; original checkout had no tickets.

## Decisions-so-far

Integration branch: feat/enhance-sync-break. Tickets 01 and 02 independent; 03 depends on both.

- [01 MCP retries](issues/01-mcp-retries.md): integrated e9605ae; bounded read-only retry and diagnostics.
- [02 Apply recovery](issues/02-apply-recovery.md): integrated 59ed843; pre-publication failure gate and safe checkpoint reuse.

## Fog

None.
