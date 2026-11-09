# 02: 正式化完整观察快照与增量导出

Type: task
Status: claimed
Blocked by: none

## Context

- [规格](../spec.md) 是完整契约，本票范围不删减规格安全边界。
- 集成分支：daily-knowledge-sync。测试入口已由用户确认：现有CLI黑盒；外部服务使用替身，无生产/付费操作。
- 当前v3及研究实物作为起点，保留所有旧工作。

## Acceptance and ownership

复用现有语雀代理/分页和导出能力，新增可安装的knowledge_capture模块，从现有dogpdkb discover入口用--snapshot显式启用新路径，旧discover行为不变。新增--baseline-snapshot、--staging；为CLI黑盒可重现支持--listing本地完整列表输入及--details-dir本地详情输入（与真实适配器等价，禁止默默联网补缺）；远端模式复用已配置book/source。不要改sync主入口，留给03。

定义并在Answer记下公开snapshot JSON契约、实际CLI用法，供03消费。包含book身份、完整列表唯一ID、页覆盖/完整性、观察起止时区、准确UTC时间与实际字段、new/updated/renamed/moved及缺失/异常、详情与列表时间、所选raw和原文阶段；标签重叠只导出一次。初次无baseline只初始化完整观察，不全库下载/判新增；不完整/无时间/倒退不得假成功。现有成功raw可引用、基线禁止覆盖。支持有界重试、私有检查点与续跑；pending已知身份可通过--pending-state或等价显式输入再次导出，远端没再变化仍保留。捕获不改语料/图谱成功版本，不执行OCR/Graphify。CLI替身测试从现有入口验证，不仅单测内部。主要拥有新knowledge_capture模块、get_sync_updates.py最小分发改动、tests/test_knowledge_capture.py；避免改cli.py和sync_knowledge_base.py。

## Working rules

在独立worktree基于集成分支工作；读取tdd skill及相关测试指引，一条失败的CLI行为测试开始再实现；每轮记录red/green证据。完成后合入最新集成分支再测试，提交本票实现并将Status改resolved，Answer列实际验证和公开合同。任何测试失败/部分实现保持claimed，不假结案。不得运行reset/clean或删私有实物。
