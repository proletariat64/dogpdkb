# Graph compilation contract

`dogpdkb.graph.run_graph(settings, book, store, run, manifest=None) -> int` is
shared by sync and `update-graph --config FILE --input MANIFEST`. The caller
holds corpus/graph writer locks. It never launches Web publication.

Input is `dogpdkb-compile-1` from apply, bound to one book and run. Each selected
source must match the current corpus version, path, metadata and saved full
`input_copy` byte for byte. A changed/missing input or newer successful graph
refuses the old plan. Frozen `previous_graph` facts handle opaque versions
without sorting them lexically. The same inputs are checked after extraction.

Graphify is an unmodified external CLI. Compilation is one native directory
`extract` call, using its original manifest/cache. Candidate outputs stay in a
run-owned workspace until successful validation. The orchestrator reads only
public graph/manifest artifacts and never rewrites their graph records.
[The native tool contract](orchestrator-graphify-contract.md) specifies exact
argv, baseline requirements, output isolation and publication order.

The accepted directory-level semantics replace the discarded per-source
protocol. All pending sources receive graph attempts before the single command.
Only a complete successful batch advances their graph versions. Extraction
failure keeps the old public graph and manifest and records batch failure;
interruption or invalid artifacts are unknown. Failed/unknown current versions
block subsequent directory scans, including other selections, so they cannot
be retried indirectly. Separate capture/OCR failures keep the run partial while usable corpus sources
can still compile. No failed raw input is promoted into the compile manifest.

A successful source keeps an independent input copy and metadata in dogpdkb
state. Same-run/version resume skips only an exact match. This records observed
source versions; it does not invent a Graphify metadata-update operation.
Pending discovery clears only when its version/metadata still match and no
unstarted tool step remains. Newer/unknown discoveries remain pending.

Exit 0 means successful batch or no work, 2 means failed/suppressed/incomplete
work, 1 means invalid input/baseline/publication or unknown output, and 130/143
means interruption. CLI logs, native candidate artifacts and dogpdkb's own
execution report remain available for external investigation and cleanup.
