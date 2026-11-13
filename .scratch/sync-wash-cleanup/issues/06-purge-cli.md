# 增加只作用于 wash 的 purge CLI

Type: implementation
Status: resolved
Assignee: wash_purge
Parent: ../spec.md
Blocked by:

## Scope

purge.py / cli.py；run 与 all 必须二选一并按 book 限定。completed/partial 可删，其他非活跃 run 需 force；批量先验证全部。复用 Store 写锁，拒绝越界/symlink目标/活跃锁。不得改动持久 state/语料/图谱/cache。通过 CLI 验证错误码、参数、锁与隔离。

规格：../spec.md；事实：../facts.md。

测试边界沿用规格已约定的公开 CLI、退出码、磁盘产物和 status，不测试工具内部。

## Answer

- 实现提交：`04fdaa5`，分支 `feat/wash-purge`；已合并集成分支 tip `3a099a1`。
- 新增独立 wash-only purge CLI、run/all 互斥选择、force 与整批预验证、同 book 写锁和路径/符号链接边界。
- 不修改 state/语料/图谱/工具缓存；拒绝 wash 与持久输入路径重叠。
- TDD 首个 CLI 用例先因缺少 purge 命令返回 64 而失败，增加实现后通过。
- 专项 CLI 回归 17/17；与 core/CLI 联跑 45/45；Ruff 检查和格式检查通过。
- 删除错误测试：真实临时目录 chmod 0555，CLI 返回 1 并报告 dogpdkb purge 错误；没有改动真实 wash。
- Graph 刷新后相对 origin/master 分析已运行；CLI subprocess 测试覆盖图无法解析的 `_main` 边。
