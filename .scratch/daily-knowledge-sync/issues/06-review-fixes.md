# 06: 修复最终双轴审查问题

Type: task
Status: resolved
Blocked by: 01, 02, 03, 04, 05

## Context

- 集成分支：daily-knowledge-sync。完整规格：[spec](../spec.md)。
- 审查基准：e9f4db8；[审查记录](../../../docs/daily-knowledge-sync-review.md)。Standards2项阻塞、Spec10项问题必须全部处理，由同一implementer在单独worktree实现。
- 完整证据：/tmp/daily-knowledge-sync-standards-review.md 与 /tmp/daily-knowledge-sync-spec-review.md；后者包含4个合成复现脚本位置，无真实模型/生产操作。

## Acceptance

- 为STD-1/2和SPEC-1至10逐项添加现有CLI缝的red/green回归，复用外部服务替身；不得只改报告和自报pass。
- 保护采集所有输入/输出别名、page/checkpoint/raw边界及失败权限；执行严格book和完整state验证。
- 先冻结旧新比较视图，绑定审批与当前观察/语料/图谱版本；旧/新输入漂移和过期plan均拒绝，已恢复瞬时错误不永久阻塞。
- 审核逐单元有稳定来源身份，明确删除/保留处置；需要保留的已知旧规则绑定实际质量cases。接口不兼容必须版本化，不能静默批准旧的通用ack。
- 在合并前检查提取/缓存节点、边、超边；保留无关来源的记录，否则拒绝，不引入事实级合并。
- journal覆盖发布与state/report写入的硬进程死亡窗口，安全续跑不重提取；保留并发及已有错误回退保护。
- 未提供token/call证据时未知，不冒充0；无提取的0计数单独说明。
- 更新受影响CLI契约、指南、smoke和私有UAT复跑材料，历史实测不改写。全量check、build/隔离安装和逐项复现全部通过后才resolved；无实际Graphify/付费/生产。

## Working rules

读取TDD skill，逐slice先red再green。单独worktree基于集成tip；结束前合入最新集成tip、实际验证和提交；禁止reset/clean。修复后Answer逐项对应证据，审查文档分别保留Standards和Spec结果，不合并或隐去历史发现。

## Answer

实施历史：`fix/daily-knowledge-sync-review` 的 `caa4f2c` 基于集成tip `5ffc866`；最终分支 `fix/daily-knowledge-sync-spec6` 的 `74de2b942776d247b67f0dbb240ba1c3867f96bc` 保留该提交并补齐超边guard。没有reset/clean、真实模型、网络、OCR或生产操作。

### 逐项red/green

- STD-1/SPEC-10：3个新增CLI用例初始全失败，分别复现baseline页被改写、snapshot成为raw、失败页0755/0644；修复后3 passed；另补既有page硬链接反例，完整capture 18 passed。统一预检所有输入和派生输出，页/checkpoint私有写入。
- STD-2：跨book同ID用例初始返回0并发布；复用严格state loader后提取前拒绝。
- SPEC-3/4/5/9：4个用例初始分别得到15字符旧证据配不同冻结正文、旧raw变化仍发布、旧plan进入Graphify、成功重试仍HOLD；修复后全部通过，原始四脚本重放亦符合预期。
- SPEC-1：两旧规则初始只有一个原因码且plan为1.0；2.0逐项issue/disposition后，缺一项拒绝、两项明确删除可执行，preserve必须绑定并通过实际质量case，旧泛ack拒绝。sync+execute聚焦35 passed。
- SPEC-6/7：无效fresh edge和无关来源edge碰撞两个用例初始均返回0；前置提取/缓存校验及冲突拒绝后通过，另验证cached hyperedge和指向被替换旧节点的fresh引用。graph_sync/update_graph 25 passed。
- SPEC-8：真实硬退出后resume初始返回1；journal恢复后返回0、报告重建且提取仍1次。普通state OSError恢复同时通过，完整execute 24 passed。
- SPEC-2：缺token反例初始返回两个0和graphify_result；现为null/unknown。缺call证据同样unknown，正常实测值及无提取0保留；相关focused 4 passed。

