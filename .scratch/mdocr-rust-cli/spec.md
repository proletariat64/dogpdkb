# Rust mdocrr CLI product specification

Status: ready-for-agent

## Purpose and ownership

Deliver a portable, self-contained CLI for OCR of images referenced by Markdown.
An operator or upstream agent can discover the contract offline, supply an exact
batch, inspect what will be executed, run or resume it, and consume structured
results. An interactive operator uses the same execution contract as automation.

The caller plans the work. It selects and orders documents, selects images, grants
download permission, chooses output destinations, and owns business versioning
and downstream publication. It hands mdocrr a JSON work plan and optionally a TOML
file. mdocrr accepts the plan, freezes effective inputs and policy, executes OCR,
assembles outputs, and manages the batch workspace. An upstream orchestrator
invokes the public CLI and reads its documented artifacts. It delegates OCR
admission, concurrency, cache reuse, retries, and checkpoints to mdocrr.

This is the first standalone Rust product contract. Implement the commands,
flags, configuration keys, and schemas defined here. Reject undocumented
command forms and unsupported schema versions with a clear diagnostic. Start
workspaces and caches under the Rust contract.

## Command interface

| Command | Purpose |
| --- | --- |
| `mdocrr draft --input-single FILE --output-single FILE --plan FILE` | Write an editable work plan for one Markdown document. |
| `mdocrr draft --input-dir DIR --output-dir DIR --plan FILE` | Write an editable work plan for a recursive directory batch. |
| `mdocrr accept --plan FILE [--config FILE]` | Validate and accept the caller's plan, then return the frozen plan and its counts. |
| `mdocrr run --plan FILE [--config FILE] [--resume] [--retry-failed]` | Accept and execute a new batch or continue an existing batch. |
| `mdocrr cancel --plan FILE` | Record a cancellation and stop new request admission for that batch. |
| `mdocrr assemble --plan FILE --output-dir DIR` | Recover publishable Markdown from accepted OCR patches offline into a new directory. |
| `mdocrr reset --plan FILE` | Clear owned OCR state and outputs so the next run performs OCR again. |
| `mdocrr purge --plan FILE [-Y]` | Explicitly remove the named batch's workspace artifacts. |
| `mdocrr doctor [--config FILE] [--pretty]` | Diagnose local readiness and probe a configured LLM once. |
| `mdocrr check-mermaid FILE` | Check Mermaid fences in a Markdown file. Use `-` for stdin. |
| `mdocrr licenses` | Print notices for bundled assets. |
| `mdocrr --help`, `mdocrr COMMAND --help`, `mdocrr help all`, `mdocrr --version` | Discover the interface and identify the binary offline. |

`--plan` always names the caller's JSON work plan. For `draft` it names the file
to create. Application commands take paths relative to the caller's working
directory unless a contract for an authored field specifies a different base.

An upstream caller can run a whole batch directly or inspect an accepted plan
before authorizing OCR.

```sh
mdocrr run --plan work.json
mdocrr run --plan work.json --config paddle.toml

mdocrr accept --plan work.json --config paddle.toml
mdocrr run --plan work.json
```

### Draft

`draft` is a hand-authoring helper. It writes exactly one editable JSON file
and fails if the target already exists. It neither creates the workspace nor
starts provider or download activity. The two input/output shapes are mutually
exclusive. Directory mode recursively enumerates Markdown files in deterministic
relative-path order and records relative output paths that preserve the tree.
Default selection is all referenced images and remote downloads default to
disabled. Normalize paths relative to the location of the new work plan.
Single-file mode writes the same one-document schema an upstream caller would
write. There is one execution workflow for both single and batch use.

```sh
mdocrr draft --input-single source.md --output-single enriched.md --plan work.json
mdocrr run --plan work.json
```

Read `enriched.md` for the OCR content and `work.mdocr/result.json` for its
status, image outcomes, and evidence.

## Work plan JSON

Use a UTF-8 JSON object with schema version `mdocr-work-1`. The caller owns this
file. Reject unknown fields, invalid field types, and duplicate object keys.

