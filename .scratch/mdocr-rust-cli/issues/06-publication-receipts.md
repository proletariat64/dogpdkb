# Record ownership before publishing

Status: ready-for-agent
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
