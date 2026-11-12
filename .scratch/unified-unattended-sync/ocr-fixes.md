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

### WP2 — plan/mdcompare (stages/plan.py, stages/mdcompare.py)
- [ ] H7: NO_WORK guard in plan._decide before unavailable branch (coupled M16).
- [ ] H21/H23: plan.handle append UNRESOLVED (pending) suppressed records to envelope anomalies; stage partial.
- [ ] H24: body-reuse requires row graph_status=="covered"; else whole_document compile.
- [ ] H26: removed image with prior OCR contribution → whole_document compile (republish).
- [ ] M11: freeze reads frozen md but resolves resources against ORIGINAL base dir.
- [ ] M16-plan: clear COMPARISON_PENDING in plan._update_state for settled/body-reuse docs.

### WP3 — compile/graph (knowledge_execute.py, workflows/update_graph.py, stages/compile.py)
- [ ] H8-read: compile trusts graph.ocr only on success status (tombstone from WP1 drives this).
- [ ] H9/M7: report emptied_sources; advance their success state (covered/version/raw) but keep out of current_sources validation.
- [ ] H22: followup attempt-failure suppression at same version; preserve backfill test_unified_compile.py:582.
- [ ] H25: followup consumption crash-safety (pin merged set / defer consume / include same-run consumed on resume).
- [ ] M16-compile: clear COMPARISON_PENDING in compile success loop filter.
- [ ] M17: enrich HTML/ref-style planned images OR record unconsumed missing items (don't falsely close followup).
- [ ] M21: compile-failure anomaly necessary_inputs reference frozen execution-corpus source.
- [ ] H4: update_graph.main() --partial post-publish loop skip audit failed_sources.

### WP4 — runner/status/lock (unified_sync.py, sync_run.py, sync_status.py, instance_lock.py, sync_knowledge_base.py, stages/capture.py)
- [ ] H5: preserve -h/--help through unified_arguments (no unlocked write).
- [ ] H11: legacy --dry-run → --stop-after plan (or reject), never full publication.
- [ ] H15: release canonical claim before archive I/O; reconcile stale active_run on terminal resume/new claim.
- [ ] H16: _validate_resume byte-compare frozen inputs/*.md against capture evidence.
- [ ] M8: resume refresh pid + running status, clear terminal fields.
- [ ] M9: find_run(..., book.key) on resume.
- [ ] M10: sync_status.view aggregate current stage counts on read.
- [ ] H14: capture.handle reuse snapshot when listing.complete regardless of overall status.
- [ ] M20: capture._handed_off skip disposition=="done" when counting toward partial.

### WP5 — results/anomalies/wash/apply (sync_results.py, sync_anomalies.py, sync_wash.py, workflows/apply_sync_updates.py, scripts/smoke_install.py)
- [ ] M3: record_posthoc_success done-path idempotency guard (identical evidence → no reopen).
- [ ] M12: scan_view include carried anomalies via durable results anomaly_ids.
- [ ] M18: book-keyed namespaces use validated book_key verbatim (not _slug).
- [ ] M2: report() schema-aware for migration rows (non-JSON wash --migrate).
- [ ] H19: publish_documents checkpoint-gate md/ preference (legacy apply).
- [ ] LOW1: drop unused env param from smoke_unified_sync + call site.

## Gate
make check (doc-guard + ruff + 1138 pytest) + make smoke. Push to PR #18.