```json
{
  "schema_version": "mdocr-work-1",
  "output_dir": "output",
  "documents": [
    {
      "path": "sources/report.md",
      "select_images": ["images/chart.png"],
      "download_remote": true
    },
    {
      "path": "sources/notes.md"
    },
    {
      "path": "archive/report.md",
      "output": "archive/report.md",
      "select_images": []
    }
  ]
}
```

| Field | Contract |
| --- | --- |
| `schema_version` | Required and exactly `mdocr-work-1`. |
| `output_dir` | Required directory path. Resolve relative values against the work plan directory. |
| `documents` | Required ordered array of at least one Markdown document. Preserve this order in results. |
| `documents[].path` | Required existing Markdown source. Resolve relative values against the work plan directory. |
| `documents[].output` | Optional path relative to `output_dir`. Default to the source basename. |
| `documents[].select_images` | Optional array of parsed target strings. Omission selects all eligible occurrences. An empty array selects zero. A populated list selects every occurrence of each listed target in this document. |
| `documents[].download_remote` | Optional boolean with default `false`. When true, authorize downloading selected HTTP or HTTPS images in this document. |

Resolve destinations and validate paths before provider calls. Reject duplicate
canonical inputs, duplicate output destinations, traversal or symlink escapes
from the output directory, and overlap between sources, output destinations and
workspace artifacts. Reject unknown targets in explicit image selection so a
typo cannot silently suppress intended work. Resolve relative image destinations
against the containing Markdown file. Selection keys use the resolved Markdown
destination before filesystem resolution, so a reference-style image uses the
destination from its definition. Document-level source metadata and downstream
publication policy remain with the caller.

## Configuration and portability

On first acceptance, an explicit `--config FILE` selects that TOML file.
The file must be present and valid. It is independent of any disk defaults.
Without `--config`, look for `default.toml` alongside the supplied work plan.
If absent, use compiled defaults. All omitted settings use compiled defaults.
Reject unknown or misspelled fields, invalid values, and settings for another
provider mode. Changing a compiled default takes a rebuild. Editing a local
`default.toml` changes defaults for future batches.

`doctor` has no work plan directory. Bare `doctor` checks compiled defaults.
`doctor --config FILE` loads the specified file with the same parser and
validation as `accept` and `run`.

### LLM example

```toml
mode = "llm"
model = "openai/gpt-6.1-terra"
effort = "medium"
concurrency = 3
request_budget = 100
admission_seconds = 900
retry_rounds = 0
mermaid_repair_rounds = 3
cache_dir = "cache"
prompt_file = "prompts/ocr.txt"
oauth_file = "credentials/pi-auth.json"
```

### Paddle example

```toml
mode = "paddle"
token_file = "credentials/paddle-token.txt"
# endpoint = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
```

| Key | Scope and semantics |
| --- | --- |
| `mode` | `llm` or `paddle`. Compiled default is `llm`. |
| `concurrency` | Maximum concurrent work from 1 to 100. Default is 3. LLM bounds active document pipelines with one request at a time per document. Paddle bounds active images. |
| `request_budget` | Nonnegative integer for the whole frozen batch. Omission means unlimited. Zero forbids new provider requests. Every durable admission counts, including retries and repairs. Cache hits count zero. |
| `admission_seconds` | Nonnegative finite number of seconds, default 900. Applies to each `run` after acceptance. In-flight requests continue to settle after the window closes. |
| `retry_rounds` | Nonnegative number of automatic retry rounds per image. Default is zero. |
| `cache_dir` | Optional persistent image cache. Omission disables persistent caching. Journal reuse is independent of this setting. |
| `model` | LLM only. An explicit `openai/…` or `openai-codex/…` route. Default is `openai/gpt-6.1-terra`. |
| `effort` | LLM only. One of `low`, `medium`, `high`, `xhigh`. Default is `medium`. |
| `prompt_file` | LLM only. Optional file. Omission uses the embedded prompt. Freeze the actual prompt content and its digest at acceptance. |
| `mermaid_repair_rounds` | LLM only. Nonnegative limit on repair rounds per image. Default is 3 and zero disables provider repair requests. |
| `oauth_file` | LLM only. Path to a Pi-format OAuth credential store. |
| `endpoint` | Paddle only. Optional HTTP(S) job endpoint. Omission uses the embedded public Paddle endpoint. |
| `token_file` | Paddle only. Path to a file containing the Paddle token. |
| `proxy_url`, `ca_file` | Optional explicit proxy URL and additional CA file. Use a URL without embedded credentials. Omission uses direct transport and embedded trust roots. |

