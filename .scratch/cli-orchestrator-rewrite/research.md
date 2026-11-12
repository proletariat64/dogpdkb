# dogpdkb CLI 编排器重写：研究记录

Status: research-complete-spec-finalized
Date: 2026-11-12
Baseline: `/home/ubuntu/dev/dogpdkb`, `feat/max-select`, HEAD `9ba4cb1eec42`，包含现有未提交修改。

## 本轮已确定的约束

来自当前用户要求，优先于历史 spec；下文保留调查时的证据和建议，最终决策以已定稿 [spec.md](spec.md) 为准：

- 职责是 plan / execute 的串行知识库增量同步与编译；日常由 crontab 调用。
- 提供状态查询、watch 所需观察能力及正常 stdout/stderr。
- mdocr、mdcompare、Graphify 是独立 CLI。只使用公开 CLI / MCP 和文档化产物；缺能力则修改工具侧 CLI。
- 不导入外部工具包，也不在子进程脚本中偷偷调用其内部函数；不复刻比较、OCR、提取或图合并算法。
- 允许重写与删除，不承担旧参数、状态格式、历史适配或旧执行器的兼容；用户认可 discover 等分步命令的使用方式，应保留有用的命令职责，底层共用新实现。
- 保留 capture 策略、delta 判定、必要状态、异常与证据的产品含义；具体保留程度经 grilling 确认。
- 4–6k 行是编排器的规模目标，不能把逻辑挪到脚本中假装达标。
- 本轮研究与规格讨论不授权实际同步、OCR、提取、重建、部署或发布。

## 现场规模与证据

按当前文件 `splitlines()` 统计 Python 物理行（含空行和注释）：

| 范围 | Python 文件 | 行数 |
| --- | ---: | ---: |
| `src/dogpdkb` | 37 | 14,981 |
| `src/mdocr` | 8 | 1,359 |
| `src/mdcompare` | 15 | 3,416 |
| `tests`（含工具测试） | 54 | 25,121 |

工具自身代码和测试不能全算成编排器负担；编排器测试应验证调用与产物契约，工具算法测试应归工具。

结构查询使用当前工作树 code-review-graph：141 文件、1,935 节点、754 Test 节点，索引 HEAD 与当前 HEAD 相同。图有大量未解析调用，且不以 HEAD 相同证明已覆盖未提交修改；关键结论已直接读当前源码核实。`tests_for` 的稀疏返回不能证明没有测试。

当前 6 个 tracked 文件已有修改；另有生产 UAT 文档、`ocr-cache/`、`sync-results/` 未跟踪。本轮不处理这些已有状态。

## 主要发现

### 1. 越界覆盖比较、配置、OCR 与图编译，不是两个局部模块

- `src/dogpdkb/stages/mdcompare.py:40–43` 直接调用比较器类型、比较函数和私有 `_uses`；`knowledge_plan.py:18–21` 也直接依赖比较器实现。
- `src/dogpdkb/stages/ocr.py:44–48` 导入 OCR 客户端、prepared image 与块生成函数。
- `src/dogpdkb/ocr_cache.py:18–31` 用工具私有模型常量、JPEG 质量和格式标记组成处理身份；`:140` 起自行校验 OCR 块。
- `src/dogpdkb/workflows/update_graph.py:136–175` 进入 Graphify 缓存、prompt、后端、模型和并行提取；`:246` 起进入分析、聚类、报告。
- `src/dogpdkb/workflows/graph_sync.py:114–378` 实现来源替换、节点重映射、边与超边合并。
- `src/dogpdkb/unified_sync.py:43` 等多处使用 `mdocr.config` 作为编排器配置模型。编排器配置应独立，工具内部配置不能成为公共模型。
- `pyproject.toml:25–32` 当前把三组命令和包发布在同一 distribution；它证明目前交付边界也耦合，不能仅凭命令名称认为已独立。

### 2. 新流水线仍以旧流水线为实现底座

- `src/dogpdkb/cli.py:14–20` 仍暴露旧 discover/apply/update-graph/publish-web 入口。
- `src/dogpdkb/unified_sync.py:45–170` 有旧参数识别和兼容转发。
- 新 `stages/compile.py:18` 进入 `knowledge_execute.execute_compile`；其余新阶段也复用 `knowledge_plan`、`knowledge_capture` 与旧 workflow helpers。
- `knowledge_sync.py` 保留审批执行与 legacy state 导入。

因此只换顶层入口、继续复用旧执行器，不能达成此次重写。

### 3. 复杂度同时来自旧规格

旧 `.scratch/unified-unattended-sync/spec.md` 的以下要求会重新长出大量代码：

| 旧要求 | 位置 | 重写处理建议（尚非确认决策） |
| --- | --- | --- |
| 导入历史 UAT/下载记录 | 80–82 | 按当前无兼容要求删除 |
| OCR 内容、配置、内部格式证据 | 97–105 | 工具负责，编排器消费公开报告 |
| 异常待处理/完成/放弃/失效与后补编译 | 128–136 | 收敛为结果和失败证据；是否需要事后处置产品待问 |
| 清理时遍历、搬迁、重写历史引用 | 137–139 | 新产物布局从源头区分长期与临时文件，不做历史迁移器 |
| 同版本失败不再参加 daily sync | 134–135 | 用户已确认保留：失败即记录，交定时清理 agent 查看 |
| 未完成 run 阻挡新 cron | 117 | 恢复政策依赖重试决策，后续确认 |

