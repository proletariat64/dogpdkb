# 01: 统一正式正文范围

Type: task
Status: claimed
Blocked by: none

## Context

- [规格](../spec.md) 是完整契约，本票范围不删减规格安全边界。
- 集成分支：daily-knowledge-sync。测试入口已由用户确认：现有CLI黑盒；外部服务使用替身，无生产/付费操作。
- 当前v3及研究实物作为起点，保留所有旧工作。

## Acceptance and ownership

在现有v3上修复明确派生边界在导出包装中的不一致，保护普通字面代码；边界/来源不足时保守UNKNOWN/REVIEW，不复制统计脚本剥缩进。正式正文与可复用统计范围共用规则；保留旧compare和knowledge-1.0字段/退出码兼容。

拥有 src/mdcompare/knowledge_units.py、knowledge.py、必要的model/manifest（尽量不改）、tests/test_mdcompare_knowledge*.py；若统计工具要共享范围可改scripts/mdcompare_diff_stats.py，不重写全部实验。黑盒CLI覆盖有界生成区、字面同名代码、未闭合与未标记内容、金融/表格/URL/顺序/缩进反例。相同字符在代码里无法凭语法证明生成来源时不可直接删掉判复用，必要时引入有版本的显式来源范围声明，并验证其位置；不要求图片/OCR产物。不要读取或评估图片。

## Working rules

在独立worktree基于集成分支工作；读取tdd skill及相关测试指引，一条失败的CLI行为测试开始再实现；每轮记录red/green证据。完成后合入最新集成分支再测试，提交本票实现并将Status改resolved，Answer列实际验证和公开合同。任何测试失败/部分实现保持claimed，不假结案。不得运行reset/clean或删私有实物。
