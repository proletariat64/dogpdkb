# OCR (OpenCodeReview) adjudication + fix pass — PR #18

47 findings adjudicated against real code + tests + spec (5 read-only subagents
A–E + self-adjudicated H23). Reports: /tmp/uus-ocr-adjudicate/{A-ocr,B-plan,
C-compile,D-runner,E-results-wash}.md. Worktree HEAD baseline d0ac27a.

## Verdicts

REAL (fix with regression): H2,H8,H12,H7,H21,H24,H26,H4,H9,H22,H25,H5,H11,H14,
H15,H16,H19,H23, MEDIUM2,3,4,6,7,8,9,10,11,12,15,16,17,18,19,20,21, LOW1.
Same root: H2≡H8 (OCR stale write+read), H9≡MEDIUM7 (emptied doc), H21≡H23
(suppressed anomaly invisible → fake completed; one plan-envelope fix covers both).

FALSE-POSITIVE (no fix): H3 (unified never creates staging/backup), H20 (capture
archives detail-mismatch → plan suppresses first), MEDIUM13 (canonical_download
reuse prevents functional harm; cosmetic only), MEDIUM14 (_body_view strips OCR
regardless of content_kind).

BY-DESIGN (no fix): MEDIUM5 (soft budget bounded by 900s job timeout, spec:103),
H6 (best-effort archiving logged + inputs preserved, test-blessed), H17 (python -m
test/migration surface, spec:144), H18 (legacy valueless --resume/--staging drop,
test-blessed), H1 (trusted post-hoc API; real defect is MEDIUM3), H10 (spec:138
reference-based cleanup vs spec:139 historical tidy asymmetry is intentional).

DISMISSED BY USER: H13 (image resource_id URL logging is NOT a credential leak;
no-token/auth contract targets API credentials only).

## Fix work packages (ordered by coupling; commit per WP)

### WP1 — OCR stage (stages/ocr.py, ocr_cache.py, paddle.py) — DONE
- [x] H2/H8-write: tombstone planned-but-unsuccessful image graph.ocr rows (non-success status) so compile keeps ref + records missing.
- [x] H12: normalize malformed-200-JSON schema errors (KeyError/TypeError/AttributeError) at paddle client boundary.
- [x] M4: changed_inputs relative to last PUBLISHED input (cache-hit resume must still register compile followup).
- [x] M6: validate artifact (_canonical_artifact) before persist_success writes; invalid → image failure, no clobber.
- [x] M15: failed-OCR anomaly necessary_inputs reference available src/prepared bytes.
- [x] M19: steps.ocr=partial when any planned image unfinished.
- Regressions (tests/test_unified_ocr.py, all red→green verified): nested_marker (M6), malformed_paddle[submit,poll] (H12), partial_ocr_records_partial (M19), failed_ocr_anomaly_preserves (M15), changed_image_failed_reocr (H2/H8), cache_hit_on_resume (M4). 21 OCR + 7 paddle + 94 consumer tests green.

### WP2 — plan/mdcompare (stages/plan.py, stages/mdcompare.py, mdcompare/ocr.py) — DONE
- [x] H7: NO_WORK guard in plan._decide before unavailable branch (coupled M16).
- [x] H21/H23: plan.handle append UNRESOLVED (pending) suppressed records to envelope anomalies; stage partial.
- [x] H24: body-reuse requires row graph_status=="covered"; else whole_document compile.
- [x] H26: removed OCR contribution → whole_document compile. compare_ocr surfaces removed_contributions (prior success URLs absent from incoming raw — representation-independent across raw/published); plan blocks body_reuse + reason OCR_CONTRIBUTION_REMOVED_FULL_RECOMPILE.
- [x] M11: _incoming/_baseline resolve resources against ORIGINAL base dir; markdown bytes still read from frozen path.
- [x] M16-plan: clear COMPARISON_PENDING in plan._update_state for body_reuse only (terminal in plan); compile decisions clear it in the compile success loop (M16-compile). Clearing compile in plan stranded sources whose compile later failed → spurious CAPTURE_RAW_UNAVAILABLE next batch (regression fixed in WP3).
- Regressions (red→green verified): test_unified_compile.py settled→no_work / unresolved-handed-off stays partial / resolved-handed-off completes / uncovered-baseline compiles (H7,M16,H21,H24); test_unified_ocr.py removed_image_with_prior_ocr_contribution_republishes (H26); test_unified_plan.py incoming_relative_image_resolves_against_original_raw_dir (M11). M16 coupling: acceptance no-update + status tests assert no-work convergence; two reused-image OCR contrast tests updated (unchanged batch→no_work; version-bump identical content→body_reuse merge). 187 unified+comparator tests green; ruff clean.

