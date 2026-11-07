# 06: 固定输入视图与故障报告

Type: task
Blocked by: 01
Status: resolved

**What to build:** 调用者面对读取失败、输入漂移和报告发布异常时，仍能分辨完整判定、逐项未知与全局失败，不会消费一份伪完整或误判可跳过的结果。

- [x] 明确调用者提供不可变输入快照的要求，比较使用固定输入视图；不得宣称文件元数据检查可抵御任意并发修改。
- [x] 文档、资源或依赖读取失败及比较期间检测到输入变化时，不产生可跳过结论；可建立身份的 incoming 仍恰好保留一个结果和有界诊断。
- [x] 逐项失败不静默丢文档，其他文档继续完成；分类计数守恒，稳定排序，不确定时间等运行信息不影响动作。
- [x] 全局清单错误、无法建立身份或无法完整发布报告返回 1；完成报告但含逐项错误或未知返回 2，不能被调用者解释为无变化。
- [x] 故障注入验证原子发布、已有报告保护和临时输出处理；异常不得留下貌似完整的新最终报告，私有权限和日志边界在失败路径也成立。
- [x] 使用 CLI 黑盒及外部可控故障验证读取错误、输入变化、部分失败、发布失败和重复运行一致性，不以内部函数调用次数代替行为证明。
- [x] 作为独立切片以无资源文档证明核心行为；后续资源、OCR、历史路径复用同一安全约束，在最终集成验收核对。

## Answer

Implemented bounded stable reads, per-document byte views and late drift checks;
manifest drift fails globally because pairing becomes untrustworthy. Input
failures retain identifiable results and preserve already determined dimensions.
Declared unread evidence and symlink/hardlink aliases are protected against report
overwrite. All reports stage before publication with caught-error rollback,
private permissions and generic diagnostics. Human reports include reason codes
and optional bounded text evidence.

Verification: 27 CLI tests passed (16 foundation plus 11 stability cases).
Red-to-green cycles demonstrated partial publication on a missing JSON parent,
unread-resource/hardlink alias overwrite, mutation during a read, late mutation
of an earlier document, manifest drift and per-file budget handling. Additional
OS-boundary failure injection verified replacement rollback, prior-report
preservation, cleanup and privacy. Deterministic mixed success/error reports and
nonregular inputs were checked through CLI subprocesses. Ruff check/format,
Pyright (explicit cached typeshed: zero errors/warnings), compileall and doc-guard
passed. Cache warnings about this worker's absent venv did not affect checking;
the existing integration Python environment was reused.

Limit: metadata checks detect ordinary observed changes, not arbitrary concurrent
rewrites; callers still provide immutable snapshots. Multi-path publication is
not crash-proof across separate filesystem replacements; JSON plus its process
exit code remains the automation contract. See the input-safety documentation.
