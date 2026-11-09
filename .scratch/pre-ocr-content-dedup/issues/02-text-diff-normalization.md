# 02: 文本变化解释与有限规范化

Type: task
Blocked by: 01
Status: resolved

**What to build:** 调用者能够通过 CLI 获得文本变化的有界 Myers 差异解释，并选择有名称、版本及适用契约的换行符规范化。细小金融变化与 Markdown 语义必须保留。

- [x] 默认规范化为恒等变换；显式启用换行符统一时记录规则名称、版本和适用契约，区分字节相等与规范化后相等。
- [x] 使用 Myers 算法生成行级插入删除差异，对预算内的变化区域提供字符级说明；不以相似度阈值或渲染相似进入跳过分支，不新增持久化哈希索引。
- [x] 不任意剥离工具元数据，不清洗正文；不默认删除空行、行尾空格、HTML、链接、图片、标点、大小写或 Unicode 差异，不排序段落或表格。
- [x] 比较预算及证据上限显式可配置；比较预算耗尽返回 UNKNOWN，不截断后判同；报告证据的有界展示与比较是否完整分开表达。
- [x] 正文证据只出现在调用者显式选择的报告中，包含可定位的前后变化，日志不泄露正文。
- [x] CLI 黑盒覆盖 0.1%→0.2%、100k→300k、允许→不允许、≤→<、规则删除、表格单元格变化，均不得跳过。
- [x] CLI 黑盒覆盖行末两个空格、代码缩进、删除线、HTML 表格、链接目标及未知扩展语法变化；补充少量规范化和 Myers 边界纯数据测试。

## Answer

Implemented exact-first comparison, opt-in LF normalization with rule version and
contract, bounded Myers line/character explanation, explicit comparison/evidence
limits, and CLI argument validation. Source-order snippets preserve Markdown and
financial details; unsupported UTF-8 and exhausted comparison work remain UNKNOWN.
No body is emitted to normal logs and no hash index or similarity filtering exists.

TDD evidence: the first amount explanation test failed on missing
`comparison_complete`, then passed with actual line/character Myers edits. The
normalization test failed on unsupported CLI arguments, then passed with the
explicit rule. Work/evidence limit tests likewise failed on missing flags, then
passed after adding the bounded controls. Follow-up financial/Markdown and Myers
boundary cases all pass through the CLI seam.

Verification: 46 text CLI cases plus 16 foundation CLI cases passed (62 total).
Ruff check/format and cached Pyright against mdcompare plus the new tests passed
with zero type errors or warnings. Contract details are in the mdcompare contract
document. Full repository suite and real-file UAT remain final integration gates.
