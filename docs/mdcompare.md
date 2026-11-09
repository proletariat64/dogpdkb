# mdcompare: body knowledge and offline content comparison

For knowledge-base deduplication, use `mdcompare knowledge`. It reports reusable
body units and actual additions or possible revisions, with source locations.
Images and OCR are completely excluded from that command and cannot block body
results. See the [body knowledge guide](mdcompare-knowledge.md).

```bash
mdcompare knowledge --baseline baseline.json --incoming incoming.json \
  --output knowledge.json --report knowledge.md
```

The remainder of this page documents the separate, strict `compare` command and
its existing schema 1.0 contract. Its whole-file actions and resource/OCR gates
are not the knowledge command's acceptance criteria.

`mdcompare compare` compares captured baseline and incoming Markdown by knowledge
base plus document ID. It reports text, resource, metadata and OCR reuse evidence
separately. It never downloads, runs OCR, syncs, publishes or advances a baseline.
Comparison advice is not proof of business correctness or production publication.

## Install and run

The command is included in the dogpdkb wheel alongside dogpdkb and mdocr. It runs
independently of their services, login sessions and configuration. From this source
checkout, install with `uv tool install . --python python3.12`; alternatively install
the built dogpdkb wheel into a Python 3.10+ environment. The managed curl installer's
public command links are unchanged by this feature; this guide uses the wheel/tool
entrypoint. Development can reuse the existing worktree environment:

```bash
PYTHONPATH=src .venv/bin/python -m mdcompare --version
mdcompare compare --help
mdcompare compare --baseline baseline.json --incoming incoming.json \
  --output decisions.json --report review.md --include-evidence
```

Create the report parent directories first. JSON is the authoritative automation
result. Body evidence is omitted unless `--include-evidence` is explicit; enabling
it permits bounded snippets in both requested reports, never ordinary logs.

| Exit | Meaning |
| --- | --- |
| 0 | Every incoming document completed without unknowns or item errors; ordinary differences are allowed. |
| 2 | A complete report exists, with unknowns or item errors. |
| 1 | Invalid manifests/identity or a global read/publication failure; do not consume a new report as complete. |

Nonzero never means unchanged. Reports use private permissions and atomic file
replacement. Multiple outputs are staged before publication and caught failures
roll back; separate files are not a crash-proof filesystem transaction. Check the
exit code and use immutable inputs. See [input safety](mdcompare-input-safety.md).

## Input schema 1.0

Each manifest has this shape. Make a separate incoming manifest with its captured
new path and metadata; filenames and directories are never document identity.

```json
{
  "schema_version": "1.0",
  "documents": [
    {
      "knowledge_base": "example-book",
      "document_id": "42",
      "content_kind": "raw",
      "path": "old/document.md",
      "metadata": {
        "name": "Transfer limits",
        "logical_path": "Payments/Limits",
        "source_ref": "example-book:42",
        "sync_timestamp": "2026-11-07T00:00:00Z"
      },
      "resources": []
    }
  ]
}
```

Paths are absolute or relative to the containing manifest. ID, knowledge base and
path must be nonempty strings. Metadata name, logical_path and source_ref are
required strings; sync_timestamp is optional. Duplicate scoped IDs and JSON keys
are rejected. The same numeric document ID in another knowledge base is distinct.
An empty incoming list is valid. Baseline-only documents are reported as absent
from this incoming set, never inferred remote deletions. Pure sync timestamp
changes are visible independently and do not trigger content updates.

`resources: []` means no evidence was supplied; it is not proof that a document
contains no images or embedded/downloaded dependencies. Declaration IDs pair
resources independently of URLs. Use local captured byte paths or the narrow
explicit immutable-version contract; a URL alone cannot prove unchanged bytes.
Resource order, count, titles, alt text and link meaning remain visible. See
[resource schema and limits](mdcompare-resources.md).

Optional `ocr` supplies captured successful baseline artifacts and desired incoming
prepared JPEG/configuration evidence. Original resource equality cannot establish
OCR reuse: source correspondence, usable artifacts, JPEG bytes, preprocessing and
OCR configuration must independently agree. Missing success/usable output and
unverifiable provenance have different outcomes. See [OCR schema and action
priority](mdcompare-ocr.md).

Declare old OCR-enriched files as `historical_enriched`, never raw. Without retained
raw and complete verifiable provenance, historical comparisons remain UNKNOWN,
including equal or contained text. Observed differences are still explained as
asymmetric input-stage differences. [Historical input schema](mdcompare-history.md)
describes the narrowly supported exact reconstruction proof; it does not recover
missing original text or certify publication.

## Output schema 1.0

Top-level fields are `schema_version`, selected `rules`, `results`, `baseline_only`,
`summary` and `errors`. There is exactly one result per incoming document in stable
scoped-ID order. `summary.total` and the six `summary.actions` counts conserve that
set. Every result includes scoped identity, `text`, `resources`, `metadata`, `ocr`,
`action` and sorted `reason_codes`. Dimension status is UNCHANGED, CHANGED, NEW,
NOT_APPLICABLE or UNKNOWN; unknown is never converted to unchanged.

