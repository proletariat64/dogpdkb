# Native Rust mdcompare

Build the portable Linux x86-64 executable with the Rust musl target and a
musl C compiler for the TLS library.

```sh
rustup target add x86_64-unknown-linux-musl
cargo build --locked --release --target x86_64-unknown-linux-musl \
  --manifest-path experiments/mdcompare-rust/Cargo.toml
```

Copy `experiments/mdcompare-rust/target/x86_64-unknown-linux-musl/release/mdcompare`
to a compatible Linux x86-64 host. The binary contains the command help, scoring
prompt, and score schema. It runs without a checkout, Python, Node, or an
installed Pi SDK. Manifests, input snapshots, and output reports remain local
files supplied at invocation.

```sh
./mdcompare doctor --json
./mdcompare compare --help --full
./mdcompare compare --baseline baseline.json --incoming incoming.json --output report.json
```

`catalog`, `compare`, `knowledge`, `semantic` and `semantic-route` run offline.
Use `semantic-score` explicitly to send eligible authored-body evidence and
`doctor --mode llm` to check the local credential prerequisites. Live scoring
requires stored Pi OAuth credentials and access to the selected provider.
Keep the execution journal and lock beside the score output when resuming.

For an opt-in dogpdkb run, point the existing tool setting at the copied
binary.

```toml
[tools]
mdcompare = "/absolute/path/to/mdcompare"
```

Returning the setting to the installed `mdcompare` restores the Python CLI.
