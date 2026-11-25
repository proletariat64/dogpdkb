# Reset OCR state and reopen a purged batch

Status: resolved
Type: task
Blocked by: [05](05-download-checkpoints.md), [06](06-publication-receipts.md)

Implement reset, purge receipts, and persistent cache-read bypass from
[the specification](../spec.md). Reuse workspace execution and control locks.
Keep output deletion confined to verified receipts. Block reset while purge
cleanup is unfinished. Repeating the interrupted operation must finish it
without allowing an ordinary run through either seal.

Verify reset and re-OCR, purge followed by reset and re-download, active and
declined cleanup, interrupted cleanup, drift and unknown-request warnings.
Verify a warm OCR cache does not bypass reset request admissions, and preserve
cache reads for ordinary execution. Reuse focused offline and loopback fixtures.

Acceptance evidence: `make checkr` passed formatting, strict Clippy, and 46
Rust tests. Public CLI fixtures covered declined and active cleanup, interrupted
purge and reset, modified and unowned outputs, unresolved-request warnings,
and warm-cache bypass across partial, canceled, and crashed executions. The
loopback run reused staged images after reset, fetched them again after purge
and reset, and continued to use the shared cache for an ordinary batch.
