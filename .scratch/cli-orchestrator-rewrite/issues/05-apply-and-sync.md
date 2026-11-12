# 串行 apply、计划、语料与唯一 sync

Status: ready-for-agent
Type: task
Blocked by: 01, 02, 04
Spec: ../spec.md

## Scope

实现整批下载→mdcompare CLI→plan→mdocr CLI→语料实际落盘/manifest，sync 与分步共用函数。支持停plan和显式恢复；固定输入；失败版本/不明结果移交；不抑制合法后续正文编译。输入缺失变动和版本回退明确拒绝。

## Acceptance

安装CLI黑盒12组相关场景；工具替身+真实Markdown；验证顺序、partial、跨批失败不重试、新版本、same raw复用、plan停点、corpus与graph版本分离。

## Workflow

独立 worktree/branch，基于 rewrite/cli-orchestrator 当前 tip；先读 tdd 技能，以已确认 CLI seam 做 red→green。完成前合入 integration tip，运行相关检查，提交后报告 SHA。所有 shell rtk 前缀，所有代码关系先 code-review-graph repo_root=本工作树。禁止生产/付费调用，禁止修改用户原 checkout。

## Comments

