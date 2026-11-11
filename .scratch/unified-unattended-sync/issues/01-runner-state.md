# 01: 统一 sync 入口：整批 runner、run 状态、status、日志

Type: task
Status: resolved
Blocked by: none

## Context

- [规格](../spec.md) 是完整契约；[地图](../map.md) 记录共享合同与集成决定。
- 先读 `/tmp/unified-unattended-sync-notes/00-base-context.md`（集成基线、已有锁/状态/进度模块、旧笔记失效范围）。探索笔记 01/02/03 作补充，工作树代码为准。
- 集成基线已含 sync-state-baseline：`instance_lock`（用户级 flock）、`sync_state`（canonical `.sync-state.json` v2）、`sync_progress.Progress`、`sync_time`（UTC+8）。规格所说"现有用户级运行锁"即 `instance_lock`，复用，不另造锁。
- 旧 `dogpdkb sync`（workflows/sync_knowledge_base.py：plan→corpus→graph→web）与 `sync --knowledge`（审批制）是被本规格取代的入口。本票建立新统一入口；旧执行器的最终移除/兼容转发收敛在 08，本票期间旧 flag 走带弃用提示的旧路径，不得 silently 改变旧行为。

## Acceptance and ownership

新建统一 `dogpdkb sync` 工作入口（新模块，如 `unified_sync.py` + `sync_status.py`），固定整批五阶段 capture→mdcompare→plan→ocr→compile：

1. **runner**：阶段整批依次执行，下一阶段等待当前阶段结束；无工作量阶段 skipped 后继续；阶段部分条目失败可正常结束并把可用集合交给下一阶段。`--stop-after <stage>` 在该阶段结束后暂停；`--resume <run>` 按原批次恢复（校验产物存在与输入仍符合原批次，不默默塞新输入）。配置走现有 `mdocr.config` 体系（--config/--book，绝对路径），不新增任意环境变量组。
2. **阶段插槽**：定义五阶段 handler 合同与 run 目录布局（wash 下 `<book>/<run-id>/`：run 状态、阶段产物、阶段日志）。本票注册并完整实现 **capture** handler（调用现有 knowledge_capture，沿用 knowledge-capture-1.0 snapshot 合同，离线替身可测）。mdcompare/plan/ocr/compile handler 由 04/05/06 注册；未注册阶段被请求时给出明确错误（不静默跳过、不伪造成功）。在 Answer 公布 run 目录布局、run 状态 schema、阶段 I/O 信封、handler 接口，供 03–06 消费。
3. **run 状态**：轻量 per-run 状态原子更新（阶段开始/条目进展/阶段结束）；run 状态 running/paused/completed/partial/failed/interrupted；阶段状态 pending/running/completed/partial/skipped/failed。跨批次文档进度仍写 canonical state（Progress），不为每条进度重写全量文档状态。completed/partial/正常 stop-after 退出 0；无法继续非零；用户中断沿用 130 语义。partial 是已结束批次，不残留阻挡下一次 cron 的活动声明；未完成批次不被新任务静默覆盖，启动时提示恢复目标。
4. **status**：`dogpdkb status` 只读，默认当前/最近批次，`--run`、`--json`；展示批次、状态、当前阶段、总数、完成/复用/失败数、开始/最近进展时间、耗时、停止原因、日志位置；结合 pid+运行锁判断存活，崩溃识别为 interrupted，不把遗留 running 当仍在运行，也不仅凭超时猜死；无记录正常返回；绝不恢复/重跑/推进任务。
5. **日志**：stdout 简洁批次结果；stderr 带阶段/进度/时间；外部工具输出流式写入对应阶段日志文件（不在内存干等、不丢诊断）；默认不输出 token/认证/文档正文。
6. **兼容**：旧 `--knowledge` 与旧默认 flag 在本票期间带弃用提示转发旧执行器（过渡）；新入口不带这些 flag 时走统一 runner。`sync --knowledge` 的最终兼容转发合同在 Answer 说明，08 收敛。

主要拥有：新 unified_sync/sync_status 模块、cli.py 分发、tests/test_unified_sync*.py（新）。避免改 knowledge_capture/plan/execute 内部（02/04/06 拥有）；capture 只按现有合同调用。

## Working rules

