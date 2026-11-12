# 05: OCR 尽力完成阶段

Type: task
Status: resolved
Blocked by: 04

## Context

- [规格](../spec.md) OCR 尽力完成节；[地图](../map.md)。
- 先读 `/tmp/unified-unattended-sync-notes/00-base-context.md`；补充笔记 `03-ocr-compare-config-tests.md`（mdocr 缓存身份/复用、超时、token/额度错误、结果格式）与 `02-knowledge-modules.md`。工作树代码为准。
- 04 Answer：plan 的 OCR 名单与 OCR 结果引用 schema（基于现有 mdocr 结果格式）。03 Answer：异常归档 API、批次共享原因、重试抑制、OCR 后补合同。
- 现有 mdocr 图片处理/缓存能力沿用，不重写识别。
- 关键现状（见 00-base-context / 笔记 03）：**当前没有 OCR 结果缓存**——现有复用仅靠同 markdown 内保留的 `<!-- mdocr:ocr-begin -->` 块、内容命名 assets、文件名 checkpoint。本票需新建持久 OCR 缓存（独立于待清理 run），复用判定走 mdcompare `prepared-jpeg-ocr-v1` 证明合同（preprocessing+configuration 规范化相等、原图↔ prepared 字节对应、prepared-JPEG 字节相等、规范非空产物），不新造指纹。PaddleClient：token 仅来自 env PADDLEOCR_TOKEN，60s/调用、3 次重试、POST 提交不重试（计费）、900s job 截止。auth/quota 分类、全批停止、阶段预算、成功/缓存/失败/未尝试计数均不存在，需新建。**默认 sync 现在 OCR 失败即阻断发布（apply_sync_updates.py:610-626），与规格尽力完成相反，须移除该阻断。** 配置无 OCR/cache 键：新增 TOML 键（如 [paths].ocr_cache、[ocr] 段）经 current_config() 读取并更新 snapshot()/模板/README，不新增任意环境变量（PADDLEOCR_TOKEN 保持 env）。

## Acceptance and ownership

1. **只处理本批计划图片**：注册 ocr handler；复用 mdocr 处理与缓存。同一已验证图片内容+兼容处理配置优先复用有效成功结果，不因重复同步主动重 OCR；URL 相同不足以证明图片未变。图片变化或无有效结果才识别。
2. **缓存保护**：失败/空白/不可解析的新结果不覆盖已有有效结果；不同图片的旧结果不当新图片结果用。成功结果经现有格式/产物检查后持久保存（长期缓存独立于待清理 run），不做模型语义质量审批。
3. **失败隔离与全批停止**：单图失败记录后继续其他图片；认证不可用/额度耗尽等全批共享原因停止本批剩余 OCR（记批次原因+枚举受影响图片，交 03 归档）；未知错误不无限重试。
4. **有限等待与预算**：外部调用有限超时+重试；OCR 阶段有限总时间预算，可运行默认值（走现有配置体系）。预算耗尽保留已完成结果、未完成项归档、进入 compile。
5. **计数**：区分成功/缓存复用/失败/未尝试；额度不足未执行不计成功。
6. **不阻断编译**：OCR 补齐非完成必要条件；产出 04 定义的 OCR 结果引用供 compile 消费；未识别图片保留引用。后补成功更新缓存+真实完成+处置，改变已发布编译输入时登记后续编译（03 合同）。
7. **重试抑制**：同版本已归档 OCR 失败不每日重试；新版本正常处理。

主要拥有：ocr 阶段 handler、mdocr 必要扩展（预算/全批停止/计数）、相关测试。避免改 plan（04）、compile（06）、比较器。

## Working rules

在独立 worktree 基于集成分支 `unified-unattended-sync` 工作（分支 `uus/05-ocr-stage`）；开工前确认基于集成 tip（含 04），否则 reset 到它（仅限本票新分支）。`make bootstrap`。调用 Skill `tdd`：从一条失败的 CLI 黑盒测试开始，每轮记录 red/green。临时 corpus + OCR HTTP 替身；无真实 OCR/token/付费调用。完成后合入最新集成 tip 再跑聚焦测试 + ruff check + format --check，提交，Status 改 resolved，Answer 列公开合同与验证证据。失败/部分实现保持 claimed。不得 reset/clean 共享状态或删私有实物。

## Answer

### 实现

注册 runner `ocr` handler（STAGE_OWNERS ocr=05），沿用 mdocr 图片处理/规范化与 PaddleOCR 客户端，不重写识别。主要改动：

