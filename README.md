<div align="center">

<img src="images/LOGO_BigA01.png" alt="BigA" width="460">

### 基于 OpenClaw 的可追溯 Multi-Agent A 股决策内核

[![Status](https://img.shields.io/badge/status-architecture%20baseline%20v1-2ea043)](TODO.md)
[![Phase](https://img.shields.io/badge/PHASE-2%20specialists%20%C2%B7%20in%20progress-d29922)](docs/design/phase-2-specialists.md)
[![Agents](https://img.shields.io/badge/AGENTS-7%20%2F%208-1f6feb)](#pipeline-中有哪些-agent)
[![Tests](https://img.shields.io/badge/1817%20TESTS-PASSING-2ea043)](#当前实现状态)
[![Store](https://img.shields.io/badge/store-SQLite%20WAL%20%C2%B7%20v19-555)](docs/tutorial/04-store-layer.md)
[![License](https://img.shields.io/badge/license-Apache--2.0-2ea043)](LICENSE)
[![Trading](https://img.shields.io/badge/live%20trading-disabled-555)](#当前边界)

**发现共识，锁定核心，让每一笔判断都有证据。**

</div>

---

## BigA 是什么

BigA 是一个建立在 [OpenClaw](https://docs.openclaw.ai) 之上的个人 A 股 Multi-Agent 系统。

**当前仓库首先建设的是 Decision Kernel：**

```text
Trigger
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
- 可追溯；
- 可回放；
- 可以证明 Agent 真实运行过；

的 `DecisionCard`。

**长期目标**是在这个内核周围继续建设完整的个人交易系统：

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

## 为什么这个项目值得重新做

BigA 不是把一批 Agent 名字放进 Prompt 里。

它重点验证并固化的是更难的部分：

1. **程序控制流程，Agent 负责判断。**
2. **不知道就是 `UNKNOWN`，不能静默变成 `PASS`。**
3. **同一次运行的事实、判断、风险和 Card 必须能追溯到同一条身份链。**
4. **OpenClaw Runtime 的真实记录，而不是 Agent 自报，才算 spawn 证据。**
5. **在线与回放共用同一套组装逻辑。**
6. **同机另一套 OpenClaw 实例必须保持隔离。**
7. **成本、超时、递归和通知失败都必须有机器级守卫。**

开发过程中真实发生过的事故、错误假设和修复过程，全部保留在
[`docs/tutorial/`](docs/tutorial/README.md) 中（目前 60 章）。

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

### OpenClaw 与 BigA 的边界

**OpenClaw 负责：**

```text
Agent Session
模型调用
Tool Policy
Subagent
Runtime Run ID
Agent Timeout
```

**BigA 负责：**

```text
Trigger
Decision / Run Identity
状态机
数据冻结
EvidenceSet
Risk
Card
Replay
预算
通知意图
```

一句话：

> **OpenClaw 是智能运行时；BigA 是业务系统。**

---

## Pipeline 中有哪些 Agent

### 交互入口

| Agent | 职责 |
|---|---|
| `main` | 与人交互、解释结果、查询状态；**不负责出卡编排**（编排是一段程序，`main` 够不到它，见「确定性编排升级」） |

### 自动出卡 Pipeline

| Stage | Agent | 职责 |
|---|---|
| Stage 1 | `market` | 指数、成交额、量能、市场宽度 |
| Stage 1 | `sector` | 板块强度、资金方向、主线结构 |
| Stage 1 | `news` | 新闻窗口与消息面判断 |
| Stage 1 | `technical` | 均线、MACD、RSI、区间位置 |
| Stage 1 | `emotion` | 涨停、炸板、连板与赚钱效应 |
| Stage 2 | `risk` | 读取冻结的 Stage 1 事实并行使否决权 |
| Stage 3 | `synthesizer` | 只做最终综合判断，不采集数据、不 spawn Agent |

`discipline` 已进入长期设计，但目前没有真实的交易行为输入源，因此注册但不启动。

---

## 六条设计地基

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

这条链的 Run Provenance 已经完整落地（schema v16/v17）：一张卡属于哪次执行、
基于哪份切片，现在是一句 SQL，不必解 `card_json`。

### 5. Runtime Proof 不能由业务代码自证

`agent_runs` 是 BigA 的执行账本，不是 spawn 证明。

真正证明 Specialist 被 OpenClaw spawn 的，是 OpenClaw Runtime 自己记录的运行数据。

### 6. 历史不可覆盖

```text
原始事实
Snapshot
Verdict
DecisionCard
Replay
```

都采用追加或版本化思路。修订产生新版本，不把历史改成"当时就知道"。

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
| Facts / Assessment 拆分 | ✅ |
| `index_daily` 冻结快照共享 Vertical Slice | ✅ |
| Strict JSON、写边界重校验、Amendment 约束 | ✅ |
| Replay 与组装一致性检查 | ✅ |
| OpenClaw Runtime spawn 核验 | ✅ |
| 飞书入站、出站闭环 | ✅ 真机端到端已确认（触发 → 出卡 → 推送成功） |
| Apache-2.0 开源合规基础 | ✅ |
| Run → EvidenceSet → Verdict → Card 强身份闭环 | ✅ |
| 外部 kill 后 stale Run 自动收尾 | ✅ |
| **确定性编排升级 Baseline 冻结**（`v1-architecture-baseline`） | ✅ 经独立 sign-off |
| 飞书启动失败重试策略 | 🔶 固定次数重试，无指数退避 |
| 飞书里用裸自然语言要卡（不发 `/card`） | 🔶 仍会走错编排，别这么用 |
| **Phase 2 自身两条出口条件**（真实否决端到端落库 / 缺失项跨天累积） | 🔶 仍未达成，见下 |
| 全量 Dataset / Provider / Pipeline Registry | ⬜ 后续 |
| 选股、回测、实时交易、Web | ⬜ 长期路线 |

当前仓库有 **1817 条测试，SQLite schema v19**，全部通过。

🔴 **"确定性编排升级"（把工作流从提示词搬进程序）已完成 Baseline 冻结**，
不代表 **Phase 2 本身**已经收口——两者是并行、互不代表对方的判据。Phase 2
自己的出口条件仍差两条：`emotion`/`news` 报"今天"、日线类报"上一交易日"，
`risk` 因此正确地拒绝合并审 ⇒ 攒不到一次真实否决；跨天累积同理。
详见 [`phase-2-specialists.md`](docs/design/phase-2-specialists.md) §3.11、
[`deterministic-orchestration.md`](docs/design/deterministic-orchestration.md)。

---

## 快速开始

BigA 的安装场景是：**在一台已经运行另一套 OpenClaw 的机器上，并排安装第二套隔离实例。**

请先按安装指南执行：

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
bin/biga-card --list       # 最近出过哪些
bin/biga-card --show <号>   # 看某一张
bin/biga-card --status <run_id>  # 说出某次运行死在哪一步
bin/biga-card --check <号>  # 用冻结证据重跑，断言结论逐字段相同
```

`--check` 验证的是：

```text
冻结证据
→ 落库
→ 取回
→ 重新组装
```

是否无损；它不是"重新独立推导一次投资结论"。

### 手工投递待发通知

```bash
bin/biga-notify                    # 真投递（默认）
bin/biga-notify --deliverer stdout # 只打印，不真发（排查用）
```

### 紧急停止生成新卡

```bash
printf 'maintenance\n' > .biga-card-stop
```

恢复前确认触发源已经停止，然后 `rm .biga-card-stop`。

`--list`、`--show`、`--check` 等只读命令不受总闸影响。

---

## 怎么确认它没有"自说自话"

BigA 不把"程序没报错"当作可信证明。

| 要验证什么 | 命令 | 证明范围 |
|---|---|---|
| Card 组装可回放 | `bin/biga-card --check <decision_id>` | 冻结输入到 Card 的管线无损 |
| Agent 真的运行 | `python3 tools/verify/spawn_check.py <decision_id>` | BigA 账本与 OpenClaw Runtime 双重对账 |
| Stage 1 真的并行 | `python3 tools/verify/latency_report.py --parallel-check` | Agent 运行时间区间存在结构性重叠 |
| 同机实例未受影响 | `python3 tools/verify/isolation.py` | Profile、文件、端口与 systemd 命名空间隔离 |
| 存量数据可读 | `python3 tools/verify/readback_check.py` | 数据库中不存在"写进去了但读不回来"的毒行 |
| 今日预算与闸门 | `python3 tools/verify/budget_report.py` | 当前是否允许发起一次新的付费 Run |
| 配置基线未漂移 | `python3 tools/verify/config_baseline.py` | OpenClaw Version / Tool Policy Hash / Agent Config Hash |

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

- 飞书发送 `/card` 命令触发 BigA（裸自然语言描述意图不会被识别，见上表）；
- DecisionCard / Run Failure 进入通知路径；
- `bin/biga-notify` 主动发送待发消息；
- systemd timer 定期投递。

飞书属于当前架构的最小集成，不是多用户、多租户通知平台。

---

## 确定性编排升级（Baseline 已冻结）

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
真机触发飞书 `/card` 走完整闭环、13 项 Live Acceptance 逐条查库核实、
两处真缺陷（编排器超时不取消残留 spawn、飞书 ACK 抢在预算闸门前面许诺
结果）当场发现并修复，最终由另一个会话复核后签字确认。

完整推导、每一批做了什么、每次评审怎么复核，见
[`docs/design/deterministic-orchestration.md`](docs/design/deterministic-orchestration.md)。

---

## 目录结构

```text
.
├── agents/          各 Agent 的 workspace（AGENTS.md = 角色契约唯一载体）
├── src/easyup_biga/ 五个共享基础设施包的真实实现
│   ├── domain/      Evidence / AgentVerdict / DecisionCard（唯一实现）；
│   │               facts.py：事实（FactBundle）与判断（AgentAssessment）已全部拆开
│   ├── providers/   采集层：五个数据源 + 重试 + 量级围栏
│   ├── persistence/ 数据访问层（唯一 DB 入口，将来切 PostgreSQL 只改这里）
│   ├── runtime/     OpenClaw 运行时适配层（Specialist 生命周期唯一入口）
│   └── application/ SnapshotCoordinator（冻结一次、多处读，跨层协调）
├── skills/
│   ├── _contract/ _sources/ _store/ _runtime/ _snapshot/
│   │               旧包路径，原地留兼容薄壳（一个字符不改地转发到 src/ 之下）
│   └── *-calc/      六个业务技能（market / sector / technical / emotion / news / risk）
├── data/            SQLite 事实层（不入库）
├── tools/verify/    巡检：隔离 / spawn 核验 / 延迟 / 缺失台账 / 配置基线 / 公开审查
│                   退出码三态由 `_verdict.py` 唯一定义（0 过 / 1 不过 / 2 判不了）
├── tests/           1817 条测试
├── docs/
│   ├── design/      架构文档（SSOT）+ 各阶段设计
│   ├── guide/       操作手册（安装 / 使用 / Schema 回滚）
│   └── tutorial/    开发教程（60 章，与代码同步）
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
| [Schema 版本与回滚](docs/guide/schema-rollback.md) | 17 个版本一句话摘要 + 没有 DOWN migration 时怎么办 |
| [开发教程](docs/tutorial/README.md) | 真实施工过程与事故复盘 |
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

### Architecture Baseline Hardening —— 🔶 I 节进行中

`v1-architecture-baseline` tag（已冻结）覆盖 Run Provenance 闭环、
Run → EvidenceSet 一对一、Strict VerdictRef、TIMEOUT / Kill / Stale Run
收敛、飞书失败语义、正式 Package 边界、Full Test Baseline。

**I 节**在此基础上补充七项防御加固（P1-1/P1-2/P1-3/P2-1/P2-2/P2-3/P2-4），
消灭已核实的静默失败模式（在线溯源根节点校验缺失、双写账本、环境依赖测试无法
在干净 clone 里运行等）。

### Phase 2 收尾 —— 进行中

```text
真实否决端到端落库（等一次够极端的行情）
缺失项跨天累积 ≥5 次（等下一个交易日）
```

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

## 品牌素材

[`images/`](images/) 目录含五张 Logo（`LOGO_BigA01`–`04` + 一张透明底变体）。
五张图由 AI 生成，保留了 C2PA 内容凭证（`caBX` 块）未作剥离。

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