在独立 worktree 基于集成分支 `unified-unattended-sync` 工作（`git worktree add`，分支 `uus/01-runner-state`）；开工前确认基于集成 tip，否则 reset 到它（仅限本票新分支）。`make bootstrap`。调用 Skill `tdd`：从一条失败的 CLI 黑盒测试开始，每轮记录 red/green。测试用临时 corpus + 现有本地 listing/details 替身，不访问真实语雀/OCR/Graphify。完成后合入最新集成 tip 再跑聚焦测试 + ruff check + format --check，提交本票实现，将 Status 改 resolved，Answer 列公开合同与实际验证证据。任何失败/部分实现保持 claimed。不得 reset/clean 共享状态或删私有实物。

## Comments

## Answer

分支 `uus/01-runner-state`，实现提交 `5406d3a`，合并集成 tip `0f3da14`（含票 02）的 merge 提交 `1badc1c`，worktree `/home/ubuntu/dev/dogpdkb/.qoder/worktrees/uus-01-runner-state`。新增 `unified_sync.py`/`sync_run.py`/`sync_stages.py`/`sync_status.py`/`stages/{__init__,capture}.py`；改 `cli.py`（status 分发）、`instance_lock.py`（只读 `lock_is_held`）、`workflows/sync_knowledge_base.py`（弃用转发）。未改 knowledge_capture/plan/execute 内部；capture 只按 knowledge-capture-1.0 合同调用。合并票 02 无冲突。

### 公开合同（供 03–06 消费）

**1. run 目录布局**（`sync_run.RunLayout`，wash 下）

```
<config.staging>/<book_key>/<run-id>/
  run.json                 # 轻量 per-run 状态（unified-run-1.0）
  <stage>/                 # stage ∈ capture|mdcompare|plan|ocr|compile
    result.json            # 阶段 I/O 信封（unified-stage-1.0）
    ...                    # 阶段自有产物（capture: snapshot.json, staging/raw, staging/capture-checkpoint.json）
  logs/<stage>.log         # 流式阶段日志（append，行缓冲）
```

- `run-id = run-<YYYYMMDD-HHMMSS>`（UTC+8），同秒冲突追加 `-2/-3`。`new_run_layout` 创建，`find_run` 接受 run-id / run 目录 / run.json 路径，`book_runs` 枚举。
- 产物引用一律存**相对 run 根**（`RunLayout.relative`/`resolve`；绝对路径亦允许）。目录 0700、状态文件 0600、原子写（tmp+fsync+replace）、拒绝 symlink。
- 跨批次文档进度仍写 canonical `.sync-state.json`（`Progress`）；per-run 状态独立，单条进度不重写全量文档状态。

**2. run 状态 schema**（`run.json`，`sync_run.RunState`，`RUN_SCHEMA="unified-run-1.0"`）

顶层：`schema_version, run_id, status, book{key,name,url}, corpus, run_dir, config, config_identity, stages[5], stop_after, current_stage, pid, started_at, updated_at, ended_at, duration_seconds, stop_reason, exit_code, resume_of, counts{total,completed,reused,failed}, stage_states{...}`。时间均 UTC+8 ISO。

- run `status ∈ running|paused|completed|partial|failed|interrupted`。
- `stage_states[stage] = {status, attempts, started_at, ended_at, updated_at, counts{}, result(相对 result.json|null), log(相对), reason}`；stage `status ∈ pending|running|completed|partial|skipped|failed`。
- `config_identity`（非密）= `{corpus, staging, graph_dir, web, graphify_python, book{key,url,name}, settings: config.snapshot()}`；resume 必须逐字段相等，否则拒绝（不静默换批次）。
- 语义分组：`FINISHED_STATUSES=completed|partial|failed`（结束→`progress.finish()` 释放 canonical claim，不阻挡下次 cron）；`UNFINISHED_STATUSES=running|paused|interrupted`（阻挡新批次，启动提示 resume 目标）；`DONE_STAGE_STATUSES=completed|partial|skipped`（resume 复用，不重跑）。
- `interrupted` 是**观察态**，runner 不写：`observed_status` 仅当 recorded=running 且 `process_alive(pid)` 且 `lock_is_held()` 才报 running，否则 interrupted（不单凭超时）。主动中断/stop-after 写 `paused`。
- 退出码：completed/partial/正常 stop-after=0；failed=1；用户中断=130。`counts` 在 finish 时由各阶段 counts 聚合。
- 原子更新点：`start_stage`（attempts+1、重新 stamp started_at）、`progress(stage,**counts)`（条目进展）、`end_stage`、`finish`、`interrupt_stages`（未结束的 running 阶段回 pending 供 resume 重试）。

