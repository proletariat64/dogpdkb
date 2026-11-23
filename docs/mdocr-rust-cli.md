# mdocrr

mdocrr extracts OCR from images referenced by Markdown. A caller supplies a JSON
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
`experiments/mdocr-rust/target/x86_64-unknown-linux-gnu/release/mdocrr`.
Set `CARGO_TARGET_DIR` to use a different build directory.

## Run one document

```sh
mdocrr draft --input-single source.md --output-single enriched.md --plan work.json
mdocrr doctor --config provider.toml
mdocrr run --plan work.json --config provider.toml > summary.json 2>> mdocrr.log
```

Run `tail -F mdocrr.log` in a second terminal for live progress. Read
`work.mdocr/result.json` for document and image outcomes. For directory batches,
use `draft --input-dir DIR --output-dir DIR` or write an exact work plan.

At first acceptance mdocrr uses the explicit TOML, the `default.toml` next to the
work plan, or compiled defaults, in that order. Provider credentials are files
named by the TOML. Provider calls and authorized remote image downloads require
network access.

## Inspect, resume, and recover

```sh
mdocrr accept --plan work.json --config provider.toml
mdocrr run --plan work.json
mdocrr cancel --plan work.json
mdocrr run --plan work.json --resume
mdocrr assemble --plan work.json --output-dir recovered
mdocrr purge --plan work.json
```

Run again with the same work plan after an interruption. After explicit
cancellation, resume with `--resume`. Use `assemble` to recover already accepted
results locally. Purge is explicit and preserves published Markdown.

Use `mdocrr --help` to browse commands and `mdocrr help all` for the full embedded
contract. The [product specification](../.scratch/mdocr-rust-cli/spec.md)
covers image syntax, provider support, and batch artifacts.
