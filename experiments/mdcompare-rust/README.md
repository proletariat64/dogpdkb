# Rust mdcompare experiment

```sh
cargo build --locked --release --manifest-path experiments/mdcompare-rust/Cargo.toml
experiments/mdcompare-rust/target/release/mdcompare compare \
  --baseline baseline.json --incoming incoming.json --output report.json
```

The Linux release is one executable. It reads the existing 1.0 manifests and
produces a 1.0 JSON report for the strict raw-text comparison path. It supports
scoped document identities, metadata comparison, exact byte equality, opt-in
line-ending normalization, and bounded line and character evidence.

Resource verification is conservative. Resource or OCR declarations, historical
documents, and bodies with possible Markdown, HTML, or CSS references receive
`UNKNOWN`. Even an ordinary text link can trigger this fallback. Keep using
Python for resource/OCR evidence, historical provenance, the other subcommands,
and human-readable reports. The experiment is not wired into dogpdkb sync.

Exit 0 means every action was determined. Exit 2 means the report includes an
unknown action. Exit 1 means the invocation or report failed.
