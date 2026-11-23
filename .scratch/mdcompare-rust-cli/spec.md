# Rust mdcompare CLI 规格

Status: resolved

## 目标和职责

上游提交工作单，mdcompare 冻结输入和规则、离线比较、发布可验证的结果和证据。上游拥有文档身份、业务版本、批次选取、跨批去重、Graphify 编译、基线推进和最终发布。mdcompare 负责单次比较与评分任务内的快照完整性、证据绑定和请求预算。

借鉴 [Rust mdocr CLI 规格](/home/ubuntu/.herdr/worktrees/dogpdkb/experiment-mdocr-rust/.scratch/mdocr-rust-cli/spec.md) 的工作单、配置解析、工作目录、离线帮助和输出流。正文比较可以在无凭据、无网络、无环境变量的系统上完成。付费评分是独立动作，且运行前需要明确的请求预算。

Rust 是独立的原生重写，本规格定义完整公开契约。所有命令共用工作单、配置解析、工作区和进程契约。Rust 二进制不带旧命令处理器、参数别名、旧格式读取器、自动迁移、双写路径或旧帮助页。dogpdkb 的 Rust 调用路径直接生成和消费新版产物。

## 命令

| 命令 | 行为 |
| --- | --- |
| `mdcompare draft OLD.md NEW.md --plan work.json` | 写一份可编辑的单对比工作单，默认 `semantic`。两侧按同一临时本地身份配对。目标已存在时失败。 |
| `mdcompare accept --plan work.json [--config FILE]` | 验证并冻结比较工作单、依赖和有效配置，返回已接受的范围和计数。评分工作单由 score 自行接受。 |
| `mdcompare run --plan work.json [--config FILE]` | 首次自动接受，再离线执行。已完成的工作单返回原结果。 |
| `mdcompare score --plan score.json [--config FILE] [--resume]` | 根据独立评分工作单验证指定文档、记录请求并评分。显式取消后用 `--resume` 继续。 |
| `mdcompare route --plan work.json --scores FILE [--config FILE]` | 基于已完成的正文比较和已绑定评分生成离线路由。支持本工具评分及符合契约的外部评分。 |
| `mdcompare status --plan FILE` | 只读查询比较或评分工作区状态及已发布产物。 |
| `mdcompare cancel --plan score.json` | 记录评分取消意图，停止新请求，允许已发请求在原期限内结算。 |
| `mdcompare doctor [--config FILE]` | 检查配置、本地能力及配置指定的评分凭据。 |
| `mdcompare --help`、`mdcompare COMMAND --help`、`mdcompare help all`、`mdcompare --version` | 内嵌英文帮助和版本，不访问配置、工作区或网络。 |

表中命令和参数构成新 Rust 二进制的完整公开接口。旧命令 `compare`、`knowledge`、`catalog`、`semantic` 迁移为 `run --plan` 配合工作单的 `operation`，`semantic-score` 和 `semantic-route` 分别迁移为 `score` 和 `route`。新二进制拒绝旧命令、旧参数和兼容别名，返回用法错误 64。

英文帮助随新接口重写，按三层渐进披露。`mdcompare --help` 列出命令及最短调用流程。`mdcompare COMMAND --help` 说明该命令的参数、默认值、示例和退出码。`mdcompare help all` 提供完整的工作单、配置、产物、输出流、恢复及评分契约，可离线阅读。帮助全部内嵌于二进制。

人手操作时只需两条命令。批量调用者直接生成同一种工作单，可直接 `run`，也可先 `accept` 检查计数。

```sh
mdcompare draft before.md after.md --plan work.json
mdcompare run --plan work.json >summary.json 2>>mdcompare.log
```

## 比较工作单

`mdcompare-work-1` 是 UTF-8 JSON。拒绝重复键、未知字段、无效类型和重复作用域身份。工作单相对路径基于工作单所在目录解析。dogpdkb 直接从已捕获的快照按作用域身份配对并生成工作单。

