# Reset OCR state and reopen a purged batch

Status: ready-for-agent
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
