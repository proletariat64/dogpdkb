# 01: 统一正式正文范围

Type: task
Status: resolved
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

## Answer

- TDD 1 red：`test_verified_generated_region_inside_code_fence_is_outside_body` 在未实现时返回 2，未得到 REUSE。
- TDD 1 green：加入 `generated-regions-v1` 起止行声明及位置验证后，同一 CLI 测试 `1 passed`；派生差异不进入报告，普通正文复用。
- 字面代码守卫：无声明的同名标记及变化值保持 REVIEW/BODY_DELTA_CANDIDATE；与首轮合并运行 `2 passed`。
- TDD 2 red/green：未闭合主标记原返回 0/REUSE；改为 UNKNOWN/HOLD 后聚焦套件 `133 passed`，旧复用测试同步更新。
- 位置守卫：声明起止行不匹配时 UNKNOWN/HOLD；测试通过。
- TDD 3 red/green：未闭合旧式注释原为 REVIEW；代码外显式起始边界现在报 BODY_SCOPE_BOUNDARY_UNCLOSED，相关 `2 passed`。
- TDD 4 red/green：未配对转写标签原返回 0；现不再仅删标签后复用尾部，聚焦套件 `136 passed`。
- TDD 5 red/green：代码内字面结束标记原会闭合外部区域；配对限定为代码外后，相关 `3 passed`。
- 扩展 CLI 守卫：围栏与缩进声明、未标记历史正文、金融/表格/URL/顺序/缩进反例合计聚焦 `144 passed`。
- TDD 6 red/green：孤立与嵌套边界原均返回 0；单遍配对校验后相关 `4 passed`。
- 统计冒烟：声明生成区被排除、无声明字面代码保留、未闭合区域产生 BODY_SCOPE_BOUNDARY_UNCLOSED，`statistics_scope_smoke=passed`。
- 最终验证：`868 passed`；Ruff check 通过；160 文件格式检查通过；doc-guard 61 documents 通过；8 个变更 Python 文件 LSP 无诊断。

公开合同：`body-units-v4`；`knowledge-1.0` 字段及退出码不变。代码外完整显式生成区自动排除；代码内同名字符串默认是字面内容，只有顶层 `body_scope` 的 `generated-regions-v1` 声明且起止行与标记精确匹配时才排除。无效声明、位置不匹配、未闭合、孤立或嵌套边界均为 UNKNOWN/HOLD，不授权提取。未标记文字不按猜测删除，历史来源限制继续显式报告。统计先共享正文范围，再做不参与复用授权的展示清洗。旧严格 compare 不消费 body_scope。

限制：没有推断代码内生成来源，没有读取图片或执行 OCR；没有运行真实 Graphify、远端服务或生产操作。未用冻结全量重跑候选统计，本票只完成合成 CLI 与仓库回归验收。
