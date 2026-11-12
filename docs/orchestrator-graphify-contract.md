# Graphify source-update CLI contract

Status: implemented and verified in independent Graphify patch
Upstream package baseline: graphifyy 0.9.45; Graphify-Labs/graphify.

## Public invocation

- `graphify source-inventory --graph-dir DIR --output INVENTORY.json` reads only the public graph and writes a source inventory.
- `graphify update-sources --graph-dir DIR --request REQUEST.json --output RESULT.json` extracts changed complete sources and publishes successful replacements.
- `graphify update-sources ... --extraction EXTRACTION.json` consumes an already-produced public Graphify extraction artifact; useful for offline delivery/UAT and extraction reuse, never a dogpdkb internal call.

## Request

Schema: `graphify-source-update-1.0`. Top-level `sources` is a list. Each item contains caller `source_id`, `source_file` (desired public provenance filename), `path` (complete Markdown input), `replace` (list of previous public source_file names; defaults to source_file), optional `metadata` (public provenance fields), and `action` (`compile` default; `metadata` requires no extraction). Paths resolve against request directory. No deletions occur merely because a source is absent from the request.

## Result

Schema: `graphify-source-result-1.0`; `status` is completed/partial/failed, `graph` is the published graph path, `sources` contains source_id/source_file/action/status/error, and `counts` contains total/completed/failed. Report is written even for documented source failures. Exit 0 completed or no work, 2 partial with successful sources published, 1 request/publication failure. Individual extraction failure preserves that source old graph contribution. Caller versions remain opaque metadata, not a tool-side business scheduling policy.

Inventory schema: `graphify-source-inventory-1.0`; `sources` contains each public source_file plus available source_id and metadata. This requires no semantic extraction.

## Boundary

Graphify owns extraction, cache, record identity, graph merging, complete-source replacement and atomic graph publication. dogpdkb only invokes this CLI and consumes its documented report/inventory. Implementation and independent install identity are recorded below.

## Implemented extraction artifact and options

`--extraction` accepts `{schema: "graphify-source-extraction-1.0", sources: [...]}`.
Rows contain source_id, status (`completed` / `failed`), extraction (`nodes`,
`edges`, optional `hyperedges`) or error. Missing rows fail individually. Duplicate
chunk node IDs are accepted through Graphify deduplication. Successful empty
extractions clear old source contributions and remain in the source inventory.

Normal extraction accepts `--backend`, `--model`, `--max-concurrency` and existing
Graphify provider environment variables. It uses Graphify's existing semantic
cache and corpus extraction; failed/truncated/uncovered extraction preserves the
old contribution. Metadata-only updates require neither a file nor extraction.
Exit 2 also covers all-sources-failed, where the old graph remains untouched.
Overlapping replacements and structural metadata are rejected before publication.

## Source and installation identity

- Independent Graphify tree: `/tmp/graphify-0.9.45-cli-source`.
- Release source baseline: installed `graphifyy==0.9.45`; snapshot commit
  `99063934772a5649d626b108b06776fe1ab0e694`.
- Upstream implementation commit: `ffd2eafef6b161d8c72a03dcdf1574d3439d3d8b`.
- Ready standalone public entry: `/tmp/graphify-source-update-bin/graphify`.
  It launches that independent Graphify tree using the existing Graphify Python
  environment. The usersite installation was never modified.
- Full patch and identity hashes: `upstream/graphify-source-update/` relative to
  this document's directory. No Graphify runtime is vendored in dogpdkb.

To reproduce in a separate source directory, copy the installed release's
`graphify/` package, verify the baseline file hashes in `identity.json`, apply
`graphifyy-0.9.45-source-update.patch` there with `git apply`, then run:

```sh
PYTHONPATH=/path/to/independent/source /path/to/graphify/python -m unittest discover -s /path/to/independent/source/tests -p test_source_update_cli.py -v
PYTHONPATH=/path/to/independent/source /path/to/graphify/python -m graphify update-sources --help
```

The public process dependency must select this patched independent Graphify
entry until the upstream project publishes the capability. The package version
still identifies its 0.9.45 release baseline; the exact patch/source identity is
above. No upstream PR, release, or default usersite replacement is claimed.

Verification: 14 public subprocess contract tests pass, including a localhost
OpenAI-compatible fixture for fresh extraction, Graphify cache reuse and HTTP
failure preservation; git diff checks pass. No paid extraction, production graph
write, or historical scan ran. GitHub clone/tarball transport did not complete;
the observed remote HEAD is recorded only as unused research evidence.
