# Historical Markdown input contract

`historical_enriched` is a source stage, never an implicit raw snapshot. Without
verified provenance, its text dimension is `UNKNOWN` even when the observed files
have equal bytes or one contains the other. Missing raw evidence records
`HISTORICAL_RAW_UNAVAILABLE`; unmarked content additionally records
`HISTORICAL_UNMARKED_CONTENT`, and incomplete marked-block evidence records
`HISTORICAL_BLOCK_PROVENANCE_INCOMPLETE`.

With `--include-evidence`, bounded Myers differences describe the observed input
stages. `comparison_scope` names those stages, such as
`HISTORICAL_ENRICHED_TO_RAW`. `observed_status` is the observed byte/text result;
it does not establish that removed historical text was a source rule deletion.
`provenance_verified: false` keeps this distinction explicit. Normal text work
and evidence budgets apply. A complete observed comparison is not a complete
source-provenance proof. No report snippets appear on stdout/stderr.

## Verified source snapshots

A deliberately narrow provenance contract can verify a retained raw snapshot
against a historical file without guessing or cleaning its content. The caller
supplies this optional document `provenance` object:

```json
{
  "contract": "mdocr-raw-snapshot-v1",
  "raw_path": "old-raw.md",
  "blocks": [
    {
      "resource_id": "image-1",
      "raw_start_byte": 7,
      "raw_end_byte": 26,
      "prepared_image": "prepared.jpg",
      "source_reference": "prepared.jpg",
      "artifact_path": "ocr-text.md"
    }
  ]
}
```

The byte offsets are zero-based half-open ranges in the retained raw snapshot.
They must be ordered, nonoverlapping and nonempty. Each range must contain one
supported simple inline Markdown image, `![alt](target)`, whose target equals the
`url` of the matching image declaration identified by `resource_id`. The first
version deliberately leaves more complex image syntax unverified. Unsupported
syntax is not cleaned or silently accepted.

`prepared_image` must fully decode as a JPEG, using the same validator as OCR
reuse verification. JPEG header/marker verification alone is insufficient for
truncated files. Prepared images are limited to 20,000,000 pixels; decompression
warnings or over-budget images remain UNKNOWN with
`OCR_PREPARED_IMAGE_BUDGET_EXCEEDED`. `source_reference` must refer to that same
local file relative to the historical Markdown document and must be suitable for
the existing mdocr marker/link grammar. `artifact_path` contains the UTF-8 OCR
Markdown that was placed in the block; no OCR is executed. All evidence file paths
are resolved relative to the manifest directory, and all reads use the bounded,
read-only input reader. Missing or unreadable evidence retains the observed
stage-to-stage diff and records `HISTORICAL_EVIDENCE_READ_FAILED` plus the input
reader's reason code.

The comparator constructs mdocr's canonical marked block from each retained OCR
artifact and prepared-image reference, replaces only the declared image byte
ranges in the raw snapshot, and directly compares the entire reconstruction with
the historical bytes. Every byte outside those transformations must agree. Extra
unmarked transcription, missing blocks, changed artifacts or altered source text
cause `HISTORICAL_RECONSTRUCTION_MISMATCH`; a marker alone proves nothing. Nested
OCR markers in either the retained raw or the OCR artifact are unsupported.
Reconstruction is bounded by `--max-file-bytes`, including all generated blocks.
Both the source block map and resource inventory are limited to 10,000 records
before any per-block decoding or reconstruction; overflow records
`HISTORICAL_PROVENANCE_BUDGET_EXCEEDED`. Repeated prepared-image paths are decoded
only once per mapping verification while every evidence read retains the input
reader's fixed-view checks.
An empty block list is valid only when the complete historical bytes equal the
retained raw bytes.

This contract trusts the caller's declaration that the retained snapshot and
mapping are authentic source records, just as ordinary raw inputs trust the
caller's declared source stage. It does not recover a missing raw snapshot or
certify a successful production publication.

After complete verification, `comparison_scope` is `VERIFIED_RAW_SNAPSHOTS`,
`provenance_verified` is true and `HISTORICAL_PROVENANCE_VERIFIED` is recorded.
Text comparison and resource discovery use the verified raw bytes, preserving
original image occurrences, alt text and source URLs. Real source deletions are
therefore retained in the raw-to-raw diff rather than dismissed as OCR noise.
This provenance proof does not authorize OCR reuse: original resources, prepared
images, preprocessing, OCR configuration and successful available artifacts still
need their independent resource/OCR checks.