### WP3 — compile/graph (knowledge_execute.py, workflows/update_graph.py, stages/compile.py) — DONE
- [x] H8-read: confirmed no-change — compile already trusts graph.ocr only on success status (WP1 tombstone drives this).
- [x] H9/M7: report emptied_sources; advance their success state (covered/version/raw) but keep out of current_sources validation.
- [x] H22: followup attempt-failure suppression at same version; preserve backfill test_unified_compile.py:582.
- [x] H25: followup consumption crash-safety (include same-run done/failed on resume; retained candidate reused).
- [x] M16-compile: clear COMPARISON_PENDING (+GRAPH_UPDATE_REQUIRED/GRAPH_SOURCE_NOT_REGISTERED) in compile success loop filter.
- [x] M17: record unconsumed successful OCR as OCR_NOT_ENRICHED missing items + hand off followup (failed) for HTML/ref-style images; don't falsely close.
- [x] M21: compile-failure anomaly necessary_inputs reference frozen execution-corpus source (durable copy via archive_anomalies).
- [x] H4: update_graph.main() --partial post-publish loop skip audit failed_sources (no version advance / graph step failed).
- [x] M16-plan regression: plan clears COMPARISON_PENDING for body_reuse only, not compile (compile loop settles it). Fixes 2 test_sync_results failures (spurious batch-2 CAPTURE_RAW_UNAVAILABLE).
- Regressions (red→green verified): test_unified_compile.py emptied_document_advances_success_state_and_becomes_no_work (H9), compile_failure_anomaly_references_the_frozen_compile_input (M21); test_update_graph.py partial_publication_does_not_advance_failed_sources (H4); test_unified_ocr.py failed_ocr_followup_is_not_retried_the_next_day (H22), resume_after_followup_consumption_reuses_retained_candidate (H25), html_image_ocr_success_is_recorded_unenriched_and_handed_off (M17); test_unified_plan.py compile_decision_retains_comparison_pending_until_compile_publishes + body-reuse pending-clear assertion (M16-plan). Full gate green.
- Pre-existing test assertions corrected (both encoded pre-M16 re-selection of settled docs; confirmed failing at WP2 HEAD e2b7c02): test_unified_sync.py status test now expects the failed-compile batch to re-select (total=2); scripts/smoke_install.py second batch now asserts no-work convergence (total=0/reused=0) instead of reused=1. make check 1158 passed + make smoke PASS.

### WP4 — runner/status/lock (unified_sync.py, sync_run.py, sync_status.py, sync_progress.py, stages/capture.py) — DONE
- [x] H5: preserve -h/--help through unified_arguments (no unlocked write).
- [x] H11: legacy --dry-run → --stop-after plan, never full publication (deprecation notice maps it).
- [x] H15: release canonical claim before archive I/O; Progress.reconcile_finished_claim clears a stale active_run bound to a finished run on terminal resume and before every new claim.
- [x] H16: _validate_frozen_inputs byte-compares each frozen mdcompare inputs/*.md against the captured raw (freeze-time comparison), fail closed on mismatch.
- [x] M8: RunState.begin_resume refreshes pid + running status, clears terminal fields.
- [x] M9: find_run(config.staging, args.resume, book.key) on resume.
- [x] M10: sync_status.view aggregates live stage counts on read.
- [x] H14: capture.handle reuses any snapshot whose listing.complete is True (incl. export-partial "incomplete"), not just COMPLETE status.
- [x] M20: capture._handed_off skips disposition=="done" records (no handoff, no partial).
- Regressions (red→green verified): test_unified_sync.py legacy_help / legacy_dry_run / resume_selects_run_under_book / status_counts_live / resume_refreshes_liveness / new_batch_reconciles_orphaned_claim / resume_reuses_incomplete_frozen_listing / resume_refuses_frozen_inputs_whose_bytes_changed; test_unified_compile.py resuming_finished_run_releases_orphaned_claim; test_sync_results.py done_anomaly_does_not_keep_next_batch_partial. 46 unified+results tests green; ruff clean.

### WP5 — results/anomalies/wash/apply (sync_results.py, sync_anomalies.py, sync_wash.py, workflows/apply_sync_updates.py, scripts/smoke_install.py)
- [ ] M3: record_posthoc_success done-path idempotency guard (identical evidence → no reopen).
- [ ] M12: scan_view include carried anomalies via durable results anomaly_ids.
- [ ] M18: book-keyed namespaces use validated book_key verbatim (not _slug).
- [ ] M2: report() schema-aware for migration rows (non-JSON wash --migrate).
- [ ] H19: publish_documents checkpoint-gate md/ preference (legacy apply).
- [ ] LOW1: drop unused env param from smoke_unified_sync + call site.

## Gate
make check (doc-guard + ruff + 1138 pytest) + make smoke. Push to PR #18.
