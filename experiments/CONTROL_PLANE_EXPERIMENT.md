# Control plane prototypes (isolated)
These two branches evaluate different questions; neither alters production sync or calls paid providers.

- Python branch: SQLite ledger, per-document stage advancement, batch admission, crash-unknown blocker.
- Rust branch: std-only single-binary deployment experiment and local stage executor. Graph dispatch is deliberately disabled pending an external-call journal.

## Required production parity before replacement
1. Real public CLI adapters for Skylark, mdcompare, mdocr, Graphify.
2. Durable before-call attempt, journal reconciliation, version fencing and output byte witnesses.
3. One writer for fixed Corpus/Graphify paths; batch boundary is an immutable set of ready document versions.
4. Unknown Graphify outcome blocks automatic replay; graph success must come from native evidence.
5. OCR native batch plan/budget semantics must be preserved.
6. End-to-end killpoint tests for each external tool and every publication boundary.

## Deployment measurement
Python: measure installed venv including Python interpreter and third-party packages, plus extra tools separately.
Rust: `cargo build --release --manifest-path experiments/rust-control/Cargo.toml`; measure the release executable with `du -h`, then run on target without Rust toolchain. Measure Skylark/Graphify/OCR separately: rewriting the orchestrator does not remove those dependencies.
