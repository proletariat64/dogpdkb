# Apply and sync contract

`dogpdkb.apply.apply_run(settings, book, store, run, differences=None, *,
stop_after=None, resume=False)` consumes a frozen discover difference object
(or its JSON path). The caller holds `store.lock()`. Returns
`{exit_code: 0|1|2|130|143, paused: bool, manifest: absolute path|null}`.
`stop_after="plan"` prepares all raw downloads and comparison reports, saves a
fixed plan, and returns 0 without OCR or corpus writes. Resume uses the same run
and validates its saved inputs; it never downloads missing plan inputs.

Public CLI: `dogpdkb apply --config FILE [--book KEY] --input DIFFERENCES
[--stop-after plan]`; resume: `--resume RUN` instead of --input.
`dogpdkb sync` calls discover → apply_run → update_graph_run with the same
Settings/Book/Store/Run and held lock. Its `--stop-after plan` and `--resume RUN`
have the same semantics. Result stdout is one JSON object; child progress remains
on stderr.

Compile manifest at `run/manifest.json`, also `run.data["artifacts"]["manifest"]`:

```json
{"schema":"dogpdkb-compile-1","book":"funds","run_id":"run-...",
 "documents":[{"document_id":"42","version":{"value":"2026-11-12T10:00:00Z","utc":"2026-11-12T10:00:00+00:00","precision":"second"},
 "path":"/absolute/corpus/source.md","metadata":{"name":"Title","logical_path":"folder","source_ref":"https://example/source"},
 "action":"compile","reason_codes":["BODY_CHANGED"]}],"failures":[]}
```

Actions are `compile` (complete current source), `metadata` (maintain only source
metadata), and `reuse` (no graph content work). All paths are actual published
corpus Markdown. OCR partial documents remain eligible for compile; failures
persist separately and contribute exit2. Source metadata, corpus version and raw
version are recorded independently; Graphify owns graph success/version records.
A compile failure must not roll back published corpus. Adapter consumers validate
schema, book/run and available full-source paths, without inspecting foreign tool
internal formats. New/uncertain body knowledge compiles automatically. Image/OCR
changes can require compile even when body knowledge is reusable.

Each manifest document and corpus record also contains `input_copy`, an exact
published Markdown witness under `run/published/`. Graph submission can compare
its bytes directly with the corpus file to reject changed/missing frozen input;
no additional hash/fingerprint mechanism is introduced. `baseline_version` states
the version used for body comparison. A reusable body does not certify a missing
or older uncompiled graph version; graph policy must account for that difference.

Exports and OCR run one document per serial CLI invocation within their whole
batch stage. Only the current document is recorded as attempted, so interruption
cannot suppress later unstarted documents. A shared OCR cause stops new OCR
invocations; those documents still publish usable body and carry
`pending_steps: ["ocr"]`. Graph publication must retain their discover pending
fact. Failed/unknown actual attempts remain suppressed; unstarted work remains
eligible for an explicit resume or the next ordinary batch. `pending_metadata`
contains the frozen raw discover metadata and controls acknowledgement together
with the version. Export has no whole-batch timeout derived from Skylark's
per-request timeout; Skylark owns request/retry limits. OCR consumes its explicit
batch budget across serial documents.

A plan freezes `previous_corpus` and `previous_graph`; a difference freezes its
`local` facts. Resume accepts the requested success version or those exact
previous facts, and refuses another intervening success. Opaque versions are
never lexically ordered. Timestamp ordering is used only when a manually supplied
difference lacks frozen facts; an opaque update then requires fresh discovery.