Resolve every TOML file path relative to the TOML file containing it. Provider
credentials are required only when new provider work would be admitted. The
Rust runtime reads the configured credential file and handles OAuth refresh
natively during execution. Freeze the credential file reference, while permitting
credential rotation at that same location. Keep credential contents and proxy
authentication out of plans, results, and logs.

The executable embeds Markdown parsing, image codecs and renderer assets, fonts,
TLS roots, Mermaid validation, the default prompt, and English help. It invokes no
Python, Node.js, browser, Pi executable, Mermaid CLI, or external image converter.
The Linux x86-64 release must be a single statically linked executable. Resolve
configuration, trust overrides, prompts, credentials, proxy settings, and working
storage without system environment variables. Disable automatic environment
proxy discovery and select temporary paths explicitly. Provider calls and
authorized image downloads still require network access.

## Acceptance, image handling, and workspace

Given `work.json`, use the adjacent `work.mdocr/` directory. Generalize this
by replacing the final `.json` suffix of the work plan basename with `.mdocr`.
Require a JSON work plan filename and derive the workspace from the resolved
work plan path. The caller may inspect documented artifacts.

| Artifact | Purpose |
| --- | --- |
| `download.json` | `mdocr-download-1`. Work and document bindings, effective acceptance policy, and image records committed one occurrence at a time. |
| `plan.json` | `mdocr-frozen-plan-1`. Frozen request digest, effective configuration, canonical paths, planned document order, input hashes, selected image occurrences, and download results. |
| `journal.json` | `mdocr-journal-1`. Runtime and execution schema versions, bound plan hash, durable request admissions and outcomes, budget use, retries, accepted patches, output publication receipts, and cancellation state. |
| `result.json` | `mdocr-batch-result-1`. Batch status, ordered document outcomes, occurrence statuses, output hashes, and evidence references. |
| `images/` | Original bytes staged from authorized downloads or inline data URLs, bound to the frozen plan. |
| `evidence/` | Published per-image evidence and its source and output bindings. |
| `reset.json` | Durable cleanup state and cache-read bypass for the execution started after reset. |
| `.purged.json` | Purge seal with work identity and the path and SHA-256 of each owned published output. |

`accept` validates the manifest and TOML, parses documents, resolves and selects
images, stages permitted remote or inline image data, and commits the frozen
plan. It can fetch authorized image bytes. Provider inference and publication of
enriched Markdown start in `run`. A missing or invalid selected image is a
per-occurrence failure in the accepted plan. A malformed manifest, unreadable
source document, or path collision is a batch-level error. Acceptance reports
the document count and selected, unselected, staged, and failed image counts
with the frozen plan path. An accepted plan may contain failed occurrences.

### Download checkpoints

After validating the manifest, configuration, paths, and image selection, create
one `download.json` before staging starts. Bind it to the resolved work path
and normalized work digest, effective policy and config source, source Markdown
paths and SHA-256 digests, and ordered selected occurrences. Each image record
contains its occurrence identity, parsed source identity, and success or failure.
A success records the verified file path and SHA-256. Use a digest identity for
inline data and store no credential material.

