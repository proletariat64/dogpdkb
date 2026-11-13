# 增加只作用于 wash 的 purge CLI

Type: implementation
Status: claimed
Assignee: wash_purge
Parent: ../spec.md
Blocked by:

## Scope

purge.py / cli.py；run 与 all 必须二选一并按 book 限定。completed/partial 可删，其他非活跃 run 需 force；批量先验证全部。复用 Store 写锁，拒绝越界/symlink目标/活跃锁。不得改动持久 state/语料/图谱/cache。通过 CLI 验证错误码、参数、锁与隔离。

规格：../spec.md；事实：../facts.md。

测试边界沿用规格已约定的公开 CLI、退出码、磁盘产物和 status，不测试工具内部。
