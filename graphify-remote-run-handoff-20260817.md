# Graphify 全量知识编译 — 远程长跑 Runbook (us.hermes)

Date: 2026-08-17 (Asia/Shanghai)
前置阅读: `graphify-uat-handoff-20260816.md` (基线: 语料管线/脚本/技术发现) + `/tmp/graphify-handoff-20260817.md` (delta: hollow 根因+修复, 500K 结论)。本文不重复推导,只给**怎么跑**和**怎么验收**。

## 0. 准备状态(已完成,勿重做)

| 项 | 位置/值 |
| --- | --- |
| 远程主机 | `us.hermes` = 43.130.49.185, user `agentuser`, 密钥 `~/.ssh/id_rsa` |
| 语料 | `~/pdkb/graphify_flatten_ready/` — **4582 md + path_map.tsv**, 99M(已校验 4583 行 map) |
| graphify | `~/.local/bin/graphify` **0.9.45**(与本地同版) |
| LLM 配置 | `~/.graphify/providers.json` → omniroute → `https://api.deepseek.com`, model `deepseek-v4-flash`, **`reasoning_effort:"low"`**(hollow 修复,md5 与本地一致 `44824d4d…`) |
| 密钥/env | `~/.config/graphify/omniroute.env`(600)= `DEEPSEEK_API_KEY`(md5 `61529dca…`, 与本地同) + `GRAPHIFY_MAX_OUTPUT_TOKENS=384000`;`~/.bashrc` 首行 source 之 |
| 监控 | `~/pdkb/scripts/graphify-watchdog-uat.py`;psutil 5.9.8 + py-spy 0.4.2 已装,`sudo -n` 可用 |
| 终端 | herdr 0.8.0, session `default` running;dev-layout skill 在 `~/.agents/skills/dev-layout/`;pi 0.84.2 |
| 加密备份 | 本地 `~/docs/pdkb/graphify_flatten_ready.tar.gz.gpg`(AES-256, 密码只在本地会话,**不在任何文件里**) |

**锁定配置(勿改,改任何一项前重读 delta handoff 的实验结论):**
`--backend omniroute --token-budget 50000 --max-concurrency 2`, thinking ON + effort=low。
规模: 4582 文件 ≈ 33.9M input tokens ≈ **677 chunks** ≈ 16M output tokens。
预估: **8–15 h**,成本 **~$18(off-peak)**。

## 1. 启动步骤

```bash
# 本地执行:attach 远程 herdr(protocol 19 两端兼容)
herdr --remote us.hermes
```

在 herdr 里(本地断线不影响远程进程):

1. **建布局** — 在一个 pane 里 `cd ~/pdkb && pi`,让 pi 跑 dev-layout skill 建 `todo`+`uat` pane(`HERDR_ENV=1` 才有用;裸 ssh shell 里 skill 会拒绝)。也可手动 `herdr pane` 建两个空 shell pane。
2. **跑抽取**(`uat` 或任一空闲 shell pane):

```bash
cd ~/pdkb && PYTHONUNBUFFERED=1 GRAPHIFY_MAX_OUTPUT_TOKENS=384000 \
  ~/.local/bin/graphify extract graphify_flatten_ready/ \
  --backend omniroute --token-budget 50000 --max-concurrency 2 2>&1 | tee /tmp/full.log
```

1. **跑监控**(另一个空闲 shell pane):

```bash
python3 ~/pdkb/scripts/graphify-watchdog-uat.py \
  --target ~/pdkb/graphify_flatten_ready --log /tmp/full.log --conc 2 --interval 4
```

**时机:** deepseek peak(价双倍)= 北京时间 09:00–12:00、14:00–18:00。**18:00 后或 12:00–14:00 启动最省**。8–15h 长跑必然跨峰,但主体落在 off-peak 即可。

## 2. 监控看点(watchdog panel)

