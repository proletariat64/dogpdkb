# Bounded read-only MCP retries

Type: task
Status: resolved
Blocked by: none

Spec: [spec.md](../spec.md)

Implement spec decisions 2 and 3: unify structured envelope/JSON-RPC/transport classification, permanent-error precedence, constrained production timeout fallback, bounded safe diagnostics with request context and attempt budget, proxy cleanup. Preserve fresh-list semantics. Tests: approved adapter and listing seams; production timeout, budgets, permanent/unknown errors, cancellation and cleanup. Own get_sync_updates.py and adapter/listing tests.

Use TDD, approved seams in spec Testing Decisions. No live calls. Integration branch: feat/enhance-sync-break, initial base 5d06e4c.

## Answer

Implemented in 536d4de (integrated via e9605ae). Read-only MCP retries classify envelope, JSON-RPC and transport errors with permanent-error precedence, bounded sanitized diagnostics and per-attempt cleanup. Implementer verified 58 focused tests plus Ruff/format/diff checks; integration adapter/sync/listing checks passed 55 tests. No live calls.
