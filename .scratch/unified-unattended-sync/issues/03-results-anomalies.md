# 03: 持久批次结果、异常登记与处置合同

Type: task
Status: resolved
Blocked by: 01

## Context

- [规格](../spec.md) 持久结果、异常移交与 wash 清理节；[地图](../map.md)。
- 先读 `/tmp/unified-unattended-sync-notes/00-base-context.md`；补充笔记 01/02/03。01 的 Answer 公布 run 目录布局与状态 schema——本票消费它。
- 现有身份/版本体系：canonical state（book key + doc ID + version{field,value,utc}）、snapshot exports、checkpoint。异常身份复用这些，不新增内容指纹机制。

## Acceptance and ownership

1. **持久结构化批次结果**：每批结束前持久保存（独立于待清理 wash run 目录，位置走现有配置体系）；含 run、book/doc ID、来源版本、阶段、计数、结果引用。历史结果可重复扫描。
2. **异常登记**：异常含 run、知识库/文档 ID、来源版本、阶段、原因、图片身份及必要输入引用；必要输入持久化（wash 清理后仍可读取）。共享 token/额度错误记录为批次原因并能枚举受影响图片。稳定身份区分待处理/完成/放弃/失效。
3. **重试抑制合同**：同一来源版本已归档失败不在每日 sync 无限重复尝试；尝试/移交状态与真实成功状态分开保存，不用虚假成功避免重试。新远端版本正常参加日常同步；对旧版本的放弃只作用该版本，不永久封禁来源。未变化远端的历史 OCR 待补交事后 agent，不每日自动重试。
4. **处置入口**：供事后 agent 扫描结果、读取必要输入、对指定版本异常记录完成/放弃/失效（新只读+处置子命令，如 `dogpdkb anomalies`；不是第二套顶层阶段命令）。重复扫描不重复执行。在 Answer 公布结果/异常 schema、处置 CLI 合同。
5. **OCR 后补合同**：事后处理成功后必须更新对应缓存、真实完成状态与处置结果；OCR 补齐若改变已发布编译输入，显式登记后续编译工作（合同在此定义，05/06 执行），不能声称仅写 OCR 缓存即完成知识更新。
6. **阶段归档 API**：提供各阶段调用的归档接口；本票完成 capture 阶段（01 已接线）的失败归档集成作为首个消费者；04/05/06 各自阶段集成时复用。归档失败不丢输入、不破坏有效知识库。

主要拥有：新持久结果/异常模块（如 sync_results.py）、处置子命令、cli.py 最小分发、capture 归档集成、新测试。避免改 runner 状态机核心（01）、plan/ocr/compile handler（04/05/06）。

## Working rules

在独立 worktree 基于集成分支 `unified-unattended-sync` 工作（分支 `uus/03-results-anomalies`）；开工前确认基于集成 tip（含 01），否则 reset 到它（仅限本票新分支）。`make bootstrap`。调用 Skill `tdd`：从一条失败的 CLI 黑盒测试开始，每轮记录 red/green。临时 corpus + 替身；无真实服务/付费调用。完成后合入最新集成 tip 再跑聚焦测试 + ruff check + format --check，提交，Status 改 resolved，Answer 列公开合同与验证证据。失败/部分实现保持 claimed。不得 reset/clean 共享状态或删私有实物。

## Comments

## Answer

分支 `uus/03-results-anomalies`，worktree `/home/ubuntu/dev/dogpdkb/.qoder/worktrees/uus-03-results-anomalies`。实现提交 `af0d120`（登记+capture 归档 API）→ `a6c4d3a`（每批结果）→ `6b81c3d`（anomalies CLI）→ `bbe1597`（重试抑制+export 修复）→ `4678039`（OCR 后补+followup）→ `f74350b`（批次原因+文档身份+归档韧性）；合并集成 tip `75b2b42` 的 merge 提交 `74dcf74`（仅 map.md，无冲突）。新增 `sync_results.py`/`sync_anomalies.py`；改 `cli.py`（anomalies 最小分发）、`knowledge_capture.py`（选择期抑制消费 + 双 SkylarkError 身份修复）、`stages/capture.py`（归档集成+韧性）、`unified_sync.py`（结果归档非致命）、`mdocr/config.py`（`[paths].results`）。未改 runner 状态机核心（01）与 plan/ocr/compile handler（04/05/06）。