**3. 阶段 I/O 信封**（`<stage>/result.json`，`sync_stages.write_envelope`/`read_envelope`，`STAGE_SCHEMA="unified-stage-1.0"`）

`{schema_version, stage, run_id, status, started_at, ended_at, counts{}, artifacts{}, inputs{}, anomalies[], reason, log}`。

- `status ∈ completed|partial|skipped|failed`。
- `artifacts: {name → 相对/绝对路径引用}`——后续阶段用 `context.artifact(stage,name)`/`require(stage,name)` 取用（resolve+exists 校验）。capture 发布 `snapshot`（必需）、`checkpoint`、`raw_dir`（存在才发）。
- `anomalies: [dict]`——capture 形如 `{stage, book_id, document_id, code, source_version}`；runner 累计各阶段 anomalies 数决定 completed vs partial。03 的持久结果/异常移交在此基础上扩展。
- `counts: {str:int}`——capture: `total/completed/reused/failed/pending/documents`。04/05/06 各自定义计数键（建议沿用 total/completed/reused/failed 以便聚合）。
- 信封由 runner 自动落盘并同步进 run 状态；handler 只返回 `StageResult`。

**4. handler 注册接口**（`sync_stages`）

- `Handler = Callable[[StageContext], StageResult]`。每阶段模块 `dogpdkb.stages.<stage>` 暴露模块级 `HANDLER`。`load_handlers()` 惰性 import 已安装模块（缺失=未注册；有模块无 HANDLER=StageError）。`register_handler`/`handler_for`/`registered_stages` 可用。
- `STAGE_OWNERS`：capture=01（已实现），mdcompare=04，plan=04，ocr=05，compile=06。请求未注册阶段→`StageNotRegistered`（含 owner 票号与可用阶段），runner 记 failed 信封、run failed、exit 1；**不静默跳过、不伪造成功**。
- `StageContext`（handler 唯一入口，不触 runner 内部）：`stage, layout(RunLayout), state(RunState), config(SyncConfig), book(Book), corpus(Path), graph_dir(Path), progress(Progress), directory(=<run>/<stage>), log(StageLog), inputs(={earlier_stage: envelope})`。方法：`artifact(stage,name)->Path|None`、`require(stage,name)->Path`（缺失抛 StageFailed）、`count(**counts)`（原子阶段进度）。
- `StageResult(status="completed", counts={}, artifacts={}, inputs={}, anomalies=[], reason=None)`；status 非 completed|partial|skipped|failed → StageError。
- `StageLog`：`write(text)`（补 newline+flush）、`stream()->TextIO`、`popen_kwargs()->{stdout:stream, stderr:STDOUT}`（外部工具输出**流式落盘**，不在内存干等）、`capture()`（把 in-process stdout/stderr 重定向进日志）。runner 已用 `log.capture()` 包裹 handler。
- runner 捕获 handler 的 `(StageError, OSError, ValueError, RuntimeError)` → failed 信封（reason=`safe_diagnostic`，无 traceback）。**handler 不得抛 AttributeError 等未列异常**（会逃逸）。
- 阶段整批顺序执行；`DONE_STAGE_STATUSES` 阶段 resume 时复用信封不重跑；failed 阶段立即终止 run；`stop_after==stage` 结束后 paused。

**5. status CLI 表面**（`dogpdkb status`，`sync_status`，只读）

`dogpdkb status [--config C] [--book K] [--run RUN] [--json]`。

