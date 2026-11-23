# Rust mdcompare

Use `mdcompare` to compare captured Markdown offline, publish verifiable
evidence, and optionally score and route specific changes. The comparison
and scoring work plans are separate so the caller controls the scoring
selection and budget.

## Compare two files

```sh
mdcompare draft before.md after.md --plan work.json
mdcompare run --plan work.json > summary.json 2>> progress.log
mdcompare status --plan work.json
```

A batch orchestrator can submit the same work-plan format directly. For longer
runs, follow progress with `tail -F progress.log`. The final summary points to
verified artifacts in the work plan's adjacent workspace. Keep this directory
private because semantic evidence contains full body text.

```sh
mdcompare --help
mdcompare run --help
mdcompare help all
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
`experiments/mdcompare-rust/target/x86_64-unknown-linux-musl/release/mdcompare`.
Set `CARGO_TARGET_DIR` to use a different build directory. The binary embeds
help, the scoring prompt, and the scoring schema. For a local runtime check,
run `mdcompare doctor`.