### 公开合同（供 04–07 消费）

**1. 持久结果库布局**（`[paths].results`，默认 `sync-results`，走现有配置体系；`results_root = Path(config.results)/<slug(book_key)>`，**独立于待清理 wash run 目录**）

```
<config.results>/<book_key>/
  anomalies.json            # 异常登记 + 批次原因（unified-anomalies-1.0）
  compile-followups.json    # 后续编译工作（unified-compile-followups-1.0）
  runs/<run-id>.json        # 每批结构化结果（unified-result-1.0）
  inputs/<anomaly-id>/<name>  # 必要输入持久副本（wash 清理后仍可读）
```

原子写（tmp+fsync+replace）、文件 0600 / 目录 0700、拒绝 symlink（复用 `sync_run.atomic_json`）。

**2. 批次结果 schema**（`unified-result-1.0`，`sync_results.archive_run`，`runs/<run-id>.json`）

顶层：`schema_version, run_id, book_key, book{key,name,url}, status, stop_reason, exit_code, started_at, ended_at, duration_seconds, corpus, config, counts{total,completed,reused,failed}, stages{<stage>:{status,counts,reason,artifacts,anomaly_ids[],result(相对 result.json|null)}}, documents[], anomaly_ids[], run_dir, results_dir`。

- `documents[]` = 每文档身份行 `{document_id, title, source_version{field,value,utc}, export_status, reused, labels[]}`，取自 capture snapshot，**复用既有 version 记录，无新内容指纹**；snapshot 缺失/不可读降级为空行，不使整份结果失败。
- `stages[stage].result` 指向相对 run 根的阶段信封；信封缺失时 status 取 run 状态、artifacts={}、result=null。
- 安全网：信封中未登记（无 `id`）的 anomaly 在此补登记，结果绝不引用未登记异常。
- 由 `unified_sync._archive_result` 在 finish/interrupt 路径调用；`(OSError,ValueError)` 非致命，stderr 记 `result archiving failed`（`safe_diagnostic`）。历史结果经 `run_results`/`scan_view` 可重复扫描。

**3. 异常登记 schema**（`unified-anomalies-1.0`，`anomalies.json`）

顶层 `{schema_version, book_key, anomalies{<aid>:record}, batch_causes{<cid>:cause}}`。

- anomaly record：`{id, book_key, book_id, document_id, source_version, version_stamp, stage, code, reason, resource_id, image, necessary_inputs[{kind,path(绝对 durable),source}], disposition, first_seen, last_seen, attempts, runs[], last_run, resolved_at, resolution, batch_cause}`。
- 身份 `anomaly_id = slug(book)-slug(doc)-version_stamp-slug(stage)-slug(code)[-slug(resource_id)]`；`version_stamp = slug(source_version.utc|value)`。复用 book+doc ID+来源版本，**不新增指纹**。
- `disposition ∈ pending|done|abandoned|stale`。幂等 upsert：重复归档保留 disposition 与 durable inputs，仅推进 last_seen/last_run/runs/attempts（新 run 才 +1）；`stale` 再失败→重置 `pending`（重新移交，避免每日无限重试）。
- `necessary_inputs` 先持久化（`_persist_input` 直接字节比较守护既有副本，冲突不覆盖，**无 hash**）再写登记：归档失败绝不留下指向缺失文件的引用，绝不触 canonical state。
- batch cause：`{id, book_key, run_id, stage, code, reason, anomaly_ids[], recorded_at, updated_at}`；`batch_cause_id = slug(book)-slug(run)-slug(stage)-slug(code)`。共享 token/额度错误记**一条**并枚举受影响 `anomaly_ids`，每个 anomaly 的 `batch_cause` 戳为该 id；幂等合并（`record_batch_cause`，经 `archive_anomalies(..., batch_cause=...)`）。

