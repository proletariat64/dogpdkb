# mdocr

mdocr extracts OCR from images referenced by Markdown. A caller supplies a JSON
work plan and optionally a TOML configuration. A batch owns its saved plan,
request journal, and results. Single-file and batch jobs use the same workflow.

## Build

Build on x86-64 Linux with Rust 1.95 or later. The build target produces one
statically linked executable with its Markdown parser, image processing,
embedded fonts, TLS roots, Mermaid checker, and help.

```sh
make checkr
make buildr
make smoker
```

`checkr` uses `rustfmt` and Clippy with warnings denied, then runs Rust tests.
`smoker` runs `buildr`, checks static linkage with `readelf`, and exercises a
relocated binary with a cleared environment. It uses a credential fixture and
submits zero OCR requests. GitHub Actions runs `checkr` and `smoker` in the Rust
job.

By default, the executable is at
`experiments/mdocr-rust/target/x86_64-unknown-linux-gnu/release/mdocr`.
Set `CARGO_TARGET_DIR` to use a different build directory.

## Run one document

```sh
mdocr draft --input-single source.md --output-single enriched.md --plan work.json
mdocr doctor --config provider.toml
mdocr run --plan work.json --config provider.toml > summary.json 2>> mdocr.log
```

Run `tail -F mdocr.log` in a second terminal for live progress. Read
`work.mdocr/result.json` for document and image outcomes. For directory batches,
use `draft --input-dir DIR --output-dir DIR` or write an exact work plan.

At first acceptance mdocr uses the explicit TOML, the `default.toml` next to the
work plan, or compiled defaults, in that order. Provider credentials are files
named by the TOML. Provider calls and authorized remote image downloads require
network access.

## Inspect, resume, and recover

```sh
mdocr accept --plan work.json --config provider.toml
mdocr run --plan work.json
mdocr cancel --plan work.json
mdocr run --plan work.json --resume
mdocr assemble --plan work.json --output-dir recovered
mdocr purge --plan work.json
```

Run again with the same work plan after an interruption. After explicit
cancellation, resume with `--resume`. Use `assemble` to recover already accepted
results locally. Purge is explicit and preserves published Markdown.

Use `mdocr --help` to browse commands and `mdocr help all` for the full embedded
contract. The [product specification](../.scratch/mdocr-rust-cli/spec.md)
covers image syntax, provider support, and batch artifacts.