`docs/daily-knowledge-sync.md:89–117` 的人工审批/质量门禁已被统一 spec 末尾替代，不能从旧文档恢复这些要求。

### 4. 需要保留含义，而非保留旧文件结构

- 近期变化、不回填历史过滤文档；book + document ID 身份；改名/搬动不等于新增。
- 比较正文与图片处理分别决策，完整 raw 与 OCR 富化内容不能混为一份来源事实。
- 完整来源编译；不能把 diff 片段当成完整来源覆盖。
- 观察到新版本、导出成功、图谱发布成功是不同事实，不能提前推进成功版本。
- 失败输入和失败原因应可定位；同版本失败退出日常尝试，新版本正常参加同步，旧失败交定时清理 agent 查看。
- cron、只读状态查询、标准输出错误流已足够支撑日常运行，不需要调度服务。

依据：旧统一 spec 的 76–126 行、`docs/sync-state-design.md:67–110`、当前 `stages/plan.py:174–259`。这些是现有合同证据；具体失败政策仍待本次确认。

## 已安装 CLI 与上游契约

仅检查本机 help、版本和公开文档/CLI 源码，不调用真实服务：

| 工具 | 现场证据 | 结论 |
| --- | --- | --- |
| mdcompare | 全局 PATH 无命令；`.venv/bin/mdcompare --version` 为 1.0.0；`compare/knowledge --help` 都有 baseline/incoming/output JSON、report、include-evidence | 可直接编排公开 CLI；退出码 2 可表示 UNKNOWN/REVIEW 等报告状态，不等同无产物故障 |
| mdocr | `mdocr --version` 为 0.2.0；prepare/ocr help 支持文件、输出目录、下载、格式、force | 当前可产出 Markdown/JPEG assets，partial 输出可能同时非零退出；没有公开逐图选择/复用及结构化逐图结果 manifest 参数 |
| Graphify | 本机 wrapper 对应安装包 0.9.45；help 有 extract/out/no-cluster、merge-graphs、cluster-only、label、diagnose multigraph/json | 基础命令存在；update 是 code-only/no LLM，不能代替 Markdown semantic update |
| Skylark | `get_sync_updates.py:279` 通过 utoo-proxy 调 STREAMABLE_HTTP MCP；未找到本地 skylark CLI | 当前适配自行组织 listing/export；未访问远程，不能断言线上没有新增能力 |

具体缺口必须在工具侧解决，且以最终确认的产品需求为准：

- **mdocr**：若保留逐图复用/失败/未执行计数，应由 CLI 提供选择、缓存与公开结果报告；dogpdkb 不制造 prepared-image/OCR block 证据。依据 `src/mdocr/cli.py:160–224`、`docs/spec/mdocr.md:185`、`docs/mdcompare-ocr.md:109`。
- **Graphify**：help 的 merge-graphs 是跨仓库合并；尚未证明其公开契约支持按完整来源替换、部分失败保留旧贡献、逐来源成功结果与候选发布。列为待确认的 upstream gap，不能靠 dogpdkb 操作节点/边补齐。`knowledge_execute.py:255` 的子进程实际运行自有 update_graph.py，也不满足独立工具 CLI 边界。
- **Skylark**：普通文档调用 doc_detail(format=md)，但表格由 `sync_formats.py:106` 自行遍历表/记录并转 Markdown。完整 listing/export artifact 适合交给上游，dogpdkb 保留近期范围和选择策略。实际网关能力需独立、有界验证。

边界验收不能只看“用了 subprocess”：源码零外部工具包导入；外部 argv 必须运行公开 CLI/MCP，不运行 `python -c` 或自有脚本访问工具内部；工具不确定/partial 退出码按公开契约和产物解释。工具内部算法、缓存、图合并和 OCR 格式测试应在工具侧完成。

## 规格决策树

已确定：CLI 边界、串行编排、cron、无兼容、保留真实成功事实。

第一轮已确认：

1. 用户认可现有 discover 等分步命令；保留操作便利性。具体职责边界下一轮确认，不能据此保留旧执行器。
2. OCR 失败继续可用内容，单篇编译失败保旧继续其他来源；完整清单或公共存储/发布失败停止本批。
3. 不采用自动跨批重试；保留当前同版本失败移交政策，由定时清理 agent 查看。
4. Web 单独，不纳入本次同步/编译流程。

分步命令职责已额外核对：discover 默认只扫描和比对，`--snapshot` 却切换为下载 raw；apply 下载/OCR 后默认合并正式 corpus，`--no-merge` 才留在 staging；update-graph 消费 manifest 编译并发布图谱；sync 已转 unified，但旧 run_pipeline 仍保留。证据分别为 `get_sync_updates.py:885–936`、`apply_sync_updates.py:425–449,810–850`、`update_graph.py:562–589`、`sync_knowledge_base.py:447–467`。保留命令名不要求继承这些双重语义或正式 corpus 的生效时机。