**4. 处置 CLI 合同**（`dogpdkb anomalies`，`sync_anomalies`，cli.py 最小分发；**不是第二套顶层阶段命令**）

`dogpdkb anomalies [--config C] [--book K] [--json] [--run RUN] [--disposition D] [--stage S] [--show ID] [--resolve ID] [--note TEXT] [--evidence FILE]`

- 默认**只读扫描**持久结果库（绝不扫 wash run 目录），不执行任何 sync 工作；**重复扫描不重复执行**。
- `--show ID`：打印单条 anomaly JSON。
- `--resolve ID --disposition abandoned|stale [--note]`：记录**版本范围**处置（handoff state，绝不伪造 canonical 成功）；同处置重复=no-op；`done` 必须走 `--evidence`。处置写持 user-level run lock（`updater_lock`），扫描不持锁。
- `--resolve ID --disposition done --evidence FILE [--note]` → `record_posthoc_success`（见 §6）。
- 过滤：`--run`（runs 含该 run）、`--disposition`、`--stage`。
- scan JSON（`unified-anomaly-scan-1.0`）：`{schema_version, book_key, results_dir, runs[], anomalies[], batch_causes[], compile_followups[], counts{runs,anomalies,pending,done,abandoned,stale,batch_causes,compile_followups,followups_pending}}`；human report 列 runs/anomalies/batch causes/compile followups。
- 错误→exit 1（`safe_diagnostic`，无 token/正文）：未知 anomaly/disposition、done 缺 evidence、evidence 仅 done 有效、`UpdateAlreadyRunning`。

**5. 重试抑制合同**

- `suppressing_anomalies(config, book_key, stage=None)` 返回 `disposition ∈ {pending, abandoned, done}` 的记录（**stale 故意排除**：失效移交回日常 sync）。
- capture 在**选择时**消费：`run_capture` 收 `suppressed_anomalies`，`_suppressed_match` 按 document_id + 既有归一化 instant 相等过滤 `selected`（无新指纹）；被抑制文档不重新导出、不重新登记（attempts 不增）。
- 尝试/移交状态（registry disposition）与真实成功（canonical `.sync-state.json`）**分开保存**，不用虚假成功避免重试。
- 新远端版本 instant 不等→不匹配→正常参加日常 sync；放弃只作用 anomaly_id 编码的**该版本**，不永久封禁来源；未变化远端的历史 OCR 待补交事后 agent（pending 抑制），不每日自动重试。
- 被抑制异常仍附进 capture envelope（`_handed_off` 从 registry 取记录，**不重登记**），有则 completed→partial，绝不伪造 completed。

**6. OCR 后补合同**（合同在此定义，**05/06 执行**）

- evidence schema `unified-posthoc-evidence-1.0`：`{schema_version, cache[{kind,path}], completion{step,details{}}, input_changed(bool), reason?}`。
- `record_posthoc_success(config, book, aid, evidence, note=None)` 顺序保证不伪造成功：① 校验 evidence schema + cache 引用可读（`_evidence_refs`，绝不伪造工作产物）② **先**写真实完成 canonical（`Progress.step(doc_id, completion.step, "completed", **details)`）③ `input_changed` 则 `register_compile_followup` ④ **最后**才置 `disposition=done` + resolution。失败留 pending 供重试，绝不无真实完成而 done。
- resolution：`{disposition:"done", note, recorded_at, cache[], completion{step,details}, input_changed, followup_id}`。
- followup schema `unified-compile-followups-1.0`（`compile-followups.json`）：`{schema_version, book_key, followups{<fid>:record}}`；record `{id, book_key, document_id, source_version, version_stamp, code, reason, inputs[], anomaly_id, status(pending|done), created_at, updated_at, run_id, resolution}`；`followup_id = slug(book)-slug(doc)-version_stamp-compile`。
- `register_compile_followup(...)`：OCR 补齐**改变已发布编译输入必须显式登记**后续编译工作；**仅写 OCR 缓存 ≠ 完成知识更新**。幂等 per version：重复补齐合并 inputs 不重复，done 后再补齐→重开 pending；followup 保持 pending 直到 compile 发布并记真实完成（06）。

