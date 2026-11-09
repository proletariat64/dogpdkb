# dogpdkb / mdcompare 实现与实验边界盘点

## 本次源码和交付状态（2026-11-09，票 05）

基于 `daily-knowledge-sync` 的 `6ef44b4` 并纳入票 05 已保存的 `4542859` 后，`mdcompare knowledge` 正文比较与 dogpdkb 的观察、只读规划和显式审批后的整来源执行已在同一源码分支集成；票 01–04 已在 `daily-knowledge-sync`，票 05 目前在验收收尾分支，尚待合回集成分支。原有严格 compare 与非 knowledge 同步入口仍保留。

本次 `make check` 通过 910 项测试；`make smoke` 完成 wheel/sdist 构建及离线隔离安装后的真实 CLI 验证。安装包和日志见 `/tmp/daily-knowledge-sync-ticket05-final.H9DDAjhR/`；隔离 venv 是临时产物，不是生产安装。具体合同与有界 UAT 见 [日常知识增量指南](daily-knowledge-sync.md)、[UAT 记录](daily-knowledge-sync-uat.md) 和 [票 05 Answer](../.scratch/daily-knowledge-sync/issues/05-acceptance-release.md#answer)。本次没有真实模型/Graphify 语义提取、远端发布或生产部署，不能把源码集成和隔离安装等同于生产已更新。

## 2026-11-09 历史盘点快照（集成前）

以下保留原日期的 `pre-ocr-content-dedup` 盘点和实验结论；其中“未提交”“未接入”“必须补齐”等是当时状态，不代表上面的当前源码和验收状态。实验日期、范围与局限不因后来集成而改变。

### 结论

`mdcompare knowledge` 的 v3 正文比较器已经写在源码中、可直接运行，但仍未提交，未接入 dogpdkb 的同步/Graphify 选择流程。三模型语义判等、diff 字符统计、两天日常增量调查和十篇编译/知识价值审查仍主要是辅助脚本或私有实验。不能把这些研究结果理解为已具备一条自动去重后安全更新生产知识库的完整产品链路。

### 当时实际可用

| 能力 | 实际位置 | 状态 |
| --- | --- | --- |
| 旧严格文件比较 | `src/mdcompare/comparison.py`、`decisions.py`、原 `compare` 命令 | 已有源码与历史提交；保留兼容，不作为新版正文业务目标 |
| 正文结构抽取、归一化与对齐 | `src/mdcompare/knowledge_units.py` | v3 工作树源码，未提交；不是只有实验伪代码 |
| 正文比较 CLI、来源证据与只读 Graphify 计划 | `src/mdcompare/knowledge.py`、`cli.py`、`manifest.py` | CLI 可运行，计划固定禁止自动提取，不触发生产 |
| 正文测试 | `tests/test_mdcompare_knowledge*.py` | 3 个未跟踪测试文件；既有门禁通过记录不等于发布 |
| Graphify 文件级提取、缓存与整来源合并 | `src/dogpdkb/workflows/update_graph.py`、`graph_sync.py` | 已有实现；不支持 diff 作为整篇替代结果 |

### 当时仍在辅助脚本或实验中

当前有 9 个 `scripts/mdcompare_*.py` 辅助脚本，均未提交；并非正式 CLI 子命令：

- 字符统计、报告比较、UAT：`mdcompare_candidate_stats.py`、`mdcompare_compare_knowledge_reports.py`、`mdcompare_knowledge_uat.py`。
- diff 统计：`mdcompare_diff_stats.py`。
- 语义实验：`mdcompare_semantic_sample.py`、`mdcompare_model_comparison.py`、`mdcompare_semantic_shards.py`、`mdcompare_validate_semantic_shard.py`、`mdcompare_full_semantic_result.py`。

其他仅在 `tmp/` 的重要实物：

| 能力/实验 | 保存位置 | 未产品化部分 |
| --- | --- | --- |
| 11月7日至9日真实日常增量 | `tmp/mdcompare-daily-20261109/` | 临时脚本按完整远端元数据和11月7日 raw 比较，尚未成为正式的采集/基线/比较命令 |
| 三模型语义判等 | `filter-exploration-20261108/model-comparison/` | subagent 提示和调度仍为实验，未进入 CLI、稳定重试、生产授权或成本门禁 |
| Kimi 全量尝试 | `filter-exploration-20261108/kimi-full-guarded/` | 本次盘点仅7/70分片通过校验、396/2651待模型单元有有效判定；1分片无效、62缺失，不能声称跑完 |
| 十篇 Graphify 冒泡及知识审查 | `filter-exploration-20261108/graphify-smoke-10-20261108-v2/` | 隔离编译确已成功，但只验证技术链路和有界知识覆盖；没有生产集成或安全增量合并 |
| 句段残差与更广HTML规范化 | `filter-exploration-20261108/fact-residual/`、`format-normalization/` | 原型实测收益不足或负收益，明确不建议直接接入，不属于必做积压 |

`filter-exploration-20261108/` 的完整前缀为 `tmp/mdcompare-knowledge-research-20261107/`。其中真实语料和图派生内容均保持私有、忽略，不提交。

### 当时认为必须补齐的产品边界

1. **交付现有 v3**：保留源码、测试和契约，处理已知缺口后验证、提交、构建并确认目标运行环境版本；不从零重写。
2. **统一正文清洗与安全格式口径**：正式 `_body_view` 对代码范围中的明确生成标记仍有保护分支；统计脚本另行排除但会去掉围栏、反引号及缩进，因此不能将统计的0 diff直接作为正式知识等价。应建立不丢失代码、URL、否定、条件和表格绑定的共同边界，解决明确富化标记污染。
3. **日常增量正式化**：复用现有远端时间戳和同步状态能力，整合本次完整元数据基线比对、隔离 raw 快照及可重复导出；避免再用历史文件名日期代表两天增量。对于 `new/updated/renamed/moved` 明确分流，标签重叠按ID去重。
4. **接入同步与 Graphify 编译名单**：当前 dogpdkb 源码未找到 `mdcompare` 或 `graphify_plan` 的使用。需要让正文复用、待核、实质更新决定是否提取，并把语料发布、元数据维护和知识重提取的成功标记分开；不把 HOLD 升级整篇重跑。
5. **补齐对客验收**：已有词法/合成测试和有界模型审计，尚无正式固定业务问题集合证明更新后答案更正确且不丢失原条件。测试发现覆盖补充与条件遗漏并存；该门槛不能由节点数增减替代。

### 当时的可选项，不要误算为必需

- 生产 LLM 判等及其全量重试、成本、证据不足回退，需单独决定是否采用；本次日常两天半仅2新文档、3篇既有正文更新，不应预先按历史数千篇投入。
- diff/块级 Graphify：稳定块归属、节点/边来源覆盖、修订/删除和保留未变知识的事务合并仍未设计实现。只想按文档减少编译次数时，不必先做这项。
- 新旧图语义合并/复用与覆盖补全：当前仍是整来源合并，不会基于事实语义自动保留两次抽取各自更好的部分。
- query 交付：已核查具体条件常位于 rationale 而默认查询输出不完整展示，中文标签匹配也有缺口；若目标是图谱覆盖补充的实际用户收益，需要单独补上。