### 集成核验：12项命名回归与实际断言

以下 `C`、`E`、`S` 分别指 `tests/test_knowledge_capture.py`、`tests/test_knowledge_execute.py`、`tests/test_knowledge_sync.py`；冒号后是完整测试函数名。它们均包含在下述集成分支930项通过的全量门禁中。除专门的旧schema反例外，相关执行测试使用 `knowledge-approvals-2.0` 和 `knowledge-quality-cases-2.0` 合法fixture及确定性Graphify替身；`E::test_approved_execution_replaces_complete_source_and_updates_state` 实际返回0、3个质量案例通过、1次图谱成功，不能用旧schema拒绝冒充正例。

- STD-1：`C::test_remote_pages_cannot_alias_or_contain_baseline_input`、`C::test_existing_page_hardlink_to_baseline_is_rejected_before_remote_write` 和 `C::test_snapshot_cannot_collide_with_selected_raw_output` 返回非0及 `REPORT_INPUT_COLLISION`，受保护baseline/detail字节不变，碰撞snapshot不创建；`E::test_execution_rejects_input_aliases_and_output_ancestors` 对hardlink、symlink、raw/candidate及graph祖先碰撞均拒绝且输入和图不变。
- STD-2：`E::test_execution_rejects_state_from_another_book_with_same_document_id` 对另一book的同ID state返回非0及 `different book`，未调用提取，原 `APPROVAL_REQUIRED` 保留。
- SPEC-1：`E::test_each_old_rule_requires_an_individual_explicit_disposition` 断言两条旧规则各有不同issue及来源行；少一项返回非0及 `exactly cover review issues`，补全两条删除处置后返回0且两条旧规则已移除。`E::test_preserved_old_rule_requires_and_validates_its_quality_case` 缺绑定拒绝，补上质量case后返回0并保留指定旧规则；`E::test_legacy_generic_review_ack_is_safely_rejected` 单独验证旧schema拒绝，不作为正例。
- SPEC-2：`E::test_missing_adapter_usage_is_unknown_not_measured_zero` 返回0，input/output token及external_calls均为null，证据均为 `unknown`；`E::test_execute_without_approved_candidates_does_not_invoke_graphify` 在无批准/无提取时的external_calls、cache_hits和graph_successes均为0；上述合法执行正例使用替身报告123/45 token并标记 `graphify_result`，不是账单。
- SPEC-3：`E::test_planner_compares_the_same_frozen_new_bytes_it_approves` 在读取竞态中返回0，比较的正文字符数与实际冻结正文一致；`E::test_raw_change_after_approval_is_rejected_before_extraction` 对审批后新raw变化返回非0及 `raw input changed`，不进入Graphify。
- SPEC-4：`E::test_old_comparison_raw_change_after_approval_is_rejected` 对审批后旧raw变化返回非0及 `old comparison input changed`，提取日志不存在。
- SPEC-5：`E::test_superseded_plan_cannot_publish_or_clear_newer_pending` 对被新观察取代的plan返回非0及 `superseded`；新observation和 `APPROVAL_REQUIRED` 保留，旧graph版本及图字节不变。
- SPEC-6：`E::test_invalid_fresh_edge_is_rejected_before_lossy_merge`、`E::test_fresh_reference_to_replaced_old_node_is_not_silently_dropped` 和 `E::test_invalid_cached_hyperedge_is_rejected_before_lossy_merge` 均返回非0及 `candidate-only failed`，原图不变且未产生ready候选。`E::test_partially_missing_fresh_hyperedge_is_rejected_after_replacement` 在合法2.0审批下拒绝部分悬空fresh超边，原图/state字节不变；`E::test_fresh_hyperedge_with_all_members_present_is_published` 在同样合同下返回0，发布含两个有效成员的超边并推进graph版本。
- SPEC-7：`E::test_unrelated_source_edge_collision_is_safely_rejected` 对覆盖无关来源边返回非0及 `candidate-only failed`，原图中 `prohibits` 边及pending保留。
- SPEC-8：`E::test_hard_exit_after_state_commit_recovers_report_without_reextracting` 在state提交后真实退出77，重跑返回0、报告重建、图字节不变且总提取次数仍为1；`E::test_graph_publish_then_state_failure_recovers_without_rebilling` 验证state写入OSError后也可恢复，提取仍为1次。
- SPEC-9：`S::test_successful_capture_retry_recomputes_transient_failure` 的失败采集重试后返回0、动作 `BODY_DELTA_CANDIDATE`，pending仅 `APPROVAL_REQUIRED`，不残留 `export_failed` 或 `CAPTURE_EXPORT_FAILED`。
- SPEC-10：`C::test_failed_remote_capture_still_creates_private_pages` 在umask 022下模拟失败，返回非0但已写出的staging及pages目录均为0700、page为0600。

