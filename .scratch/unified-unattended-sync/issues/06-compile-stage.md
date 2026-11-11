# 06: compile 阶段：整来源更新、部分发布与恢复

Type: task
Status: resolved
Blocked by: 04

## Context

- [规格](../spec.md) compile、状态与成功边界节；[地图](../map.md)。
- 先读 `/tmp/unified-unattended-sync-notes/00-base-context.md`；补充笔记 `02-knowledge-modules.md`（execute 合同、Graphify 调用、候选隔离/发布恢复、必须保留的结构检查、当前批级全有或全无失败、_run_graphify 内存缓冲输出）与 `01-default-sync-pipeline.md`（update_graph/graph_sync）。工作树代码为准。
- 04 Answer：compile 名单、输入引用、OCR 结果引用 schema、实际最终输入/缺失项字段。03 Answer：异常归档、重试抑制、OCR 后补登记后续编译。01 Answer：run 状态/阶段信封。
- 现有 Graphify 编译缓存/隔离候选/发布恢复能力沿用，不重写 Graphify、不全量重建历史图谱。
- 关键现状（见 00-base-context / 笔记 01/02/03）：Graphify 非 CLI 子进程，而是 `[graphify_python, update_graph.py, …]` 导入 graphify 模块；候选隔离/publication_lock/rollback/发布字节校验已存在（保留）；提取当前全有或全无（failed_chunks/uncovered_files→RuntimeError），本票改部分发布。新阶段须冻结**富化后** markdown 并按插入 OCR 块声明 body_scope `generated-regions-v1` 区域（正文复用与 OCR 独立），每图证据持久化到 wash 外。修复 update_graph 把新导出来源误标 content_kind:"historical_enriched" 的 bug。

## Acceptance and ownership

1. **整来源更新**：Graphify 按完整来源更新，不用 diff 片段替换完整来源。注册 compile handler。
2. **部分发布**：编译成功来源发布；失败来源保留旧知识与旧成功版本，不因部分提取失败清空失败来源既有记录（改造当前批级全有或全无）。一次 run 固定本次观察/选择范围/来源版本，后来远端变化交下一批。
3. **产物检查（非语义门禁）**：保留输入可读、输出可解析、基本图结构、写入成功等必要检查；不以节点数/知识差异/语义问题集/人工审核为日常发布门槛。正式写入成功后才推进相应成功状态。
4. **编译输入**：使用本批实际准备好的正文+有效历史 OCR+本次成功 OCR；保留未识别图片引用；记录实际最终输入与缺失项，不伪装成已完成 OCR 输入。OCR 不完整不阻断正文编译。
5. **元数据维护执行**：执行 04 计划的改名/搬动/来源引用维护（与 04 计划侧协调）。
6. **恢复**：复用发布恢复——提交与状态记录之间故障不重复计费/不重提取；无效图结构或写入失败不推进成功版本。
7. **流式日志**：Graphify 输出流式写入阶段日志（改 _run_graphify 内存缓冲），遵循 01 日志合同，不输出 token/正文。
8. **重试抑制与后补**：同版本已归档编译失败不每日重试；新版本正常；OCR 后补登记的后续编译工作可被本阶段消费。
9. **run 收尾**：与 01 runner 协作判定 completed/partial（结合 03 异常）；partial 不残留阻挡下一次 cron 的活动声明。

主要拥有：knowledge_execute.py、compile handler、update_graph 必要扩展、相关测试改写（只改被取代的审批/质量断言，保留输入完整性/恢复回归）。避免改比较器、OCR handler（05）、plan（04）。

## Working rules

在独立 worktree 基于集成分支 `unified-unattended-sync` 工作（分支 `uus/06-compile-stage`）；开工前确认基于集成 tip（含 04），否则 reset 到它（仅限本票新分支）。`make bootstrap`。调用 Skill `tdd`：从一条失败的 CLI 黑盒测试开始，每轮记录 red/green。临时 corpus + Graphify 确定性替身（现有 shim）；无真实 Graphify/付费/生产。完成后合入最新集成 tip 再跑聚焦测试 + ruff check + format --check，提交，Status 改 resolved，Answer 列公开合同与验证证据。失败/部分实现保持 claimed。不得 reset/clean 共享状态或删私有实物。

## Answer

### 实现

compile handler 已注册（`sync_stages.py` `STAGE_OWNERS["compile"]="06"`；`dogpdkb/stages/compile.py` 暴露 `HANDLER`）。`execute_compile`（`knowledge_execute.py`）消费 04 plan 的 `compile` 与 `metadata` 名单，按**完整来源**更新：每文档冻结为一份 OCR 富化后的 `source-[id].md` 整来源（绝不用 diff 片段替换完整来源），经现有 Graphify 候选隔离 / publication_lock / rollback / 发布字节校验 machinery 发布。无人工审批、无语义质量门禁，仅产物检查。改造原批级全有或全无：成功来源发布并推进成功版本，失败来源保留旧知识与旧成功版本并归档为 wash 外持久异常。沿用现有编译缓存，不重写 Graphify、不全量重建历史图谱。

