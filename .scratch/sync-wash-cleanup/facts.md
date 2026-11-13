# 当前 wash 的归属和引用

核对基线：master 9615a85。仅静态检查，未运行 sync、未删除数据。

- `src/dogpdkb/sync.py:23-36`：计划暂停提前返回；最终 combined code 0/2/1
  分别记录 completed/partial/failed。无工作可正常 completed；不能仅以 exit 0 判断。
- `src/dogpdkb/records.py:100-101,139-174,273-300`：run 在 staging/book/run-*；
  observation、文档版本、失败抑制事实在独立 state/book/state.json。
- `src/dogpdkb/graph.py:136-143,179-206`：graphify-* 工作区中的正式图、manifest、
  cache 等公开产物已复制发布；该目录仍含本次工具报告，删除时需明确报告保留规则。
- `src/dogpdkb/apply.py:117-122,140,213-217,285-288`：原始文档及资源位于 run，
  后续比较优先用旧 raw.path，缺失时改用已发布语料。删除会改变比较输入。
- `src/dogpdkb/apply.py:372-390`：同版本 OCR 复用读取旧 run 的请求、报告和输出；
  外部 OCR 缓存另有位置，不能把两者等同。
- `src/dogpdkb/apply.py:536-564` 与 `src/dogpdkb/graph.py:62-69,211-215`：
  corpus/graph 的 input_copy 指向 run 内 published/ 和 graph-success/；
  显式图谱输入检查依赖见证文件存在且字节一致。
- `src/dogpdkb/observe.py:67-76`：status 扫描 run-*/run.json；整个 run 删除会使记录消失。
  `src/dogpdkb/sync.py:77` 返回 run 和 manifest 路径。
- `tests/test_orchestrator_apply_cli.py:793-825` 验证删除 run 后仍抑制旧失败重试；
  该测试不证明成功输入、复用和结果引用均独立于 run。
- 现行 `docs/sync-state-design.md:22-24`、`docs/unattended-sync.md:35-36` 与
  `.scratch/cli-orchestrator-rewrite/spec.md` 把旧 run 清理交给独立 agent。

代码图 CLI 尝试读取时提示已有 schema v15、新装 CLI 仅支持 v13；
因此使用定点源码核查，未修改现有图数据库，也未据不完整图作全局结论。

## partial 的实际范围

- 导出：来源版本不明、同版本导出受抑制、某文档导出失败或版本不匹配
  （apply.py:113,137,222-229）。Skylark 退出码不在 0/2 则整批 failed。
- 比较：mdcompare 报告缺失或格式无效（apply.py:332-344）。
  有效的“不确定”结果会保守编译，本身不等于 partial。
- OCR：失败、不明、额度或批次预算导致未尝试、同版本失败受抑制
  （apply.py:393-410,497-504）。
- 图谱：已有版本失败抑制目录编译（graph.py:110-119）；或者 Graphify 普通非零退出
  （graph.py:169-170,266-273）。后者也是 partial，整批图可能未更新、保留旧图。
- 显式恢复保留此前 run 的失败列表，后续成功也可能仍为 partial
  （apply.py:625-626,654-658）。
- Graphify 超时、无可靠输出、输入改变或发布失败走 unknown/failed1；
  信号中断与计划暂停另有状态，不应混入“部分 OCR 失败”。
