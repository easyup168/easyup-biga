<div align="center">

<img src="images/LOGO_BigA01.png" alt="EasyUp for BigA" width="480">

# EasyUp for BigA 2.0

### 基于 OpenClaw 的 Multi-Agent 个人 A 股智能交易平台

![Phase](https://img.shields.io/badge/PHASE-2%20specialists%20%C2%B7%20in%20progress-d29922)
![Agents](https://img.shields.io/badge/AGENTS-7%20%2F%208-1f6feb)
![Tests](https://img.shields.io/badge/1140%20TESTS-PASSING-2ea043)
![Store](https://img.shields.io/badge/SQLITE-WAL%20%C2%B7%20v11%20%C2%B7%208%20tables-555)
![Tutorial](https://img.shields.io/badge/%E6%95%99%E7%A8%8B-33%20%E7%AB%A0-8957e5)
![Latency](https://img.shields.io/badge/%E7%AB%AF%E5%88%B0%E7%AB%AF-%E7%9B%98%E4%B8%AD%20172.6s%20%C2%B7%20%E7%9B%98%E5%90%8E%20198s-dbab09)
![IM](https://img.shields.io/badge/%E9%A3%9E%E4%B9%A6-%E5%B7%B2%E6%8E%A5%E9%80%9A-1f6feb)
![NoTrade](https://img.shields.io/badge/%E4%B8%8D%E8%87%AA%E5%8A%A8%E4%B8%8B%E5%8D%95-%E7%8E%B0%E9%98%B6%E6%AE%B5-555)

***发现共识，锁定核心，让每一笔交易都有逻辑***

</div>

---

**当前阶段：Multi-Agent 决策辅助，不自动执行交易。** 产出一张证据可追溯、可回放的
Decision Card，最终决策由人做——这是通往自动化交易平台的第一个 Kernel，
不是这个项目的终态（见下方「为什么重新做一套」）。

---

## 为什么重新做一套？

已经有第一代 EasyUp 了，为什么还要重建？

**原因一：OpenClaw 升级到了 2.0。** Multi-Agent 协同支持大幅提升 ——
这是重新设计架构的最佳时机。

**原因二：做对第一次比打补丁更值得。** 与其在旧代码上修修补补，
不如从零重建，把结构做对，让它能撑住未来几年的演进。

所以 BigA 2.0 是一次**全程公开的重建过程** ——
架构决策、踩过的坑、放弃的方案，全部记录在 [`docs/tutorial/`](docs/tutorial/README.md)。

**现在这个多 Agent DecisionCard 闭环不是终点。** 它是一个更长期的个人交易平台的
第一块 Kernel——后续如果有选股 / 计划 / 风控 / 组合 / 复盘 / 回测这类能力，
设想中是围着这个 Kernel 长出来，不是另起一套系统。当前阶段的施工范围仍然只是
Decision Card 闭环本身，见下方「确定性编排升级」。

---

## 架构

```
人 ──► BigA Supervisor（main）
         │
         │ Stage 1：并行 5 个分析 Agent
         ├──► Emotion（情绪）──┐
         ├──► Sector（板块）  ─┤
         ├──► News（新闻）    ─┼──► AgentVerdict × 5（含 Evidence）
         ├──► Technical（技术）┤
         └──► Market（市场）  ─┘
         │
         │ Stage 2：制衡 Agent（输入 = Stage 1 的冻结证据）
         └──► Risk（风险）——BLOCK / WARNING / PASS
              （Discipline·纪律 设计上也在这一层，Phase 3 才建——见下方状态表）
         │
         │ Stage 3：Supervisor 合成
         ▼
   BigA Decision Card ──► 落库（可回放）──► Human-in-the-loop
```

**1 个 Supervisor · 7 个专家 Agent** —— 每个 Agent 只做自己领域的事，
用 Evidence 说话，不猜，不编。

---

## 三条设计地基

**`UNKNOWN` ≠ `PASS`**
算不出来必须说算不出来，进「缺失项」并显示在卡片上。
静默 fail-open 是本项目最优先防范的失败模式。

**数据与智能分离**
有唯一正确答案的计算归 Python Skill，需要取舍的判断归 Agent。
Agent 不在 prompt 里做算术；任何数字必须来自工具返回值并附 Evidence。

**事实可追溯**
每条 Evidence 带 `source` / `as_of` / `retrieved_at`。
证据冻结后可重跑合成，用于换模型对比、提示词回归、事后复盘。

---

## 开发教程系列

本仓库同时是一份**从零开始的开发教程**。

这不是普通教程 —— 它有一个真实约束：
**同机上已经跑着另一套长期运行的 OpenClaw 实例，不能碰它一根手指。**
新系统必须在它旁边安全地长出来。

> 多数「把新系统装崩老系统」的事故，不是因为有人做了危险操作，
> 而是因为**默认行为在共享资源上悄悄生效**了。

教程里大量篇幅在讲「为什么这条看起来无害的命令有毒」 —— 这是它与普通教程最大的区别。

| # | 章节 | 主题 |
|---|---|---|
| 01 | [隔离安装](docs/tutorial/01-isolated-install.md) | 在已有实例旁装第二套，两个必踩的陷阱 |
| 02 | [Profile 初始化](docs/tutorial/02-profile-setup.md) | `setup --baseline` + `config patch`，为什么不走向导 |
| 03 | [契约层](docs/tutorial/03-contract-layer.md) | Evidence / AgentVerdict / DecisionCard，AST 扫描钉死唯一实现 |
| 04 | [数据层](docs/tutorial/04-store-layer.md) | SQLite WAL 单一入口，只追加触发器，回放不覆盖 |
| 05 | [第一个技能](docs/tutorial/05-first-skill.md) | 真采 A 股情绪数据；与会静默骗人的接口打交道 |
| 06 | [建 Agent](docs/tutorial/06-agents.md) | 脚手架默认值多半不是你要的；角色契约写在 AGENTS.md |
| 07 | [端到端](docs/tutorial/07-end-to-end.md) | 三层认证迷宫；怎么**证明** Specialist 真的被调用过 |
| 08 | [回放](docs/tutorial/08-replay.md) | 冻结证据；在线与回放共用同一份组装代码 |
| 09 | [隔离演练](docs/tutorial/09-isolation-drill.md) | `kill -9` 自己，逐项核对已有实例毫发无伤 |
| 10 | [延迟与成本](docs/tutorial/10-latency-and-cost.md) | 216s→75s；延迟其实是正确性 bug 的症状 |

配套抖音系列同步更新 · **关注 易涨EasyUp** 不迷路

---

## 当前状态

**Phase 1 · Walking Skeleton 已完成。**

| 项 | 状态 |
|---|---|
| Node v24.21.0 + OpenClaw 2026.9.5（隔离安装） | ✅ |
| `biga` wrapper（强制 profile 隔离，消灭误操作可能性） | ✅ |
| Workspace 骨架 + git | ✅ |
| `biga setup`（端口 19789，未接 IM） | ✅ |
| 契约层 `_contract`（四条铁律构造时拒绝 + AST 唯一实现扫描） | ✅ |
| 数据层 `_store`（三张表，只追加由触发器强制） | ✅ |
| `emotion-calc` Skill（真采 A 股情绪数据） | ✅ |
| `emotion` Agent + Supervisor 委派配置 | ✅ |
| 合成与回放（两条路径共用同一份组装代码） | ✅ |
| 隔离演练（`kill -9` 自己，已有实例六项未变） | ✅ |
| 124 条测试全绿 | ✅ |<!-- 冻结：Phase 1 验收当时的数 -->
| 端到端 74.8s（预算 90s） | ✅ |

Phase 1 的验收标准由机器逐条核对，`PENDING` 不计为通过：

```bash
python3 tools/verify/phase1_acceptance.py --baseline data/neighbour-baseline.json --live
```

---

## 快速开始

```bash
cd ~/.openclaw-biga/workspace

bin/biga-card              # 出一张决策卡（约 3 分钟、$1.2）
bin/biga-card --list       # 最近出过哪些
bin/biga-card --show <号>   # 看某一张
bin/biga-card --check <号>  # 用冻结证据重跑，断言结论逐字段相同
```

完整用法与「怎么确认它没骗你」见 [`docs/guide/usage.md`](docs/guide/usage.md)。

⚠️ **不是交易信号。** 系统不下单、不接账户，最终决定由人做。

### 手机上能看 —— 飞书已接通

用 OpenClaw 官方的 `@openclaw/feishu` 通道（长连接，**不需要公网地址**）。
gateway 跑成 systemd 用户服务，开机自起：

```bash
~/.openclaw-biga/bin/biga gateway install   # 单元名带 profile 后缀，见红线 R-2
~/.openclaw-biga/bin/biga gateway start
journalctl --user -u openclaw-gateway-biga.service -f
```

聊天收发已实测通。访问控制与成本闸门都在：

| 项 | 配置 |
|---|---|
| 谁能私聊 | `dmPolicy: allowlist` —— 只有名单内 |
| 群里能不能触发 | `groupPolicy: allowlist` + 空名单 ⇒ **任何群都不响应** |
| 一次出卡的代价 | 约 3 分钟 / $1.2~1.4 ⇒ 预算闸门在 **Stage 0 占号**处拦 |

🔴 **别用自然语言在飞书里要卡** —— 通道把消息直接交给 Supervisor，
它会**自己编排**，而实测那么做会出错（4/5 个 agent、顺序反了、$0.4 白花）。
编排现在只有一份实现（[`ORCHESTRATION.md`](skills/decision-card/ORCHESTRATION.md)），
契约只说一句「跑 `bin/biga-card`」—— 这条路径的实测收口还没做。

⚠️ **盘中会一直是 `WAIT`**，这是对的行为不是 bug ——
实时源说「此刻」、日线源说「上一交易日」，风控拒绝把两者当同一天审。
🔴 **「收盘后就对齐」是错的 —— 实测证伪。**

15:21（收盘 21 分钟后）跑了一次，交易日**仍然分裂**：

```
technical/market/sector  20260918     ← 日线
emotion/news             20260921     ← 实时源
```

查源：15:22 时新浪日线的最后一根还是 `20260918`。
**当天的日线不在 15:00 发布，有一段未知长度的延迟。**

⚠️ 这条「收盘后跑就好了」是**没有验证就写下的推断**，
写进了 README、操作手册和教程三处。现在全部改掉。

> 通用原则：**「应该会……」和「实测是……」之间隔着一次运行。**
> 而这类推断特别危险，因为它听起来太合理了 —— 谁会怀疑「收盘后日线就有了」。

⇒ 正确的说法是：**要等当天日线真正发布之后**。
具体时刻正在实测（见 `TODO.md` 的待测项），在测出来之前，
判断方法是直接看：

```bash
python3 -c "
import sys;sys.path.insert(0,'skills')
from _sources.sina import fetch_index_daily
print(fetch_index_daily('sh000001',bars=1).bars[-1].day)"
```

打印出今天的日期，才是出卡的时机。

---

## Phase 2 · Specialists（进行中——`phase2` 分支已并入，后续在 `orchestration` 分支）

| 项 | 状态 |
|---|---|
| Agent **7 / 8** —— Stage 1 五个 + Stage 2 `risk` + Supervisor | ✅ |
| 第 8 个 `discipline` —— **故意不建**（没有输入源，见裁定 13） | — |
| Stage 1 **五个实测并行**（区间相交 27.9s，墙钟 64.2s vs 串行 227.3s） | ✅ |
| Stage 2 拿的是冻结证据（结构保证 + AST 测试） | ✅ |
| schema v11 —— 决策编号原子分配器 + 只追加保护（v6 加事实/判断拆分的 `kind` 列；v10 让 `run_id` 贯穿；v11 令一个 `(task_id, agent)` 至多一份 fact 原件） | ✅ |
| 1140 条测试 | ✅ |
| 成本分解 $1.20/次（`main` 占 37%） | ✅ |
| 隔离自检 `tools/verify/isolation.py` **三态**，`UNKNOWN` 不计入通过 | ✅ |
| **spawn 核验** —— 每次出卡自动对账，`agent_runs` 不算凭证 | ✅ |
| **飞书接入** —— 官方通道 + 长连接 + 私聊/群双白名单 | ✅ 聊天已通 |
| **出卡预算闸门** —— 最小间隔 / 当日上限 / 上一次还在跑 | ✅ |
| 飞书里用自然语言要卡 | 🔶 会走错编排，**实测收口未做**（见待裁定）|
| 两份外部对抗性评审共 29 条发现 | 🔶 15 模式级 / 7 **实例级（模式还在）** / 1 修不干净 |
| 第三轮深度评审 6 条 —— **全是同一个形状**（守卫不会红，§9 L-13） | ✅ 每条都有探针红灯 |
| 至少 1 次真实「否决」端到端落库 | ⬜ |
| 真实缺失项跨天累积 ≥5 次 | 🔶 数够了，但全在同一天 |

🔴 **最后两条曾经卡在同一个架构问题上**：`emotion`/`news` 报「今天」、
日线类报「上一交易日」，`risk` 正确地拒绝合并审 ⇒ 每次「无法判定」。
这是**对的行为**，但它意味着那段时间出不了有把握的卡。

⏩ **2026-09-21 17:18 更新**：当天日线发布之后（实测收盘后约 35 分钟）
跑的 `BIGA-20260921-020`，**六个 Agent 首次报同一个交易日**，
`risk` 第一次真的裁决（`放行`），缺失项从 8~9 条降到 3 条。

⇒ **前置条件解决了，但这两条出口条件仍未达成**：
「放行」不是「否决」—— 要攒到真实否决得等一个真的触发阈值的行情；
跨天累积同样要等下一个交易日。**别把前置条件当成达成。**

详见 [`phase-2-specialists.md`](docs/design/phase-2-specialists.md) §3.11。

---

## 确定性编排升级（进行中，在 `orchestration` 分支）

Phase 2 跑通之后，出过好几次同一形状的事故：证据合成到错误的决策号上、
出卡递归成 187 个会话烧掉 \$8.99、飞书路径 4 spawn 缺一个 agent 却没有任何报错。
共同点是**工作流一直靠 Supervisor 读提示词临场决定**——提示词表达意图，
表达不了不变量，下一条缝总会从别处长出来。

⇒ 把"下一步跑什么、谁被调用、超时多久、什么算失败"这些**程序说了算**的东西，
从提示词里搬进一个显式状态机（`DecisionOrchestrator`）；"当前市场是什么状态、
证据意味着什么"这类**判断**，仍然是 Agent 的事。完整推导、每一批做了什么、
每次评审怎么复核，见 [`docs/design/deterministic-orchestration.md`](docs/design/deterministic-orchestration.md)。

| 批 | 内容 | 状态 |
|---|---|---|
| A – D | 契约收紧、运行身份、Runtime Adapter、Orchestrator 主干、冻结快照 | ✅ 已落地 |
| E-I / E-II | 把 `AgentVerdict` 拆成事实（`FactBundle`）与判断（`AgentAssessment`）；六个 skill 里五个已迁 | ✅ 已落地 |
| E-III | 迁最后一个（`risk`，带否决权）+ 退役旧的事后修订路径 | ⬜ 分发提示词已就绪 |
| J | 收敛 `run_id` 这个名字在库里同时指三个不同东西的历史遗留 | 🔶 进行中 |
| F – L | Risk 拆两层 / Outbox+飞书 trigger / RawArtifact / Registry / 交易日历 | ⬜ 排期见设计文档 §6 |

### 关于「徽章全绿」的说明

这里必须说清楚，否则就是粉饰。**端到端徽章绿过两次，两次都改过预算。**

**第一次 60s → 90s。** 60s 从来不是合理的预算 —— 它是四个阶段预估**下界**之和，
等于要求所有阶段同时命中最优。实测每个阶段都落在自己的区间内，
超的是那个加法，不是系统本身。

**第二次 90s → 180s。** 这次更需要说清楚，因为**数字变差了**：
Phase 1 的 74.8s 是**休市日**测的，那时只有一个 Specialist 且大多因数据未形成早退。
盘中六 Agent 实测 172.6s。

改预算的三个必答问题（缺一条就是作弊）：

1. **旧数字错在哪** —— 在一个 agent 都没有时把四个区间相加得到的，是愿望不是预测
2. **新数字怎么来的** —— 四次盘中实测，每项取最好值仍需 158s，180s 留 14% 余量
3. **什么时候它该红** —— 优化后三次都在 158–173s；超 180s 说明有组成异常了

而且要指出**哪部分不在我们控制内**：Stage 1 的最慢项是第三方接口延迟，
实测并发度调到 4 会触发渐进限流（7.2 → 25.2 → 60.1s）。

**第三次：还没改，因为回答不了第 3 问。** 首次盘后实测 **198s / $1.37**，
超了 180s。原因清楚 —— 收盘后一小时快讯 **184 条**（盘中约 70 条），
`news` 单轮从 78.3s 涨到 100.0s。

🔴 但只有一次盘后数据，**说不出「什么时候它不该红」** ——
而那正是前两次改预算时反复强调的必答问题。
顺手调到 210s 会让盘中的异常不再报红。⇒ 留作待裁定，先攒实测。

> 注意这三次的共同点：**旧数字之所以不对，都是因为测量窗口没覆盖到某个情形。**
> 第一次没有 agent，第二次是休市日，第三次是所有实测都在 15:00 之前。

完整推导见[第 10 章](docs/tutorial/10-latency-and-cost.md)与
[第 17 章](docs/tutorial/17-closing-phase-2.md)。

---

## 目录结构

```
.
├── agents/          各 Agent 的 workspace（AGENTS.md = 角色契约唯一载体）
├── skills/
│   ├── _contract/   Evidence / AgentVerdict / DecisionCard（唯一实现）；
│   │               facts.py：事实（FactBundle）与判断（AgentAssessment）拆开，
│   │               六个 skill 迁移中（见「确定性编排升级」）
│   ├── _sources/    采集层：四个数据源 + 重试 + 交易日 + 量级围栏
│   ├── _store/      数据访问层（唯一 DB 入口，将来切 PostgreSQL 只改这里）
│   └── *-calc/      六个业务技能（market / sector / technical / emotion / news / risk）
├── data/            SQLite 事实层（不入库）
├── tools/verify/    巡检：隔离 / spawn 核验 / 延迟 / 缺失台账 / 公开审查
│                   退出码三态由 `_verdict.py` 唯一定义（0 过 / 1 不过 / 2 判不了）
├── tests/           1140 条测试
├── docs/
│   ├── design/      架构文档（SSOT）+ 安装指南
│   └── tutorial/    开发教程（29 章，与代码同步）
└── images/          品牌素材（LOGO_BigA01–04 + 透明底变体，含 C2PA 内容凭证）
```

---

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/README.md`](docs/README.md) | **文档规约**：放哪、叫什么、谁该更新它（由测试强制） |
| [`docs/design/architecture.md`](docs/design/architecture.md) | 架构 SSOT：Agent 拓扑、通信契约、数据架构、失败模式清单 |
| [`docs/design/phase-1-walking-skeleton.md`](docs/design/phase-1-walking-skeleton.md) | Phase 1 设计与验收结果（已冻结） |
| [`docs/design/phase-2-specialists.md`](docs/design/phase-2-specialists.md) | Phase 2 设计：范围、步骤、关键取舍、出口条件 |
| [`docs/design/deterministic-orchestration.md`](docs/design/deterministic-orchestration.md) | 确定性编排升级：为什么、各批范围/判据、评审断言复核 |
| [`docs/guide/usage.md`](docs/guide/usage.md) | **怎么用**：出卡、读卡、以及四个「确认它没骗你」的检查 |
| [`docs/guide/install.md`](docs/guide/install.md) | 环境安装：在已有 OpenClaw 实例旁并排装第二套 |
| [`docs/tutorial/`](docs/tutorial/README.md) | 开发教程：29 章，真实建造过程 |
| [`CHANGELOG.md`](CHANGELOG.md) | 变更历史（每条写「为什么」，不只是「做了什么」） |

---

## 运行环境

| 项 | 值 |
|---|---|
| OS | WSL2 · Linux 6.6 |
| Node | v24.21.0 (LTS Krypton) |
| OpenClaw | 2026.9.5 |
| 数据层 | SQLite (WAL) |
| Gateway 端口 | 19789 |

---

## 品牌素材

[`images/`](images/) 目录含五张 Logo：

- `LOGO_BigA01`：横版字标，黑字白底（GitHub 首图 / 浅色背景用）
- `LOGO_BigA02`：方形图标（头像 / 方形场景用）
- `LOGO_BigA03`：方案总览
- `LOGO_BigA04`：方形 App 图标，红底白字圆角
- `bigA01`：横版字标的**透明底白字**版（深色背景 / 视频叠加用）

五张图由 AI 生成，保留了 C2PA 内容凭证（`caBX` 块）未作剥离。

---

## License

BigA is licensed under the **Apache License, Version 2.0**. See
[`LICENSE`](./LICENSE) and [`NOTICE`](./NOTICE).

Unless explicitly stated otherwise, BigA-owned source code and documentation
are provided under Apache-2.0. Third-party components remain subject to their
respective licenses and attribution requirements; see
[`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md).

### Market data and third-party content

The Apache-2.0 license applies to the **BigA software**, not to third-party
market data or content accessed through it.

Market data, financial data, news, research reports, regulatory filings,
web content, and other third-party information may be subject to separate
provider terms, copyright, licensing, rate limits, access restrictions, and
redistribution rules. Users are responsible for ensuring that their use of
each provider complies with the applicable terms.

### Financial disclaimer

BigA is software for research, analysis, automation, and trading-system
experimentation. It does not provide investment advice, a recommendation,
or a guarantee of investment performance.

Trading and investing involve risk, including possible loss of principal.
Users remain responsible for reviewing analyses, configuring risk controls,
complying with applicable rules and broker requirements, and authorizing any
live trading activity.

### Trademarks

The Apache License 2.0 does not grant trademark rights. Project names, logos,
and branding are governed separately from the software license.

---

<div align="center">

⚠️ **本系统不构成投资建议 · 当前阶段不自动下单 · 最终决策由人做**

</div>
