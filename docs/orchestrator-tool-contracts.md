# Independent tool CLI contracts

These are generic tool interfaces. Book/version selection, plan decisions, agent
handoff, scheduling, and publication belong to the caller.

## mdocr plan and run

The active interface is `mdocr plan --mode paddle|llm --input FILE --output FILE --plan PLAN`,
then `mdocr run --plan PLAN --result RESULT`. For batch inputs, use `--list` (JSON array
of Markdown paths resolved from the list) or `--directory` (recursive Markdown paths)
with `--output-dir`. See `mdocr plan --help --full` and [OCR runtime prerequisites](mdocr-runtime.md).
The orchestrator explicitly chooses Paddle and uses a single input plan per document. It binds the
result to the plan SHA-256, the planned source and destination, and the SHA-256 of output/evidence.
It does not import the OCR implementation. `--selection` is a separate optional map keyed by
absolute input path; selected image hashes are bound to each occurrence. Planning may download
selected remote images only with `--download`; it performs no OCR.

`mdocr-result-2` contains `plan_sha256`, `status`, request/reservation state,
concurrency transitions and ordered document rows. Complete rows have `output`,
`output_sha256`, and `mdocr-ocr-2` evidence containing effective mode configuration,
resource IDs, and original image and OCR-block paths/byte digests. LLM evidence also
has a context digest. Failed/held/unknown rows cannot claim a completed Markdown
output. The internal plan-side journal prevents resubmission if result mirroring was
interrupted. Exit 0 means complete, 2 incomplete, and 1 fatal invocation or artifact error.

## Independent legacy mdocr batch command

```
mdocr batch --manifest input.json --output-dir output --report report.json \
  --cache-dir cache [--download] [--endpoint JOB_URL] [--max-seconds 900]
```

Input is JSON with `schema_version: "mdocr-batch-input-1.0"` and `documents`:

```json
{"schema_version":"mdocr-batch-input-1.0","documents":[
  {"document_id":"opaque-id","path":"raw.md","resources":["image.png"]}
]}
```

Document IDs must be unique, nonempty strings. Paths resolve relative to the
manifest. `resources` is an optional list of image targets (decoded, without URL
fragment): omitted selects every supported inline Markdown image; `[]` selects
none. Selection controls new OCR calls. Existing successful cache entries may be
reused for unselected images. HTML/reference image syntax is reported as a warning
and retained. Output paths are reported; callers must not guess filenames.

The report has `schema_version: "mdocr-batch-1.0"`, `status` (`completed` or
`partial`), `counts` (resource status counts), `causes` (shared stop reason codes),
and `documents`. Each document has `document_id`, absolute `path`, `output`
(absolute enriched Markdown path, or null), `status` (`completed`, `partial`, or
`failed`), `warnings`, and `resources`. Each resource has `resource_id`, `status`
(`success`, `reused`, `failed`, `unattempted`, or `unselected`), and optional
`reason`, `source_path`, `prepared_image`, and `artifact`. Repeated references use
one resource record. Failed/unattempted/unselected images remain in Markdown.
A document read failure is represented in its `error` field and does not stop
other documents.

Each document also has `ocr`, an independent `prepared-jpeg-ocr-v1` evidence object
with `preprocessing`, `configuration`, and successful `images`. Successful image
records use status `success` (including reused results) and the same absolute
`source_path`, `prepared_image`, `artifact` paths. `source_path` holds retained
original bytes; `prepared_image` is the normalized JPEG; `artifact` is a complete
marked mdocr OCR block linked to that JPEG. Enriched document blocks use absolute
prepared-image paths so callers can relocate the returned Markdown. Callers can map resource `path` to
`source_path` for mdcompare without parsing a block or an image.

`PADDLEOCR_TOKEN` is needed only for a new OCR call. `--endpoint` overrides the
Paddle jobs URL (for compatible gateways and local acceptance servers). A shared
auth/quota failure marks the triggering image failed and later selected uncached
images unattempted. Other image failures are isolated. `--max-seconds` is a batch
wall-time budget checked before preparation and OCR; the client's own bounded
request/job deadlines still apply to an in-flight call.

Only successful OCR is cached. Cache reuse verifies current prepared bytes and
processing configuration; a URL alone never authorizes reuse. Cache entries and
report paths are tool-owned disk artifacts. Caller may retain the cache across
runs; deleting it forces fresh work. No caller should inspect its layout.

