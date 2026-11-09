# 05: 完成正式UAT、安装与使用交付

Type: task
Status: ready-for-agent
Blocked by: 04

## Context

- [规格](../spec.md) 是完整契约，本票范围不删减规格安全边界。
- 集成分支：daily-knowledge-sync。测试入口已由用户确认：现有CLI黑盒；外部服务使用替身，无生产/付费操作。
- 当前v3及研究实物作为起点，保留所有旧工作。

## Acceptance and ownership

对全部规格执行仓库check/build/隔离安装smoke，修复发布遗漏；knowledge源码随wheel安装，dogpdkb discover --snapshot、sync --knowledge和mdcompare knowledge离线真实CLI一致。扩展既有smoke_install即可，不加新测试缝。不发布不部署远端。

使用冻结11月7日/9日私有完整元数据+6篇raw从正式产品命令复跑日常UAT并记录2new、4timestamp、1字节相同、3正文差异；不得写入真实正文到git。从三篇已审计变化/旧限制准备私有固定cases与验收记录，不能拿抽象模型审计当已验证Graphify输出；未授权真实提取不调用模型，采用确定的外部替身验证正反例并明确没有真实重编译。

新增用户指南说明观察/语料/graph基线、初始化、规划、审批绑定、待办、质量gate、失败恢复、计数/费用限制和旧入口兼容；更新原实现盘点和README相关入口。只有check/build/安装/UAT全通过才resolved，不可把失败当文档局限。

## Working rules

在独立worktree基于集成分支工作；读取tdd skill及相关测试指引，一条失败的CLI行为测试开始再实现；每轮记录red/green证据。完成后合入最新集成分支再测试，提交本票实现并将Status改resolved，Answer列实际验证和公开合同。任何测试失败/部分实现保持claimed，不假结案。不得运行reset/clean或删私有实物。
