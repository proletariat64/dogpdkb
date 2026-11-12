# 通用 mdocr/mdcompare CLI 合同

Status: ready-for-agent
Type: task
Blocked by: none
Spec: ../spec.md

## Scope

只修改独立 mdocr/mdcompare 工具与工具测试/文档。补批次 manifest 输入、逐资源选择与成功复用、prepared image/OCR block 工具内生成、结构化成功/失败/未尝试报告，使编排器无需工具导入即可消费。先核实 mdcompare 已有公开报告，必要时补公开输入构建能力，严禁把 book 选择或失败移交策略挪入工具。

## Acceptance

经工具 CLI 黑盒 TDD；真实 Markdown fixture，无真实付费 OCR；明确 exits/report/schema；在 docs/orchestrator-tool-contracts.md 先写可消费合同并同步 integration。

## Workflow

独立 worktree/branch，基于 rewrite/cli-orchestrator 当前 tip；先读 tdd 技能，以已确认 CLI seam 做 red→green。完成前合入 integration tip，运行相关检查，提交后报告 SHA。所有 shell rtk 前缀，所有代码关系先 code-review-graph repo_root=本工作树。禁止生产/付费调用，禁止修改用户原 checkout。

## Comments

