# mdcompare contract (schema 1.0)

Standalone invocation: `mdcompare compare --baseline baseline.json --incoming incoming.json --output decisions.json [--report review.md] [--include-evidence]` or `python -m mdcompare`.

Manifest root: `schema_version: "1.0"`, `documents: []`. Each document requires nonempty string `knowledge_base`, `document_id`, `path`; `content_kind` is `raw` or `historical_enriched`; `metadata` requires string `name`, `logical_path`, `source_ref` and optional string `sync_timestamp`; `resources` is an explicit list of objects. Optional `ocr` is an object, `provenance` an object. Paths are local, relative to each manifest's directory or absolute. Nested resource/OCR/history evidence contracts are owned by their feature tickets and must be validated before use. Unsupported or unverifiable evidence cannot authorize skipping.

Results contain schema_version, rules (name/version), results, baseline_only (identity pairs only), summary.total and summary.actions (all six action counts), errors. Every result has knowledge_base, document_id, text, resources, metadata, ocr, action, reason_codes. Each comparison dimension has status UNCHANGED, CHANGED, NEW, NOT_APPLICABLE or UNKNOWN; text also has reason_codes/differences, resources has reason_codes/items, metadata has changed_fields/sync_timestamp_changed, OCR has reason_codes. IDs and reason codes stay available without body evidence. No run timestamp affects decisions. Baseline-only does not mean remotely deleted.

Exit 0: complete no unknown; 2: complete with unknown or item errors; 1: global input/output failure. Reports are private and atomic; all source files are read-only. Supply immutable input snapshots. No comparison conclusion is business correctness or production publication proof.

## Internal extension boundaries

- `model.Document`: knowledge_base, document_id, content_kind, path (resolved Path), metadata, resources, base_dir, ocr, provenance. `identity` scopes ID; `resolve(str)` resolves evidence file paths against the manifest directory.
- `model.CompareOptions`: normalize_line_endings=False, include_evidence=False, max_file_bytes=16777216, max_diff_work=2000000, max_evidence_chars=8000, max_char_region=4096. Text ticket owns normalization/diff option flags and rules reporting.
- `text.compare_text(old_bytes_or_none, new_bytes, options) -> text dimension`. Text ticket owns this file. New baseline is None; returns NEW. Text rules must also flow into top-level rules (currently identity version 1).
- `resources.compare_resources(old_doc_or_none, new_doc, old_bytes_or_none, new_bytes, reader, options) -> resource dimension`. Resource ticket owns resource extraction/association, local-byte evidence schema and this module. Current conservative placeholder treats Markdown links as unverified dependencies.
- `history.compare_history(old_doc_or_none, new_doc, old_bytes_or_none, new_bytes, reader, options) -> text dimension or None`. None delegates to text.compare_text; history ticket owns verifiable provenance handling.
- `ocr.compare_ocr(old_doc_or_none, new_doc, resources_dimension, reader, options) -> OCR dimension`. OCR ticket owns this and decisions.choose_action.
- `decisions.choose_action(old_exists, text, resources, metadata, ocr) -> action`. Initial version safely returns UNKNOWN when any content/OCR dimension is UNKNOWN.
- `io.InputReader(max_file_bytes).read_bytes(Path)` raises InputError(reason_code); `paths` retains resolved reads. All evidence reads must use this boundary; stability ticket owns fixed-view/failure/alias protection. It also owns atomic_write and CLI publication behavior.
- `comparison.compare_documents(baseline_docs, incoming_docs, options, reader)` orchestrates dimensions; minimal shared glue, coordinate changes rather than duplicating decisions. Feature tests should use the CLI seam.

The initial implementation cannot verify resources, OCR reuse or enriched history and deliberately returns UNKNOWN for those cases. Later tickets replace these safe placeholders.
