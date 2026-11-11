# 02: capture 下载复用与版本证据

Type: task
Status: ready-for-agent
Blocked by: none

## Context

- [规格](../spec.md) capture 与下载复用节；[地图](../map.md)。
- 先读 `/tmp/unified-unattended-sync-notes/00-base-context.md`；补充笔记 `02-knowledge-modules.md`（capture/checkpoint 合同、历史导入来源、exact-dict-vs-instant 版本匹配陷阱）。工作树代码为准（knowledge_capture.py 已含 state-baseline 的 canonical 状态/精确时间改动）。
- 现有能力：knowledge-capture-1.0 snapshot、knowledge-capture-checkpoint-1.0（按 book+列表版本复用成功 raw）、标签去重、近期 cutoff、pending 续跑、canonical state 下载选择。本票在其上补齐规格要求的复用证据链，不重写采集。

## Acceptance and ownership

1. **同版本有效原文零重复下载**：同知识库+同文档 ID+相同远端版本且原文文件有效时复用；有效性检查复用现有保证或直接比较，不新增通用指纹系统。缺失/失效必需文件重新获取。
2. **历史下载记录导入**：可导入历史同步或 UAT 中具有相同证据（知识库+文档 ID+版本+有效文件）的下载记录；不仅凭文件名、日期或文件存在判断同版本。导入记录与本次 checkpoint 复用走同一证据判定。在 Answer 公布导入入口/合同（CLI 参数或配置，沿用现有配置体系）。
3. **历史富化不冒充 raw**：无法证明版本的历史文件可继续作为已有基线（historical_enriched），但不能标成已验证当前 raw；不得把历史 OCR 富化 Markdown 标成原始导出。
4. **版本区别保留**：观察版本、下载版本、成功发布版本分开；成功下载不提前声称图谱更新。列表/详情版本分别保存（沿用）。
5. **既有边界不回归**：重叠标签只下载一次；不回填历史缺失；完整清单失败=阶段失败，单篇导出失败归档不阻断；缺席标签不推断删除。
6. snapshot 合同保持向后兼容（可加字段）；01 的 capture handler 按现有 documents/exports/raw 引用消费，不破坏。

主要拥有：knowledge_capture.py、必要的 get_sync_updates.py 最小分发、tests/test_knowledge_capture.py 及新增复用测试。避免改 cli.py/unified runner（01）、knowledge_plan/execute（04/06）。

## Working rules

在独立 worktree 基于集成分支 `unified-unattended-sync` 工作（分支 `uus/02-capture-reuse`）；开工前确认基于集成 tip，否则 reset 到它（仅限本票新分支）。`make bootstrap`。调用 Skill `tdd`：从一条失败的 CLI 黑盒测试开始，每轮记录 red/green。临时 corpus + 本地 listing/details 替身；不访问真实语雀，不运行 OCR/Graphify，不改生产语料/图谱/基线。完成后合入最新集成 tip 再跑聚焦测试 + ruff check + format --check，提交，Status 改 resolved，Answer 列公开合同与验证证据。失败/部分实现保持 claimed。不得 reset/clean 共享状态或删私有实物。

## Comments