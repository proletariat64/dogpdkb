# Independent tool CLI contracts

These are generic tool interfaces. Book/version selection, plan decisions, agent
handoff, scheduling, and publication belong to the caller.

## mdocr batch

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
uses. Entries contain `resource_id` (target), `url`, `kind` (`image` or
`attachment`), and `path` when a local resource exists. Existing declared resource
evidence is preserved by target. Remote resources are never fetched. A document's
`resource_warnings` records parser uncertainty; unresolved syntax is not silently
claimed complete. Output must not replace an input document or the input manifest.
Exit 0: catalog written; exit 1: invalid/unreadable input or publication failure.

## Existing mdcompare reports

`mdcompare knowledge --baseline OLD --incoming NEW --output REPORT` compares body
knowledge only and ignores images/OCR. `mdcompare compare` with the same arguments
compares full resources and declared OCR evidence. Both accept optional `--report`
for human Markdown. Exit 0 is a resolved report, 2 is an unresolved report, and 1
is an invalid invocation/input. Reports, not exit 2 alone, carry per-document
classification and reason codes. See the tool's schema for complete reports.
