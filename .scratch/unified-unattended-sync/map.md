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

## Fog

- 真实 Graphify/OCR/语雀调用与生产迁移未授权；替身验收不得冒称真实生产验收。
- 旧实现暂停的 run 不默认新实现可恢复；历史 wash 首次整理需盘点迁移，不整目录删除。
- 元数据改名/搬动现有仅 HOLD 无执行器：04 补计划侧、06 补执行侧，需协调。
- Graphify 当前批级全有或全无失败、_run_graphify 内存缓冲输出：06 改部分发布 + 流式日志。
