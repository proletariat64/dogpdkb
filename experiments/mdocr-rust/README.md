# Rust mdocr experiment

Build with Rust 1.95 or later:

```sh
cargo build --locked --release --manifest-path experiments/mdocr-rust/Cargo.toml
```

The experiment produces one `mdocr` executable. Keep it in the experiment
directory when using the Python CLI alongside it.

- `plan` accepts a Markdown file, a JSON path list, or a directory. It captures
  local image references and outputs an immutable `mdocr-plan-2` plan for Paddle
  or LLM mode. URLs are flagged for follow-up. The experiment does not support
  `--download` or `--download-list`.
- `check-mermaid <file|->` checks Markdown fences with embedded `merman-core`
  0.11.0. It emits JSON block diagnostics and returns 0 for success, 1 for
  invalid Mermaid, and 2 for argument or I/O errors.

The existing Python CLI still owns provider calls, image preparation,
journaling, recovery, and publication. It can consume plans written by this
experiment for supported inputs.
