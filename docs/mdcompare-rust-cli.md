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

Install the `x86_64-unknown-linux-musl` Rust target and a musl C compiler. Then
build with the locked dependency versions.

```sh
cargo build --locked --release --target x86_64-unknown-linux-musl \
  --manifest-path experiments/mdcompare-rust/Cargo.toml
```

Copy the executable at
`experiments/mdcompare-rust/target/x86_64-unknown-linux-musl/release/mdcompare`
to a compatible Linux x86-64 host. The binary embeds help, the scoring prompt
and the scoring schema. For a local runtime check, run `mdcompare doctor`.