For each selected image, validate the bytes, atomically write staged bytes into
`images/` when needed, then atomically replace `download.json` with the
updated record before emitting progress. Local image records bind the original
file. A retry of interrupted staging reuses a success only when the work,
document version, occurrence and source match and the recorded path still
holds a verified file with the recorded SHA-256. If a record is absent, failed,
or its bytes are missing or invalid, process that occurrence again under the
same authorization. An unrecorded file left by a crash never establishes a
successful checkpoint. A malformed checkpoint fails with a diagnostic.

Before freezing, verify the staged records and publish `plan.json` atomically.
The checkpoint supplies the saved policy during staging recovery; the frozen
plan is authoritative once present. Omission of `--config` uses that saved
policy. An explicit configuration must match effective values, resolved paths
and prompt content. Changes to the work, source Markdown, selected occurrence
order, or effective policy fail with a diagnostic.

After freezing, repeat `accept` to obtain the same plan. If a previously bound
remote or inline staged file is missing or damaged, recover only that file at
its recorded path and only if the validated replacement bytes match the frozen
SHA-256. A changed remote response fails with a drift diagnostic and leaves the
binding intact. A changed caller-owned local file also fails. Frozen failed
occurrences stay frozen until the batch is purged and reset. Serialize staging,
frozen-asset recovery, and publication under the workspace locks.

### Image syntax and loading

Authored Mermaid source, including fenced Mermaid blocks, remains part of the
Markdown text available to downstream semantic extraction. It is outside OCR
admission. `check-mermaid` can validate that text separately. The SVG rendering
requirement applies when a document references an exported flowchart as an image.

- Use `pulldown-cmark` as the Markdown parser. Image discovery and execution
  validation share one extraction implementation. Use image events and source
  offsets to identify each occurrence and its parsed destination. The parser
  is the sole source of Markdown image occurrences.
- Cover inline images, optional titles, angle-bracket paths, percent-encoded
  spaces, balanced parentheses, all three reference-style forms, and images
  inside Markdown links. Ignore escaped image syntax, inline code, fenced or
  indented code, and generated OCR blocks. Test the actual source range
  for collapsed references such as `![alt][]`, including the trailing `[]`.
- Support local relative paths, authorized HTTP(S), and Base64
  `data:image/...;base64,...` destinations. Decode data URLs locally, validate
  actual bytes using the supported image limits, and freeze a staged
  source and its SHA-256. A data URL never grants remote download permission.
  Use a safe reference to the staged original in output evidence. Avoid copying
  the data URL into logs or duplicating it in public reports.
- Support HTML `<img src="…">` embedded in Markdown. Consume the raw HTML events
  from `pulldown-cmark` and extract `src` using `html5ever`. Use the same
  resolved dependency as the Mermaid parser, initially version 0.40.1.
  Apply the same selection, download authorization, byte validation, and source
  binding as for Markdown image destinations. Preserve surrounding authored HTML
  and Markdown when placing the OCR result. No browser is needed.
- Local absolute image destinations and `file:` URLs are outside the supported
  input policy. Responsive image selection from `srcset` or `<picture>` and
  Obsidian embeds are outside the supported OCR syntax. Preserve these constructs
  and document their scope in help. Unsupported recognized destinations and
  malformed selected data URLs must have an explicit outcome.
- Publish a versioned OCR block bound to the original occurrence and image
  content. Preserve authored bytes outside the required edit. In linked-image
  cases verify that the edited Markdown retains the enclosing link and remains
  well formed. Keep original image provenance and exclude model-supplied
  reserved delimiters. A supported format such as PNG can be used as a local
  or Base64 source.
- Render ordinary SVG into PNG for LLM OCR using embedded `resvg` and fonts.
  For an exported flowchart, preserve readable labels and the visual evidence
  of arrow direction, branch conditions, and loops. Terra can summarize those
  relationships as Markdown for downstream semantic extraction. Retain the
  original SVG as evidence.
