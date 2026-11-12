# 完整观察与近期差异选择

Status: ready-for-agent
Type: task
Blocked by: 02
Spec: ../spec.md

## Scope

用 core 合同实现 discover：完整 listing、book+doc ID、首次 cutoff/已有ID版本同日判断、后续完整快照差分、只记录缺席/改名/搬动。Skylark 仅公开 MCP/CLI薄适配，表格 export 归工具侧。可用本地公开 listing artifact 做离线输入。

## Acceptance

CLI 黑盒 TDD真实Markdown；discover零正文下载，完整清单失败不推进基线，历史缺失不回填，选择策略覆盖版本与元数据；不导入工具包。

## Workflow

独立 worktree/branch，基于 rewrite/cli-orchestrator 当前 tip；先读 tdd 技能，以已确认 CLI seam 做 red→green。完成前合入 integration tip，运行相关检查，提交后报告 SHA。所有 shell rtk 前缀，所有代码关系先 code-review-graph repo_root=本工作树。禁止生产/付费调用，禁止修改用户原 checkout。

## Comments

