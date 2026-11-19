# Apply and sync contract

`dogpdkb.apply.apply_run(settings, book, store, run, differences=None, *,
stop_after=None, resume=False)` consumes a frozen discover difference object
(or its JSON path). The caller holds `store.lock()`. Returns
`{exit_code: 0|1|2|130|143, paused: bool, manifest: absolute path|null}`.
`stop_after="plan"` prepares all raw downloads and comparison reports, saves a
fixed plan, and returns 0 without OCR or corpus writes. Resume uses the same run
and validates its saved inputs; it never downloads missing plan inputs.
Each raw input is checked against its saved byte copy before and after its OCR
call, and all plan inputs are checked again before corpus publication. A changed
in-flight OCR input records an unknown outcome and requires a new plan.
`prepared-inputs.json` freezes each completed/reused download before comparison;
pre-plan resume validates those inputs rather than overwriting their witnesses
or downloading missing prepared content. Only unstarted exports proceed.

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
published Markdown witness under `state/<book>/inputs/<document-id>/`. Graph submission can compare
its bytes directly with the corpus file to reject changed/missing frozen input;
no additional hash/fingerprint mechanism is introduced. `baseline_version` states
the version used for body comparison. A reusable body does not certify a missing
or older uncompiled graph version; graph policy must account for that difference.

Export invokes Skylark for each document and waits for all selected exports before
comparison. OCR freezes one policy and one mdocr plan for the whole selected batch.
The default is LLM via OpenAI Pi OAuth (`openai/gpt-6.1-terra`, medium, concurrency
3); `[ocr].mode = "paddle"` is an explicit override. `[ocr].request_budget` is a
nonnegative OCR admission limit for the batch, independent of scoring. Omission
means unlimited requests; zero allows cache hits but no new requests. The native
journal, not the absence of a result, is the authority for continuing a running
batch. Failed and unknown same-version requests remain suppressed. Unattempted
rows publish usable raw Markdown and are eligible for a later batch; verified OCR
added later can make the document eligible for compilation.

Tool originals live under `state/<book>/ocr/<run>/`: request mapping, plan, result,
journal, staged Markdown, exact raw Markdown and images, output, and OCR blocks.
The tool-facing Markdown can relocate catalogued local targets. Links are rewritten
only in a separate publication copy, whose digest is recorded separately from the
mdocr output. A report must bind the plan and its complete identity set; an invalid
row does not invalidate its verified peers. Publication and evidence remain usable
when completed/partial sync cleans its own wash. Failed wash artifacts remain
available until explicit purge.

A plan freezes `previous_corpus` and `previous_graph`; a difference freezes its
`local` facts. The compile manifest carries `previous_graph` through to graph
submission. Resume accepts the requested success version or those exact
previous facts, and refuses another intervening success. Opaque versions are
never lexically ordered. Timestamp ordering applies to chronologically meaningful
versions; a manually supplied opaque update without frozen facts requires fresh
discovery.
Provably older timestamps are refused even with matching frozen facts. Day-only
versions compare business dates in UTC+8, so same-day precise versions remain
eligible for comparison.