```json
{
  "schema_version": "mdcompare-work-1",
  "operation": "semantic",
  "documents": [
    {
      "knowledge_base": "payments",
      "document_id": "42",
      "baseline": {
        "path": "snapshots/v17/42.md",
        "content_kind": "raw",
        "metadata": {"name": "Limits", "logical_path": "Rules", "source_ref": "payments:42"},
        "provenance": {"version": "17"}
      },
      "incoming": {
        "path": "snapshots/v18/42.md",
        "content_kind": "raw",
        "metadata": {"name": "Limits", "logical_path": "Rules", "source_ref": "payments:42"},
        "provenance": {"version": "18"}
      }
    }
  ]
}
```

- `operation` 明确取 `semantic`、`knowledge`、`strict`、`catalog` 之一，每张工作单执行一种比较或盘点。如需多种诊断，由调用方创建各自工作单。
- `documents` 按提交顺序输出，空批次合法。每个条目至少一侧非空，缺侧用 `null`。baseline-only 只进入提示，不由工具解释为删除。catalog 只接受 incoming。
- 文档身份在条目层提供一次。`content_kind` 取 `raw` 或 `enriched`。每侧描述捕获的快照，可包含 `content_kind`、`metadata`、`provenance`、`body_scope`、`resources`、`ocr`。strict 要求明确资源声明，空数组仍按正文实际引用核验。catalog 可以从缺省资源声明开始盘点。
- 仅 `semantic` 工作单可带 `body_recovery`，引用 `mdcompare-body-recovery-1`。每项指定作用域身份、baseline/incoming、与原文逐字节相同的本地见证，以及按原始 UTF-8 字节偏移排序的插入。只接受生成区域边界标记或换行。修复清单与 witness 均参与接受时绑定。只在内存投影正文，证据保存原始坐标。正文投影只识别明确配对的生成区域边界及已声明的范围。
- `draft` 只创建工作单文件。新文件使用独立本地身份和可编辑的快照元数据。批量输入仍由有身份和快照知识的上游生成。

## 操作、配置和输出

各操作使用首版结果格式 `mdcompare-semantic-result-1`、`mdcompare-knowledge-result-1`、`mdcompare-strict-result-1`、`mdcompare-catalog-result-1`。共用字段为 `schema_version`、`state`、`results`、`baseline_only` 和 `counts`。每个 incoming 身份恰有一条结果，按工作单顺序输出，baseline-only 单列。各操作附上自身裁决、原因和证据。语义索引、证据、评分 journal 和路由结果分别使用新版 `mdcompare-diff-index-1`、`mdcompare-diff-evidence-1`、`mdcompare-score-journal-1`、`mdcompare-route-result-1`。完整字段、枚举及示例随 `help all` 交付。

| 操作 | 产物 | 裁决 |
| --- | --- | --- |
| semantic | `result.json`、`diff-index.json`、`evidence/` | 只有配对、上下文和规范化序列一致才能 SKIP。可用但未确定的正文走 COMPILE，正文不可用走 DEFER。证据按文档流式写入，保留完整上下文和源坐标。 |
| knowledge | `result.json`，可选 `review.md` | 片段诊断解释复用、增补、修订和待复核项。正文与图片、OCR 证据独立。候选是诊断信息。 |
| strict | `result.json`，可选 `review.md` | 分别验证文本、资源、元数据、OCR 四维证据。任一必要证据不可验证时返回 UNKNOWN。证据齐备时依次判定新文档、资源变更、OCR 待处理、正文变更、元数据变更及验证后跳过。解释额度耗尽只截断解释，已确定的比较结论保持有效。 |
| catalog | `result.json` | 盘点本地 Markdown 的资源声明，保留未验证原因。远程资源不下载。 |

显式 `--config FILE` > 工作单旁 `default.toml` > 编译默认值。显式文件使用自身设置加编译默认值。每个 TOML 路径相对该 TOML。已接受任务以冻结有效配置为准，显式重传时要求语义一致。比较仅冻结 compare/report，评分仅冻结 score 与当前提示词、schema、执行协议版本。评分使用有界证据包，包含完整变更操作、涉及的正文单元、每侧至多两个相邻单元及显式关联的保留文本候选；过大的包整体挂起，发给 provider 的内容排除路径、图片和 OCR。恢复须能执行冻结版本，否则退出 1。route 仅读 route，已有任务未显式传配置时不重读旁置默认文件。route 无参配置仍按该次调用选择比较工作单旁的 default.toml。裸 `doctor` 检查编译默认值，`doctor --config` 用同一解析器检查指定配置。