- The initial standalone release rejects SVG containing HTML `foreignObject`
  before provider submission with `svg_text_renderer_unavailable`. Document
  this limit and raster decoder coverage in help. Add HTML-backed SVG coverage
  once a maintained, embeddable Rust rendering library qualifies. Delegate HTML
  and CSS layout, text shaping, and painting to the library. Limit mdocrr code
  to integration, resource policy, and evidence binding. Qualify real Mermaid
  exports with CJK text, wrapped node labels, and edge labels. Verify placement,
  clipping, arrow direction and branch readability in the static executable
  with no browser, display server, GPU, system fonts, or network access.

## Run, cancel, and resume

`run` calls the same acceptance operation for a new batch and reuses an accepted
workspace. `run --resume` requires a frozen `plan.json` or an initialized
`download.json`; the latter allows resuming interrupted downloads before
acceptance finishes. Use the saved effective policy. Acquire workspace ownership
before dispatch. Verify plan binding and data witnesses. At most one live
scheduler may dispatch a given batch.

Persist admission to the journal before sending each provider request, then
persist its outcome and accepted patch before publishing Markdown. Track
unselected, unattempted, admitted, successful, reused, held, failed, and
unknown occurrences. Preserve unknown admissions when an interruption prevents
settlement. A new process can continue the same workspace, honoring the
cumulative request budget, retry counters, and accepted patches. It must not
repeat completed work. Cache keys bind source content and the effective OCR
policy and omit credentials and paths.

Automatic retry rounds are limited to the frozen policy and eligible failure
codes. An unknown outcome can be retried only for a classified connection
interruption when the frozen retry policy allows it. Record the previous
attempt as unknown and count every newly admitted attempt against the budget.
Unresolved admissions recovered from a crash remain unknown.
`--retry-failed` explicitly includes known failed work on a later run.
Optional Mermaid repair requests use the original image, previous patch and
parser diagnostics. Repair and retry rounds remain separately identifiable and
both consume the same request budget. Apply the embedded Mermaid parser and
response validation before publishing. Share provider-wide throttling and
authentication stops across the batch. A remote OCR request carries one image
and the effective prompt, plus repair evidence only when repairing.

`cancel` records durable cancel intent for the named accepted batch. Stop new
admissions after that intent takes effect and let in-flight requests settle
where possible within their normal deadlines. Preserve every outcome and emit
the final `canceled` state. Cancellation can be called again for that batch
without changing its outcome or deleting its workspace. It cannot retract work
already accepted by a provider. An unexpected interruption permits ordinary
`run` to continue. After explicit cancellation of a batch, require
`run --resume` to continue. The same configuration and cumulative budget apply.
Completed batches return their existing result without submitting work again.

## Results, recovery, reset, and purge

### Result and publication

Use the `mdocr-batch-result-1` JSON result object with `schema_version`, `plan_sha256`,
`status`, `causes`, `budget`, and ordered `documents`. The public batch states
are `running`, `completed`, `partial`, and `canceled`. `causes` is an array of
stable batch reason codes. `budget.requests` is cumulative admissions and
`budget.request_limit` is the configured limit or null.

Document rows contain `document_id`, `path`, `status`, `output`, and ordered
`resources`. Each resource has an occurrence `id`, a `resource_id`, a `status`,
and a `reason_code` when applicable. A completed row binds `output` and
`output_sha256`. Its `ocr` evidence binds effective settings, original image
bytes, and OCR block bytes. An incomplete row includes a null `output` and
explicit per-occurrence reasons. Never publish an incomplete document as
complete. Other documents whose selected occurrences are all satisfied can
publish independently. A document with zero selected images can complete
without provider traffic. Use a digest-based `resource_id` for inline Base64
in public reports and use the staged original as the source witness.

Before publishing each output, persist its resolved destination and expected
SHA-256 as a journal publication receipt bound to the frozen plan. Publish
output files and evidence atomically. On resume, verify existing
immutable output and evidence before accepting them. Refuse drift and preexisting
unowned destinations. The receipt lets cleanup recognize an output even if the
process stopped after writing it and before updating the result.

### Assemble

```sh
mdocrr assemble --plan work.json --output-dir recovered/
```