### 合同和UAT

- plan/approval/quality升级为2.0；CLI指南、安装smoke及测试fixture已更新。新合法审批正例先执行成功，再验证恶意场景，未以旧schema拒绝冒充修复。
- 新冻结UAT：`/tmp/daily-knowledge-sync-ticket06-uat-v2`。冻结输入与票05逐字节一致；采集7548→7550、2 new/4 updated、6导出，详情异常按原合同保持；正反例执行通过，2批准、4质量案例、2图谱成功，负例图/state不变。确定性替身未做真实提取；合成token不是账单。
- 首次v1复跑因测试脚本让正负例共享负例缓存而未通过，未计为产品缺陷或通过；v2隔离缓存根后完成。历史实物未修改。
- 审查历史及逐项定位：`docs/daily-knowledge-sync-review.md`。共享进度：`/tmp/daily-knowledge-sync-fix-progress.md`。

此前`make check`：**928 passed**，doc-guard 65篇，Ruff及171文件格式检查通过，日志`/tmp/daily-knowledge-sync-ticket06-make-check-final.log`。离线build和隔离安装smoke通过，日志`/tmp/daily-knowledge-sync-ticket06-make-smoke-final.log`。当时10文件主动LSP为7 clean、3个warning文件、0 error；warning均为0700规则误报或既有有界数字转换，未宣称全clean。

### SPEC-6尾部补充

主线程合并前发现fresh hyperedge检查误用proper superset `>`。新增真实CLI/外部替身反例：doc1旧节点被替换，doc2无关节点保留，fresh hyperedge同时引用fresh node和消失旧节点，合法审批及质量case下旧代码返回0并静默丢弃超边发布，red成立。后置guard改为真正的subset检查后，提取后拒绝且原图/state字节不变；全部成员有效的fresh hyperedge正例发布成功。原直接merge测试改用有效超边，继续验证alias保留而不再依赖有损丢弃。

聚焦graph/execute反例4 passed，完整execute 28 passed，graph_sync/update_graph 25 passed。最终`make check`：**930 passed**，doc-guard 65篇，Ruff及171文件格式检查通过，日志`/tmp/daily-knowledge-sync-ticket06-spec6-make-check.log`。源码变化后的离线build/隔离安装smoke通过，日志`/tmp/daily-knowledge-sync-ticket06-spec6-make-smoke.log`。未重复冻结UAT、真实模型或生产操作。当时集成tip `5ffc866` 未前进，最终分支保留`caa4f2c`全部成果。

### 集成收尾

`fix/daily-knowledge-sync-spec6` 以 `--no-ff` 合入，合并提交 `963015773ba1e3bf333e36337d8b153297f2a3d4`，父提交分别为 `5ffc866d84b4cb441e7584b084aef818f005138b` 和 `74de2b942776d247b67f0dbb240ba1c3867f96bc`。合入后离线环境 `make check` 实测 **930 passed**、doc-guard **65** 篇、Ruff通过、**171** 文件格式通过，日志 `/tmp/daily-knowledge-sync-integration-ticket06.qaIODoxS/make-check.log`。已读取上述最新源码版本离线build/隔离安装smoke日志，wheel/sdist构建及smoke PASS，未重跑。合入后11文件主动LSP探测均 inconclusive，不能称为clean。
