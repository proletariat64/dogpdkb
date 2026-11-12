# 文档、隔离安装与最终验收

Status: resolved
Type: task
Blocked by: 06
Spec: ../spec.md

## Scope

更新使用/工具合同/安装文档和示例配置，清除与新spec冲突的有效操作说明；运行focused suite+必要gate+隔离安装smoke。刷新图、分析实际default branch影响，再进行standards/spec独立review并修复确认问题。

## Acceptance

所有票据有实际提交与验证证据；不冒充付费/生产验收；报告源码/测试规模、各proof层及公开工具前置任务是否真正完成。

## Workflow

独立 worktree/branch，基于 rewrite/cli-orchestrator 当前 tip；先读 tdd 技能，以已确认 CLI seam 做 red→green。完成前合入 integration tip，运行相关检查，提交后报告 SHA。所有 shell rtk 前缀，所有代码关系先 code-review-graph repo_root=本工作树。禁止生产/付费调用，禁止修改用户原 checkout。

## Comments

## Answer

最终实现完成，所有确认的 Standards/Spec 问题和自动 OCR 审查的 21 个 high/medium 问题均由同一个隔离修复 implementer 完成；逐项结案见 [review](../review.md)。最终 `make check`：811 passed，67.62s；docguard109、Ruff、205 Python 格式检查与边界零违规通过。

`/tmp/dogpdkb-orch-reviewed-dist` 的 wheel/sdist 重建后，隔离安装 CLI smoke 通过；43 个运行时 Python 模块在两种归档中与最终源码逐字节相同。编排器 3,531 行，加运行脚本 343 行，共 3,874 行；全部测试 10,331 行。实际证明、工具前置交付、各层未执行项见 [acceptance](../acceptance.md)。独立 Graphify/Skylark 已提供源码提交、可复现补丁与公共 CLI 验证，但未全局安装、发布或执行生产/付费处理。
