# OCR round-2 adjudication + fix pass — PR #18

28 round-2 findings adjudicated against real code + tests + spec (5 read-only
subagents A–E). Reports: /tmp/uus-ocr-round2-adjudicate/{SUMMARY,A-ocr,B-plan,
C-compile,D-runner,E-results-wash}.md. Worktree baseline b740afe (round-1 fixes
already merged). Greptile's 4 PR findings cross-checked: all real, all a subset
of the 20 below (3 exact file/line matches; Greptile Issue2 expands [15]).

## Verdicts

REAL (20, fixed with red→green regression): [0],[1],[2],[4],[5],[6],[7],[8],
[12],[14],[15],[16],[17],[19],[20],[21],[22],[23],[26],[27].

NON-REAL (8, no fix): [3],[9],[10],[11],[24] by-design; [13],[25] false-positive;
[18] dismissed. H13 dismissal carries over (image resource_id URL logging is not
a credential leak; the no-token/auth contract targets API credentials only).

## Fix batches (4 parallel worktree subagents, disjoint source ownership)

Source files never overlap across batches, so all four merged cleanly into the
integration branch; only test files could have collided (they did not).

### Batch A — OCR (stages/ocr.py, mdocr/paddle.py) — DONE

- [x] [5] paddle `_parse_result_jsonl` normalizes malformed nested result
  (JSONDecodeError/ValueError/TypeError/AttributeError) to a ValueError at the
  client boundary instead of leaking a raw traceback.
- [x] [4] `_prepare` writes each original under a content-hash subdir so two
  images sharing a basename keep distinct cache-key stems (no recovery collision).
- [x] [19] the batch-stop triggering image keeps its recovery bytes
  (`stop_inputs` wired through the OcrSharedError handler).
- [x] [2] a body-reuse source whose changed image failed OCR registers a
  republish follow-up to drop stale text (invalidated prior published success).
- Regressions: test_convert_malformed_nested_result_is_value_error,
  test_same_basename_originals_do_not_collide_in_recovery_inputs,
  test_batch_stop_triggering_image_preserves_recovery_bytes,
  test_body_reuse_changed_image_failed_reocr_republishes_to_drop_stale_text.

### Batch B — plan/compile (knowledge_execute.py, stages/mdcompare.py) — DONE

- [x] [21] `_enrich_compile_body` falls back to the fragment-stripped resource
  key so a `url#fragment` image still enriches (canonical resource_id preserved).
- [x] [22] `_resource_catalog` upgrades a link-before-embed entry to kind image
  so the image still reaches OCR (identity stays the URL).
- [x] [6] compile prunes published graph.ocr to images the current raw still
  references, so a removed contribution stops re-flagging forever (durable OCR
  cache untouched); later version-only bumps reuse the body.
- [x] [23] a removed image is ABSENT, not OCR_NOT_ENRICHED; a referenced HTML
  `<img>` whose OCR succeeded but was not inserted is still reported unenriched.
- [x] [15] `_consume_followups` closes done only when every registered backfill
  input was consumed by the published source; an unconsumed input hands off
  (failed), never a false done. (Greptile's wider graph.ocr wiring expansion is
  deferred: post-hoc evidence is {kind,path} with no resource_id, so the full
  image record cannot be reconstructed — tracked separately.)
- Regressions: test_compile_enriches_fragment_image_via_normalized_fallback,
  test_link_before_embed_of_same_url_still_reaches_ocr,
  test_removed_ocr_contribution_is_pruned_so_later_version_bump_reuses_body,
  test_removed_image_is_not_reported_unenriched,
  test_referenced_html_image_ocr_success_is_still_reported_unenriched,
  test_posthoc_backfill_not_consumed_hands_off_instead_of_false_done.

### Batch C — capture/runner (knowledge_capture.py, stages/capture.py, unified_sync.py) — DONE

- [x] [16] freeze the completed observation to stage-local observation.json
  BEFORE the export loop; an interrupted capture resumes against the frozen
  scope/versions/selection instead of refetching drifted remote input.
- [x] [17] imported-raw materialization and checkpoint I/O failures stay inside
  the per-document failure boundary (fall back to normal export), never losing
  the whole capture stage.
- [x] [0] reuse of a complete snapshot re-validates each captured raw via the
  existing `_raw_validity` markers; enriched/invalid raw becomes a visible
  CAPTURE_RAW_UNAVAILABLE anomaly (partial), never a faked completed.
- [x] [1] abandoned (given-up) anomalies are excluded from the current failure
  list (still suppressed), mirroring the plan stage's pending-only carry-forward.
- [x] [20] durable capture-time raw evidence is persisted outside wash and
  byte-checked on resume even when mdcompare has not run; a raw modified while
  paused fails closed, missing/enriched raws keep the availability path.
- [x] [7] legacy `--dry-run` caps the batch at plan regardless of argument order
  (every explicit `--stop-after` is dropped, plan appended once).
- Regressions: test_interrupted_capture_resumes_against_the_frozen_observation,
  test_import_oserror_archives_one_document_and_keeps_the_other,
  test_reused_snapshot_rejects_enriched_raw_instead_of_faking_completed,
  test_abandoned_capture_anomaly_does_not_keep_the_next_batch_partial,
  test_resume_rejects_capture_raw_tampered_while_paused,
  test_resume_keeps_availability_path_when_a_paused_capture_raw_is_deleted,
  test_legacy_dry_run_caps_stop_after_plan_in_any_argument_order.

### Batch D — results/wash/apply (sync_results.py, sync_wash.py, workflows/apply_sync_updates.py) — DONE

- [x] [26] `anomaly_id` percent-encodes the resource_id component so two image
  URLs differing only by slug-collapsed characters stay distinct identities.
- [x] [27] `_persist_input` keys the durable inputs directory by a bounded
  sha256 of the anomaly id, so an over-long id (>255B) cannot overflow the path.
- [x] [8] cleanup keeps a finished run whose durable result is a stale paused
  file: `_result_is_current_final` compares status + ended_at against run state,
  not mere archive existence.
- [x] [12] `publish_documents` reads the historical `checkpoints/ocr-complete.json`
  layout, falling back to the flat layout (fixes the WP5 H19 regression).
- Regressions: test_resource_ids_differing_only_by_slug_characters_stay_distinct,
  test_over_long_anomaly_id_archives_inputs_in_a_bounded_directory,
  test_cleanup_keeps_a_finished_run_whose_durable_result_is_stale_paused,
  test_publish_documents_uses_historical_checkpoints_layout,
  test_publish_documents_falls_back_to_flat_checkpoint_layout.

### [14] — pending follow-ups at a settled version (inline, after Batch C) — DONE

- [x] [14] capture re-selects pending compile follow-up documents whose version
  stamp matches the observed listing version (union into `selected` after
  suppression, before the observation freeze). The verified same-version download
  reuse supplies the raw with no re-export; mdcompare makes the row available and
  `_pending_followup_items` consumes it. Version scoping keeps stale follow-ups
  out; failed/abandoned follow-ups stay excluded (H22). Responsible layer is
  capture selection, not mdcompare alone (an unselected doc has no raw).
- Regression: test_unchanged_batch_consumes_followup_at_settled_published_version
  (first compile succeeds → settled published version → post-hoc follow-up →
  unchanged batch consumes it done; red→green verified).

## Gate

make check (doc-guard + ruff check + ruff format --check + full pytest) + make
smoke. Push to PR #18, then re-run OCR to confirm the 20 real defects are gone.
