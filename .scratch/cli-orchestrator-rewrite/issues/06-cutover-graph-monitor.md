# 图谱薄适配、入口切换与旧代删除

Status: ready-for-agent
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