Use `assemble` to retrieve already completed work without continuing OCR. For
example, if A has all patches in the journal and the process crashes while B is
in flight, assembly can write A even when no output for it was published. It
reports why B is incomplete. To finish B, the operator uses `run`.

Resolve the frozen plan, journal and producing runtime version from the
workspace. Bind assembly to the frozen plan and a stable, verified journal.
Require a new output directory outside the original workspace and protected
paths. Require the active run to settle before assembly. Write recovered
Markdown, corresponding evidence, and `assembly.json` with schema
`mdocr-assembly-1` into the new directory.
Record the frozen plan hash, original journal hash, runtime version, and
ordered document outcomes. Preserve original inputs, workspace, outputs, and
budget. The command works offline with provider credentials unavailable.
A crash-only admission remains unknown in the assembly report.

### Reset

```sh
# Keep verified downloads and perform OCR again.
mdocrr reset --plan work.json
mdocrr run --plan work.json

# Discard staged bytes and perform downloads and OCR again.
mdocrr purge --plan work.json -Y
mdocrr reset --plan work.json
mdocrr run --plan work.json
```

Reset acts only on the named owned workspace. Acquire the execution and control
locks and reject an active writer. Show the outputs and OCR state that will be
removed and ask for confirmation before changing state. A decline leaves the
workspace intact. Warn when the old journal or purge seal records unresolved
provider requests, since a new OCR submission can repeat a request and its cost.
Report the cleanup scope and unresolved-request warning in the reset summary.

For an unpurged workspace, keep its saved policy, frozen plan if present,
`download.json` and staged `images/`. Clear the OCR journal, result, cancel
marker, and old OCR evidence. Remove prior published Markdown only when its
resolved path and current SHA-256 match an owned publication receipt. For a
purged workspace, take the receipts from its purge seal and verify the seal's
work path and digest. A caller-declared output path alone provides no ownership.
Validate all destinations before cleanup and refuse a changed or unowned file.
Missing outputs are already clear. Never delete source Markdown, caller-owned
images, recovery directories, or whole output directories.

Persist reset intent and its cleanup targets before deleting anything. If
cleanup is interrupted, `run` refuses the unfinished reset; repeating `reset`
finishes cleanup from the saved intent. Clear the purge seal only after its
outputs have been handled and durable reset state prevents old OCR replay.
After reset of a purged workspace, the next run uses normal fresh acceptance
and stages all selected images again under their download permissions.

The next execution starts with a fresh journal and zero used OCR budget.
It bypasses persistent OCR cache reads for all jobs, including on continuation
after interruption, cancellation, or a partial result. Verified download
checkpoints remain usable when retained. Clear the bypass only after successful
batch completion. Keep shared cache contents and the regular cache-write
policy. Ordinary runs outside a reset execution keep the existing cache rules.

### Purge

`mdocrr purge --plan work.json` explicitly discards both download state and OCR
state. Normal completion, cancellation, and recovery perform no automatic purge.

- A `completed` batch gets one `Y/n` confirmation.
- Any other state gets the same first confirmation and a second `Y/n` after
  a warning that reports the state and the loss of journal, evidence, and resume
  ability. The warning calls out unresolved provider requests if present.
- `mdocrr purge --plan work.json -Y` bypasses all confirmations regardless of
  batch outcome. Use it for intentional noninteractive deletion. Without it,
  an unavailable interactive input must not imply consent.
- Obtain exclusive workspace ownership before deleting. A live writer must not
  race cleanup. Deletion covers `download.json`, the frozen plan, journal,
  result, cancel/reset state, staged images and evidence. Preserve the caller's
  work plan, source Markdown, original local images, config, previously
  published Markdown and shared cache.
- Before removing the journal, durably save the work identity, unresolved-request
  indicator and owned output receipts in the purge seal. Repeating purge
  preserves those receipts. A subsequent `run` reports `purged` until the
  caller explicitly resets the workspace. Without the seal or another owned
  publication receipt, reset cannot remove existing destinations.

## Diagnostics and help

