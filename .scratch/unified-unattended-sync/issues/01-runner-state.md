# 01: 统一 sync 入口：整批 runner、run 状态、status、日志

Type: task
Status: ready-for-agent
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