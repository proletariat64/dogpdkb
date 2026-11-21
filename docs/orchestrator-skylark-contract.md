# Skylark listing/export CLI contract

Use the independent Skylark CLI from `feat/cli-local-help`, implementation commit
`0dbb2cc904ce257596252b377769d94ec6811cc1`, built on the restored CLI contract
at `0e7c21883481d04a42a771c6fad1ddd5e6e0aa91`. Run `npm run build` in that
source checkout or install its built local npm package. The help implementation
is a separate source delivery; do not assume the default branch or an older
user installation already contains it. See the
[delivery and acceptance record](../.scratch/cli-help-redesign/skylark-acceptance.md).
The original checkout's uncommitted edits remain separate. No Graphify or Yuque
source changes are needed.

Root and command `-h`/`--help` provide English first-invocation guidance;
`skylark listing --help --full` and `skylark export -h --full` expose the complete
bundled local schemas, environment, write scope, exits and repeat-call semantics.
Root `skylark --version` identifies the package. These requests need no gateway,
SSO or writable runtime; `--full` is a help modifier, not an execution mode.

Configure `[tools]` with `skylark = ["node", "/path/to/pi_skylark/dist/cli.mjs"]`,
or use the installed `skylark` bin. Tool environment retains `UTOO_PROXY_BIN`,
`SKYLARK_MCP_URL`, `SKYLARK_TIMEOUT_MS`, `SKYLARK_MAX_RETRIES` and existing SSO.
Do not persist credentials in a saved plan. Image download has no independent
CLI entry in this build; image links in Markdown are not downloaded images.

`skylark listing --url BOOK_URL --output JSON` emits `skylark-listing-1`:
`complete: true`, `book` (book_id/url/items_count/name), `documents`, `toc`,
`captured_at`. Documents retain gateway metadata plus `document_id` (string)
and `version` (content_updated_at, updated_at, version, or null). All pages
and TOC must succeed with unique IDs and exact advertised count; count is
rechecked after pagination. No bodies are downloaded. Remote API has no snapshot
token, so concurrent same-count edits remain a tool limitation.

`skylark export --request INPUT --output REPORT` accepts:

```json
{"schema":"skylark-export-request-1","documents":[{"document_id":"123","path":"/tmp/123.md","format":"md"}]}
```

The orchestrator's `sync` and `apply --concurrency N` schedule up to N independent
single-document export calls at once: default 3, allowed values 1, 2 and 3 only.
This is a dogpdkb runtime option, not a Skylark flag or TOML setting. Each call has
its own request/report/raw destination. The main coordinator alone records
attempts and settled outcomes, validates source identity/version and checkpoints
successful inputs before further stages. An isolated failure does not cancel
other eligible exports. All exports settle before comparison starts. SIGINT or
SIGTERM stops all in-flight process groups and prevents queued dispatch. Changing
the value on resume does not refetch completed inputs or clear failed-version
suppression; graph-only recovery never re-enters export. Skylark transport retries
and connection management are unchanged.

Use `format: table` or `laketable` for native table export. Skylark owns MCP,
table/record traversal, Markdown rendering, image URL rewriting and atomic raw
output. Failed exports never publish error text as Markdown. Output starts with
the tool's document header followed by the full body; it is suitable as a complete
source, never a delta fragment. No image download or OCR happens here.

Report `skylark-export-result-1` contains `status`, `documents`, `counts`.
Each result: document_id/path/status; completed adds actual version, metadata
(id/title/content_updated_at/updated_at), bytes; failed adds error. A caller
compares actual exported version with the frozen selection. Missing version is
unknown and must not be upgraded to a known successful version. Table metadata
is checked before and after record pagination.

Exits: 0 complete, 2 isolated export failures, 1 command/listing/shared storage
failure, 64 invalid CLI usage. Stdout is a JSON output pointer, stderr is progress.
The orchestrator owns baseline cutoff, selection, durable attempt suppression and
publication policy. It never parses MCP envelopes or renders remote tables.

Validation: public CLI subprocess tests use a local fake stdio MCP proxy and real
Markdown outputs: complete metadata-only listing; incomplete listing rejection;
regular/table export; malformed source isolation; null/boolean/string listing total rejection. Existing eight transport/retry
tests also pass. No live gateway, paid OCR, graph extraction or production write.

## Explicit admission of staged exports

`dogpdkb recover-export --run ORIGINAL --input STAGED --output-dir PRIVATE`
prepares a SHA-256 approval receipt read-only against the configured book. The
`dogpdkb-staged-export-1` input specifies a nonempty `documents` list; each
row has `source_key`, `remote_id`, and absolute canonical `request`, `report`,
and `markdown` paths. Every row must be within the saved source subscription
and uniquely match a frozen, known failed export. Both the report and current
observation must match the original version. The Markdown must match the
completed report's exact path and byte count. Preparation does not attest to
an independent remote fetch.

`recover-export --publish RECEIPT --receipt-sha256 SHA` revalidates the whole
set under the writer locks, copies the original failure and staged evidence to
durable storage, and commits only the exact raw and export facts in one state
save. The command neither starts external tools nor touches Corpus or Graph.
`--resume RUN` verifies an already committed raw admission and finalizes its
run record if interrupted after the state save. If the state was not committed,
re-validate and publish the original approved receipt instead. The resulting
`dogpdkb-admitted-export-1` is raw evidence, not an `apply` or Graphify manifest.

## Consuming a committed admission in apply

`dogpdkb apply --input ADMITTED --stop-after plan` also accepts the
`dogpdkb-admitted-export-1` handoff; the handoff remains raw evidence, not an
`apply` difference selection or a Graphify manifest. The receive path
revalidates the admission read-only through the shared `verify_admission`
checks: exactly one `recover-export` run, input bytes identical to the
run-recorded admitted SHA-256, an intact receipt chain, committed raw facts,
frozen versions and raw file digests. Verified rows become witnessed plan
inputs and enter the existing compare/plan/OCR/Corpus flow unchanged; the
independent OCR decision stays inside the plan (`ocr_policy` and per-row
`action`/`retry_ocr`). The initial receive and preparation validate the whole
set before the run writes any state, so scope, identity, version, membership,
metadata or SHA drift refuses there. Drift detected later — the admission is
revalidated before resumed OCR or Corpus publication — refuses the run
without rolling back facts the run already persisted (comparison records,
prepared witnesses); start a new run instead. This path never constructs an
export request or invokes Skylark. Plain `dogpdkb-differences-2` inputs are
unaffected.
