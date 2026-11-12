# 07: wash 清理、历史迁移与部署文档

Type: task
Status: resolved
Blocked by: 03, 05, 06

## Context

- [规格](../spec.md) 持久结果、异常移交与 wash 清理节 + 查询、日志与部署方式节；[地图](../map.md)。
- 先读 `/tmp/unified-unattended-sync-notes/00-base-context.md`；补充笔记 01（wash 布局）。03 Answer：持久结果/异常输入位置。01 Answer：run 目录布局。05/06 Answer：长期缓存/正式产物位置。
- wash 是批次工作空间（分页/阶段报告/plan/临时编译文件），不能是长期原文/成功 OCR/正式语料/图谱/异常恢复输入的唯一位置。

## Acceptance and ownership

1. **自动清理**：每次 completed 或 partial 结束后清理更早的已结束工作目录，只保留最近一次已结束批次 + 暂停/未完成批次。持久历史结果、异常输入、长期缓存不随 wash 清理。
2. **清理安全**：清理前先完成结果归档与必要输入持久化；确认状态/缓存/异常记录不再引用待删目录中的唯一文件。清理失败记录结果、不破坏有效知识库，下次结束可再清理。
3. **历史 wash 首次整理**：盘点引用并迁移必要产物，不直接删整目录。长期缓存与异常归档本次不引入自动淘汰策略。旧实现暂停的 run 不默认新实现已验证可恢复（迁移标注，恢复需校验）。
4. **部署文档**：crontab 明确程序/配置/日志位置、运行环境提供既有认证；shell 合并追加 stdout/stderr 到普通日志；系统 logrotate 轮转。工具不内置日志轮转守护、不自动安装 cron。文档落 docs/（如 docs/unattended-sync.md），含可复制 crontab/logrotate 示例。
5. **清理后异常仍可读**：历史异常必要输入在清理后仍可读取（与 03 归档一致）；旧状态引用迁移正确。

主要拥有：清理模块/runner 收尾钩子、历史迁移工具、部署文档、相关测试。避免改阶段 handler 核心逻辑。

## Working rules

在独立 worktree 基于集成分支 `unified-unattended-sync` 工作（分支 `uus/07-wash-cleanup`）；开工前确认基于集成 tip（含 03/05/06），否则 reset 到它（仅限本票新分支）。`make bootstrap`。调用 Skill `tdd`：从一条失败的 CLI 黑盒测试开始，每轮记录 red/green。临时 corpus + 替身；清理测试用构造的 wash 目录，不删真实私有实物/生产语料。完成后合入最新集成 tip 再跑聚焦测试 + ruff check + format --check，提交，Status 改 resolved，Answer 列公开合同与验证证据。失败/部分实现保持 claimed。不得 reset/clean 共享状态或删私有实物。

## Answer

### 实现

新增 `src/dogpdkb/sync_wash.py`（自动清理 + 历史盘点/迁移 + `dogpdkb wash` CLI）、`tests/test_unified_wash.py`（10 条 CLI 黑盒测试）、`docs/unattended-sync.md`（部署文档，README 增指针）。runner 收尾钩子 `unified_sync._clean_wash` 接在 03 归档之后：`state.finish` → `_archive_result`（`archive_run` + 异常归档）→ `progress.finish()` 释放 claim → `_clean_wash`，且**仅 completed/partial** 触发。未改任何阶段 handler 核心逻辑（capture/mdcompare/plan/ocr/compile），未新增配置键（复用 `[paths].staging/results/ocr_cache`），未新增环境变量。清理/迁移测试全部用 tmp_path 内构造的 wash 目录（真实批次 + `clone_run` 克隆 + 手工 legacy 目录），不触碰真实私有实物与生产语料。

### 公开合同（供 08）

**收尾钩子语义**：`cleanup_wash(config, book_key, *, corpus, trigger=state.layout)` 的异常被 `_clean_wash` 捕获（`OSError`/`ValueError`），只打印 `wash cleanup failed: <safe_diagnostic>` 后返回，**绝不改变批次状态/退出码**；成功打印 `wash cleanup: removed=N kept=N failed=N migrated=N`，每条失败追加 `wash cleanup deferred: <safe_diagnostic>`。failed/interrupted/paused 的 run 不清理。

**保留规则**：遍历 `book_runs(<staging>, book_key)`，用 `observed_status` 判定（含 pid/锁存活）。kept 的四种理由：`run state is unreadable`（读不出）、`unfinished batch must stay resumable`（不在 `FINISHED_STATUSES`，即 running/paused/interrupted）、`most recent finished batch`（按 `ended_at or started_at` + run_id 排序的最新已结束，或本次 trigger）、`batch result is not archived yet`（`<results>/<slug>/runs/<run-id>.json` 不存在）。其余才候选删除。**永不触碰** `<results>`、`<ocr_cache>`、canonical corpus/graph/`.sync-state.json`；长期缓存与异常归档**无自动淘汰策略**。