- **Quality 行**: `trunc / hollow / invalid / failed` — 健康 = **hollow 0, trunc ≈0**。hollow>0 ⇒ `--backend omniroute` 没生效(effort 没下去),停跑排查。
- **Pipelines (py-spy)**: W1/W2 各自当前 chunk idx + 起始时间;某 worker 单 chunk 卡 >20 min 记一笔。
- **Knowledge Compiled**: nodes/edges/src-files 累计 + output MB + est tokens。
- **ETA**: 677 chunks 总量,conc 2。

## 3. 中断与恢复

- **本地断线/关终端**: 无影响,全部进程在远程 herdr pane 里。重连 `herdr --remote us.hermes`。
- **主动停**: pane 里 `ctrl+c`;若卡在 LLM 调用无响应,`kill -TERM <pid>`。**不要** `herdr server stop`。
- **断点续跑**: 已完成 chunk 的缓存落在 `graphify_flatten_ready/graphify-out/cache/semantic/`。原命令重跑即复用缓存,只补未完成的 chunk。
- **彻底重跑**(仅当配置变更): 先 `rm -rf ~/pdkb/graphify_flatten_ready/graphify-out` 再跑。

## 4. 验收标准(全部通过才算成)

| # | 检查 | 命令/位置 | 通过线 |
| --- | --- | --- | --- |
| 1 | 终产物存在 | `~/pdkb/graphify_flatten_ready/graphify-out/graph.json` | 文件存在且非空(此前 UAT 两次都被 Ctrl+C,只有 cache,没有它) |
| 2 | 响应质量 | log 里 quality 计数 | **hollow 0**,trunc ≈ 0,invalid 0(对照: smoke 8/8 clean) |
| 3 | 文件覆盖 | watchdog src-files / graph.json | **4582/4582**(对照: 500K 测试漏 23/34 = 失败模式) |
| 4 | 抽取密度 | output tokens ÷ input tokens | **≈10–20%**;<5% = 欠抽(500K 测试 1.4% 即此失败) |
| 5 | 跨文档边 | graph.json edges 抽样 | 存在跨文件边;A/B 基准 ≈ 4–5 条/文件 |
| 6 | 图可查 | 抽 3 个已知产品/主题名查询 | 能返回合理节点+关联边 |
| 7 | 成本 | deepseek 控制台 | ≈ $18 ±50%(off-peak 为主) |

验收快照命令(跑完后在远程执行,结果贴回):

```bash
cd ~/pdkb/graphify_flatten_ready && ls -la graphify-out/graph.json && \
python3 -c "
import json; g=json.load(open('graphify-out/graph.json'))
n=g.get('nodes',[]); e=g.get('edges',[])
print(f'nodes={len(n)} edges={len(e)}')"
grep -cE "hollow" /tmp/full.log   # 应为 0(或无匹配)
```

## 5. 失败模式 → 对策

| 症状 | 根因(已验证) | 对策 |
| --- | --- | --- |
| hollow > 0 | 没走 omniroute,effort 没降到 low | 确认命令带 `--backend omniroute`;查 providers.json |
| src-files 远小于 4582 / output 比 <5% | chunk 太大欠抽 | token-budget 维持 50K(sweet spot 50–100K),勿加大 |
| trunc 飙高 | output cap 没生效 | 确认 `GRAPHIFY_MAX_OUTPUT_TOKENS=384000` 在 env(`bash -lc 'echo $GRAPHIFY_MAX_OUTPUT_TOKENS'`) |
| 并发报错/限流 | conc >2 撞 deepseek 吞吐 | 保持 `--max-concurrency 2` |
| 其他 | — | 用 `diagnosing-bugs` skill;基线 handoff §技术发现 |

## 6. 红线

- `DEEPSEEK_API_KEY` / tarball 密码**永不**写进任何文档、log、handoff。
- 语料只动 `~/pdkb/graphify_flatten_ready/`,不要碰其它目录。
- 本 runbook 对应 wayfinder ticket #12;启动命令即视为用户已授权(用户说跑才跑)。
