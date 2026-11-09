# 04: 实现审核后完整来源提取与安全发布

Type: task
Status: claimed
Blocked by: 03

## Context

- [规格](../spec.md) 是完整契约，本票范围不删减规格安全边界。
- 集成分支：daily-knowledge-sync。测试入口已由用户确认：现有CLI黑盒；外部服务使用替身，无生产/付费操作。
- 当前v3及研究实物作为起点，保留所有旧工作。

## Acceptance and ownership

根据03已落地的plan/state/approval入口，实现knowledge_execute可安装模块与必要的现有update_graph钩子；从dogpdkb sync --knowledge验收。执行必须显式选择批准的book/id、完整raw快照、当前版本、旧侧删除/上下文问题处理及完整来源替换范围；仅审批候选不得隐式忽略REVIEW。批准以后raw变化使许可失效，默认没批准0提取。无图的新文不能KEEP。元数据更新维护稳定doc-id到source映射；做不到安全更新就HOLD，不重编译。

调用现有Graphify整文提取/缓存/整来源事务，输入完整冻结raw而不是diff或统计视图；隔离候选先验证完整来源覆盖、重复ID/悬空边、版本和私有固定新事实/旧限制cases，再发布。支持全新文档与已有图谱但不创建/重建整个生产图。验收案例非仅检查节点数；固定cases为明确有界规则/事实断言，不能让外部任意未绑定的pass文件绕过gate，报告其有界性。缓存/模型调用/token/费用分开，未知不伪造，优先复用已存在提取使用量证据。

逐文档失败保旧图和graph baseline。支持checkpoint/resume，尤其图谱写成功而状态失败能通过直接比较候选/图谱+事务记录恢复不重复计费；并发基线漂移拒绝发布。测试替身只在外部语雀/Graphify边界，拒绝任意命令执行/不需真实付费。主要拥有knowledge_execute模块、tests/test_knowledge_execute.py、update_graph可选参数/入口兼容改动，必要接03入口。

## Working rules

在独立worktree基于集成分支工作；读取tdd skill及相关测试指引，一条失败的CLI行为测试开始再实现；每轮记录red/green证据。完成后合入最新集成分支再测试，提交本票实现并将Status改resolved，Answer列实际验证和公开合同。任何测试失败/部分实现保持claimed，不假结案。不得运行reset/clean或删私有实物。
