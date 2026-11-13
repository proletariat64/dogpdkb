# 四个 CLI 的 help 审查

基线：dogpdkb master `9615a85`；Skylark 为用户提供的
`/tmp/pi-skylark-restored-cli-0e7c218/dist/cli.mjs`。

## 结论

四者都存在输入/输出合同披露不足。Python CLI 已有根级到子命令的结构，
但许多叶子帮助只列参数；Skylark 尚未形成有效的子命令帮助。
AI 目前常需另读文档或源码才能构造正确调用。

| CLI | 覆盖的子命令数 | 完整性 | 渐进披露 | 主要问题 |
| --- | ---: | --- | --- | --- |
| dogpdkb | 11 | 不足 | 有两层结构，叶子说明不足 | 核心参数无说明，流程、副作用、退出码和产物合同未披露 |
| mdcompare | 3 | 不足 | 根级分类清楚，输入合同缺失 | 不解释 manifest；参数错误不指出错处 |
| mdocr | 3 | prepare/ocr 较好，batch 不足 | 两层结构成立 | batch 全部业务参数无说明；鉴权、报告、partial 语义缺失 |
| skylark | 2 | 明显不足 | 各层输出相同两行 | 无请求示例、环境说明、退出码；-h 和 --version 不支持 |

## 实测范围

- 四个根命令加 19 个子命令：共 23 条 `--help`，均 exit 0。
- 相同 23 条 `-h` 路径：20 条 exit 0；Skylark 根级/listing/export 三条 exit 1。
- 覆盖根级版本、空调用、未知命令、未知选项，以及安全的缺参错误路径。
- Python CLI 使用本仓库 `.venv/bin/`。三者根帮助与现有小订阅 UAT 安装的
  `.scratch/small-subscription-uat/venv/bin/` 完全一致。
- PATH 上 `/home/ubuntu/.local/bin/dogpdkb` 仍是旧命令集，缺 status/init-state；
  PATH 的 mdocr 缺 batch；mdcompare/skylark 未在 PATH。此为安装环境差异，
  不将旧安装输出混入当前源码质量判断，也未更新全局安装。
- 在临时 cwd 执行的短帮助和错误路径均未在那里创建文件；未验证所有 HOME/系统路径
  的写入，也未进行网络抓包。源码帮助分支在业务执行前退出。
- 本轮仅执行帮助、版本与参数校验；未运行真实导出、OCR、Graphify 编译或 sync。
- 原始命令输出保存于本目录 `*-help.txt`、`*-error.txt` 等记录；
  `short-help-matrix.json` 保存 19 个子命令短帮助的返回码及 cwd 文件检查。

## 发现与优先级

### 高：JSON 参数不能仅靠 help 正确构造

- `src/mdcompare/cli.py:52`、`:100`、`:130`：baseline/incoming/input/output 仅列名字，
  没说输入是 JSON manifest，而非 Markdown；catalog 也无最小输入示例。
- `src/mdocr/cli.py:96`：batch 的 manifest/output-dir/report/cache-dir/download/
  endpoint/max-seconds 全部未写 help；没有 schema、路径基准、900 秒默认值、
  报告字段或退出码说明。
- `/tmp/pi-skylark-restored-cli-0e7c218/cli.ts:133`：export 未解释 request JSON 的
  schema/documents/document_id/path/format；用户无法仅靠帮助构造一次导出。
- 修正：对应叶子帮助增加最小有效 JSON、路径解析规则、产物与完整合同入口。
  现有合同分别在 docs/mdcompare.md、docs/orchestrator-tool-contracts.md、
  docs/orchestrator-skylark-contract.md；工具自己的帮助应提供可发现入口。

### 高：mdcompare 的参数错误无法自我修正

`src/mdcompare/cli.py:23` 丢弃 argparse 的 message。空调用、未知命令、未知选项、
compare 缺参都只输出 `mdcompare: INVALID_ARGUMENTS`，exit 1。
保留稳定错误码的同时，应附上具体错处和该层帮助提示。

### 高：dogpdkb 核心操作缺少业务语义

- `src/dogpdkb/cli.py:70` 根级多个命令只解释为 run COMMAND。
- `src/dogpdkb/apply.py:687` sync/apply 的 config/book/resume/stop-after 全无说明。
- `src/dogpdkb/discover.py:298` 未解释 listing 是离线输入、discover 的输出是什么。
- `src/dogpdkb/observe.py:92` 未解释 run 可选值、默认最新/活跃选择、watch 和 JSONL。
- 应披露配置选择规则、book 是 key、暂停与恢复流程、写入范围、输出位置及
  0/2/1/64/130/143 的意义，避免 AI 将 partial 机械当成重跑理由。

### 中：Skylark 子命令帮助与短帮助不完整

`cli.ts:132-141`：根、listing、export 的 --help 完全相同；-h/--version 均未知选项。
未知选项返回 1，而参数不齐返回 64，与文档所述 invalid usage=64 不一致。
建议标准化帮助和错误分类，并让 listing/export 分别展开各自合同。
本轮没有修改其源码。

### 中：认证、输出与 partial 含义没有出现在帮助

- mdocr ocr 需要 PADDLEOCR_TOKEN，但帮助未提示；batch 新调用、复用和 partial
  的含义应按该工具合同说明，不能套用 prepare/ocr 的返回码。
- 四者帮助均无具体 Exit codes 节，也缺少 stdout/stderr 与磁盘产物的简明区分。
- mdcompare 的 2 表示 unresolved/review；mdocr batch 的 2 表示结构化 partial；
  dogpdkb 的 2 表示有失败记录的批次结束。AI 不能只据数字猜测是否重试。
- Skylark CLI.md 已记录 gateway 环境变量和 SSO、请求与结果 schema，但 help 没有入口。

### 低：显示细节

- `src/dogpdkb/workflows/publish_web.py:320` 未指定子命令 prog，实测 usage 显示
  dogpdkb 而非 dogpdkb publish-web，复制命令容易丢失动作。
- `src/mdocr/cli.py:91` prepare/ocr 共用 force 描述；prepare 帮助也声称会 re-OCR，
  但该分支只做准备，应分别描述实际行为。
- 本轮尚在规划的 purge 不是已实现命令，不作为当前 help 遗漏。

## 建议的最小披露层次

1. 根级：工具目的、每个命令用途、最常见起步路径、子命令 --help 提示。
2. 叶子：用途、必填参数及输入类型、默认值/路径基准、产物和副作用、
   认证前提、退出码，以及一两个可复制的最小调用或 JSON 示例。
3. 完整合同：详细 schema、边界行为与进阶参数，提供明确文档入口。

Python 侧用现有 argparse 的 description/help/argument groups/epilog 即可。
无需为此次改进增加通用帮助平台；机器可读帮助可以后续按实际需要评估。

## 验证建议

只验证 CLI 合同：根/叶子的 -h 与 --help 都成功且内容对应；缺参和未知参数指出
具体错误；示例输入符合工具公开 schema；离线无认证也能看帮助。
这些检查不需要真实知识库下载或付费模型调用。

本次未修改运行代码，未执行部署或更新已安装工具。