Exit 0: every selected resource succeeds or reuses. Exit 2: structured partial
report (failed/unattempted resources or documents). Exit 1: invalid manifest or
unwritable output/report, with a concise stderr diagnostic. CLI usage errors keep
mdocr's existing exit 2. Reports are atomically replaced.

## mdcompare catalog

```
mdcompare catalog --input descriptors.json --output manifest.json
```

Input is the existing mdcompare manifest schema (`schema_version: "1.0"`,
`documents` with `knowledge_base`, `document_id`, `path`, `content_kind`, and
`metadata`); `resources` may be omitted. Output is a full mdcompare manifest with
absolute document/resource paths and a `resources` list populated from Markdown
uses. Entries contain `resource_id` (target), `url`, `kind` (`image`, `attachment`
or `media`), and `path` when a local resource exists. Existing declared resource
evidence is preserved by target. Remote resources are never fetched. A document's
`resource_warnings` records parser uncertainty; unresolved syntax is not silently
claimed complete. Output must not replace an input document or the input manifest.
Exit 0: catalog written; exit 1: invalid/unreadable input or publication failure.

## mdcompare semantic

```text
mdcompare semantic --baseline OLD.json --incoming NEW.json \
  --output semantic-report.json --diff-output semantic-diff.json \
  [--recovery body-recovery.json]
```

Inputs are the existing descriptor schema1.0, paired by knowledge_base/document_id.
Optional descriptor provenance.version records the actual source version; it does
not establish equality. Filtering is offline: no model, login, image/OCR request
or compilation. New documents do not depend on unrelated baseline failures.

The semantic-1.0 report has scope authored_body and results with identity,
semantic_status, action, reason_codes, extraction.available and
comparison_complete. Legal routes are NEW/CHANGED/UNCERTAIN→COMPILE,
EQUIVALENT→SKIP, UNAVAILABLE→DEFER. Only paired contextual normalized sequence
equality proves EQUIVALENT. Changes include authored deletions, context/order,
conditions, numbers, tables and code. Ambiguous/incomplete alignment with usable
bodies compiles. Missing/unreadable/unscoped bodies defer individually. A reliable
empty body is explicit NO_TEXT evidence, not successful graph extraction.
Baseline-only identities are advisory and never remote document deletion.

Images/alt text and explicitly scoped generated OCR are excluded. Resource/OCR
unknowns are advisory and never veto body routing. Existing knowledge and strict
compare reports remain diagnostics, not substitutes for this routing contract.

mdcompare-semantic-diff-1 indexes all expected documents through relative evidence
paths under its private evidence_directory. Preserve the directory with the index.
Each sidecar contains full old_units/new_units, input paths/metadata/versions,
context/original line coordinates and changes. Operations add/delete/modify/move/
equal/uncertain use zero-based old_ref/new_ref, null for an absent side. Full
machine evidence is never clipped by a human excerpt limit. Per-document streaming
and canonical references avoid corpus-sized JSON memory materialization. Unique
evidence generations keep previous indexes valid; obsolete standalone generations
require explicit cleanup, not silent history deletion.

Optional mdcompare-body-recovery-1 has documents with knowledge_base, document_id,
side (baseline/incoming), source_copy and ordered insertions[{byte_offset,text}].
source_copy resolves from the recovery JSON and must equal original source bytes.
Offsets address original UTF-8 bytes. Only marker/newline insertions are accepted;
no arbitrary content replacement/removal or automatic heuristic repair. Projection
is in memory. Provenance retains original offsets/source_line; units retain
original start_line/end_line and repaired projected_start_line/projected_end_line.
A repair can establish availability but never automatically establishes equality.
Invalid whole schema is error1; invalid per-document replay/witness defers that
document. Runtime snapshot drift refuses stale equality/publication.

Exit0: complete routing including uncertain compilation; exit2: complete report
with DEFER; exit1: invalid inputs/arguments or global publication failure. Outputs
cannot alias inputs, manifests or declared repair witnesses. Requested private
reports are staged together; caught ordinary failures roll back replacements.
A failed invocation must not make an old report appear newly complete. Full
schema/example is installed in `mdcompare semantic --help --full`.

## Existing mdcompare reports

`mdcompare knowledge --baseline OLD --incoming NEW --output REPORT` compares body
knowledge only and ignores images/OCR. `mdcompare compare` with the same arguments
compares full resources and declared OCR evidence. Both accept optional `--report`
for human Markdown. Exit 0 is a resolved report, 2 is an unresolved report, and 1
is an invalid invocation/input. Reports, not exit 2 alone, carry per-document
classification and reason codes. See the tool's schema for complete reports.
