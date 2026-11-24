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
mdocrr doctor --config provider.toml --pretty
mdocrr run --plan work.json --config provider.toml > summary.json 2>> mdocrr.log
```

Run `tail -F mdocrr.log` in a second terminal for live progress. Read
`work.mdocr/result.json` for document and image outcomes. For directory batches,
use `draft --input-dir DIR --output-dir DIR` or write an exact work plan.

At first acceptance mdocrr uses the explicit TOML, the `default.toml` next to the
work plan, or compiled defaults, in that order. Provider credentials are files
named by the TOML. Provider calls and authorized remote image downloads require
network access.

Doctor reports local readiness and sends at most one short text request in LLM
mode with usable OAuth and a nonzero or omitted request budget. Bare doctor
and Paddle mode only check local readiness. The JSON summary distinguishes
HTTP, remote credentials, and model results. Use `--pretty` for indented JSON.

## Inspect, resume, and recover

```sh
mdocrr accept --plan work.json --config provider.toml
mdocrr run --plan work.json
mdocrr cancel --plan work.json
mdocrr run --plan work.json --resume
mdocrr assemble --plan work.json --output-dir recovered
```

Run again with the same work plan after interrupted OCR. Use `--resume` for an
interrupted download or an explicit cancellation. Use `assemble` to recover
already accepted results locally.

To perform OCR again with the retained downloads, confirm a reset and run.

```sh
mdocrr reset --plan work.json
mdocrr run --plan work.json
```

Reset clears the batch's OCR state and removes published Markdown whose paths
and digests match its publication receipts. The next execution starts with zero
used OCR budget and bypasses persistent OCR cache reads until it completes.
Repeat an interrupted reset to finish cleanup before running again.

To discard staged images as well, purge before resetting. Purge preserves
published Markdown until the operator confirms reset. Finish an interrupted
purge by repeating it before requesting reset.

```sh
mdocrr purge --plan work.json
mdocrr reset --plan work.json
mdocrr run --plan work.json
```

Use `mdocrr --help` to browse commands and `mdocrr help all` for the full embedded
contract. The [product specification](../.scratch/mdocr-rust-cli/spec.md)
covers image syntax, provider support, and batch artifacts.
