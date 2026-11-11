# 09: 最终双轴审查修复

Type: task
Status: ready-for-agent
Blocked by: 08

## Context

- [规格](../spec.md)；[地图](../map.md)。08 完成后由 orchestrator 调用 Skill `code-review`（Standards + Spec 双轴）对集成分支审查。
- 本票关闭审查发现的全部问题；审查记录落 docs/（如 docs/unified-unattended-sync-review.md）。

## Acceptance and ownership

1. 修复 code-review 提出的全部 Standards 与 Spec 问题，每项有命名回归测试。
2. 不扩大范围：只修审查发现，不新增功能。
3. 修复合入后完整 make check + make smoke 绿；Answer 列每项发现→修复→回归证据。

主要拥有：审查修复改动、回归测试、审查记录文档。

## Working rules

在独立 worktree 基于集成分支 `unified-unattended-sync` 工作（分支 `uus/09-review-fixes`）；开工前确认基于集成 tip，否则 reset 到它（仅限本票新分支）。`make bootstrap`。调用 Skill `tdd` 为每项修复先写失败回归。完成后合入最新集成 tip 再跑完整 make check + make smoke，提交，Status 改 resolved，Answer 列证据。失败/部分实现保持 claimed。不得 reset/clean 共享状态或删私有实物。

## Comments
