# 06: compile 阶段：整来源更新、部分发布与恢复

Type: task
Status: ready-for-agent
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

## Comments
