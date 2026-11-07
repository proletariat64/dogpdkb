# mdcompare OCR reuse evidence

Resource equality and OCR reuse are independent. mdcompare never runs OCR or
preprocessing. The opt-in `prepared-jpeg-ocr-v1` contract describes caller-captured
immutable evidence and the incoming desired configuration. A successful original
resource comparison alone cannot establish OCR reuse.

## Captured baseline and desired incoming inputs

Each document may supply an `ocr` object. The baseline records successful OCR;
the incoming side describes desired preprocessing/configuration and prepared
images, without needing to run OCR first:

```json
{
  "contract": "prepared-jpeg-ocr-v1",
  "preprocessing": {"name": "mdocr", "version": "1"},
  "configuration": {"engine": "example", "model": "version-1"},
  "images": [
    {
      "resource_id": "limits-diagram",
      "source_path": "captured/original.png",
      "prepared_image": "prepared/diagram.jpg",
      "status": "success",
      "artifact": "ocr/diagram.md"
    }
  ]
}
```

The incoming image entry needs `resource_id`, `source_path` and `prepared_image`.
Its `status` and `artifact` are not required or used: reuse selects the baseline
artifact. Every baseline success record must identify one stable resource ID,
original captured bytes, the prepared JPEG actually used and the OCR block.
Missing or duplicate IDs and unsupported contracts cannot establish provenance.
At most 10,000 image records are accepted per document; exceeding this limit is
UNKNOWN with OCR_RECORD_BUDGET_EXCEEDED.
The record is a capture attestation, not a signature: the caller is responsible
for its truth, original-to-prepared correspondence and immutable storage.
mdcompare checks supplied evidence; it does not authenticate the capture or
recreate the preprocessing operation.

Paths resolve against the containing manifest, except the OCR block's own image
reference, which resolves against the artifact's parent. All file reads use the
bounded fixed-input reader. The image's source bytes must match the `path` bytes
of its declared original resource. This positive OCR reuse contract requires
local original bytes even if the resource dimension has immutable version proof.
A version-qualified remote URL by itself cannot prove the local prepared image's
origin.

Preprocessing and OCR configuration are nonempty JSON objects. Compatibility is
exact canonical JSON equality, preserving types (`true`, `1` and `1.0` differ).
Callers must include every setting affecting the artifact, including tool/model
versions. mdcompare does not invent missing configuration defaults.

Both old and incoming prepared images must decode successfully as local JPEGs,
with a limit of 20 million pixels and the normal file-byte budget. Their bytes
must match exactly; visually similar images cannot authorize reuse. A changed or
invalid prepared image requires processing; exceeding validation budget is UNKNOWN.

The baseline artifact must be nonempty UTF-8 Markdown in the canonical mdocr
block form. Its begin marker and original-image link must identify the same
prepared JPEG, and it must contain nonempty OCR text:

```markdown
<!-- mdocr:ocr-begin source="../prepared/diagram.jpg" -->

Recognized Markdown text

> Original image: [../prepared/diagram.jpg](../prepared/diagram.jpg)

<!-- mdocr:ocr-end -->
```

Arbitrary unmarked text, incomplete or mismatched markers and source links cannot
establish the required mapping. Missing artifacts or empty OCR text are known
unusable. Missing success records or explicit failed/pending records require
processing. Read failures, incomplete or unverifiable provenance remain UNKNOWN.
Artifact content is never printed to ordinary logs or embedded in OCR status
records.

## Per-image result and action priority

The OCR dimension reports `status`, `reason_codes`, `items`, `reusable` and
`processing_required` resource IDs. Known reusable entries remain visible when a
different entry is unknown. Newly added or changed original images are processing
work and do not require obsolete OCR reuse proof. Unchanged images still require
the complete proof above. Removed images require no OCR. Attachments and media are
not image OCR inputs under this contract; documents without images need no
nonexistent OCR record. Unresolved resource dependencies keep applicability UNKNOWN.

Document action priority is deterministic:

1. Unknown text, resource or OCR prerequisite gives UNKNOWN, preserving known
   changes and per-resource/per-image work lists.
2. A new document gives PROCESS_REQUIRED, never a duplicate skip.
3. Known added or changed resources give PROCESS_CHANGED_RESOURCES. Also inspect
   OCR `processing_required`: an unchanged image can need processing separately.
4. Determinate missing, failed, invalid or incompatible required OCR gives
   PROCESS_REQUIRED.
5. Changed text with otherwise verified reusable inputs gives
   UPDATE_TEXT_REUSE_OCR (OCR can be not applicable).
6. Meaningful metadata changes alone give UPDATE_METADATA_ONLY; otherwise all
   verified equal/reusable inputs give SKIP_VERIFIED.

These are advice for a caller. They neither execute downstream processing nor
establish that an installed mdocr supports per-image reuse.
