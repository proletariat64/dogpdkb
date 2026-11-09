# 06: 修复最终双轴审查问题

Type: task
Status: claimed
Blocked by: 01, 02, 03, 04, 05

## Context

- 集成分支：daily-knowledge-sync。完整规格：[spec](../spec.md)。
- 审查基准：e9f4db8；[审查记录](../../../docs/daily-knowledge-sync-review.md)。Standards2项阻塞、Spec10项问题必须全部处理，由同一implementer在单独worktree实现。
- 完整证据：/tmp/daily-knowledge-sync-standards-review.md 与 /tmp/daily-knowledge-sync-spec-review.md；后者包含4个合成复现脚本位置，无真实模型/生产操作。

## Acceptance

- 为STD-1/2和SPEC-1至10逐项添加现有CLI缝的red/green回归，复用外部服务替身；不得只改报告和自报pass。
- 保护采集所有输入/输出别名、page/checkpoint/raw边界及失败权限；执行严格book和完整state验证。
- 先冻结旧新比较视图，绑定审批与当前观察/语料/图谱版本；旧/新输入漂移和过期plan均拒绝，已恢复瞬时错误不永久阻塞。
- 审核逐单元有稳定来源身份，明确删除/保留处置；需要保留的已知旧规则绑定实际质量cases。接口不兼容必须版本化，不能静默批准旧的通用ack。
- 在合并前检查提取/缓存节点、边、超边；保留无关来源的记录，否则拒绝，不引入事实级合并。
- journal覆盖发布与state/report写入的硬进程死亡窗口，安全续跑不重提取；保留并发及已有错误回退保护。
- 未提供token/call证据时未知，不冒充0；无提取的0计数单独说明。
- 更新受影响CLI契约、指南、smoke和私有UAT复跑材料，历史实测不改写。全量check、build/隔离安装和逐项复现全部通过后才resolved；无实际Graphify/付费/生产。

## Working rules

读取TDD skill，逐slice先red再green。单独worktree基于集成tip；结束前合入最新集成tip、实际验证和提交；禁止reset/clean。修复后Answer逐项对应证据，审查文档分别保留Standards和Spec结果，不合并或隐去历史发现。
