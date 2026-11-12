# 文档、隔离安装与最终验收

Status: claimed
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
