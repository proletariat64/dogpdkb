# Graph compilation contract

`dogpdkb.graph.run_graph(settings, book, store, run, manifest=None) -> int` is
shared by sync and the `update-graph --config FILE --input MANIFEST` command.
The caller holds the corpus/graph writer locks. It returns 0 complete, 2 partial,
1 batch failure, or 130/143 interrupted. It never launches Web publication.

Input is `dogpdkb-compile-1` from apply, bound to the selected book and run.
Every source must remain the current corpus version at its recorded path and
match apply's saved `input_copy` byte for byte. This existing witness detects a
source edited after planning without adding a second fingerprint mechanism.
Changed/missing input, an invalid manifest or a newer graph success refuses the
old plan before starting tool work.

Graphify is invoked only through its documented `source-inventory` and
`update-sources` CLI. The consumer validates their public schema, source coverage,
counts, exit code, and graph artifact path/existence; it never reads graph node,
edge, cache, clustering or extraction internals. Its tests deliberately use an
opaque graph artifact that cannot be parsed as JSON.

The compile phase runs one complete source per public CLI call, serially. Only
the currently launched source receives a durable graph attempt. A missing,
invalid or interrupted result records that source as unknown and stops the
phase; sources not yet launched retain their pending work and have no graph
attempt. Each source's request/result lives in `run/graph/<index>/`; the run
records this evidence directory and the most recent report.

A valid Graphify success advances only that document's graph version. A source
failure keeps its prior graph success while leaving newly published corpus
truth intact; later sources continue. Same-version failed/unknown work is handed
off and not retried. A successful step in the same resumed run is not repeated.

Stable public source IDs are `book-key:document-id`. Source names and replacement
lists come from public source inventory and prior recorded publication facts.
Baseline corpus filename IDs may identify preexisting inventory sources; this
only selects Graphify replacement arguments and never reads the graph itself.
Metadata/reuse actions use Graphify metadata updates only when the graph version
matches the comparison baseline/current version. Otherwise the complete current
source compiles. The initial registered baseline also permits metadata reuse
when the comparison baseline is that exact registered day version and the public
inventory covers its exact registered source path. An older inventory filename
or an advanced corpus baseline does not establish this starting point.
Metadata maps source references to source URLs.

Pending selection clears only after acknowledged graph success, when its source
version and captured metadata still match and no unstarted tool step remains.
A newer discovery or never-launched OCR work remains pending for a later cron.
OCR partials and manifest failures contribute exit 2 without blocking usable
source compilation.

The independent Graphify capability and patch/install identity are documented
in [the upstream CLI contract](orchestrator-graphify-contract.md).