```toml
[compare]
max_file_bytes = 16777216
max_diff_work = 2000000

[report]
markdown = false
include_body_excerpts = false
# max_excerpt_chars = 8000  # Set only when include_body_excerpts is true.

[score]
request_budget = 20
model = "openai/gpt-6.1-terra"
effort = "medium"
concurrency = 3
oauth_file = "credentials/pi-auth.json"

[route]
compile_above = 3
compile_ambiguous = false
```

- `[compare]` 是可影响比较完整性的限制。`normalize_line_endings` 只适用于 strict，默认关闭。工作单读入上限为 16 MiB。字符级解释每个区域上限为删除与新增合计 4096 字符，解释截断不影响裁决。
- `[report]` 只控制可选人读输出及其正文摘录。开启摘录须先开启 Markdown 报告。配置了摘录上限却未开启摘录时返回配置错误。完整语义机器证据独立于该配置。帮助和产物说明应提醒完整证据包含正文。
- `[score]` 只在评分前生效。必须提供 `request_budget`，`0` 产出未评分结果并产生零请求，正值限制本次评分工作单累计 admission。空授权或预算为零直接离线结算，无需凭据。OAuth、代理和附加 CA 通过显式配置解析，运行不读取 HOME、PI_CODING_AGENT_DIR、HTTPS_PROXY 等环境变量。`oauth_file` 只记录引用，允许同一位置凭据轮换。
- `[route]` 是独立策略。路由按当前调用解析该节，每份产物记录自身有效策略。只有具备绑定有效分数的合格 CHANGED/COMPILE 结果可以改线。分数严格大于 `compile_above` 走 COMPILE，其余可 SKIP。`compile_ambiguous = true` 将其中含歧义的 SKIP 改回 COMPILE。已验证的分数缺失时维持原路由。改变路由配置可重新运行，无需重新比较或评分。

## 工作区、冻结和恢复

工作单使用 `.json` 扩展名，按规范绝对路径将最后的 `.json` 换为 `.mdcompare/`（如 `work.json → work.mdcompare/`）。工作区绑定该工作单的规范路径及接受时的文件字节 SHA-256；同一路径换单须报错。工作单内容和依赖在不同路径下可形成新任务。该目录由本任务独占。拒绝输出与输入、配置、恢复见证或已有外部产物相互覆盖，包括 symlink、hardlink 和目录逃逸。新工作区通过锁序列化接受，先写同盘暂存区，再原子提交。

- `plan.json` 冻结带版本的有效工作单、调用顺序、规范路径、输入摘要和有效策略。接受时区分不可读的单个输入与无效的批次契约。正文不可用的条目仍可接受并在运行报告 DEFER，strict 的不可验证结果保留 UNKNOWN。
- semantic 和 knowledge 仅冻结正文比较消费的输入，绝不打开图片、附件或 OCR 证据文件。图片标记和已声明的正文范围由 Markdown 投影处理。strict 冻结实际消费的本地资源和 OCR 证据，catalog 记录盘点消费的本地路径存在状态。读取、比较并校验同一份字节，禁止先核验再重新打开未核验的内容用于裁决。
- 比较期间只读原始快照。检测到接受后漂移便拒绝使用该批发布新结果，调用方须提交新的工作单。缺失输入在计划中显式记录，状态变化同样需要新计划。已提交结果可依据工作区中冻结计划及产物哈希独立验证。
- 比较发布以工作区内最后写入的 `commit.json` 为唯一提交标记。`mdcompare-commit-1` 含 `plan_sha256` 和 `artifacts`（工作区相对路径到文件 SHA-256 的映射，含 result、适用的 diff-index 和 review）。semantic 的 index 记录 result 原始字节摘要及每个 sidecar 的相对路径和摘要。result 不反向写入 index 摘要。所有摘要均为已落盘文件原始字节的 SHA-256 小写十六进制。commit 自身的字节摘要即 `comparison_sha256`，由 run 和 status 返回。
- 单写者先持久化依赖文件和新目录项，再写入并同步暂存标记，原子 rename 标记后同步父目录，随后才报告成功。读者从标记验证冻结计划、整套产物和索引内证据，拒绝缺失、替换或逃逸。无标记时，调用方不能消费裸 result；run 可基于冻结输入重算。已有标记损坏时报绑定错误。已发布的完整或部分结果重复运行只验证并返回原代，不要求原输入继续在线。
- 离线路由输出写入比较工作区内独立的 `routes/<binding>/result.json`，binding 取 UTF-8 紧凑 JSON 数组 `[comparison_sha256,scores_sha256,compile_above,compile_ambiguous]`（无空白、无末尾换行）的 SHA-256；scores_sha256 对实际消费的评分文件字节计算。结果记录这四项和逐文档的原裁决、最终裁决及原因。不同策略保留各自结果，重复执行同一策略返回同一结果。

