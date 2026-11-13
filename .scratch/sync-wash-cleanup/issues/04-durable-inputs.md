# 将后续同步所需输入独立于 wash 保存

Type: implementation
Status: claimed
Assignee: wash_inputs
Parent: ../spec.md
Blocked by:

## Scope

apply.py / records.py / graph.py。保留每文档最新原始 Markdown、资源、必要 OCR 复用结果与发布见证到 state 管辖的持久输入区，不扫描迁移历史 run。通过公开 CLI 验证删除旧 run 后真实增量比较与 OCR 复用正确，失败抑制保持。避免保存成功 run 全套历史副本。

规格：../spec.md；事实：../facts.md。

测试边界沿用规格已约定的公开 CLI、退出码、磁盘产物和 status，不测试工具内部。
