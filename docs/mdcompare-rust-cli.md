# mdcomparer

Use `mdcomparer` to compare captured Markdown offline, publish verifiable
evidence, and optionally score and route specific changes. The comparison
and scoring work plans are separate so the caller controls the scoring
selection and budget.

## Compare two files

```sh
mdcomparer draft before.md after.md --plan work.json
mdcomparer run --plan work.json > summary.json 2>> progress.log
mdcomparer status --plan work.json
```

A batch orchestrator can submit the same work-plan format directly. For longer
runs, follow progress with `tail -F progress.log`. The final summary points to
verified artifacts in the work plan's adjacent workspace. Keep this directory
private because semantic evidence contains full body text.

```sh
mdcomparer --help
mdcomparer run --help
mdcomparer help all
```

## Build for Linux x86-64

Install the `x86_64-unknown-linux-musl` Rust target and a musl C compiler.
Install `rustfmt` and Clippy for `checkr`, and `readelf` for `smoker`.

```sh
make checkr
make buildr
make smoker
```

`checkr` verifies formatting, runs Clippy with warnings denied, and runs Rust
tests. `buildr` builds the release binary using the locked dependency versions.
`smoker` runs `buildr`, verifies static linkage, and runs an offline comparison
and status check with a relocated binary in a cleared environment.

By default, the executable is at
`experiments/mdcompare-rust/target/x86_64-unknown-linux-musl/release/mdcomparer`.
Set `CARGO_TARGET_DIR` to use a different build directory. The binary embeds
help, the scoring prompt, and the scoring schema. For a local runtime check,
run `mdcomparer doctor --pretty`.

## Diagnose semantic scoring

```sh
mdcomparer doctor --config scoring.toml --pretty
```

The JSON report includes the runtime, configuration source and individual checks.
The LLM section shows the provider, model, reasoning effort, request budget,
concurrency and local readiness. Doctor validates transport settings and the
saved OAuth credential locally. Provider access and inference remain
`not_checked`. Use `--pretty` to indent the JSON.