### 公开合同（供 07/08）

**阶段信封** `<run>/compile/result.json`，schema `unified-stage-1.0`（`STAGE_SCHEMA`）：`status` ∈ completed|partial|skipped|failed；`artifacts={"result":"compile/compile-result.json"}`；`inputs={"plan":"plan/plan.json"}`；另含 `counts`、`anomalies`、`reason`、`log`。失败时 runner 捕获 `StageFailed` 写 `StageResult(status="failed")`，`artifacts={}`（默认），stderr/日志为 `compile failed: <safe_diagnostic>`。`compile-result.json` 已存在则拒绝重跑（应 resume 已记录的 run）。compile==0 且无 metadata 无异常 → skipped。

**编译结果** `compile/compile-result.json`，schema `unified-compile-1.0`（`COMPILE_SCHEMA`）：`status`=partial（有 failed）否则 completed；`book`；`counts{compile,published,failed,metadata,external_calls,cache_hits}`；`results[]`=`{book_id,document_id,status:"published",source_scope:"whole_document",source_version,execution}`；`failed[]`=`{book_id,document_id,source_version,code:"COMPILE_EXTRACTION_FAILED",reason}`；`metadata[]`=`{book_id,document_id,source_version,tasks,status:"executed"}`；`followups[]`（已消费 fid）、`tokens{...,evidence}`、`usage`、`transaction{candidate,backup,resume:"reuse_retained_candidate"}|null`。

**execution（按 04 合同）**：`{final_inputs:[{kind:"raw"|"ocr_artifact"|"prepared_image",resource_id?,path,source_version?}], missing_items:[{kind:"ocr_artifact",resource_id,code,reason}], completed_at, status:"completed"}`。编译输入 = 本批实际准备好的正文 + 有效历史 OCR + 本次成功 OCR（`_compile_ocr_evidence` 读 canonical `graph.ocr`，contract `prepared-jpeg-ocr-v1`，历史与本次同等复用，绝不重跑 OCR）。成功产物把图片 embed 替换为 mdocr 块并记入 final_inputs；失败/待处理/不可用图片**保留原引用**并记 missing_items（code OCR_UNAVAILABLE/OCR_FAILED/OCR_PENDING/OCR_ARTIFACT_UNAVAILABLE）。OCR 不完整不阻断正文编译。

**canonical graph 记录**：`{status:"covered",coverage:"compiled_whole_document",version,raw,content_kind:"raw",metadata{name,logical_path,source_ref},body_scope{version:"generated-regions-v1",regions:[{kind:"mdocr_ocr",start_line,end_line}]}}`，并**保留 05 写入的 `graph.ocr` 证据**。regions 按插入的 mdocr 块声明（1-based 行号，匹配 mdcompare generated-regions-v1 校验），使下一批正文复用与 OCR 独立。

**部分发布**：成功来源整来源发布、成功版本仅在真实写入成功后推进；失败来源保留旧知识（旧文件名不被清除/替换）与旧成功版本，不因部分提取失败清空既有记录。一次 run 固定本次观察/选择范围/来源版本，远端后续变化交下一批。

**恢复**：复用发布恢复——`_run_graphify` 非零退出或缺结果文件即 raise，保留旧图与 retained transaction；提交与状态记录之间故障不重复计费/不重提取（resume 复用 retained candidate）；无效图结构（`_validate_graph_records`：记录集合/节点身份/悬边/悬超边/覆盖每已发布整来源/无陈旧归属）或写入失败（发布后 graph.json 字节 != candidate 字节）不推进成功版本。

**流式日志**：`_run_graphify` 用 `log.popen_kwargs()`（`stdout=stream, stderr=STDOUT`）把 Graphify 自身输出**流式**写入 `<run>/logs/compile.log`（行缓冲追加），不再内存缓冲；遵循 01 日志合同，失败 reason 经 `safe_diagnostic`，不输出 token/正文。

**元数据维护执行侧**：`_apply_metadata_maintenance` 消费 04 plan `metadata[].tasks`，把 incoming name/logical_path/source_ref 落盘到 `graph.metadata` 并记 `steps.metadata_maintenance{status:completed,version,tasks}`。对每个计划文档执行（含正文复用者），且独立于正文成功版本——提取失败来源仍维护其改名/搬动/来源引用。无正文工作时也执行 metadata。

**historical_enriched 修复**：`update_graph.py:728` 新导出来源 `content_kind` 由误标 `"historical_enriched"` 改为 `"raw"`（commit 4d37504），避免扭曲后续 metadata 比较与正文复用判定；compile 路径同样写 `content_kind:"raw"`（`knowledge_execute.py:1528`）。历史 baseline 仍为 historical_enriched（受 test_capture_reuse / test_sync_state_cli / test_sync_progress_cli 与 `test_knowledge_sync.py::test_historical_enriched_manifest_cannot_register_a_raw_graph_baseline` 保护）；新导出 raw 由整来源发布/resume 路径测试行使。