**7. 阶段归档 API**

- `archive_anomalies(context, anomalies, *, run_id=None, batch_cause=None)`：各阶段调用，返回富化记录（稳定 id / durable input refs / disposition）供信封。capture 为**首个消费者**（已接线）；04/05/06 各自阶段集成时复用。
- **归档失败不丢输入、不破坏有效知识库**：capture `_archived` 捕 `(OSError,ValueError)`，记 stage log `anomaly archiving failed`，保留原始 anomalies + run 目录内可读输入，继续（非致命）；`_archive_result` 同样非致命。

### 验收对照

1. 持久结构化批次结果 ✓（§2；`test_batch_result_is_persisted_outside_wash_and_survives_cleanup`、`test_batch_result_carries_document_identities_surviving_wash`）
2. 异常登记 + 必要输入持久化 + 批次原因枚举 + 稳定身份四态 ✓（§3；`test_capture_anomaly_is_archived_outside_wash_with_a_persistent_input`、`test_persistent_input_survives_wash_cleanup`、`test_batch_cause_enumerates_affected_images`）
3. 重试抑制合同 ✓（§5；`test_archived_export_failure_is_not_retried_by_the_next_daily_sync`、`test_a_new_remote_version_syncs_normally_despite_an_archived_failure`、`test_abandoning_an_old_version_does_not_ban_the_source`、`test_stale_disposition_hands_the_failure_back_to_daily_sync`）
4. 处置入口 ✓（§4；`test_anomalies_scan_lists_durable_results_and_anomalies_without_executing`、`test_anomalies_scan_without_history_returns_an_empty_view`、`test_anomalies_show_reads_one_record_and_resolve_records_disposition`）
5. OCR 后补合同 ✓（§6；`test_posthoc_done_updates_real_completion_and_registers_compile_followup`、`test_posthoc_done_refuses_missing_or_unreadable_evidence`、`test_posthoc_done_without_input_change_registers_no_followup`）
6. 阶段归档 API + capture 首消费者 + 归档失败韧性 ✓（§7；`test_a_single_remote_export_failure_is_archived_without_blocking_others`、`test_archiving_failure_keeps_inputs_and_a_valid_corpus`）

### 验证证据

- TDD：每轮先红后绿（CLI 黑盒 + 临时 corpus + 本地 Yuque/Graphify/OCR 替身，无真实服务/付费/网络调用）。`tests/test_sync_results.py` **17 passed**（含 Slice D 3 条：文档身份、归档韧性、批次原因）。
- 聚焦回归（10 套件）：`test_sync_results / test_unified_sync / test_knowledge_capture / test_capture_reuse / test_cli / test_knowledge_sync / test_sync / test_sync_progress_cli / test_sync_state_cli / test_instance_lock_cli` → **208 passed**。
- `ruff check .` All checks passed；`ruff format --check .` 209 files already formatted；`scripts/doc_guard.py` PASS（84 documents）。
- 修复既有缺陷：`knowledge_capture._export_remote` 现同时捕获两个 `SkylarkError` 身份（workflows 顶层 re-export ≠ `dogpdkb.workflows.get_sync_updates`），单文档远端导出失败此前会逃逸 RuntimeError 使 capture 失败，现按合同归档 `export_failed` 不阻塞其余文档；票 01 `test_capture_that_cannot_observe_the_book_fails_the_batch` 改用真实 listing 失败（`page_overrides` 非 list 载荷）保留其原意。
- 默认日志/诊断经 `safe_diagnostic` 脱敏并限 300 字符，不含 token/auth/文档正文；`PADDLEOCR_TOKEN` 仅 env。
