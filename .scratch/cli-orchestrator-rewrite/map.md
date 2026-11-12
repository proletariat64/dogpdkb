# CLI orchestrator rewrite integration

Status: in-progress
Integration branch: rewrite/cli-orchestrator
Base: 9ba4cb1eec42154e821d23966dd183f820d1ae6c
Default branch: origin/master

## Task graph

01 工具CLI合同 ─────────┐
02 基础层 → 04 discover ├→ 05 apply/sync ─┐
03 Graphify公开CLI ──────────────────────┴→ 06 切换/删除 → 07 交付/审查

## Notes

当前原checkout未提交内容保留原处，integration仅复制本任务spec/research。实际实现基于已提交HEAD；用户本轮提供的完整AGENTS边界仍适用。

## Decisions so far

规格已确认；先工具公开合同，不以自研图算法/内部脚本绕过缺口。

## Fog

Graphify upstream源码与整来源公共合同需先验证。没有生产/付费执行授权。