**run 收尾（与 01 runner + 03 异常协调）**：runner `_run` 任一阶段 failed → run failed（exit 1）；无 failed 但有归档异常（03 `archive_anomalies`）→ run partial（exit 0）；否则 completed（exit 0）。`FINISHED_STATUSES=(completed,partial,failed)` 均 `progress.finish()` 释放 canonical claim（`active_run=None`），partial 不残留阻挡下一次 cron 的活动声明。

**后补消费（03 followup）**：`_pending_followup_items` 把 pending `compile-followups`（03 `unified-compile-followups-1.0`）按登记版本并入本批 compile 集（用本批准备好的正文 + 更新后 OCR 证据）；文档缺席/不可用/版本不符则留 pending，绝不伪造。`_consume_followups` 仅在真实发布并记录成功版本后按 document+version 标记 done（写 run_id/resolution），幂等；此前崩溃留 pending 交下一批。同版本已归档编译失败由 04 plan 抑制（decision=suppressed，compile 名单空 → 阶段 skipped），不每日重试；新版本正常编译。

### 验收对照

1. 整来源更新 + 注册 handler → `test_compile_publishes_whole_sources_and_advances_the_success_version`（整来源发布、旧文件名被替换、成功版本推进）；`STAGE_OWNERS["compile"]="06"` + `HANDLER`。
2. 部分发布 → `test_partial_publication_keeps_failed_source_knowledge_and_version`；`test_knowledge_execute.py::test_partial_extraction_failure_preserves_graph_and_resumes_from_cache`。
3. 产物检查（非语义门禁）、写入成功才推进 → `test_compile_publishes_whole_sources_and_advances_the_success_version` + `test_invalid_candidate_structure_does_not_advance_the_success_version`。
4. 编译输入（正文+有效历史 OCR+本次 OCR、保留未识别引用、final_inputs/missing_items） → `test_compile_enriches_body_with_successful_ocr_and_records_final_inputs` + `test_compile_keeps_unrecognized_image_and_records_missing_items`。
5. 元数据维护执行 → `test_compile_executes_planned_metadata_maintenance_for_body_reuse`。
6. 恢复（不重计费/不重提取；无效结构/写入失败不推进） → `test_fault_between_publish_and_state_record_recovers_without_rebilling` + `test_invalid_candidate_structure_does_not_advance_the_success_version` + `test_knowledge_execute.py::test_graph_publish_then_state_failure_recovers_without_rebilling` + `::test_hard_exit_after_state_commit_recovers_report_without_reextracting`。
7. 流式日志 → `test_graphify_output_streams_to_the_stage_log_not_the_console`。
8. 重试抑制 + 后补 → `test_same_version_compile_failure_is_not_retried_the_next_day` + `test_a_new_source_version_is_compiled_normally_after_a_failure` + `test_compile_consumes_pending_posthoc_followup_for_suppressed_version`。
9. run 收尾 completed/partial + 释放 claim → `test_partial_publication_keeps_failed_source_knowledge_and_version`（summary partial、exit 0、`active_run is None`）。

### 验证证据

- 2 条被取代的 runner 边界断言已改写（ocr→compile，同 04 的 mdcompare→ocr 边界迁移）：`test_unregistered_stage_fails_loudly_after_ocr_ends` 重命名为 `test_a_failed_compile_ends_the_batch_loudly_after_ocr`，两处 `"no registered handler"` stderr 子串改为 `"compile failed"`，并修正 resume 测试陈旧注释；其余断言全部保留。两测试 → 2 passed。
- 聚焦回归（11 套件：test_unified_compile / test_knowledge_execute / test_update_graph / test_unified_sync / test_unified_plan / test_unified_ocr / test_pipeline / test_apply_recovery / test_sync_results / test_config / test_knowledge_sync）→ **207 passed**（含上述 2 条；本轮无 flaky 失败）。
- `ruff check .` → All checks passed；`ruff format --check .` → 217 files already formatted。
- `python scripts/doc_guard.py` → PASS（84 documents）。
- 基于集成 tip `b34897b`（`git merge-base --is-ancestor b34897b HEAD` 成立；起点 HEAD a301f18 已含 05 OCR handler）。

### 未完成 / 协调

- 无未完成项；9 条验收全部实现并验证。
- 07/08 消费 `<run>/compile/result.json`（unified-stage-1.0）→ `artifacts.result` → `compile/compile-result.json`（unified-compile-1.0）；`execution.final_inputs/missing_items` 与 `body_scope` generated-regions-v1 供下一批正文复用（与 OCR 独立）；`graph.ocr`（prepared-jpeg-ocr-v1）由 05 写入、compile 保留不重跑；03 `compile-followups.json` 由本阶段消费并在真实发布后标记 done。

## Comments
