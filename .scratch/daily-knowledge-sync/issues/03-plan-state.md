# 03: 接通只读知识规划与三类版本状态

Type: task
Status: claimed
Blocked by: 01, 02

## Context

- [规格](../spec.md) 是完整契约，本票范围不删减规格安全边界。
- 集成分支：daily-knowledge-sync。测试入口已由用户确认：现有CLI黑盒；外部服务使用替身，无生产/付费操作。
- 当前v3及研究实物作为起点，保留所有旧工作。

## Acceptance and ownership

在dogpdkb sync现有入口提供--knowledge分支（默认规划，不执行旧apply/OCR/Graphify），复用02快照和01比较。新增可安装knowledge_sync/knowledge_plan等深模块，不要求复制现有pipeline。规划支持--snapshot、--state、--staging、可选--baseline（mdcompare清单）；远端采集用02同模块。新增版本化流程报告及快照、语料成功、图谱成功的独立逐文档状态。graph比较相对最后成功图谱来源而不是最新观察；支持从显式raw baseline首次登记graph来源，不猜旧富化为raw；首次无baseline输出未覆盖待核不全库提取。

KEEP保留旧知识且独立元数据任务；HOLD/UNKNOWN不调用外部；候选仅证据不是许可；新ID单列且无图时不可宣称覆盖。pending包括拒绝/失败/未授权，后续远端没再改也保留。报告每身份一个结果、守恒计数、原因、来源位置、非原子时间、分类与正文差异及无费用证据则unknown；私有原子输出，拒绝输出/状态覆盖输入。输入版本绑定用私有完整raw快照和直接字节比较，不新建hash。

为04定义明确的plan/state/approval契约及入口分发，规划源码不接提取。可在knowledge_sync main接--execute/--approvals/--quality-cases解析然后委托04的knowledge_execute模块，但先确保默认无执行，合同写Answer。拥有sync_knowledge_base.py的最小--knowledge分发、新planner/state模块、黑盒tests/test_knowledge_sync.py。不修改02拥有的capture除必要兼容。

## Working rules

在独立worktree基于集成分支工作；读取tdd skill及相关测试指引，一条失败的CLI行为测试开始再实现；每轮记录red/green证据。完成后合入最新集成分支再测试，提交本票实现并将Status改resolved，Answer列实际验证和公开合同。任何测试失败/部分实现保持claimed，不假结案。不得运行reset/clean或删私有实物。