- 默认最近批次（优先 running，否则 started_at 最新）；`--run` 接受 run-id/目录/run.json。无记录→exit 0（human "no sync runs recorded"；JSON `{schema_version, status:"no-runs", runs:0}`）。未知 run→exit 1 "unknown run: ..."。
- JSON view：`schema_version, run, book, status(观察态), recorded_status, alive, current_stage, stop_after, stages{<stage>:{status,attempts,counts,started_at,ended_at,reason,result(绝对)}}, counts, started_at, updated_at, ended_at, duration_seconds, stop_reason, exit_code, run_dir, state(绝对), logs{<stage>:绝对}, resume(命令串|null；completed/partial 为 null)`。
- human report：run/book/status、current stage、stages 概览、items total/completed/reused/failed、started/last progress、elapsed、stop reason、run directory、logs、resume 提示。
- 存活判定结合 pid+用户级运行锁；崩溃识别 interrupted。**绝不写 run/canonical 状态或产物，绝不恢复/重跑/推进**（测试校验 canonical 字节不变）。watch 只需重复调用。

**6. 旧入口兼容转发合同**（过渡，08 收敛）

- `dogpdkb sync` **不带** LEGACY_FLAGS → 统一 runner（`unified_sync.main`）。带任一 legacy flag → stderr 打印 `deprecation_notice` 后转发旧执行器：含 `--knowledge` → `knowledge_sync.main`（审批制旧 knowledge）；否则 → 旧 `sync_knowledge_base` 管线（plan→corpus→graph→web）。旧行为不静默改变。
- `LEGACY_FLAGS`：`--knowledge, --snapshot, --baseline, --state, --execute, --approvals, --quality-cases, -i/--input, -s/--skylark-url, --staging, --cutoff, --dry-run, --graph-dir, --graphify-python, --web-dir, --skip-graph, --skip-web, --rebase-graph`。
- `--resume` 歧义消解：带值（下一 token 非 `-` 开头，或 `=值`）→ 统一 runner 的 run-id；布尔（无值/后接 flag）→ legacy。
- 弃用提示文案：`dogpdkb sync: deprecated legacy flags (...); forwarding to the legacy executor. The unified runner accepts --config, --book, --stop-after and --resume <run>.`
- `sync --knowledge` 最终兼容转发合同：本票期间保留审批制旧执行器 + 弃用提示；08 负责移除旧执行器/旧默认规则，届时 `--knowledge` 若保留只能作为带弃用提示、行为与新入口一致的转发，不保留另一套默认规则。
- 锁：统一 sync 走既有 `@locked_main`（用户级 flock），并发第二启动→`UPDATE_ALREADY_RUNNING`；status 用新增只读 `instance_lock.lock_is_held()`（探测即释放，从不授予/保持所有权）。`dogpdkb status` 在 `cli.py` argparse 前分发。

### 验证证据

- `make bootstrap` → PASS（worktree-local .venv，offline）。
- 合并集成 tip `0f3da14`（票 02：knowledge_capture +401、get_sync_updates、docs、test_capture_reuse）→ **无冲突**；capture handler 按 knowledge-capture-1.0 消费，`run_capture` 所需 args 字段齐全。
- 聚焦回归（一次）：`.venv/bin/python -m pytest tests/test_unified_sync.py tests/test_cli.py tests/test_lifecycle.py tests/test_sync.py tests/test_pipeline.py tests/test_instance_lock_cli.py tests/test_sync_progress_cli.py tests/test_knowledge_capture.py tests/test_capture_reuse.py -q` → **188 passed**（51.48s），其中 `test_unified_sync.py` **18 例**。
- `.venv/bin/python -m ruff check .` → All checks passed；`.venv/bin/python -m ruff format --check .` → 206 files already formatted；`.venv/bin/python scripts/doc_guard.py` → PASS (84 documents)。
- 18 例覆盖：stop-after 暂停可恢复、未注册阶段 loud fail、resume 复用冻结 capture、拒绝覆盖 paused 并提示 resume、崩溃识别 interrupted 且不触碰状态、legacy 弃用转发、零工作量 skipped、partial 交可用集合给下一阶段、完整清单失败=阶段失败、resume 校验产物存在/配置一致、failed 批次只重试失败阶段、无记录正常返回、最近/指定批次查询、运行锁拒绝并发、用户中断 130、结束释放 claim/暂停保留 claim、工具输出落盘且正文/凭证不入控制台。
- 全程临时 corpus + 本地 listing/details 替身（`write_remote_adapter`）+ fake-proxy；无真实语雀/OCR/Graphify/网络/付费/生产调用，未写生产语料/图谱/基线。
- 无新增功能超出本票；mdcompare/plan/ocr/compile handler 留待 04/05/06 注册。
