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

实现分支：`fix/daily-knowledge-sync-review`，确认集成tip `5ffc866` 为起点祖先。没有reset/clean、真实模型、网络、OCR或生产操作。

### 逐项red/green

- STD-1/SPEC-10：3个新增CLI用例初始全失败，分别复现baseline页被改写、snapshot成为raw、失败页0755/0644；修复后3 passed；另补既有page硬链接反例，完整capture 18 passed。统一预检所有输入和派生输出，页/checkpoint私有写入。
- STD-2：跨book同ID用例初始返回0并发布；复用严格state loader后提取前拒绝。
- SPEC-3/4/5/9：4个用例初始分别得到15字符旧证据配不同冻结正文、旧raw变化仍发布、旧plan进入Graphify、成功重试仍HOLD；修复后全部通过，原始四脚本重放亦符合预期。
- SPEC-1：两旧规则初始只有一个原因码且plan为1.0；2.0逐项issue/disposition后，缺一项拒绝、两项明确删除可执行，preserve必须绑定并通过实际质量case，旧泛ack拒绝。sync+execute聚焦35 passed。
- SPEC-6/7：无效fresh edge和无关来源edge碰撞两个用例初始均返回0；前置提取/缓存校验及冲突拒绝后通过，另验证cached hyperedge和指向被替换旧节点的fresh引用。graph_sync/update_graph 25 passed。
- SPEC-8：真实硬退出后resume初始返回1；journal恢复后返回0、报告重建且提取仍1次。普通state OSError恢复同时通过，完整execute 24 passed。
- SPEC-2：缺token反例初始返回两个0和graphify_result；现为null/unknown。缺call证据同样unknown，正常实测值及无提取0保留；相关focused 4 passed。

### 合同和UAT

- plan/approval/quality升级为2.0；CLI指南、安装smoke及测试fixture已更新。新合法审批正例先执行成功，再验证恶意场景，未以旧schema拒绝冒充修复。
- 新冻结UAT：`/tmp/daily-knowledge-sync-ticket06-uat-v2`。冻结输入与票05逐字节一致；采集7548→7550、2 new/4 updated、6导出，详情异常按原合同保持；正反例执行通过，2批准、4质量案例、2图谱成功，负例图/state不变。确定性替身未做真实提取；合成token不是账单。
- 首次v1复跑因测试脚本让正负例共享负例缓存而未通过，未计为产品缺陷或通过；v2隔离缓存根后完成。历史实物未修改。
- 审查历史及逐项定位：`docs/daily-knowledge-sync-review.md`。共享进度：`/tmp/daily-knowledge-sync-fix-progress.md`。

最终`make check`：**928 passed**，doc-guard 65篇，Ruff及171文件格式检查通过，日志`/tmp/daily-knowledge-sync-ticket06-make-check-final.log`。离线build和隔离安装smoke通过，日志`/tmp/daily-knowledge-sync-ticket06-make-smoke-final.log`。最终10文件主动LSP为7 clean、3个warning文件、0 error；warning均为0700规则误报或既有有界数字转换，未宣称全clean。集成tip未前进且仍为当前分支祖先；实施分支提交后由集成merger合入。