第二轮 Q5–Q8 用户全部确认：discover 扫描、apply 准备、update-graph 编译、sync 串行组合；中断不挡新 cron，允许显式恢复；0/2/1 分别完整/部分/整批失败；旧 run 和失败证据由清理 agent 统一处理，程序删除内置 wash 扫描/迁移/引用改写能力。

近期规则核实：当前不是 rolling 最近 N 天。首次没有观察快照时，本地缺失 ID 以现有基线文件日期最大值为 cutoff，已有 ID 按各自精确版本/日期判断（只有日期时同日也获取比较）；后续完整快照按 ID、版本、标题和 TOC 差分。证据为 `knowledge_capture.py:935–1040`。init-state 当前只登记既有语料和来源关系，不重写正文/图、不调用 Graphify（`sync_state.py:99–239`）；这些事实供第三轮确定新实现起点，不表示要保留旧状态格式或图内部扫描代码。

第三轮 Q9–Q12 用户全部确认：apply 直接写正式 corpus，图谱独立记录成功且失败不回滚正文；原计划固定范围/输入，输入改变需重新生成且旧计划不能回退新版本；缺席只移交；保留既有语料/图谱起点和首次截止+后续快照差分，不迁移旧状态。

第四轮 Q13–Q14 用户全部确认：status 支持 run/json/watch，只有公开计数才显示细粒度进度；整批下载/比较/计划结束后进入 OCR，OCR 结束后编译；支持 stop-after plan，不建编排器工作池。Q1–Q14 产品决策已完成，工具 CLI 缺口继续作为实施前置工作。

同目录 `spec.md` 已定稿为 ready-for-agent。仅完成规格，不代表实现、工具升级或真实运行验收已完成。

以下文件映射保留已确认重写计划的现场定位信息；主规格以职责描述为准，不要求沿用当前文件布局。

## 实施定位附件：当前模块与预算

这里描述最终职责，不要求沿用下列旧文件名：

| 当前模块 | 最终处理 |
| --- | --- |
| workflows/update_graph.py、workflows/graph_sync.py | 删除提取、缓存访问、节点/边/超边合并、重映射算法；重写为 Graphify 公开 CLI 调度 |
| stages/ocr.py、ocr_cache.py | 删除内部 OCR 运行时和缓存管理；替换为 mdocr CLI 薄调用及公开结果消费 |
| knowledge_plan.py、stages/mdcompare.py、stages/plan.py | 删除库对象拼装、私有解析和旧审批规则；保留读取报告后的产品策略，并只留一份 |
| knowledge_capture.py、stages/capture.py、get_sync_updates.py | 删除多种历史导入/下载复用兼容；保留近期范围、稳定身份、观察/选择；listing/export 和表格渲染尽量交 Skylark |
| apply_sync_updates.py、sync_knowledge_base.py、knowledge_sync.py、knowledge_execute.py、unified_sync.py | 删除被取代的执行器和旧参数转发；四个公开命令共用一套新实现 |
| sync_wash.py | 整体删除，清理交 agent |
| sync_anomalies.py、sync_results.py | 删除处置四态平台、事后写成功、followup 和历史搬迁；结果/失败记录合并进精简持久记录与 run 结果 |
| sync_state.py、knowledge_state.py、sync_progress.py、sync_run.py、sync_stages.py | 收敛为文档版本事实与 run 记录；不保留动态 handler 注册框架和重复状态写入 |
| cli.py、doctor.py、sync_status.py、instance_lock.py、sync_time.py | 保留必要职责并精简；doctor 只检查公开入口、配置和产物可用性 |
| workflows/sync_formats.py | 表格导出放工具侧；可保留不涉工具内部的薄格式适配 |
| lifecycle.py、workflows/publish_web.py、resources | 保留独立功能；只做必要配置解耦 |
| src/mdocr、src/mdcompare | 独立工具维护，CLI 所需能力在此补齐；不作为编排器代码，也不混淆其测试归属 |

### 行数预算

按物理行含注释/空行估算，最终必须测量并解释偏差，不通过增加抽象填满预算：

| 职责 | 目标行数约 |
| --- | ---: |
| 命令、配置、起点登记、简化 doctor | 650 |
| discover、选择策略、Skylark 薄适配 | 750 |
| apply、计划策略、mdcompare/mdocr CLI 适配 | 1,100 |
| Graphify CLI 编排和发布结果消费 | 450 |
| 持久状态、run 结果、锁、基础 I/O | 850 |
| 状态观察、日志、公共子进程调用 | 450 |
| 独立 Web/lifecycle 等必要保留代码 | 600 |
| 合计 | 4,850 |

预算范围为 4–6k；工具测试与工具本身另计。`.code-review-graph/`、真实语料、图谱、缓存和已有未提交状态不属于清理对象。