- `src/dogpdkb/stages/ocr.py`（`HANDLER`）：只消费 plan 的 OCR 工作名单 `ocr[].resources`（kind=image），不重规划。逐图下载原图→mdocr 规范化为内容命名 prepared JPEG→`verify_reuse` 命中则复用、否则 `PaddleClient.convert`→非空+`build_ocr_block` 格式检查→`persist_success` 持久化。单图失败记录后继续；共享 auth/quota 原因或预算耗尽停止本批剩余、归档未完成、进入 compile。成功证据写回 canonical graph 记录 `ocr` 字段。产物 `ocr/ocr.json`（OCR 结果引用）+ 01 信封 `ocr/result.json`。
- `src/dogpdkb/ocr_cache.py`（新增）：长期成功缓存，独立于待清理 wash run。原子写（tempfile+fsync+os.replace，0600，symlink 守护）。
- `src/mdocr/paddle.py`：`OcrSharedError`/`OcrAuthError`(401)/`OcrQuotaError`(403/429) 分类；有限超时/重试（60s/请求、GET 瞬态 3 次重试、POST 提交不重试（计费）、900s job 截止）。
- `src/mdocr/config.py`：`[paths].ocr_cache`、`[ocr].budget_seconds` 经 `load_config`/`current_config()` 读取，纳入 `snapshot()`。
- `src/dogpdkb/workflows/apply_sync_updates.py`：移除发布阻断 OCR 步（旧 main 流程 610-626 块），legacy apply 不再跑 mdocr/不再因 OCR 失败中止发布；`run_ocr_with_resume` 工具保留供直接调用者；模块 docstring 同步更正。
- 配置模板/文档：`default.toml`、`src/dogpdkb/resources/dogpdkb.toml`（init-config 便携模板）、`README.md` 均含新键。

未改 plan（04）、compile（06）、比较器算法。

### 公开合同

**1. OCR 成功缓存布局/schema**（`ocr_cache.py`，长期、独立于 wash）

- 根：`<config.ocr_cache>/<slug(book_key)>/`；条目目录键 = 现有内容命名 prepared-asset stem `<name>-<digest8>`（**不新造指纹**）。
- 条目文件：`original<ext>`（原图字节）、`prepared.jpg`（规范化 JPEG）、`artifact.md`（mdocr OCR 块）、`evidence.json`（下述证据）。
- 证据 schema = 04 `prepared-jpeg-ocr-v1`：`{contract:"prepared-jpeg-ocr-v1", preprocessing:{name:"mdocr",version}, configuration:{engine:"paddleocr",model,jpeg_quality}, images:[{resource_id, source_path, prepared_image, status:"success", artifact}]}`（绝对 durable 路径）。
- 复用判定 `verify_reuse`：preprocessing/configuration 规范化完全一致 + 存在 success 记录 + 缓存原图字节==本批下载字节 + 缓存 prepared 字节==本批规范化字节且 `is_prepared_jpeg` + artifact 为规范非空 mdocr 块且其声明 source 解析到缓存 prepared JPEG。**直接字节比较**：URL 相同但内容变化不复用；不同图片旧结果不当新图片结果用；截断 digest 别名被全字节比较拦截。
- 保护：`persist_success` 仅在非空+格式/产物检查通过后调用；失败/空白/不可解析结果绝不入缓存、绝不覆盖既有有效结果。

**2. 阶段产物 + OCR 结果引用**（供 06 compile 消费）

- `ocr/ocr.json`，schema `unified-ocr-1.0`：`{schema_version, book, documents:[{book_id, document_id, source_version, ocr:<prepared-jpeg-ocr-v1 证据>, execution:{final_inputs:[{kind:"ocr_artifact",resource_id,path}], missing_items:[{kind:"ocr_artifact",resource_id,code,reason}], completed_at, status:completed|partial}}], counts, batch_cause?}`。每个计划文档保留一行，未识别图片保留引用（记入 `missing_items`，不伪装成已完成输入）。
- 信封沿用 01 `unified-stage-1.0`：`artifacts={"ocr":"ocr/ocr.json"}`，`inputs={"plan":...}`，`anomalies`=03 富化记录。
- canonical 回写：成功证据按 resource_id 合并进 `.sync-state.json` `books.<book>.documents.<id>.graph.ocr`，`steps.ocr=completed`，供下一批 mdcompare 验证复用。

**3. 预算/全批停止/计数**

- 计数 `counts={total, success, reused, failed, unattempted}`：success=本批新识别，reused=缓存复用，failed=单图错误，unattempted=未执行（全批停止/预算耗尽）。**额度/认证/预算不足未执行计 unattempted，绝不计 success**。
- 单图失败（ImageError/ImageSkip/OSError/RuntimeError/ValueError）→ failed + `OCR_FAILED` 异常 + missing_items，继续其他图片；未知错误不无限重试。
- 全批共享原因（`OcrAuthError`→`OCR_AUTH_UNAVAILABLE`、`OcrQuotaError`→`OCR_QUOTA_EXHAUSTED`）或预算耗尽（`OCR_BUDGET_EXHAUSTED`）→ 停止剩余 OCR，经 03 `archive_anomalies(..., batch_cause={stage:"ocr",code,reason})` 记**一条**批次原因并枚举受影响 anomaly_ids；剩余图片计 unattempted + missing_items + 异常。
- 预算：`config.ocr_budget_seconds`（`[ocr].budget_seconds`，默认 1800，minimum 0），逐图检查 deadline；耗尽保留已完成结果、归档未完成、进入 compile（OCR 完成非 compile 前提）。

