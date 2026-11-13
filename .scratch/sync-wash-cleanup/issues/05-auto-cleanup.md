# sync 完成后清理成功临时副本并保留失败输入

Type: implementation
Status: open
Assignee: unassigned
Parent: ../spec.md
Blocked by: 04

## Scope

新增精简 cleanup 模块并在 sync 持锁结束 completed/partial 时调用；保留 run 简要记录、失败报告/Markdown/相关图片。清理旧路径引用要如实，暂停失败中断不自动清理，异常只 stderr 不改变退出码。CLI 回归覆盖混合 partial、OCR 失败后图谱成功、候选图清理、失败输入与下一次同步。

规格：../spec.md；事实：../facts.md。

测试边界沿用规格已约定的公开 CLI、退出码、磁盘产物和 status，不测试工具内部。
