# Execute, resume, cancel, and assemble a batch

Status: resolved
Type: task

Implement the execution and recovery contracts in [the specification](../spec.md)
against the defined workspace and settings interfaces. Verify durable admission,
recovery after an interrupted request, cancellation, and JSON result binding.

## Answer

Implemented in `171fd7b`, with provider failure classification, bounded progress
snapshots, and publication validation in `3a34b57`. Public CLI tests verified
crash recovery without replay, offline assembly, active cancel and explicit
resume, and retry of a known Paddle failure. Separate loopback acceptance
verified throttling, retry bounds, cache reuse, zero request budget, and a
successful Mermaid repair.
