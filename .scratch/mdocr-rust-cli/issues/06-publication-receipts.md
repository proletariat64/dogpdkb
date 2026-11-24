# Record ownership before publishing

Status: resolved
Type: task
Blocked by: [03](03-execution-lifecycle.md)

Implement the publication receipts in [the specification](../spec.md).
Separate rendering from durable writes where needed. Record the resolved
destination and expected SHA-256 in the existing plan-bound journal before
publishing, including documents without selected images. Preserve ordinary
recovery from valid receipts and keep offline assembly from updating the
original journal.

Expose an internal way to retrieve receipts and unresolved-request state for
purge and reset without depending on live providers or intact staged bytes.
Verify recovery across the journal, output and result commit boundaries and
failure on changed or unowned output.

## Answer

The runner persists plan-bound output receipts before publication. Recovery
verifies the saved path and digest and can publish a missing output or
reconstruct a result for one already written. An existing destination without
a receipt fails closed, even when its bytes match the expected output.
Offline assembly leaves the original journal intact. Workspace cleanup can
read typed receipt and unresolved-request state without reading image bytes.

The CLI receipt test failed on the missing journal field before implementation
and passed afterward. `make checkr` passed formatting, Clippy and 39 tests,
including both publication interruption boundaries and changed, redirected,
and unowned outputs.
