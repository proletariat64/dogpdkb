# Sync 完成后清理本次 wash

Labels: wayfinder:map
Status: resolved

## Destination

明确 sync 完成后自动清理本次 wash 的规则，形成可直接实施的小规格：
触发时机、删除与保留范围、失败表现，以及后续同步和监控的验收合同。
同时明确手动 purge 的目标范围与 force 的含义。

## Notes

- 本轮为规划，不修改运行代码，不执行真实清理。
- 使用 wayfinder、grilling 和 domain-modeling；遵循本仓库 CLI 编排边界。
- 用户已确认只清理本次 run，不扫描历史 run；历史失败仍由清理 agent 处理。
- 用户已确认手动 purge 也只清 wash，不扩大到正式语料、图谱、持久状态或工具缓存。
- 保持实现精简，不恢复旧 wash/anomalies 平台、引用扫描或历史迁移体系。
- 现行规格明确排除自动清理；本特性是对该条款的局部修订。
- 当前实现事实见 [当前 wash 的归属和引用](facts.md)，核对基线 master 9615a85。
- 实施交接规格：[Sync wash 清理规格](spec.md)。
- 子议题按 issues/ 文件编号查询；Status: open 且依赖均 resolved 的议题可领取。

## Decisions so far

- [什么情况下清理本次 sync，清理失败如何报告](issues/01-completion-policy.md#answer)：
  成功和 partial 结束后清理本次；partial 不要求原 run 恢复，清理错误不改变同步退出码。
- [清理哪些 wash 产物，保留哪些复用与观察能力](issues/02-artifact-boundary.md#answer)：
  自动清理保留失败 Markdown、相关图片、报告和简要记录；成功临时副本删除，必要复用输入独立保存。
- [手动 purge 的彻底清理范围和 force 含义](issues/03-manual-purge.md#answer)：
  purge 只清选定 wash，force 允许丢弃未完成 run，仍受写锁和路径边界约束。

## Not yet specified

无。决策路线已明确，不增加保留天数、容量阈值或配置平台。

## Out of scope

- 自动历史 run 扫描、失败重试、清理 agent 调度和旧状态兼容。
- 修改 Graphify、Yuque 或 Skylark CLI；清理外部工具的正式缓存。
- 清理正式语料、正式图谱或持久文档版本状态。
- 本轮功能编码、生产运行、发布或部署。
