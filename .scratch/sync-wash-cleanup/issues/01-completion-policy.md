# 什么情况下清理本次 sync，清理失败如何报告

Type: grilling
Labels: wayfinder:grilling
Status: resolved
Assignee: codex
Parent: ../map.md
Blocked by:

## Question

本次 sync 只有完全成功才自动清理，还是部分成功也清理并保留失败证据？
清理失败是否保持同步原退出码，仅向 stderr 报告？

现有事实：退出码 0 也包括停在计划处；必须与真正 completed 区分。
只清理本次 run 的范围已由用户确认，不在本议题重新询问。

## Comments

- 首轮建议：仅真正完整完成时清理；partial、failed、中断和计划暂停保留。
  清理错误写 stderr，保持业务同步的退出码。已询问，等待用户回答。
- 用户已确认：清理失败保持同步原退出码，仅 stderr 报告。
- 用户要求解释 partial 的全部含义，并明确不要因过细保护失去日常清理价值。
  因而首轮“只要 partial 就整批保留”的建议不作为已定规则。

## Answer

- 只清理本次 run，不自动扫描历史 run。
- 真正完成的成功或 partial 批次均纳入清理；不以“有失败记录”为由整批保留。
  计划暂停不是完成，不能仅凭退出码 0 判断。
- 用户明确不需要恢复原 partial run；失败交由后续 agent 判断，以新 run 处理。
  这是交接约定，不代表改变现有同版本失败抑制规则、自动重试或实现新的重试入口。
- 清理失败保持原同步退出码，向 stderr 报告。
- 批次需要留下的最小失败材料及后续复用输入归属，由
  [清理哪些 wash 产物，保留哪些复用与观察能力](02-artifact-boundary.md) 决定。
