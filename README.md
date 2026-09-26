<div align="center">

<img src="images/LOGO_BigA01.png" alt="BigA" width="460">

### 基于 OpenClaw 的可追溯 Multi-Agent A 股决策内核

[![Status](https://img.shields.io/badge/status-phase%202%20code%20closed%20%C2%B7%20baseline%20v1--2-2ea043)](#当前边界)
[![Runtime](https://img.shields.io/badge/runtime-OpenClaw-1f6feb)](https://docs.openclaw.ai)
[![Tests](https://img.shields.io/badge/tests-2028%20selected-555)](#当前实现状态)
[![Store](https://img.shields.io/badge/store-SQLite%20WAL%20%C2%B7%20schema%20v27-555)](docs/tutorial/04-store-layer.md)
[![License](https://img.shields.io/badge/license-Apache--2.0-2ea043)](LICENSE)
[![Trading](https://img.shields.io/badge/live%20trading-disabled-555)](#当前边界)

**发现共识，锁定核心，让每一笔判断都有证据。**

</div>

---

## BigA 是什么

BigA 是一个建立在 [OpenClaw](https://docs.openclaw.ai) 之上的个人 A 股 Multi-Agent 系统。

当前仓库首先建设的是一套可靠的 **Decision Kernel**：

```text
Trigger
  → Decision
  → Run
  → Frozen Evidence
  → Specialist Agents
  → Risk
  → DecisionCard
  → Replay / Verification / Notification
```

它的产物不是自动交易指令，而是一张：

- 有来源；
- 有时间；
- 有缺失项；
- 有运行身份；
- 可追溯；
- 可回放；
- 可以核验 Agent 是否真实运行；

的 `DecisionCard`。

BigA 的长期目标，是在这个内核周围继续建设完整的个人交易系统：

```text
Data
  → Features
  → Screening
  → Trading Plan
  → Realtime Tracking
  → AI Decision
  → Risk
  → Execution
  → Portfolio
  → Review / Backtest
```

当前多 Agent 闭环是 BigA 的第一块完整 Vertical Slice，不是项目终点。

> ⚠️ 当前阶段不接券商账户、不自动下单，也不构成投资建议。最终决定由人做。

---

## BigA 的架构主张

BigA 不是把多个 Agent 名称写进 Prompt，然后让一个 Supervisor 自由决定工作流的股票分析 Demo。

它探索的是一种目前仍较少见的 AI 交易系统架构：

> **OpenClaw 负责智能运行，BigA 的确定性业务层负责流程、冻结证据、风险、身份血缘、回放，以及未来完整的交易生命周期。**

```text
OpenClaw Runtime
        ↓
Deterministic Orchestrator
        ↓
Decision + Run
        ↓
Frozen EvidenceSet
        ↓
Specialist Agents
        ↓
Fact / Assessment
        ↓
Risk Veto
        ↓
DecisionCard
        ↓
Replay / Verification / Review
```

BigA 不声称每一项技术都是原创，也不声称自己是世界上唯一的相关项目。

它的辨识度来自于把下面这些原则同时应用到一个个人 A 股系统中：

- 程序拥有工作流，Agent 只拥有判断；
- 确定性数字由代码计算，LLM 负责解释；
- 一个 Run 只能使用自己的冻结数据世界；
- Runtime Proof 不能由 Agent 自报；
- `UNKNOWN`、`MISSING`、`TIMEOUT` 是正式业务状态；
- Replay 不重新访问外部数据源；
- 历史判断不能被后来结果覆盖；
- Risk 拥有独立否决权；
- DecisionCard 不等于订单；
- Agent 不能直接下单、修改业务状态或重新启动 Pipeline。

因此，BigA 的目标不是成为另一个"AI 股票分析器"，而是逐步演进成：

> **一个以 OpenClaw 为智能底座、以可信数据和确定性控制面为核心的个人 A 股交易操作系统。**

---

## 当前不是在做什么

当前 BigA 不是：

- 自动下单机器人；
- 已经完成的全功能量化交易平台；
- 让 LLM 自己计算技术指标的数值引擎；
- 每个 Agent 自己联网、自己选数据源的松散脚本集合；
- 由 Prompt 控制全部状态、流程和失败恢复的工作流；
- 用一条 `BUY / SELL / HOLD` 文本代替证据、风险和运行记录的分析器；
- 对收益做任何保证的投资建议服务。

当前阶段首先把 Multi-Agent Decision Kernel 做成一套：

```text
可靠
可证明
可回放
可扩展
失败时能够明确说"不知道"
```

的基础架构。

---

## 为什么这个项目值得重新做

BigA 重点验证和固化的，不是"能不能让几个 Agent 说话"，而是更难的部分：

1. **程序控制流程，Agent 负责判断。**
2. **不知道就是 `UNKNOWN`，不能静默变成 `PASS`。**
3. **同一次运行的事实、判断、风险和 Card 必须属于同一条身份链。**
4. **OpenClaw Runtime 的真实记录，而不是 Agent 自报，才算 spawn 证据。**
5. **在线与回放共用同一套组装逻辑。**
6. **同机另一套 OpenClaw 实例必须保持隔离。**
7. **成本、超时、递归、外部 kill 和通知失败必须有机器级守卫。**
8. **历史产物只能追加或修订，不能把后来知道的结果改写成"当时就知道"。**

开发过程中真实发生过的事故、错误假设、对抗测试和修复过程，保留在
[`docs/tutorial/`](docs/tutorial/README.md) 中，目前共 60 章。

---

## 当前架构

```text
                    Feishu / CLI / Cron
                              │
                              ▼
                 Entry Guard / Budget / Lock
                              │
                              ▼
                  DecisionOrchestrator
                 （确定性程序控制流程）
                              │
                 ┌────────────┴────────────┐
                 ▼                         ▼
        SnapshotCoordinator          OpenClaw Runtime
        冻结共享数据切片                   │
                 │             ┌───────────┼───────────┐
                 │             ▼           ▼           ▼
                 └────────► Market      Sector       News
                               │           │           │
                               ├──── Technical ────────┤
                               └──── Emotion ──────────┘
                                           │
                                  Stage 1 Facts /
                                  Agent Assessments
                                           │
                                           ▼
                                   Risk（制衡层）
                                           │
                                           ▼
                               Synthesizer（综合判官）
                                           │
                                           ▼
                                     DecisionCard
                                      │          │
                                      ▼          ▼
                                  SQLite       Notification
                                  Replay       Feishu
```

---

## OpenClaw 与 BigA 的边界

### OpenClaw 负责

```text
Agent Session
模型调用
Tool Policy
Subagent
Runtime Run ID
Agent Timeout
Agent Lifecycle
```

### BigA 负责

```text
Trigger
Decision / Run Identity
状态机
数据冻结
EvidenceSet
Risk
DecisionCard
Replay
预算
通知意图
```

一句话：

> **OpenClaw 是智能运行时；BigA 是业务系统。**

这也意味着：

```text
Agent 不拥有顶层流程
Agent 不保存飞书凭证
Agent 不直接写核心业务状态
Agent 不直接调用券商
```

---

## Pipeline 中有哪些 Agent

### 交互入口

| Agent | 职责 |
|---|---|
| `main` | 与人交互、解释结果、查询状态；**不负责出卡编排** |

### 自动出卡 Pipeline

| Stage | Agent | 职责 |
|---|---|---|
| Stage 1 | `market` | 指数、成交额、量能、市场宽度 |
| Stage 1 | `sector` | 板块强度、资金方向、主线结构 |
| Stage 1 | `news` | 新闻窗口与消息面判断 |
| Stage 1 | `technical` | 均线、MACD、RSI、区间位置 |
| Stage 1 | `emotion` | 涨停、炸板、连板与赚钱效应 |
| Stage 2 | `risk` | 读取冻结的 Stage 1 事实并行使否决权 |
| Stage 3 | `synthesizer` | 只做最终综合判断，不采集数据、不 spawn Agent |

`discipline` 已进入长期设计，但目前没有真实交易行为输入源，因此注册但不启动。

---

## 设计地基

### 1. `UNKNOWN` 不等于 `PASS`

```text
数据不足
→ UNKNOWN + missing[]

不是：
数据不足
→ 没发现问题
→ PASS
```

缺失项是正式业务数据，不是日志噪音。

### 2. 程序决定流程，Agent 决定观点

```text
什么时候开始
下一步是谁
超时多久
什么算完成
如何落库
```

由 `DecisionOrchestrator` 决定。

```text
事实意味着什么
市场处于什么状态
风险如何解释
```

由 Agent 判断。

### 3. 确定性计算不交给 LLM

数字和有唯一答案的计算归 Python：

```text
行情
指标
覆盖率
时间差
风险硬规则
```

Agent 只解释已经生成的 Facts / Evidence。

### 4. 一个 Run 只能使用自己的数据世界

目标身份链：

```text
Trigger
→ Decision
→ Run
→ EvidenceSet
→ OpenClaw RuntimeRun
→ Verdict
→ DecisionCard
```

这条链的 Run Provenance 已完整落地（批 N 起，到 v1 架构基线收口）：一张卡属于哪次执行、
基于哪份切片，现在是一句 SQL，不必解 `card_json`。

### 5. Runtime Proof 不能由业务代码自证

`agent_runs` 是 BigA 的执行账本，不等于 OpenClaw spawn 证明。

真正证明 Specialist 被执行的，是 OpenClaw Runtime 自己记录的运行数据。
`bin/biga-card` 在每次出卡后按 Run ID 精确核验，不允许跨 Run 借用 spawn 记录。

```text
DecisionCard
→ BigA Run
→ AgentRun (orchestration_run_id)
→ runtime_run_id
→ OpenClaw Runtime Record
```

### 6. 历史不可覆盖

```text
Raw Artifact
Snapshot
Verdict
DecisionCard
Replay
```

都采用追加或版本化思路。

修订产生新版本，不把历史改成"当时就知道"。

Replay 不重新采集数据，组装结果由守卫强制与原卡一致（业务结论不可变）。

### 7. Risk 拥有独立否决权

Agent 可以提出判断，但：

```text
身份不成立
数据过期
覆盖率不足
硬性风险规则触发
```

必须由确定性 Risk Policy fail closed。

### 8. 通知不等于业务成功

```text
DecisionCard 保存成功
Feishu 发送失败
```

应被记录为：

```text
Decision = COMPLETED
Notification = FAILED / RETRYING
```

通知失败不能篡改决策事实。

---

## 当前实现状态

| 能力 | 状态 |
|---|---|
| 隔离 OpenClaw Profile、独立 Runtime、独立 Gateway | ✅ |
| 确定性 `DecisionOrchestrator` | ✅ |
| Stage 1 五 Agent 并行 | ✅ |
| Risk 制衡层 | ✅ |
| Synthesizer 综合判官 | ✅ |
| Run 状态机与事件历史 | ✅ |
| Global Deadline | ✅ |
| TIMEOUT 与 FAILED 分离 | ✅ |
| 部分 Agent 启动失败清理 | ✅ |
| stale-run reaper | ✅ |
| Facts / Assessment 拆分 | ✅ |
| Fact / Evidence canonical value 一致 | ✅ |
| `index_daily` 冻结快照共享 Vertical Slice | ✅ |
| Strict JSON、写边界重校验、Amendment 约束 | ✅ |
| Replay 与组装一致性检查 | ✅ |
| Replay 业务结论不可变守卫 | ✅ |
| 飞书入站、出站、有限重试与 Outbox | ✅ |
| Apache-2.0 开源合规基础 | ✅ |
| Run → EvidenceSet → Verdict → Card 强身份闭环 | ✅ |
| 真实 Run / EvidenceSet 根节点写边界校验 | ✅ |
| Exact Runtime Proof（BigA run + agent + OpenClaw runtime 三元组） | ✅ 真实运行实测 6/6 `exact_run_agent_runtime_match` |
| Schema Migration 原子性 | ✅ |
| 通知 Worker 单实例防重复发送 | ✅ |
| Source ZIP / Clean Clone 默认测试全绿 | ✅ |
| 确定性编排升级 Baseline 冻结 | ✅ 2026-09-25 重新冻结于 `v1-architecture-baseline-2`，口径 = **代码与架构收口**（旧 `v1-architecture-baseline` 保留不动，它的解冻经过见下文）|
| 飞书启动失败重试策略 | 🔶 固定次数，无指数退避 |
| Phase 2 出口条件 4（缺失跨天累积） | ✅ **严格判据**实测跨 5 天 31 张卡（2026-09-25 生产库，`tools/verify/exit_conditions.py`；演练注入与编排层弱证据不计入）|
| Phase 2 出口条件 3（真实否决落库） | ⬜ 等一个命中 risk 阈值的交易日——不是开发问题 |
| Phase 2 出口条件 5（延迟预算） | ✅ 盘中 Decision SLO **180s**，唯一定义在 `latency_report.py::DEFAULT_BUDGET_MS`；盘后负载另立 Profile（裁定 17）|
| AgentRun 写边界闭合（三个显式入口） | ✅ 裸接口已私有 |
| 交易日历（`market_is_open` 认节假日） | ✅ 2026-09-25 起真的认了（`bin/biga-calendar`）|
| Dataset / Provider / Pipeline Registry | ⬜ 后续 |
| 选股、回测、实时交易、Web | ⬜ 长期路线 |

当前仓库有 **2028 条 Hermetic 测试选中，SQLite schema v27**，0 failed。

| 口径 | 数字 | 怎么复现 |
|---|---|---|
| Hermetic **选中** | 见徽章 | `pytest -m "not installed and not live and not git"` |
| 其中 **通过 / 跳过 / 失败** | 见下方 CI 输出 | 🔴 **「选中」不是「通过」** —— 见下 |
| 环境相关（需显式开关） | `installed` 2 · `git` 2 | `pytest --run-installed` / `--run-git` |
| Live Acceptance（`phase1_acceptance --run-id`） | **PASS 8 · FAIL 0 · PENDING 1** | `BIGA-20260925-002`；剩 1 项需 `--live` 断源 |

🔴 **「选中」≠「通过」。** 徽章上那个数是**选中**的条数；其中一部分是 skip
（禁网、需要真实环境）。把 selected 当成 passed 报，就是把「没跑」写成「跑过了」——
外部评审 N3 点名的正是这个。⇒ 徽章文案改成 `N selected`，具体 passed/skipped
以 CI 输出为准（`tools/verify/sync_test_count.sh` 同步的是选中数）。

🔴 **「全部通过」指的是 Hermetic 那一行，不包含 Live。** 这三个数以前混在一句话里，
外部评审因此数出「至少三套数字」—— 徽章停在一个早就不成立的旧值上（同步脚本的
正则按老徽章格式写，徽章改版后**从来没有匹配上过**，于是既不被同步也不被校验，
而失效的表现是「一直通过」）。现在徽章、正文、`CLAUDE.md` 由
`tools/verify/sync_test_count.sh` 同源生成，schema 版本由
`test_README徽章的schema版本与代码一致` 钉住。

### tag 现状

| tag | 指向 | 现在该怎么读它 |
|---|---|---|
| **`v0.5.0`** | 当前 `main` | **最新状态。**克隆下来对着它读，本页的数字描述的就是它 |
| `v0.3.6` · `v0.3.7` · `v0.4.0` | 前几次复核 | 过程快照 |
| `v1-architecture-baseline` | 一个更早的提交 | ⚠️ **已解冻，不再代表「可发布」。**保留是因为它是历史事实（当时确实 sign-off 过），不是因为它仍然成立 |
| `v0.3.1` … `v0.3.4` | 各自的发布点 | 过程快照 |

🔴 **`v1-architecture-baseline` 为什么解冻**：它打过，也确实经过独立 sign-off。
但 sign-off 之后的**又一轮**独立评审在正常路径上找到了阻塞项（在线溯源根节点未校验、
Spawn Proof 仍是 decision 级、测试基线不可复现）；随后那批修复**本身**再被复核时，
又发现十二处 —— 其中三处是「守卫写了、但没有任何生产代码调用它」，也就是说
**打勾的那几项当时并没有真的在守**（见 CHANGELOG `[0.3.6]`）。
第三轮评审对那批修复再复核，又找到三个写边界漏口与两个确定性测试失败
（见 CHANGELOG `[0.3.7]`）。⇒ 徽章从 ✅ 退回 🔶。

> 连续三轮都在**已经宣布修好**的东西上找到东西 —— 这本身就是「暂不冻结」
> 最好的理由。每一轮找到的都不是新写的代码，是上一轮那句「已到位」。

这一步是裁定 14 的直接应用：**徽章必须描述一个真被测过的状态，未达成就写 🔶，
不写 ✅。** 一个描述过期状态的 ✅ 比 🔶 更糟 —— 🔶 诚实，它不诚实。

⚠️ 这个 tag **没有被删除**，也不建议删：删掉等于抹掉「我们曾经以为它成立」这件事，
而那恰恰是这份记录里最有价值的部分。要重新冻结时**另打一个新 tag**，
不覆盖、不移动旧的 —— 与 `decision_records` 用 `replay_of` 追加而不是原地改，
是同一条纪律（L-8：当时看到的必须可重建）。

解冻本身也说明打 tag 的时机偏早：`v1` 该在 Live Acceptance 重新通过之后再打。

🔒 **Phase 2 代码收口已完成，出口条件 7/8。** 剩下的只有条件 3（一次真实 Risk Veto），
它**等的是行情、不是开发** —— 要盘面真的命中 risk 的阈值之一。
条件 4 与 5 在 2026-09-25 用收紧后的判据重新实测后达成，不是把线放宽换来的：
条件 4 的工具改严了（演练与弱证据不再计入）数字反而还在线上，条件 5 是把
早已重推完的 180s 从两个字面量收敛成一处定义。
详见 [`phase-2-specialists.md`](docs/design/phase-2-specialists.md) §4。

---

## 当前边界

### 当前适合

```text
离线开发
受控 Live Run
人工触发 DecisionCard
手工飞书验证
架构与 Contract 演进
```

### 当前暂不建议

```text
自动 Retry
大量 Cron / systemd 无人值守任务
高频飞书触发
长期连续生产运行
自动下单
将当前测试徽章视为发布质量证明
```

🔒 **2026-09-25：基础架构已按「代码收口」口径重新冻结于 `v1-architecture-baseline-2`。**

这个 tag 声称的是**代码与架构收口**，不是「全部验收通过」。它明确**不**覆盖两件事：

```text
⬜ Phase 2 条件 3   一次真实 Risk Veto —— 等行情命中 risk 阈值，不是等开发
🔶 Live Acceptance  2026-09-25 跑过一次：PASS 6 / FAIL 0 / PENDING 3
```

🔴 **把这两条写在 tag 旁边，是裁定 14 本身的要求。** 上一个
`v1-architecture-baseline` 出事不是因为打早了一天，是因为它**没说自己不包含什么** ——
读的人于是把它读成「全绿」。这次换一种做法：冻结的范围写死在 tag message 里，
剩下的两条留在这儿显眼地开着。

旧 tag 不删、不移动（L-8：当时看到的必须可重建）。

---

## 快速开始

BigA 的安装场景是：**在一台已经运行另一套 OpenClaw 的机器上，并排安装第二套隔离实例。**

请先阅读：

- [安装与隔离](docs/guide/install.md)
- [完整使用手册](docs/guide/usage.md)

进入工作区：

```bash
cd ~/.openclaw-biga/workspace
```

### 生成一张新卡

```bash
bin/biga-card
```

历史实测约需 3 分钟、约 `$1.2–1.4`；实际耗时和成本受模型、数据源和市场时段影响。

### 查看与验证

```bash
bin/biga-card --list
bin/biga-card --show <decision_id>
bin/biga-card --status <run_id>
bin/biga-card --check <decision_id>
```

`--check` 验证的是：

```text
冻结证据
→ 落库
→ 取回
→ 重新组装
```

是否无损；它不是重新独立推导一次投资结论。

### 手工投递待发通知

```bash
bin/biga-notify
```

只打印、不实际发送：

```bash
bin/biga-notify --deliverer stdout
```

### 回收超时或失联 Run

```bash
bin/biga-reap
```

默认行为和可写模式请以使用手册为准。

### 紧急停止生成新卡

```bash
printf 'maintenance\n' > .biga-card-stop
```

恢复前确认触发源已停止，然后：

```bash
rm .biga-card-stop
```

`--list`、`--show`、`--check` 等只读命令不受总闸影响。

---

## 怎么确认它没有"自说自话"

BigA 不把"程序没报错"当作可信证明。

| 要验证什么 | 命令 | 证明范围 |
|---|---|---|
| Card 组装可回放 | `bin/biga-card --check <decision_id>` | 冻结输入到 Card 的管线无损 |
| Agent 真的运行 | `python3 tools/verify/spawn_check.py --run-id <run_id>` | BigA 账本与 OpenClaw Runtime 双重对账（Run 级精确核验） |
| Stage 1 真的并行 | `python3 tools/verify/latency_report.py --parallel-check` | Agent 运行区间存在结构性重叠 |
| 同机实例未受影响 | `python3 tools/verify/isolation.py` | Profile、文件、端口与 systemd 命名空间隔离 |
| 存量数据可读 | `python3 tools/verify/readback_check.py` | 数据库中不存在"写入但无法读取"的毒行 |
| 今日预算与闸门 | `python3 tools/verify/budget_report.py` | 当前是否允许发起新的付费 Run |
| 配置基线未漂移 | `python3 tools/verify/config_baseline.py` | OpenClaw Version / Tool Policy / Agent Config |

验证工具使用三态语义：

```text
0 = PASS
1 = FAIL
2 = UNKNOWN / 当前证据不足
```

`UNKNOWN` 不是"基本通过"。

---

## 飞书

BigA 使用 OpenClaw 的飞书 Channel：

```text
OpenClaw
→ 管 appId / appSecret / Token / 消息运输

BigA
→ 管 Trigger / Decision / 消息内容
```

Agent 和 BigA 业务数据库都不保存 `appSecret`。

当前支持：

- 飞书发送 `/card` 命令触发 BigA；
- DecisionCard / Run Failure 进入通知路径；
- `bin/biga-notify` 主动发送待发消息；
- systemd timer 定期投递；
- Retryable / Permanent Error 分类；
- 有限次数重试与 abandoned 状态。

飞书属于当前架构的最小集成，不是多用户、多租户通知平台。

---

## 确定性编排升级（Baseline 冻结后已解冻）

Phase 2 跑通之后，出过好几次同一形状的事故：证据合成到错误的决策号上、
出卡递归成 187 个会话烧掉 \$8.99、飞书路径 4 spawn 缺一个 agent 却没有任何报错。
共同点是**工作流一直靠 Supervisor 读提示词临场决定**——提示词表达意图，
表达不了不变量，下一条缝总会从别处长出来。

⇒ 把"下一步跑什么、谁被调用、超时多久、什么算失败"这些**程序说了算**的东西，
从提示词里搬进一个显式状态机（`DecisionOrchestrator`）；"当前市场是什么状态、
证据意味着什么"这类**判断**，仍然是 Agent 的事。

🔴 每一批都经过**独立复核**——不是提出方自己宣布通过：读全部 diff、亲自
重跑每一道探针（含 sabotage-revert，故意弄坏一遍确认它真的会报红）、
跑真实数据验证，再决定合并。这条纪律在 Baseline 冻结本身上也没有例外——
真机触发飞书 `/card` 走完整闭环、Live Acceptance 逐条查库核实、
当场发现的真缺陷修复后由另一个会话复核签字确认。

完整推导、每一批做了什么、每次评审怎么复核，见
[`docs/design/deterministic-orchestration.md`](docs/design/deterministic-orchestration.md)。

---

## 项目结构

```text
.
├── agents/          OpenClaw Agent 的角色契约
├── src/easyup_biga/
│   ├── domain/      Evidence / Verdict / Card / Facts / Registry
│   ├── providers/   数据源适配与采集边界
│   ├── persistence/ SQLite、Run、Verdict、Outbox
│   ├── runtime/     OpenClaw Runtime Adapter
│   └── application/ SnapshotCoordinator 等应用服务
│
├── skills/
│   ├── _contract/ _sources/ _store/ _runtime/ _snapshot/
│   │               旧路径兼容薄壳
│   └── *-calc/      确定性业务技能
├── bin/             biga / biga-card / biga-notify / biga-reap
├── deploy/openclaw/ Agent、Tool Policy、Profile 与 systemd 配置
├── tools/verify/    隔离、spawn、延迟、预算、配置与读回核验
├── tests/           2028 条测试
├── docs/
│   ├── design/      当前架构与阶段设计
│   ├── guide/       安装、使用、回滚与运维
│   ├── tutorial/    真实施工过程与事故复盘
│   └── external/    外部评审材料
├── data/            本地运行数据（不提交）
└── images/          品牌素材
```

---

## 文档导航

| 文档 | 用途 |
|---|---|
| [架构设计](docs/design/architecture.md) | 当前架构的单一事实源 |
| [确定性编排](docs/design/deterministic-orchestration.md) | 为什么把流程控制从 Agent 移到程序 |
| [Phase 1](docs/design/phase-1-walking-skeleton.md) | 第一条端到端 Walking Skeleton |
| [Phase 2](docs/design/phase-2-specialists.md) | Specialist、Risk 与并行 |
| [安装指南](docs/guide/install.md) | 与既有 OpenClaw 实例隔离共存 |
| [使用手册](docs/guide/usage.md) | 出卡、读卡、验证与排错 |
| [Schema 回滚](docs/guide/schema-rollback.md) | Schema 版本和恢复方式 |
| [开发教程](docs/tutorial/README.md) | 60 章真实施工与事故复盘 |
| [TODO](TODO.md) | 当前未完成项与机器验收条件 |
| [CHANGELOG](CHANGELOG.md) | 每次变更及其原因 |
| [CONTRIBUTING](CONTRIBUTING.md) | 贡献规则 |

文档按生命周期管理：

```text
design/    当前设计，随代码更新
guide/     操作手册，跑不通就是错
tutorial/  建造过程，写完冻结
external/  外部材料，只读保存
```

---

## 路线图

### Architecture Baseline Hardening — 当前阶段

在线溯源根节点校验、Run-scoped Spawn Proof、Hermetic Test Baseline、
Replay 专用写入口、Migration 原子性、Notification Worker 单实例——
这六项在 v0.3.1–v0.3.4 落地，**v0.3.6 复核后才真正到位**：那次复核发现其中
三项的守卫没有任何生产调用方（写了函数 ≠ 那条路被守住），详见 CHANGELOG。

当前剩余的出口条件：

```text
Phase 2 条件 3（真实否决）—— 等行情，不是等开发
Live Acceptance 重新独立通过
```

🔒 **已另打新 tag**（不移动、不覆盖旧的那个 —— 见上文「tag 现状」）：

```text
v1-architecture-baseline-2    2026-09-25 · 口径 = 代码与架构收口
```

上面那两条**不在这个 tag 的声称范围内**，见「当前边界」一节。

### Data Platform

```text
Dataset Registry
Provider Registry
Security Master
Trading Calendar
EOD Daily Bars
Emotion Snapshot
```

### Research & Decision

```text
Feature / Factor
Screening
CandidateSet
TradingPlan
Realtime Tracking
Review
Backtest
```

### Trading Platform

```text
Portfolio Ledger
Deterministic Risk Engine
Paper OMS
Broker Adapter
Web
Controlled Live Trading
```

BigA 的演进原则是：

> **先用一个真实闭环把基础架构证明正确，再把更多业务能力接入同一套身份、状态、数据和测试体系。**

---

## BigA 与其他项目的关系

BigA 借鉴多 Agent、量化研究、交易执行和数据平台项目中的成熟思想，但核心业务对象与控制面由 BigA 自己拥有。

BigA 不试图证明自己"世界唯一"。

它希望做到的是：

```text
OpenClaw Runtime
+
Deterministic Domain Control
+
Frozen Evidence
+
Run-scoped Provenance
+
Failure-first Contracts
+
Future Trading Lifecycle
```

在一个面向个人 A 股系统的统一架构里成立。

---

## 品牌素材

[`images/`](images/) 目录包含 BigA Logo 及透明背景版本。

---

## 贡献

贡献前请阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

尤其请遵守：

- 不在 Agent 内增加顶层业务编排；
- 不让自动 Agent 直接访问外部 Provider；
- 不绕过 Contract、Risk、Budget、Lock 或 Runtime Proof；
- 行为修改必须有回归测试；
- 第三方代码和数据条款必须单独核对。

---

## License

BigA 使用 [Apache License 2.0](LICENSE)。

第三方依赖、改编代码和归属信息见
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

软件许可证不等于行情、新闻、研报、公告或其他第三方数据的使用许可。
使用者仍需遵守各数据源的条款、访问限制和再分发规则。

---

## 风险声明

BigA 是用于研究、分析、自动化与交易系统实验的软件。

它不构成投资建议，不保证收益。证券交易存在本金损失风险。
使用者需自行审查分析结果、配置风险控制、遵守适用规则，并对任何交易决定负责。

<div align="center">

**当前阶段：Human-in-the-loop · 不自动下单**

</div>
