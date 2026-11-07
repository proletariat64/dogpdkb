# mdcompare contract (schema 1.0)

Standalone invocation: `mdcompare compare --baseline baseline.json --incoming incoming.json --output decisions.json [--report review.md] [--include-evidence]` or `python -m mdcompare`.

Manifest root: `schema_version: "1.0"`, `documents: []`. Each document requires nonempty string `knowledge_base`, `document_id`, `path`; `content_kind` is `raw` or `historical_enriched`; `metadata` requires string `name`, `logical_path`, `source_ref` and optional string `sync_timestamp`; `resources` is an explicit list of objects. Optional `ocr` is an object, `provenance` an object. Paths are local, relative to each manifest's directory or absolute. Nested resource/OCR/history evidence contracts are owned by their feature tickets and must be validated before use. Unsupported or unverifiable evidence cannot authorize skipping.

Results contain schema_version, rules (name/version), results, baseline_only (identity pairs only), summary.total and summary.actions (all six action counts), errors. Every result has knowledge_base, document_id, text, resources, metadata, ocr, action, reason_codes. Each comparison dimension has status UNCHANGED, CHANGED, NEW, NOT_APPLICABLE or UNKNOWN; text also has reason_codes/differences, resources has reason_codes/items, metadata has changed_fields/sync_timestamp_changed, OCR has reason_codes. IDs and reason codes stay available without body evidence. No run timestamp affects decisions. Baseline-only does not mean remotely deleted.

Exit 0: complete no unknown; 2: complete with unknown or item errors; 1: global input/output failure. Reports are private and atomic; all source files are read-only. Supply immutable input snapshots. No comparison conclusion is business correctness or production publication proof.

## Internal extension boundaries

- `model.Document`: knowledge_base, document_id, content_kind, path (resolved Path), metadata, resources, base_dir, ocr, provenance. `identity` scopes ID; `resolve(str)` resolves evidence file paths against the manifest directory.
- `model.CompareOptions`: normalize_line_endings=False, include_evidence=False, max_file_bytes=16777216, max_diff_work=2000000, max_evidence_chars=8000, max_char_region=4096. Text ticket owns normalization/diff option flags and rules reporting.
- `text.compare_text(old_bytes_or_none, new_bytes, options) -> text dimension`. Text ticket owns this file. New baseline is None; returns NEW. Text rules must also flow into top-level rules (currently identity version 1).
- `resources.compare_resources(old_doc_or_none, new_doc, old_bytes_or_none, new_bytes, reader, options) -> resource dimension`. Resource ticket owns resource extraction/association, local-byte evidence schema and this module. See resource evidence rules in mdcompare-resources.md.
- `history.compare_history(old_doc_or_none, new_doc, old_bytes_or_none, new_bytes, reader, options) -> HistoryComparison or None`. None delegates to text.compare_text. HistoryComparison carries text plus the source-view bytes for resource discovery; these remain the original stage bytes when provenance is unknown, and become retained raw bytes only after complete verification. See [historical input contract](mdcompare-history.md).
- `ocr.compare_ocr(old_doc_or_none, new_doc, resources_dimension, reader, options) -> OCR dimension`. OCR ticket owns this and decisions.choose_action.
- `decisions.choose_action(old_exists, text, resources, metadata, ocr) -> action`. Returns UNKNOWN when any content/OCR prerequisite is UNKNOWN; see the complete priority in mdcompare-ocr.md.
- `io.InputReader(max_file_bytes).read_bytes(Path)` raises InputError(reason_code); `paths` retains resolved reads. All evidence reads must use this boundary; stability ticket owns fixed-view/failure/alias protection. It also owns atomic_write and CLI publication behavior.
- `comparison.compare_documents(baseline_docs, incoming_docs, options, reader)` orchestrates dimensions; minimal shared glue, coordinate changes rather than duplicating decisions. Feature tests should use the CLI seam.

Resource, OCR and historical provenance verification are implemented under their explicit evidence contracts. Unsupported or incomplete evidence deliberately stays UNKNOWN.

## Text comparison and explanation

The default identity rule preserves every byte, including Markdown whitespace,
Unicode, links, HTML and unknown extensions. `--normalize-line-endings` explicitly
selects `line-endings-lf` version `1`: CRLF and lone CR become LF; every other byte
is preserved. The selected rule and its contract appear in top-level `rules` via
`text_rules(options)`. Exact byte equality is checked first, independent of diff
budget. Normalized equality additionally records `TEXT_NORMALIZED_EQUAL` while
`byte_equal` remains false.

Changed UTF-8 text uses Myers shortest insert/delete edits at line level,
preserving line terminators and final-newline differences. Changed content that
cannot decode as UTF-8 is UNKNOWN with `TEXT_ENCODING_UNSUPPORTED`; it is never
silently decoded with character replacement. The CLI exposes these limits:

- `--max-diff-work` (default 2000000): frontier cells plus matched snake steps,
  shared by line and requested character comparisons. Exhaustion produces
  UNKNOWN, `comparison_complete: false`, and `DIFF_BUDGET_EXCEEDED`; known byte
  inequality remains visible. Zero allows only exact or normalized equality.
- `--max-evidence-chars` (default 8000): total Unicode code points of removed,
  added and character snippets per document. This only bounds display; clipped
  evidence sets `evidence_truncated` without pretending the comparison was equal.
- `--max-char-region` (default 4096): maximum combined removed/added region length
  eligible for character explanation. Larger regions keep their line explanation
  and set `characters_omitted`; this optional detail omission does not make a
  complete line comparison unknown. Zero disables character explanation.

All limits accept nonnegative integers. Body snippets require
`--include-evidence`; absent that flag, `differences` is empty and the reports
still contain classifications and reasons. No snippets appear on stdout/stderr.

Each text result includes `byte_equal`, `comparison_complete`, and
`evidence_truncated`. Evidence regions appear in source order, with one-based
`old_start_line`/`new_start_line`, verbatim `removed`/`added` strings, `characters`,
and `characters_omitted`. Character edits have `operation` (`delete` or `insert`),
zero-based `old_offset`/`new_offset` within that region, and `text`. Deterministic
Myers tie-breaking prefers deletion when frontier choices reach equally far;
ambiguous alignments therefore stay repeatable. Evidence clipping can stop within
a line and must not be read as a complete replacement patch.