## 独立评分工作单

`score` 接收 `mdcompare-score-work-1`。主比较任务完成后，上游依据自己持有的业务版本状态保留本次允许评分的身份，再提交工作单。这样不用为每个文档展开 `--document-id`，也不用在开始比较前预测哪些文档可评分。

```json
{
  "schema_version": "mdcompare-score-work-1",
  "comparison_plan": "work.json",
  "comparison_sha256": "9b71c6a41b0d5eac96458f3bc3c92a50ade0cf891738947fe12a6f05c89d4471",
  "documents": [
    {"knowledge_base": "payments", "document_id": "42"}
  ]
}
```

- `comparison_plan` 定位已提交的 semantic 工作区。`comparison_sha256` 必填，调用方在挑选并预留业务版本时从该代提交摘要取值（示例为占位摘要）。score 在首次授权和恢复时验证整条绑定，有待发请求时才打开 provider。部分提交中合格的文档仍可评分，DEFER 保留原裁决。
- `documents` 是明确的授权清单，空数组代表无候选。要求唯一且可评分的已绑定身份。合格条件为 CHANGED/COMPILE、正文可用和比较完成。无法构造符合证据约束的请求时，在结果中记录未发出的原因。
- `score.mdcompare/` 有自己的 `plan.json`、`journal.json`、`result.json` 和执行锁。接受评分工作单时，初始空 journal 与冻结计划同时持久提交；已有计划但缺 journal 是损坏。每次请求先落盘 admission，再发送。429 后先结算在途请求，再将持久化并发上限降一级；上限为 1 时停止。认证失败或运行中观测到未知发送结果也停止新增 admission。取消意图与新增 admission 串行化，活跃任务可以接收取消。显式取消需要 `--resume`，仍使用原冻结配置和剩余预算。进程中断留下的未结算 admission 归为 unknown，计入预算且不重发。每个授权身份恰好一条终态记录，按授权顺序输出。

| 行状态 | 可核验的事实 | dogpdkb 对该版本的处理 |
| --- | --- | --- |
| `scored` | admission 已结算，答复覆盖全部期望操作，且通过内嵌评分 schema 和范围校验 | 保留预留，可按分数路由 |
| `unscored` | 已 admission，已知失败或答复无有效评分 | 保留预留，沿用正文裁决 |
| `unknown` | 已持久化 admission，发送或结算结果无法确定 | 保留预留，沿用正文裁决 |
| `not_sent` | 完整 journal 证明该身份零 admission，记录预算、取消或证据限制等原因 | 终态核验后可释放该版本预留 |

