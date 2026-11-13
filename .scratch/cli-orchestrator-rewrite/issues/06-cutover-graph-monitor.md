# 图谱薄适配、入口切换与旧代删除

Status: resolved
Type: task
Blocked by: 03, 05
Spec: ../spec.md

## Scope

接公开 Graphify CLI，接正式CLI/status/watch，移除旧执行器、图/OCR内部算法、wash/anomalies处置与兼容参数层，只保留一套行为。修复 Web/lifecycle 必需的配置边界，不改产品行为。删除仅镜像旧越界内部的测试。

## Acceptance

安装CLI黑盒验证全部合同、导入守卫零违规、无内部脚本绕行、Web不被sync调用；0/2/1、64、130/143；最终源码4–6k预算含编排脚本。

## Workflow

独立 worktree/branch，基于 rewrite/cli-orchestrator 当前 tip；先读 tdd 技能，以已确认 CLI seam 做 red→green。完成前合入 integration tip，运行相关检查，提交后报告 SHA。所有 shell rtk 前缀，所有代码关系先 code-review-graph repo_root=本工作树。禁止生产/付费调用，禁止修改用户原 checkout。

## Comments


- Implemented `graph.run_graph` and direct CLI routing. Graphify receives one complete source per serial public subprocess, with public inventory/report validation only; the graph artifact is opaque.
- Removed 55 obsolete source/test files, including both old pipelines, foreign tool internals, duplicated state/results/progress, cleanup/disposition platforms and mirrored legacy tests. Retained independent Web/lifecycle; Web now uses orchestrator settings only.
- Durable graph versions advance only on acknowledged publication; failed/unknown versions remain quarantined. Interrupted current sources are unknown; later unstarted sources retain pending work and have no graph attempt. Reuse requires verified graph baseline and metadata acknowledgement. Input witnesses prevent stale manifest execution; pending clears only for matching version/metadata with no unstarted OCR step.
- Merged integration05 `7f6be0312d59c385680afb17196ed1474c517dc6`; joint public CLI tests: 44 passed (12 graph, 13 apply/sync, 14 core/discover, 5 Web). Focused Ruff/format and diff checks pass; graph refreshed and changes analyzed against origin/master.
- Boundary test confirms zero foreign imports in src/dogpdkb. Full runtime guard currently has only two violations in old `scripts/smoke_install.py`; deleting those embedded foreign imports is ticket07 scope and the only remaining guard dependency. Root explicitly authorized landing06 before07 to avoid an integration cycle. Full repository gates/install proof remain07.

## Answer

Graphify 薄适配、直接入口和状态监控已切换，旧执行器/内部工具算法/重复状态与清理平台已删除。保留独立 Web/lifecycle。最终守卫含 smoke 脚本零越界，真实 graph 内容保持不透明；见 [graph contract](../../../docs/orchestrator-graph-contract.md) 和 [acceptance](../acceptance.md)。
