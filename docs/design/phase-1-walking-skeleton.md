# Phase 1 设计 —— Walking Skeleton

> 📄 **阶段 · 已完成并冻结**（2026-09-19 验收 9/9）
> **覆盖**：Phase 1 的范围、步骤、验收标准与最终结果 ｜ **不覆盖**：结构细节（见 [`architecture.md`](architecture.md)）、施工过程（见[教程 01–10 章](../tutorial/README.md)）

## 0. 这份文档为什么单独存在

Phase 1 的设计原本是 `architecture.md` 的 §11。抽出来的理由是**生命周期不同**：

- `architecture.md` 是**常青**文档 —— 它永远描述「现在是什么样」
- 而 Phase 1 的设计描述的是**一个已经结束的阶段** —— 它不该再变

两者放在一起，结果就是常青文档里长期躺着一段历史，
而读者无法判断哪些还作数。（实测：`architecture.md` 顶部
长期写着「设计中，未开工」，那时 Phase 2 都做完两步了。）


> 小飞的原话：「先安装好新版 openclaw，实现最简单的功能验证」。
> 本节把「最简单」定义清楚，避免范围漂移。

## 2. 目标：一条最细的、端到端能走通的线

**只建 2 个 agent**（`main` + `emotion`），不是 8 个。

理由：Phase 1 要验证的是**机制**（跨 agent spawn 能不能通、契约能不能落、能不能回放），
不是**覆盖面**。机制通了，其余 6 个是复制。
而且一上来建 8 个空 agent = 6 个零消费方组件，正是 L-1 要防的。

选 `emotion` 做第一个 specialist：它最确定性（阈值+计数），最容易判断「答得对不对」。

## 3. 步骤

| # | 动作 | 产物 / 验证 |
|---|---|---|
| 1 | 装 node **v24.21.0**（增量；不改 `default`、不卸 v24.18.0） | `nvm ls` 见到它；生产 gateway pid 不变 |
| 2 | `npm i --prefix ~/.openclaw-biga/runtime openclaw@latest` —— 🔴 **不装进 nvm bin**（约束 D-1） | `runtime/node_modules/.bin/openclaw --version` ≥2026.9.5；且 `v24.21.0/bin/` 里**没有** openclaw |
| 3 | 写 `~/.openclaw-biga/bin/biga` wrapper（强制 `--profile biga`）并加执行位 | `biga --version` 可用 |
| 4 | 🔴 **生产侧**加一条守卫测试，断言其 PATH 解析 → `v24.18.0` | 绿（这是 D-1 的常驻守卫） |
| 5 | `biga setup`：端口 **19789**，**跳过飞书** | `~/.openclaw-biga/openclaw.json` 生成 |
| 6 | 在 `~/.openclaw-biga/workspace/` 建 git 仓库 + §2.4 骨架 + `.gitignore`（`data/`） | `git log` 有首个 commit |
| 7 | 写 `skills/_contract/`（Evidence / AgentVerdict / DecisionCard） | `tests/test_contract_single_impl.py` 绿 |
| 8 | 写 `skills/_store/db.py` + 三张表（`decision_records` / `agent_runs` / `raw_market_snapshot`） | `tests/test_no_raw_sqlite.py` 绿 |
| 9 | 写 `skills/emotion-calc/` —— 真采一次 A 股情绪数据，输出 `AgentVerdict` | 命令行跑出带 `as_of` 的 JSON |
| 10 | 建 agent `emotion`（`--workspace ~/.openclaw-biga/workspace/agents/emotion`）+ 写它的 AGENTS.md | `biga agents list` 见到它 |
| 11 | 配 `main`(Supervisor)：AGENTS.md / SOUL.md / IDENTITY.md 落在**仓库根** + `allowAgents:["emotion"]` + `agentToAgent.allow` | |
| 12 | 跑通：`biga agent --agent main -m "今天市场情绪怎么样？"` | 见 §4 |
| 13 | 实现 `replay <decision_id>`（与在线路径共用同一份合成代码） | 同一 verdicts 重跑出一致结论 |
| 14 | 隔离演练：`kill -9` BigA gateway 进程 | 生产 gateway pid 不变（不变式 I-2） |

⚠️ 第 1 步只装 **node**；openclaw 在第 2 步用 `--prefix` 装到 profile 目录内。
把这两步合成「在新 node 下 `npm i -g openclaw`」就会直接踩中 D-1。

## 4. Phase 1 验收（全部满足才算过）