Text includes `byte_equal`, `comparison_complete`, `evidence_truncated` and bounded
`differences`. Historical text additionally declares comparison scope, observed
status and provenance verification. Each evidence region has one-based old/new
line locations, removed/added text and optional character insert/delete details.
Clipped evidence is explanatory, not a complete patch or an equality proof.
Resources and OCR include independent per-resource items and processing lists;
confirmed local changes survive an aggregate UNKNOWN. Read failures retain these
same dimension keys: unverified `byte_equal` and `references_changed` are null,
`comparison_complete` is false, and evidence/work lists contain only confirmed
items (possibly empty). Empty lists or null facts never establish no dependencies
or unchanged content. Metadata includes changed meaningful fields and a separate sync timestamp flag.

| Action | Required evidence / caller work |
| --- | --- |
| UNKNOWN | Any required provenance, dependency, read, stability or comparison prerequisite is unverified. Preserve known local changes and investigate the stated gap. |
| PROCESS_REQUIRED | New document or determinate missing/failed/invalid/incompatible required OCR; process as appropriate. |
| PROCESS_CHANGED_RESOURCES | Known added/changed resources; inspect resource and OCR processing lists as well as text/metadata changes. |
| UPDATE_TEXT_REUSE_OCR | Text changed and resources/OCR are verified reusable or not applicable; recompose the document. |
| UPDATE_METADATA_ONLY | Verified reusable content, but meaningful name/path/source metadata changed. |
| SKIP_VERIFIED | Text equal by bytes or the selected normalization contract, all resources verified unchanged, required OCR successful/usable/compatible, no meaningful metadata update. |

UNKNOWN takes priority. Otherwise new identity precedes resource processing, then
required OCR processing, changed text, metadata and verified skip. Advice does not
establish that an installed downstream mdocr supports per-image reuse.

## Normalization, budgets and diagnostics

Default normalization is identity. `--normalize-line-endings` opts into
`line-endings-lf` version 1, converting CRLF and lone CR to LF only. It does not
remove Markdown whitespace, links, HTML, punctuation, Unicode, table order or
negation. Myers shortest insertion/deletion differences explain changed UTF-8
text; no similarity threshold authorizes a skip and no persistent hash index is
created.

| Limit | Default | Exhaustion |
| --- | --- | --- |
| `--max-file-bytes` | 16777216 per Markdown/evidence file | UNKNOWN; never compare a truncated prefix. Manifests have a separate fixed 16 MiB global limit. |
| `--max-diff-work` | 2000000 line/character frontier and matching steps | UNKNOWN / DIFF_BUDGET_EXCEEDED. Exact or selected-normalized equality needs no diff budget. |
| `--max-evidence-chars` | 8000 per document | Display clipping only; evidence_truncated is explicit. |
| `--max-char-region` | 4096 combined removed/added characters | Omit optional character detail; keep the line comparison. |

File limits must be positive; diff/evidence/region limits may be zero. Resource and
OCR declarations/occurrences have explicit 10,000-item limits. Resource discovery
has a separate fixed cumulative syntax work estimate capped at 20,000,000 before
code masking and matching; exceeding it is RESOURCE_BUDGET_EXCEEDED. See the
[resource limits](mdcompare-resources.md) for its conservative calculation.
Prepared JPEGs also have a 20-million-pixel validation limit. Budget exhaustion never proves equality.

Reason codes are machine-readable identifiers; inspect dimension-specific reasons,
not just the final action. Common groups are:

- Input/global: MANIFEST_UNREADABLE_OR_INVALID, MANIFEST_SCHEMA_INVALID,
  DUPLICATE_DOCUMENT_ID, DUPLICATE_JSON_FIELD, MANIFEST_CHANGED, INPUT_READ_FAILED, INPUT_CHANGED,
  FILE_BUDGET_EXCEEDED, REPORT_INPUT_COLLISION, REPORT_WRITE_FAILED.
- Text: TEXT_BYTES_EQUAL, TEXT_BYTES_DIFFER, TEXT_NORMALIZED_EQUAL,
  TEXT_ENCODING_UNSUPPORTED, DIFF_BUDGET_EXCEEDED, NEW_DOCUMENT.
- Resources: RESOURCE_REFERENCE_UNMAPPED, RESOURCE_SYNTAX_UNVERIFIED,
  RESOURCE_DECLARATION_INVALID, RESOURCE_BYTES_UNAVAILABLE,
  RESOURCE_BUDGET_EXCEEDED.
- OCR: OCR_REUSE_VERIFIED, OCR_SUCCESS_RECORD_MISSING,
  OCR_SUCCESS_RECORD_UNSUCCESSFUL, OCR_CONFIGURATION_INCOMPATIBLE,
  OCR_PREPARED_IMAGE_CHANGED, OCR_PREPARED_IMAGE_INVALID,
  OCR_ARTIFACT_UNAVAILABLE, OCR_ARTIFACT_EMPTY and OCR_*_MAPPING_* or
  OCR_PROVENANCE_INCOMPLETE for unverified correspondence.
- History: HISTORICAL_RAW_UNAVAILABLE, HISTORICAL_UNMARKED_CONTENT,
  HISTORICAL_BLOCK_PROVENANCE_INCOMPLETE, HISTORICAL_RECONSTRUCTION_MISMATCH,
  HISTORICAL_PROVENANCE_VERIFIED.

Missing/unreadable dependencies, unsupported syntax and legacy source gaps are
expected UNKNOWN cases. Do not force a higher skip rate by changing input stage
declarations, cleaning business text or treating research classifications as CLI
gold labels. See the separate [real-file UAT record](mdcompare-uat.md).