- 每个身份在一张工作单中最多一次 admission，包括 429。每个实际 provider 请求须先有对应的持久化 admission，禁用传输层自动重发。仅持久化结算后才能发布有效评分；崩溃时无结算记录的 admission 一律 unknown。已初始化的执行记录缺失、损坏或与计划不符时退出 1，禁止重建为空账。dogpdkb 在启动子进程前保存预留、比较代及评分工作单路径。丢失终态证据时保持预留，恢复到同一工作区；另建工作单本身不能证明未发送。
- cancel 在与 admission 共用的短期锁上持久化意图，执行锁覆盖整个 score 进程。意图生效后零新增 admission，在途可结算。重复 cancel 幂等。尚未接受的任务返回 1，已终态任务返回其现状。取消生效后 status 可查询剩余身份，退出 3 的 score 只发布取消时快照；仍有恢复可能时，上游继续保留预留。仅 `score --resume` 清除本次取消意图，继续零 admission 的身份。所有授权身份已结算则完成优先。进程异常退出后再次 score 可恢复未取消任务，恢复时把上次中断留下的 admission 结为 unknown，仅剩余预算内从未 admission 的身份可发送。journal 已全部结算但 result 未写成时，直接从 journal 发布，零网络。一般终态（含预算耗尽的 partial）重复调用返回既有结果。
- score 发布 `mdcompare-score-result-1`，带 `comparison_sha256`、`score_plan_sha256`、`journal_sha256`、任务状态及上述有序 rows，result 原子替换前须持久化对应 journal。每条记录含作用域身份、状态、稳定原因及 admission 序号（零次为 null）；有效分数另含 evidence 相对路径及摘要、1–5 的整数 score 和布尔 ambiguous。可恢复的取消快照在 resume 后失效。status 与调用方须验证 journal 绑定和行与 admission 的对应关系，不能仅凭一条 not_sent 文本释放预留。
- route 接受已终态的本工具评分或外部 `mdcompare-external-scores-1` 清单。顶层字段为 `schema_version`、`comparison_sha256` 和 `scores`。每条分数提供 `knowledge_base`、`document_id`、`evidence_sha256`、整数 `score` 和布尔 `ambiguous`。索引和证据路径由比较工作区和作用域身份确定。外部评分仅授权路由，不能证明 provider 发送状态。route 在任何发布前验证比较提交、分数文件及全部行。重复身份、跨代、坏摘要、无效类型或越界直接退出 1，无新路由产物。只有合格正文行的有效评分能改线；空清单、未列出的身份和无有效评分的行沿用原裁决。阈值接受整数 1–5，布尔值不能充当整数。读取取消快照须先恢复到终态，或使用上游提供的独立外部评分。
- 首版提供取消与只读状态，便于控制线上请求。离线比较可安全重算。若后续出现需要从已结算 journal 单独恢复评分报告的操作场景，再设计 `assemble`。工作区清理由调用方的保留策略管理，Rust CLI 当前提案不提供 `purge`，以保护计划仍引用的证据。

```sh
mdcompare score --plan score.json --config scoring.toml
mdcompare route --plan work.json --scores score.mdcompare/result.json
```

## 进程契约和交付

业务命令向 stdout 输出一个最终 `mdcompare-summary-1` JSON，含命令、状态、绝对产物路径、计数及适用的稳定 `reason_code`。stdout 不承载整份报告。stderr 输出带时间戳、可立即读取的英语逐行进度，至少包含阶段、文档计数、请求数、在途数和停止原因。长时间运行提供心跳。两路输出均不包含正文、完整 data URL、原始模型回复或凭据。帮助和版本仍使用人读文本。

| 退出码 | 含义 |
| --- | --- |
| 0 | 已完成请求动作。允许有已确定的差异与 COMPILE。 |
| 1 | 输入、配置、绑定、工作区或发布失败。 |
| 2 | 已产生可用的部分结果，或只读诊断发现可行动问题。按结构化状态区分。 |
| 3 | 评分处于显式取消状态，继续执行需要 `--resume`。成功写入取消意图的 cancel 本身返回 0。 |
| 64 | CLI 用法错误。 |

成功读取 status 一律退出 0，所查任务的状态置于 `state`。它不创建目录、不修复 journal、不读取凭据。状态为 `accepted|running|cancel_requested|cancelled|complete|partial`；工作区不存在或绑定无效退出 1。running 以在用执行锁为准，失锁且未终态的任务显示 accepted，并报告 `reason_code=interrupted`；持久取消意图优先。score 终态只要有任一授权行未获有效分数即为 partial、退出 2；空授权为 complete。semantic 有 DEFER 即 partial；可用正文的 UNCERTAIN/COMPILE 本身不导致退出 2。route 成功发布返回 0，逐行保留 DEFER。doctor 仅做本地诊断，发现已配置能力不可用返回 2。