**删除前安全顺序**：`migrate_references(..., strict=True)` 盘点引用 → 复制唯一文件到持久库 → 原子改写记录 → 重新 `references()` 复核为空 → 才 `shutil.rmtree`。引用来源 `sources()`：canonical `.sync-state.json`（base=corpus，改写走 `save_state` 原子写 + symlink 守卫）、`<results>/<slug>/anomalies.json`（忽略 provenance 键 `source`，保留历史来源）、`compile-followups.json`、每个 `<ocr_cache>/<slug>/*/evidence.json`（其余走 `atomic_json`）。记录字符串只有解析为**真实存在**的路径且落在待删目录内才算引用（`_resolved`，绝对或相对 base）；**目录引用**（活动 claim/阶段工作区）在 strict 模式阻断删除并记 `directory-reference`。任一持久记录不可读 → `records-unreadable`，整轮不删。

**持久副本**：`<results>/<slug(book)>/migrated/<slug(run-id)>/<相对原目录路径>`，目录 0700、文件 0600，tmp+replace 原子落盘；目标已存在则**直接字节比较**——相同即复用，不同抛 `WashError("durable copy conflict for …")` 并保留目录（无新指纹机制）。只有新建副本计入 `migrated`，故重复清理/迁移幂等。

**清理记录** `<results>/<slug>/cleanup.json`，schema `unified-wash-cleanup-1.0`（`CLEANUP_SCHEMA`）：`book_key, staging, results_dir, trigger_run, updated_at, counts{removed,kept,failed,migrated}, runs[], migrated[], failures[]`。`runs[]`=`{run_id, run_dir, status, action: removed|kept|failed, reason}`（按 run_id 排序，覆盖全部 work 目录）；`migrated[]`=`{run_id, from, to, source, field}`；`failures[]` 的 reason ∈ `records-unreadable`/`directory-reference`/`migration-failed`/`reference-rewrite-failed`/`removal-failed`/`cleanup-record-failed`，detail 经 `safe_diagnostic`（不含 token/正文）。失败非致命、不破坏有效知识库，下次结束重试；记录本身写失败也进 failures。

**历史整理 CLI**：`dogpdkb wash --config <file> --book <key> [--json] [--migrate]`（`cli.py` 分发 `sync_wash.main`，`wash --help` 可用）。默认**只读盘点**；`--migrate` 在既有 `updater_lock()` 内执行（并发批次 → exit 1 `dogpdkb wash: …`）；`WashError/RunError/OSError/ValueError` → exit 1 + safe_diagnostic。**绝不删除任何目录**。

盘点视图 schema `unified-wash-inventory-1.0`：`book_key, staging, results_dir, counts{runs,unified,legacy,unknown,references,necessary_artifacts,cleanable,migrate}, runs[]`；每 run=`{run_id, run_dir, kind, status, resume_verified, annotation, result_archived, references[{source,field,path}], necessary_artifacts[], action, reason}`（legacy 另含 `records[]`）。`classify()`：仅 `run.json` schema `unified-run-1.0` → `kind=unified, resume_verified=true`；含 `pipeline-state.json`/`export-progress.json`/`capture-checkpoint.json` 任一 → `kind=legacy, resume_verified=false`（status 取 legacy `pipeline-state.json.status`）；其余 `kind=unknown, resume_verified=false`。`action`：unified+已结束+结果已归档+无引用 → `cleanable`；有引用或必要产物 → `migrate`；否则 `preserve`。必要产物 `necessary_artifacts()` = `**/raw/*.md` + `**/snapshot.json` + 三种 legacy 恢复记录。

迁移视图 schema `unified-wash-migration-1.0` → 持久 `<results>/<slug>/wash-migration.json`：`counts{runs,migrated,failures}, runs[{…classify 字段, references_migrated, artifacts[{from,to}], migrated[]}], migrated[], failures[]`；对非 unified 目录**就地**写 `<run-dir>/wash-migration.json`（同 schema，`resume_verified:false` + annotation + `migrated_at` + artifacts）。legacy 标注：`paused under the superseded implementation; the unified runner has not verified recovery - recovery requires validation of inputs and artifacts`；unknown：`unrecognized work directory; preserved for manual review`。迁移用 `strict=False`（目录引用不阻断可复制的文件），重复执行 `counts.migrated == 0`。

