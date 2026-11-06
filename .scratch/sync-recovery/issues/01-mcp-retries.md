# Bounded read-only MCP retries

Type: task
Status: claimed
Blocked by: none

Spec: [spec.md](../spec.md)

Implement spec decisions 2 and 3: unify structured envelope/JSON-RPC/transport classification, permanent-error precedence, constrained production timeout fallback, bounded safe diagnostics with request context and attempt budget, proxy cleanup. Preserve fresh-list semantics. Tests: approved adapter and listing seams; production timeout, budgets, permanent/unknown errors, cancellation and cleanup. Own get_sync_updates.py and adapter/listing tests.

Use TDD, approved seams in spec Testing Decisions. No live calls. Integration branch: feat/enhance-sync-break, initial base 5d06e4c.
