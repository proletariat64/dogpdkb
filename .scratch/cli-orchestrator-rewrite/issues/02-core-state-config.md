# 精简配置、版本记录与进程层

Status: resolved
Type: task
Blocked by: none
Spec: ../spec.md

## Scope

新增编排器自有 settings、持久版本/步骤事实、run 记录、互斥锁、公开子进程调用和只读 status。先在 docs/orchestrator-core-contracts.md 记录给 discover/apply 使用的最小 Python API，再实现。现有 Web/lifecycle 独立保留，不导入 mdocr.config。不写通用工作流引擎/handler registry/envelope。

## Acceptance

通过已确认的安装 CLI 黑盒边界做 red/green；在 CLI 未接入前可用产品公开模块入口，不新增测试注入接口；重点状态不明/新run不阻挡/失败版本抑制/JSON只读/实时日志/锁。保持旧入口到最终切换时统一删。

## Workflow

独立 worktree/branch，基于 rewrite/cli-orchestrator 当前 tip；先读 tdd 技能，以已确认 CLI seam 做 red→green。完成前合入 integration tip，运行相关检查，提交后报告 SHA。所有 shell rtk 前缀，所有代码关系先 code-review-graph repo_root=本工作树。禁止生产/付费调用，禁止修改用户原 checkout。

## Comments

- Implemented settings/records/processes/observe/preflight; public shared API:
  docs/orchestrator-core-contracts.md. init-state/status/doctor use the new core.
- CLI red→green: empty/read-only status; abandoned running owner; registration
  preserves existing Markdown/graph and unknown graph truth; tool diagnostics
  visible before subprocess completion. Four focused tests pass; Ruff passes.
- Full pipeline failure-version suppression, lock contention and signal cases
  remain for downstream assembled CLI acceptance; these are not yet validated
  by this ticket's four focused tests. No paid or production calls were made.
