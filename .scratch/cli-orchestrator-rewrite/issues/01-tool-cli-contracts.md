# 通用 mdocr/mdcompare CLI 合同

Status: resolved
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


- 2026-11-12: 通用合同已发布于 `docs/orchestrator-tool-contracts.md`；新增 `mdocr batch` 和 `mdcompare catalog`，未修改 dogpdkb 编排器。
- CLI 黑盒 red→green：catalog 最初报 INVALID_ARGUMENTS；batch 最初无报告，再到图片记录为空；新增命令后分别通过真实本地 Markdown 与本地 HTTP Paddle 服务验收。目录迁移证据测试先因输出父目录不存在失败，修复后通过。
- 验证：188 个相关 mdocr/mdcompare 测试通过；新增批次测试覆盖资源选择/重复引用、成功缓存无 token 复用、鉴权共享失败与未尝试、失败重跑、相同路径图片字节改变、预算耗尽、缺少文档隔离及 mdcompare 消费 prepared-jpeg-ocr-v1 证据。两个 catalog CLI 测试覆盖本地/远程资源和跨目录证据路径。ruff 与 git diff --check 通过。
- 已合入 integration `88e99ce`；图索引更新并对实际默认分支 `origin/master` 分析。未运行真实付费 OCR、生产数据或安装验收；批次预算在资源间检查，单个在途调用仍受现有 Paddle 请求/任务期限约束。