**部署文档合同** `docs/unattended-sync.md`：位置表（程序 `/home/ops/.local/bin/dogpdkb`、配置 `/home/ops/.config/dogpdkb/dogpdkb.toml`、books.json、批次日志 `/home/ops/.local/state/dogpdkb/sync.log`、wash/results/ocr-cache/corpus 及各自所有者）；认证由运行环境提供既有凭据（utoo-proxy 登录、`PADDLEOCR_TOKEN` 仅进程 env，不入 crontab/日志/结果/配置）；可复制 crontab（`30 2 * * * … dogpdkb sync --config … --book fund >> …/sync.log 2>&1`，shell 合并追加 stdout+stderr 到普通日志，多 book 错峰）；可复制系统 logrotate stanza（`/etc/logrotate.d/dogpdkb`：daily/rotate 14/missingok/notifempty/compress/delaycompress/**copytruncate**，轮转的正是 cron 追加的那个日志）；退出码 0（completed/partial/正常 `--stop-after` 暂停）、1、130，partial 释放 claim 不挡下次 cron；只读查询 `status`/`anomalies`；清理与历史整理语义。明示工具**不安装 cron、不跑调度器或日志轮转守护**、不改用户 crontab/logrotate 配置。

### 验收对照

1. **自动清理** → `test_a_finished_batch_cleans_earlier_finished_work_directories`（第二次结束后第一次目录消失、只剩最近已结束、两批持久结果都在）+ `test_cleanup_keeps_paused_and_unfinished_workspaces`（真实 `--stop-after capture` 暂停 run + 构造 paused 克隆 + 陈旧 pid 的 running 克隆均 kept，`--resume` 后批次 completed，更早已结束目录 removed）+ `test_archived_anomaly_inputs_and_ocr_cache_survive_cleanup`（持久结果/异常输入/长期 OCR 缓存不随 wash 清理）。
2. **清理安全** → `test_cleanup_defers_a_workspace_whose_result_is_not_archived`（结果未归档先 kept，归档后下次结束 removed）+ `test_cleanup_failure_is_recorded_and_retried_without_failing_the_batch`（0o500 目录删除失败 → `counts.failed=1`、`removal-failed`、批次仍 completed/exit 0、语料与结果完好，权限恢复后重试 removed）+ `test_cleanup_leaves_the_canonical_corpus_graph_and_state_intact`（稳态两批间语料 md 字节/图节点与边结构/发布 status-version-file 投影完全不变、`active_run is None`、状态中每条绝对路径仍存在）。
3. **历史首次整理** → `test_wash_inventory_reports_legacy_references_without_deleting`（只读：目录字节与 canonical 状态不变、无 migrated/无 manifest；legacy `resume_verified=false` + 含 validation 的标注 + 引用清单 + action=migrate）+ `test_wash_migrate_persists_necessary_artifacts_and_rewrites_old_references`（raw 原文字节相同落到 `<results>/migrated/<run>/raw/…`、canonical `steps.download.raw.path` 改写指向持久副本、legacy 目录与恢复记录一个不删、就地标注、重跑 `migrated=0`）。无缓存/异常自动淘汰（代码无淘汰路径，由 item1 的缓存/异常存活测试保护）。
4. **部署文档** → `test_deployment_doc_offers_copyable_crontab_and_logrotate_examples`（fence 内 crontab 行含绝对程序/`--config`/`--book`/`>> 日志 2>&1` 且不含凭据；logrotate stanza 含 daily/rotate N 且轮转同一日志路径；文中明示 does not install、logrotate、`PADDLEOCR_TOKEN`、`dogpdkb wash`、results 与 ocr-cache 位置）。
5. **清理后异常仍可读 + 旧引用迁移正确** → `test_archived_anomaly_inputs_and_ocr_cache_survive_cleanup`（partial 批次的异常自身 work 目录被下一批清理后，`anomalies --show` 仍 pending、`necessary_inputs[0].path` 在 wash 外且字节等于原 raw；OCR 缓存整条目字节不变）+ `test_cleanup_migrates_an_anomaly_input_that_only_lives_in_wash`（唯一副本仅在待删 wash 内 → 先迁移再删，异常记录指向持久副本且字节一致，provenance `source` 保留原值）+ item3 的 canonical 下载复用引用改写测试。

### 验证证据

- TDD 竖切（每轮先红后绿，早提交）：`5a87776` 清理钩子 → `b750154` 暂停/未归档/不可删安全 → `4518a49`+`47cd382` wash 盘点与迁移 CLI → `0867e70` 异常输入与 OCR 缓存存活 → `395ec00` 部署文档 → `55ad518` README 指针。
- 聚焦门禁 7 套件（test_unified_wash / test_unified_sync / test_sync_results / test_unified_ocr / test_unified_compile / test_config / test_pipeline）→ **105 passed**（85.64s，含新增 10 条；本轮 flaky interrupt 测试未触发）。
- 全量门禁 `make check`（doc-guard + lint + 全量 pytest）→ **1128 passed**（229.23s；基线 1118（06 合入后）+ 本票新增 10），exit 0，无 flaky 失败。
- `ruff check .` → All checks passed；`ruff format --check .` → 220 files already formatted。
- `python scripts/doc_guard.py` → PASS（85 documents，含新增 docs/unattended-sync.md）。
- 集成 tip：开工基于 `de20f83`（含 01–06，`merge-base --is-ancestor` 校验通过）；收尾前合入最新集成 tip `29037ce`（merge 提交 `c276d9d`，仅 map.md 一行，无冲突），合并后重跑上述聚焦门禁与 ruff/doc-guard 全绿。

## Comments
