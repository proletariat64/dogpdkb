# 日常知识增量：最终双轴审查

审查基准：`origin/master...e9f4db8ac1bd2d13ece0528fb6ba5816d90ca675`。该提交的910项测试通过不代表下列安全场景已覆盖。原审查为阻塞；票06统一修复和复验现已通过，证据见下。

## Standards

规范轴发现2项阻塞，均违反数据/操作边界保护要求：

- STD-1：采集页缓存、raw与snapshot之间未完整防碰撞，可能覆盖旧快照、输入或刚导出的正文；须在任何写入前保护全部已知输入与动态输出，含文件别名。
- STD-2：执行只校验state schema，未像planner一样校验book身份；同文档ID的其他知识库state可能被改写。须共享严格状态校验。

另一个重复校验的代码味道是判断性维护风险，不单算规范错误。该轴最严重问题是输入数据可被采集写入覆盖。

## Spec

规格轴发现10项可执行问题，8项P1、2项P2，未证明新增范围失控：

1. P1：复核事项合并成通用原因码，一次ack可掩盖多条旧规则删除；需逐单元、逐来源绑定明确处置。
2. P2：缺失token证据默认0并声称实测；需标未知，仅对实际返回的有效数值声明证据。
3. P1：先比较live raw再冻结，比较与执行可能对应不同字节；需先冻结再比较并检查原始输入漂移。
4. P1：批准后旧侧raw变动不使许可失效；需绑定冻结旧侧并复验。
5. P1：旧plan可在新版已pending时执行并清空新版待办；需拒绝被新观察/语料状态取代的许可。
6. P1：无效边在merge中被丢弃后逃过完整性门禁；需在有损合并前验证提取/缓存结果。
7. P1：新边按端点覆盖未批准来源的旧边；需保持无关来源记录，否则拒绝候选，不引入事实级自动拼接。
8. P1：进程在state提交后、报告提交前死亡导致无法续跑；需有可核验的发布journal并恢复报告，不能重复提取。
9. P1：捕获失败原因恢复后仍永久HOLD；需重新计算已恢复的瞬时错误，保留真正未解决的审核事项。
10. P2：远端采集失败前尚未收紧目录/页权限，可能留下可公开读取的私有列表元数据；需从首次写入前保障权限。

该轴最严重问题包括过期/不完整许可可以发布并移除未批准事实。以上问题来自合成CLI、外部替身或受控竞态复现；没有进行真实模型提取、语料修改或生产操作。

## 证据与处理

完整原始报告（私有主机文件）：`/tmp/daily-knowledge-sync-standards-review.md`、`/tmp/daily-knowledge-sync-spec-review.md`。规格报告还附四个可重放的合成复现脚本；代码修改由同一个实现代理统一处理，[修复票](../.scratch/daily-knowledge-sync/issues/06-review-fixes.md)。

主动LSP探测的8个产品模块有6条warning、无error；私有0700目录被错误建议改为0644属于误报，NetworkX由独立Graphify环境提供，数值转换有已验证输入或在发布前失败。该诊断分流不替代上述审查。记录：`/tmp/daily-knowledge-sync-final-diagnostics.md`。

审查结案必须补充逐项修复证据、针对性失败/通过测试、完整门禁和隔离安装复验；不能仅按现有910项通过结案。

## 票06修复复验

修复分支：`fix/daily-knowledge-sync-review`。以下保留原Standards/Spec分类；原发现不被合并或隐去。每项均先取得新CLI反例失败，再实现并复跑；另外重放了审查所附四个合成脚本。

### Standards修复

- STD-1：`discover --snapshot` 在远端页写入前统一保护baseline及其raw、pending、listing、details、snapshot、pages/checkpoint/raw，检查软硬链接和父子碰撞。失败前先建0700 staging，页与checkpoint以0600写入。baseline-page、既有page硬链接和snapshot/raw反例由覆盖/成功变为拒绝且输入不变；完整capture测试18 passed。
- STD-2：执行复用planner严格state loader，校验schema、book及documents。跨book同ID反例由成功发布变为提取前拒绝，pending保持。

### Spec修复

1. `knowledge-plan-2.0`/`knowledge-approvals-2.0`/`knowledge-quality-cases-2.0` 使用稳定`issue_id`及单元来源行。旧规则逐项`approve_removal`或`preserve`；preserve绑定同来源质量案例，旧泛ack拒绝。两条旧规则缺少任一处置时拒绝，完整合法审批可执行。
2. 缺失/非法token和调用数记录`null`/`unknown`；有效0只来自适配器证据或无提取。缺字段反例由伪0变为unknown。
3. 规划先冻结新旧输入，再比较冻结视图；批准绑定实际比较字节。竞态复现的比较证据与冻结正文一致。
4. 候选保存旧侧冻结raw，执行前与已登记旧graph raw直接比较；旧raw变化在提取前拒绝。
5. 执行核对当前observation/corpus/pending与plan；新观察已登记的旧plan拒绝，不能清新pending。
6. 提取及缓存node/edge/hyperedge在merge前验证身份与引用，并在来源替换后再次检查fresh引用。fresh dangling edge、指向被替换旧节点的边和cached dangling hyperedge均不发布。
7. fresh edge若覆盖无关来源同端点记录，候选安全拒绝；原`prohibits`边与旧state保持，不引入事实级合并。
8. 发布前journal保存原/目标state、candidate、审批/质量/raw绑定及报告。真实exit 77后核验字节重建报告，未再次提取；原OSError回退仍通过。
9. 成功重试重新计算采集/比较瞬时原因；原永久HOLD复现恢复为BODY_DELTA_CANDIDATE，仅保留APPROVAL_REQUIRED。
10. 原umask 022失败采集复现现为staging 0700、page 0600；私有标题未进入公共日志。

四个原始复现脚本均得到预期拒绝/恢复结果。新冻结UAT位于`/tmp/daily-knowledge-sync-ticket06-uat-v2`：输入与票05实物逐字节一致；7548→7550、2 new/4 updated、6捕获，2个批准、4个质量案例及2个图谱成功；负例图/state不变。全部Graphify为确定性外部替身，token也是合成值，不是实际模型或生产证据。

最终门禁：`make check` **928 passed**，doc-guard 65篇，Ruff及171文件格式检查通过（`/tmp/daily-knowledge-sync-ticket06-make-check-final.log`）。离线build和隔离安装smoke通过（`/tmp/daily-knowledge-sync-ticket06-make-smoke-final.log`）。最终10文件主动LSP为7 clean、3个既有/规则误报warning文件、0 error；0700私有权限不建议改为0644，数字转换为既有有界输入，未将这些warning冒充clean。