业务调用 stdout 的公共字段为 `schema_version="mdcompare-summary-1"`、`command`、`state`、`artifacts`（名称到绝对路径）、`counts`（名称到非负整数）和 `reason_code`（正常时 null）。有比较绑定时同时返回 `comparison_sha256`。未产生的产物不列出。工作区查询以外的成功动作可用 complete 表示动作完成；accept 返回 accepted，cancel 返回当前任务状态。评分和评分状态计数分别公开 `selected`、`admitted`、`scored`、`in_flight`，避免把授权数当作已发送数。受控业务失败退出 1 时返回 state=error 及原因，至少区分 `invalid_input`、`invalid_config`、`input_drift`、`binding_invalid`、`workspace_busy`、`publish_failed`、`workspace_missing`。参数解析失败退出 64、stdout 为空。进程被强杀时允许没有最终 JSON，调用方用 status 和已提交产物恢复事实。

单个静态 Linux x86-64 文件，内嵌英语帮助、默认提示词和评分 schema。目录、代理、证书和凭据显式解析，离线执行与诊断在清空环境下可运行。保持 strict 对已支持资源证据的验证范围。mdocr 首版的图片解析边界不直接成为 mdcompare 的资源相等规则，新型资料以可验证证据契约为准。

## 实现和验收

1. 先实现工作单、接受、离线 run、统一进程契约和三层帮助。在冻结的真实 Markdown 快照上验证四种操作的结果、证据、正文范围和退出状态。
2. 接入独立评分工作单和离线路由。用环回 provider 验证预算、先落盘后发送、取消、未知请求不回放、外部评分绑定和改阈值零网络。线上 OAuth 与已安装主机验收分开。
3. 单独迁移 dogpdkb 的公共 CLI 适配。它从当前捕获结果生成比较工作单，读已提交产物，按业务版本保留可评分身份后生成评分工作单。通过公开 CLI 和新版产物验收集成。
4. 所有阶段验证无配置环境的帮助，空 PATH 下运行复制后的二进制，验证非法字段、快照漂移、输出碰撞、崩溃提交、stdout 单个可解析对象和 stderr 实时脱敏。切换 Markdown 报告和正文摘录配置，验证业务裁决一致。
5. 对新二进制逐一验收三层帮助。顶层有最短调用流程，命令帮助覆盖实际参数，`help all` 自带完整契约。逐一调用旧命令，并给新命令传入旧版专用参数，均须退出 64、stdout 为空，且不创建工作区或产物。提交旧清单、旧评分产物或旧 journal，须拒绝执行且零 provider 请求。

### 编排器黑盒验收

| 场景 | 必须观测到的结果 |
| --- | --- |
| 同一批真实 Markdown 含 EQUIVALENT、CHANGED、NEW 和正文不可用，另有同 ID 不同知识库文档 | run 退出 2，仅不可用项 DEFER；按作用域独立授权 CHANGED，零误选；移走图片/OCR 证据不改变正文路由。 |
| 在比较提交标记前后分别强杀；随后替换一个 sidecar | 标记前无可消费结果；标记后可恢复同一摘要；证据被替换后 score 和 route 均退出 1，零请求、零新路由。 |
| 上游预留后将同路径工作区换为另一份有效比较，或将外部分数拷至另一代 | 摘要或行绑定不符，拒绝整次调用。 |
| 预算 1、两个合格身份，在 admission 持久化后发送前、发送后结算前分别强杀并重启 | 两种情况均为一行 unknown、另一行 not_sent；两次执行合计环回 provider 收到的请求分别为 0、1。删除已初始化 journal 则退出 1，保留预留。 |
| 预算 2、并发 1，在第一请求在途时 cancel，再恢复两次 | 取消后收到的新增请求数为 0；第一行可结算；第二行仅在显式 resume 后发送一次；累计 admissions 为 2。最后结算后、报告发布前强杀可离线还原同一评分。 |
| 阈值 3，评分分别为 3、4、含歧义的 3、缺分，另有 DEFER | 默认依次 SKIP、COMPILE、SKIP、保留原路由、DEFER；打开歧义拦截仅第三项变为 COMPILE；两次 route 均零网络，先前发布的路由仍可验证。 |
