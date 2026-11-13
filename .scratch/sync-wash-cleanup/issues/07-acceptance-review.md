# 完成集成验收、文档与评审

Type: implementation
Status: open
Assignee: unassigned
Parent: ../spec.md
Blocked by: 04, 05, 06

## Scope

同步 README、操作/状态合同、配置注释和 AGENTS 旧清理条款；运行 make check、隔离安装及 CLI UAT，不调用真实付费服务或删除现有数据。执行 code-review 双轴评审并修复，关闭实施议题。

规格：../spec.md；事实：../facts.md。

测试边界沿用规格已约定的公开 CLI、退出码、磁盘产物和 status，不测试工具内部。
