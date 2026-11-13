# CLI orchestrator rewrite integration

Status: complete
Integration branch: rewrite/cli-orchestrator
Base: 9ba4cb1eec42154e821d23966dd183f820d1ae6c
Default branch: origin/master

## Task graph

01 工具 CLI + 02 基础层 + 08 Skylark → 04 discover → 05 apply/sync
03 Graphify + 05 → 06 切换/删除 → 07 最终验收/审查

## Notes

原 checkout 的未提交修改、语料、图谱、缓存与无关 worktree 保留。实现与验证在隔离 integration 分支；未运行生产同步、真实付费处理或发布。

## Decisions so far

- [01](issues/01-tool-cli-contracts.md#answer)：mdocr/mdcompare 公开批次与目录合同已交付，编排器不拥有工具算法。
- [02](issues/02-core-state-config.md#answer)：精简配置、版本/步骤事实、目标锁、子进程和只读监控已交付。
- [03](issues/03-graphify-public-cli.md#answer)：Graphify 独立整来源 CLI、源码身份、可复现补丁及 14 项合同证明已交付。
- [04](issues/04-discover.md#answer)：完整快照增量策略已交付，不下载正文、不回填历史、不按缺席删除。
- [05](issues/05-apply-and-sync.md#answer)：唯一串行 apply/sync、固定计划、尽力 OCR 与正式语料独立发布已交付。
- [06](issues/06-cutover-graph-monitor.md#answer)：Graphify 薄适配和入口切换已交付；旧流水线/越界实现/清理平台已删，Web 独立。
- [08](issues/08-skylark-export-cli.md#answer)：Skylark 独立 listing/export CLI 已交付；完整性在工具侧验证。
- [07](issues/07-delivery-validation.md#answer)：811 项完整检查、重建安装包 smoke 和独立两轴/OCR 审查已完成；全部确认问题已修复，证据集中在 [acceptance](acceptance.md)。

## Fog

本次实现与验收无未决问题。付费 OCR/Graphify、生产 UAT、上游发布和操作员全局安装未执行，不能从测试与隔离安装证明推断这些结果。
