# Graphify 公开整来源更新合同

Status: ready-for-agent
Type: task
Blocked by: none
Spec: ../spec.md

## Scope

定位独立 Graphify 的真实源码/公开 CLI，核实整来源替换、partial 失败保旧、逐来源报告、来源清单和元数据维护。若不足，必须在 Graphify 独立项目修改其 CLI；不能在 dogpdkb 编写图算法或工具内部脚本。将源码/patch/验证证据保存任务可访问目录，提供版本与明确 install/dependency 交付方式，不修改真实图谱。

## Acceptance

公开 Graphify CLI TDD，可使用离线语义提取 fixture/受支持测试 seam，不进行付费提取。若源码/授权不可得，报告真实 blocking gap，同时完成合同/可审阅 upstream patch，不伪称已满足。

## Workflow

独立 worktree/branch，基于 rewrite/cli-orchestrator 当前 tip；先读 tdd 技能，以已确认 CLI seam 做 red→green。完成前合入 integration tip，运行相关检查，提交后报告 SHA。所有 shell rtk 前缀，所有代码关系先 code-review-graph repo_root=本工作树。禁止生产/付费调用，禁止修改用户原 checkout。

## Comments