```sh
mdocrr doctor
mdocrr doctor --config provider.toml --pretty
```

`doctor` checks configuration, prompt and CA readability, credentials, and
embedded Markdown, image, and Mermaid capabilities. Bare doctor and Paddle
mode perform local checks only. LLM mode with usable OAuth and a nonzero or
omitted request budget sends one text-only PING through the configured route,
model, and effort. A completed response with nonempty assistant text passes.
The probe uses a 20-second deadline and does not retry, refresh OAuth, create
a workspace, or consume batch budget. The provider may meter the request.

The JSON summary includes `config_source`, `mode`, `checks`, and
`network_checked`. The `probe` reports HTTP, remote credential acceptance,
and model results separately. `--pretty` indents the same JSON object. Exit
`0` means the probe passed or local checks passed with an intentional skip.
Exit `1` means invalid configuration, and `2` means a local check or the
attempted probe failed.

Help is progressive, embedded, offline, and English-only. Root `-h` and
`--help` explain purpose, list commands, and provide a first-use example.
`mdocrr COMMAND --help` describes that command's arguments, defaults, path bases,
effects, exit statuses and typical examples. `mdocrr help all` prints the full
English manual, including work plan fields, config schema, artifacts, examples,
logging and recovery. Help, version, and licenses execute without config,
credentials, workspace changes, or network access.

## Process output and monitoring

Operational commands use stdout for one final JSON summary of schema
`mdocr-summary-1`. Include `command`, `status`, applicable artifact paths,
and counts. Use `result_path` for run and `assembly_path` for assembly.
On failure emit a stable `reason_code` when a structured failure summary is
available. Always preserve valid JSON for emitted summaries. Keep full document
and evidence detail in the result artifact. Prompts and progress belong on
stderr. Help, version, and licenses print human-readable text to stdout.

Use timestamped, line-oriented, immediately flushed stderr for progress.
Include the phase and relevant counts for document progress, in-flight
requests, and cumulative requests and budget. Emit events for acceptance,
admission, settlement, retries, throttling, budget or deadline stops,
cancellation, and publication. During long waits emit periodic status even when
no provider event arrives. Use English messages and stable reason codes.
Avoid credentials, data URLs, image bytes, raw provider responses, terminal
control sequences, and progress that rewrites prior lines.

A completed run can end with the following stdout record. Paths in summaries
are absolute so callers can open them from a different working directory.

```json
{"schema_version":"mdocr-summary-1","command":"run","status":"completed","result_path":"/jobs/example/work.mdocr/result.json","counts":{"documents_total":3,"documents_completed":3,"requests":8}}
```

One stderr progress line during that run could read as follows.

```text
2026-11-23T09:15:07Z phase=ocr event=settled documents=1/3 in_flight=2 requests=6/100
```

```sh
mdocrr run --plan work.json > summary.json 2>> mdocrr.log
tail -F mdocrr.log
```

Run the two lines in separate terminals for live monitoring. The caller owns the
log file and retention. Resume can append to the same log. The workspace
`result.json` is the durable artifact. The shell output summary is independent.

| Exit | Meaning |
| --- | --- |
| `0` | Requested action completed. For `run` or `assemble`, all planned documents completed. `accept` may report individual image failures in a successfully frozen plan. |
| `1` | Configuration, input, workspace binding, I/O, or other fatal action error. |
| `2` | Usable partial output or an actionable diagnostic outcome, such as a failed local readiness or Mermaid check or a declined purge or reset. Use the structured status to discriminate. |
| `3` | A batch is canceled and requires explicit resume. A successful `cancel` request itself exits zero. |
| `64` | Invalid CLI usage. |

Preserve standard shell signal behavior for interrupted processes. Durable
journal state, rather than a missing final stdout record, determines recovery.

## Acceptance tests

Test the standalone release through public commands against temporary
workspaces, real Markdown and image fixtures, and loopback provider fixtures.
Keep installed-host and live-provider acceptance distinct from this suite.

