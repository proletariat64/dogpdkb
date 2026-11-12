# Skylark 独立 listing/export CLI

Status: resolved
Type: task
Blocked by: none
Spec: ../spec.md

## Scope

在独立 pi_skylark 项目的隔离 worktree 增加公开 listing/export CLI 与机器产物。工具侧拥有 MCP、完整分页、普通正文与表格导出、输出格式/图片引用处理；不把这些搬入 dogpdkb。保留两项目原checkout未提交修改。

## Acceptance

CLI 黑盒TDD + 假MCP/真实Markdown；完整listing失败不宣告完整；导出单项错误不写假正文；记录版本与成功/失败。无需SSO/真实gateway。交付upstream commit/patch、可复现构建和配置调用方式，不伪称已全局安装。

## Comments

调查发现本机 pi_skylark 是工具源码但只提供 extension，需先补独立CLI供cron调用。

Implemented upstream commit `0e7c21883481d04a42a771c6fad1ddd5e6e0aa91` in isolated
`/tmp/pi-skylark-orchestrator-cli`. `npm test` passes eight existing gateway
scenarios plus the CLI contract scenarios; `npm run build` and diff-check pass.
Contract and reproducible patch are in `docs/orchestrator-skylark-contract.md`.
No global installation or production calls were performed.

Supplement: reproduced null/false count wrongly becoming a complete empty listing.
Upstream now requires a real nonnegative integer total; null, boolean and string
count subprocess regressions pass with no successful output. `npm test` and build
pass at the updated upstream commit; complete patch and identity are refreshed.

## Answer

独立 Skylark 提交 `0e7c218` 提供完整 listing、普通正文与表格 export，工具内分页/渲染/资源引用处理；8 个网关场景、CLI 合同及非法总数回归通过。补丁与构建说明见 [contract](../../../docs/orchestrator-skylark-contract.md)。原 checkout 保留，未全局安装或进行生产请求。
