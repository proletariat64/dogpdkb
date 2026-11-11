# 统一无人值守阶段同步实现地图

## Notes

- 集成分支：`unified-unattended-sync`；规格：[spec](spec.md)。
- 集成基线 `f09f45d` = origin/master（含 sync-state-baseline：instance_lock / sync_state / sync_progress / sync_time）。本地 master(e66970b) 落后 20 提交；工作树代码为准。
- 基线门禁：`make check` 1043 passed（2026-11-11）。
- 共享上下文笔记：`/tmp/unified-unattended-sync-notes/`（00-base-context 权威；01/02/03 探索笔记对 state-baseline 触及文件失效，以工作树为准）。/tmp 易失。
- 测试缝：可安装 dogpdkb CLI 黑盒 + 临时 corpus + 现有替身（本地 listing/details、Graphify shim、OCR HTTP 替身）。不用真实语雀/OCR/Graphify，不部署生产，不付费。
- 实现票各自 worktree（分支 `uus/NN-slug`），merger 合入集成；完成后 Standards/Spec 双轴审查（09）。
- 真实 corpus/graph 仅在主 checkout（gitignored）；有界离线 UAT 只读引用，不修改。

## Tickets

- [01 统一入口/runner/状态/status](issues/01-runner-state.md) — none
- [02 capture 下载复用](issues/02-capture-reuse.md) — none
- [03 持久结果/异常/处置](issues/03-results-anomalies.md) — 01
- [04 mdcompare 整批 + 自动 plan](issues/04-mdcompare-plan.md) — 02, 03
- [05 OCR 尽力阶段](issues/05-ocr-stage.md) — 04
- [06 compile 部分发布/恢复](issues/06-compile-stage.md) — 04
- [07 wash 清理/迁移/部署](issues/07-wash-cleanup.md) — 03, 05, 06
- [08 验收/UAT/门禁/smoke](issues/08-acceptance.md) — 01–07
- [09 最终审查修复](issues/09-review-fixes.md) — 08

## Decisions-so-far

- 任务图由 orchestrator 依据规格编写（无预置票）。01/02 并行起步；03 依赖 01；04 依赖 02/03；05/06 并行依赖 04；07 依赖 03/05/06；08 全依赖；09 审查修复。
- 复用既有 instance_lock 作为规格"用户级运行锁"，不另造锁。跨批次文档进度走 canonical `.sync-state.json`（Progress）；per-run 轻量状态单独保存（01）。
- 01 期间旧 sync flag 走带弃用提示的旧执行器（过渡），最终收敛在 08；不保留另一套默认规则。
- 比较器算法、Graphify 模型、图谱合并器不重写；不新增通用指纹系统；异常身份复用 book+doc ID+version。
- 规格+任务图提交 `cb8dfb9`。Wave 1 已启动：01（分支 uus/01-runner-state，worktree uus-01-runner-state）∥ 02（分支 uus/02-capture-reuse，worktree uus-02-capture-reuse），后台 implementer。
- [02 resolved](issues/02-capture-reuse.md#answer) 已合入：合并提交 `6b90ee8`（--no-ff，实现 HEAD 93ecc1e）。统一证据复用（canonical 下载→checkpoint→import→新导出），同 book+doc ID+版本瞬时相等+raw 直接有效（无新指纹）；历史富化保持 historical_enriched；观察/下载/发布版本分离。公开合同：`discover --snapshot … --import-downloads <records…>` 接受 knowledge-capture-1.0 snapshot 与 legacy export-progress.json；additive 字段 imports[]/exports[].imported_from。集成聚焦 207 passed、ruff check/format 干净、doc-guard PASS(84)。01 的 capture handler 不受影响。
- 2026-11-11 暂停后恢复：03 续作 implementer 后台启动（worktree uus-03-results-anomalies，基于集成 tip d7d605d）。保留提交 af0d120（异常登记+capture 归档+config）+ 未提交 WIP（archive_run 持久批次结果+finish/interrupt 接线+wash 存活测试）。剩余：item4 处置 CLI（cli.py 尚无 anomalies 分发，未起）、item3 重试抑制消费接线核验、item5 OCR 后补 followup 登记合同。04 阻塞于 03，frontier 暂仅 03。
- [01 resolved](issues/01-runner-state.md#answer) 已合入：合并提交 `00c2dfb`（--no-ff，实现 HEAD e5a5025；首个 implementer 触顶 150 turn，continuation 完成提交/合并/结案）。新增 unified_sync/sync_run/sync_stages/sync_status/stages.capture。公开合同：run 目录 `<staging>/<book>/<run-id>/`（run.json + `<stage>/result.json` + logs）；run 状态 `unified-run-1.0`（running/paused/completed/partial/failed/interrupted；阶段 pending/running/completed/partial/skipped/failed；config_identity resume 校验；FINISHED 释放 claim 不挡 cron）；阶段信封 `unified-stage-1.0`（artifacts/inputs/anomalies/counts）；handler `dogpdkb.stages.<stage>.HANDLER`（StageContext/StageResult/StageLog 流式日志），STAGE_OWNERS mdcompare+plan=04/ocr=05/compile=06；`dogpdkb status` 只读+JSON+pid/锁存活；legacy flag 弃用转发（08 收敛）。集成聚焦 188 passed、ruff 干净、doc-guard PASS(84)。

## Fog

- 真实 Graphify/OCR/语雀调用与生产迁移未授权；替身验收不得冒称真实生产验收。
- 旧实现暂停的 run 不默认新实现可恢复；历史 wash 首次整理需盘点迁移，不整目录删除。
- 元数据改名/搬动现有仅 HOLD 无执行器：04 补计划侧、06 补执行侧，需协调。
- Graphify 当前批级全有或全无失败、_run_graphify 内存缓冲输出：06 改部分发布 + 流式日志。