**4. 配置键**（经 `current_config()`，无新增环境变量）

- `[paths].ocr_cache`（默认 `ocr-cache`）、`[ocr].budget_seconds`（默认 1800）。二者纳入 `snapshot()`（resume 兼容校验）；`default.toml`/便携模板/README 已更新。`PADDLEOCR_TOKEN` 保持 env-only。

**5. 重试抑制**（消费 03）

- plan 用 `suppressing_anomalies(config, book_key)` 按 `(document_id, version_stamp)` 抑制同版本已归档失败（pending/abandoned/done，stale 除外）。同版本 `OCR_FAILED`（pending）→ 该文档移交事后 agent，不进 OCR 工作名单、不重新提交、attempts 不增；新远端版本（listing+details 的 `content_updated_at` 同步 bump）version_stamp 不同→正常处理。放弃只作用该版本，不永久封禁来源。

**6. OCR 后补**（03 合同，05 产物兼容）

- `OCR_FAILED` 异常经 `dogpdkb anomalies --resolve ID --disposition done --evidence FILE` 处置；evidence `unified-posthoc-evidence-1.0`：`{cache:[{kind,path}], completion:{step:"ocr",details}, input_changed}`。`record_posthoc_success` 先校验 cache 可读→写真实 canonical 完成（`steps.ocr`）→`input_changed` 则 `register_compile_followup`（pending，直到 06 compile 发布记真实完成）→最后置 disposition=done。**仅写 OCR 缓存 ≠ 完成知识更新**。

### 验收对照

1. 只处理本批计划图片 + 复用不主动重 OCR ✓（`test_ocr_stage_processes_the_planned_image_and_persists_a_success_result`、`test_repeat_sync_reuses_the_cached_success_without_a_second_submission`、`test_same_url_with_changed_image_content_is_recognized_again`）。
2. 缓存保护 ✓（`test_an_invalid_result_never_overwrites_a_cached_success[blank|garbage]`：空白/不可解析不覆盖有效结果、不同图片不复用；成功经格式/产物检查后持久化；缓存独立于 wash）。
3. 失败隔离 + 全批停止 ✓（`test_a_single_image_failure_is_recorded_and_the_others_continue`、`test_auth_unavailable_stops_the_whole_batch`、`test_quota_exhaustion_stops_the_whole_batch`）。
4. 有限等待 + 预算 ✓（PaddleClient 60s/3 重试/POST 不重试/900s job；`test_budget_exhaustion_archives_unfinished_and_proceeds_to_compile`：预算耗尽保留已完成、归档未完成、进入 compile）。
5. 计数区分 ✓（success/reused/failed/unattempted；`_batch_stop_assertions`：额度不足未执行 unattempted=2、success=0）。
6. 不阻断编译 + 引用 + canonical 回写 + 后补 ✓（reference 含未识别图片 missing_items；graph.ocr 回写；`test_posthoc_success_resolves_an_archived_ocr_failure_and_registers_compile`：真实完成+处置+登记后续编译）。
7. 重试抑制 ✓（`test_same_version_archived_ocr_failure_is_not_retried_until_new_version`：同版本移交不重试、新版本正常 sync）。

### 验证证据

- 聚焦 `tests/test_unified_ocr.py`：11 passed（CLI 黑盒，临时 corpus + OCR HTTP 替身，无真实 OCR/token/付费/网络）。
- 合并聚焦：`test_unified_ocr + test_unified_plan + test_unified_sync + test_ocr + test_paddle + test_mdcompare_ocr + test_sync_results + test_config + test_platform + test_apply_recovery + test_pipeline` = **199 passed**。
- `ruff check .`：All checks passed；`ruff format --check .`：215 files already formatted。
- `python scripts/doc_guard.py`：PASS (84 documents)。
- 已合入最新集成 tip `c300b63`（docs-only map.md），merge 提交 `6fc1f25`，无冲突。实现 HEAD `2037c07`。
- 已知 flaky `test_user_interrupt_pauses_the_batch_with_exit_130` 本轮聚焦运行通过。
- 修复一处测试替身隐患：`set_mode` 重写 sitecustomize 时，同长度 MODE 串在同一秒内可能命中按 (mtime,size) 校验的 stale `.pyc`，导致沿用旧边界；已禁用同步子进程字节码写入并在重写时清除缓存（`bef1a99`），garbage 模式 15 次迭代 0 失败。

### 未完成 / 协调

- compile（06）消费 `ocr/ocr.json` 引用与 `execution.final_inputs/missing_items`；本 worktree compile handler 仍未注册（属 06），全量 sync 在 compile 失败属预期并释放批次 claim。
- mdcompare incoming 证据复用短路属 04/比较器；OCR 阶段长期缓存复用已保证不主动重 OCR（reused 计数、无二次提交）。
- 后补 followup 保持 pending 直到 06 compile 发布并记真实完成（03/06 合同）。

## Comments