1. Supervisor 收到问题后**确实 spawn 了 `emotion`**（`agent_runs` 有该行，不是自己编的）
2. `emotion` 返回的是**合法 `AgentVerdict`**，含 ≥1 条带 `as_of` 的 `Evidence`
3. 输出一张 **Decision Card**，含状态 + 证据 + 缺失项三段
4. `decision_records` 落库 1 行，`biga replay` 能重跑出一致结论
5. 故意把情绪数据源打断 → Card 显示 `UNKNOWN` + `missing` 非空，**不是 PASS**（L-2）
6. 生产侧：gateway pid、openclaw 版本、`default` alias、PATH、18789 监听 —— 五项全部未变
6b. 🔴 生产侧的 PATH 解析仍指向 **v24.18.0**（约束 D-1 未被破坏）
7. 单次端到端 **< 90s**（热缓存，2 个 agent）—— 判据是 `latency_report.py` 的
   **等卡墙钟**，不是 Card 上 Supervisor 自报的 `elapsed_ms`。
   90s = Stage 0/1/3 预估上界之和（10+40+30 = 80s）+ 10s 盘中网络余量。
   ⚠️ 八 Agent 的 105s 出自同一套「下界相加」的算术，且 Stage 3 在**只有一个**
   specialist 时就已经压着 30s 上沿 —— 那个数要等 Phase 2 有数据了重新推，
   **现在不改，也不要拿它当承诺**。

## 5. Phase 1 明确不做

装飞书 / 建任何 cron / 接任何下单路径 / 建其余 6 个 agent /
PostgreSQL / Redis / 回测 / 历史数据回补 / Web UI。

---

## 6. 实际结果（2026-09-19 冻结）

九项验收 **`PASS 9 · FAIL 0 · PENDING 0`**，决策 `BIGA-20260919-010`：

```bash
python3 tools/verify/phase1_acceptance.py \
        --decision-id BIGA-20260919-010 --baseline data/neighbour-baseline.json --live
```

⚠️ 脚本把「没测」记为 `PENDING` 而不是 `PASS` —— 不带 `--decision-id`
跑只会得到 `PASS 2 · PENDING 7`。**这是故意的**：R-3 对验收脚本自己同样适用。

| 项 | 结果 |
|---|---|
| 跨 agent spawn | ✅ 两份独立记录都核过：`agent_runs` + 运行时 `subagent_runs` |
| Evidence | 13 条，全部带 `as_of` |
| 缺失项 | 3 条上卡 |
| 回放 | `replay --check` 结论逐字段一致 |
| R-3 三路径 | PASS / WARNING / UNKNOWN 均可复现，无一行「PASS + 有缺失」 |
| 邻居未受影响 | pid / 启动时间 / config / `nvm default` / 18789 五项逐位一致 |
| I-1 | `/proc/<pid>/fd` 逐个核，无 BigA 进程持有邻居目录句柄 |
| 端到端 | **74.8s / $0.2179**（预算 90s） |

### 🔴 两个必须一起记住的数字

`74.8s` 这个结果是**修掉三个 bug 之后**的。修之前是 **216.3s**，
而那三个 bug **没有一个会让流程失败** —— 它们只是让 Agent 不停自救：

| bug | 表现 |
|---|---|
| `synthesize.py` 写死 `new_task_id(1)` | 当天第二次决策必撞主键，Supervisor 每次自己查库改号重试**并最终成功** |
| emotion 契约用了仓库根相对路径 | 它的 cwd 是 `agents/emotion/`，找不到脚本就去 `find /` |
| emotion 契约自相矛盾 | 同时要求「JSON 原样透传」和「局限写进 missing」，连续被契约层拒两次 |

> **只看「有没有出卡」，这三个永远发现不了。**

### 延迟预算从 60s 改成 90s —— 改了指标就要说清楚

原来的 60s **不是预算**，是 §10.1 四个阶段预估「**下界之和**」（5+20+15+20）。
把一个区间的最好情况当及格线，等于要求四个阶段同时命中最优。

实测按同样的阶段拆开，**每个阶段都落在自己的预估区间内**（8.0 / 36.0 / 30.0s），
合计仍然 75s。**没有哪个阶段超支，超的是那个加法。**

推导见 `architecture.md` §10.1 与[教程第 10 章](../tutorial/10-latency-and-cost.md)。

---

## 7. Phase 1 留给后面的账

| 项 | 去向 |
|---|---|
| 共享的 anthropic 凭据（裁定 11） | Phase 3 前必须换掉 —— token 轮换时两套系统一起停 |
| 起 gateway 自动创建的 4 条 cron | 已查实是上游托管任务，Phase 3 建自己的调度域时统一处理 |
| 八 Agent 的 105s 预算 | 出自同一套「下界相加」的算术，**Phase 2 用实测重推** |
| 盘中 `as_of > retrieved_at` 会崩 | 🔴 Phase 1 九项全过却没发现 —— 验收没覆盖「什么时候跑」这一维。Phase 2 修复（教程第 11 章坑 4） |
