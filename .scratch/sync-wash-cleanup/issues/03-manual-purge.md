# 手动 purge 的彻底清理范围和 force 含义

Type: grilling
Labels: wayfinder:grilling
Status: resolved
Assignee: codex
Parent: ../map.md
Blocked by: 01

## Question

用户提出增加 purge 或 force 以允许彻底清除。
彻底清除是否指删除所选 run 的全部 wash，包括运行记录与失败材料？
是否还需要按所选 book 明确选择全部历史 wash？
force 是否仅允许丢弃未完成 run，而不改变目标范围、越过写锁或清除持久版本事实？

## Comments

- 建议独立 purge 命令表达删除动作；--force 仅表达丢弃未完成 run 的意图。
- 建议显式指定 run 或 --all，并限定到选中的 book；不触碰正式语料、图谱、
  持久 state 或工具缓存，不终止活跃 sync，不将 purge 等同于解除失败抑制。
- 以上为待确认建议，尚无破坏性操作授权，本轮不会执行删除。
- 用户已明确确认：彻底清除仅限 wash；正式语料、图谱、持久状态和工具缓存均不在范围内。
  这是功能范围决定，不是本轮执行真实 purge 的指令。

## Answer

用户已确认 purge 仅限 wash。按此前建议收敛其余 CLI 细节，作为实现默认合同：

- 独立 `purge --book BOOK`，必须显式选择 `--run RUN` 或 `--all`，二者互斥。
  `--all` 仅指所选 book 的 wash，不跨 book 扫描。
- 删除选定 run 的整个 wash，包括运行记录、失败 Markdown、图片和报告。
- completed/partial 可以直接 purge；暂停、失败或中断的 run 需要 `--force`。
  批量选择含这些 run 时，未给 force 则报错并不执行本次删除，避免悄悄跳过。
- force 只允许丢弃未完成 run，不扩大路径范围、不终止进程、不越过写锁。
  purge 与 sync 使用同一 book 写锁；锁被占用时拒绝执行。
- 不删除正式语料、图谱、持久 state、必要持久比较输入或工具缓存；
  不解除失败抑制，也不触发工具执行、历史回填或自动重试。
- 只接受所选 staging/book 下的合法 run；路径和符号链接不能使删除越出 wash。
- 手动 purge 的删除错误返回非零；自动清理失败仍保持原 sync 退出码。

本次仅确定合同，没有运行 purge 或删除真实数据。