1. Draft one document and a nested directory. Check deterministic work JSON,
   correct outputs, and zero workspace writes or network access.
2. Verify direct run and explicit accept followed by run produce the same frozen
   inputs and ordered outcomes. Reaccept unchanged work without network or
   artifact replacement. Reject changed work, mismatched explicit strategy,
   missing files, duplicate inputs, output collisions, and path/symlink escape.
3. Cover built-in defaults, neighboring defaults, explicit TOML isolation,
   invalid fields and mode combinations, and relative config paths. Run from a
   different working directory with environment variables stripped or set to
   deliberately conflicting values.
4. Exercise the Markdown parser with boolean download permission, all selection
   forms, Unicode, parentheses, reference styles, escapes, code, linked images,
   HTML `img` with `src`, and valid or malformed Base64. Cover quoted HTML
   attributes, multiple images in a block, and preserving surrounding HTML.
   Assert outbound request counts, original witnesses, actual published text
   and valid Markdown link structure. Verify ordinary SVG produces a readable
   PNG without Chrome, and HTML-labeled SVG fails before submission.
5. With loopback Paddle and LLM fixtures, check bound evidence and exact admission
   counts, concurrency, throttle behavior, cumulative budgets, cache reuse,
   retry bounds, Mermaid repair, and no inference on zero budget. Verify no
   provider calls before durable admission.
6. Kill a process after one accepted patch and while a second request is in
   flight. Verify offline assembly recovers completed work into a new directory,
   marks unknown outcomes, leaves original files byte-identical, and does not
   replay the interrupted request on run. Separately test explicit cancellation,
   settling in-flight requests and requiring `--resume`.
7. Verify purge after completion prompts once, incomplete purge prompts twice,
   `-Y` prompts zero times, declining preserves artifacts, and cleanup only
   removes the named workspace while preventing accidental reexecution.
8. Read root, command and full English help with no config or credentials.
   Check version and embedded licenses. Verify that diagnostics and help cause
   no external communication or workspace writes. Check the documented exit
   statuses and JSON schemas. Verify a command form outside the public command
   table or an unsupported input schema receives an explicit diagnostic.
9. Capture stdout and stderr from a long running fixture. Verify progress
   arrives before completion, is line-oriented, and appends across a resume.
   Verify stdout is one valid JSON record and neither stream exposes secrets.
10. Inspect the x86-64 Linux release for dynamic runtime dependencies and run it
    with no executable search path, a stripped environment and no source
    checkout. Run independent live provider smoke checks only as a separate
    acceptance step with authorized credentials and nonproduction images.
11. Interrupt staging before `plan.json`, then `run --resume`. Verify zero
    repeat GETs for valid recorded files, and fetches only for missing,
    corrupt, failed or unrecorded occurrences. After freezing, recover a lost
    staged file only if the replacement matches the frozen SHA-256.
12. Reset and run against a warm OCR cache and old published outputs. Verify
    zero image GETs for retained valid files, fresh OCR admissions, fresh
    evidence and publication at the original destinations. Interrupt the
    reset and the new execution separately, verify safe continuation and
    persistent cache-read bypass. Reject active runs and changed or unowned
    outputs, and report possible repeated provider charges from unknown
    admissions.
13. Purge, reset and run the same work plan. Verify the seal blocks run until
    reset, purge retains marked published files, reset clears only recorded
    outputs with matching hashes, and the new execution fetches authorized
    remote images again. Reject a replacement caller file at an output path,
    and preserve already published files when purge is run without reset.

## Deployment boundary

Production adoption uses an explicit upstream integration change that submits
the Rust work plan and consumes the Rust result. Publishing, installing, and
changing production routing are separate delivery tasks.

For work that requires HTML-backed SVG labels, the upstream orchestrator selects
the Python workflow before batch execution. Its isolated Chrome or Chromium
renderer makes the PNG and retains the original SVG as evidence. Each batch has
one execution owner, plan, journal, and budget. The Rust CLI runs as the standalone
product defined above.
