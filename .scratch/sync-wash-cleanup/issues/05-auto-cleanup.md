# sync 完成后清理成功临时副本并保留失败输入

Type: implementation
Status: resolved
Assignee: wash_cleanup
Parent: ../spec.md
Blocked by: 04

## Scope

新增精简 cleanup 模块并在 sync 持锁结束 completed/partial 时调用；保留 run 简要记录、失败报告/Markdown/相关图片。清理旧路径引用要如实，暂停失败中断不自动清理，异常只 stderr 不改变退出码。CLI 回归覆盖混合 partial、OCR 失败后图谱成功、候选图清理、失败输入与下一次同步。

规格：../spec.md；事实：../facts.md。

测试边界沿用规格已约定的公开 CLI、退出码、磁盘产物和 status，不测试工具内部。

## Answer

实现提交：`6f65148`，分支 `feat/wash-auto-cleanup`，已包含集成 `7a44ccd`。

- sync 仅在 completed/partial 完成后持锁调用精简 cleanup；保留 run.json 与公开 manifest，移除成功临时副本和重复候选图。
- 保留失败文档 Markdown、相关本地图片、请求及报告；拒绝导出但已有文件时保留其本地 bundle。Graphify 失败可引用字节一致的正式语料，保留结果报告并清空失效候选路径。
- 仅清理当前 run 的失效步骤产物引用；已清理 run 的 sync resume 被拒绝且原结果不被改写。
- 失败收集、路径检查和删除均在错误隔离中；清理错误仅 stderr，保持原同步退出码。采用写锁、直接子目录与受保护数据重叠检查。
- 新增 9 个公开 CLI 用例；自动清理、持久输入、purge、既有 apply/graph/core 合计 **92 passed**。Ruff check/format 通过；code-review-graph 刷新并对 origin/master 分析完成。
- 仅使用一次性测试目录；未删除真实 wash、语料、图谱或外部工具缓存。
