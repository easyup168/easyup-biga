<div align="center">

<img src="images/LOGO_BigA01.png" alt="EasyUp for BigA" width="480">

# EasyUp for BigA 2.0

### 基于 OpenClaw 的 Multi-Agent A 股短线决策辅助系统

![Phase](https://img.shields.io/badge/PHASE-1%20walking%20skeleton-555)
![Agents](https://img.shields.io/badge/AGENTS-2%20%2F%208-1f6feb)
![Tests](https://img.shields.io/badge/124%20TESTS-PASSING-2ea043)
![Store](https://img.shields.io/badge/SQLITE-WAL%20%C2%B7%203%20tables-555)
![Tutorial](https://img.shields.io/badge/%E6%95%99%E7%A8%8B-10%20%E7%AB%A0-8957e5)
![Latency](https://img.shields.io/badge/%E7%AB%AF%E5%88%B0%E7%AB%AF-74.8s%20%C2%B7%20%E9%A2%84%E7%AE%97%2090s-2ea043)
![NoTrade](https://img.shields.io/badge/%E4%B8%8D%E8%87%AA%E5%8A%A8%E4%B8%8B%E5%8D%95-by%20design-555)

***发现共识，锁定核心，让每一笔交易都有逻辑***

</div>

---

**不自动执行交易。** 产出是一张证据可追溯、可回放的 Decision Card，最终决策由人做。

---

## 为什么重新做一套？

已经有第一代 EasyUp 了，为什么还要重建？

**原因一：OpenClaw 升级到了 2.0。** Multi-Agent 协同支持大幅提升 ——
这是重新设计架构的最佳时机。

**原因二：做对第一次比打补丁更值得。** 与其在旧代码上修修补补，
不如从零重建，把结构做对，让它能撑住未来几年的演进。

所以 BigA 2.0 是一次**全程公开的重建过程** ——
架构决策、踩过的坑、放弃的方案，全部记录在 [`docs/tutorial/`](docs/tutorial/README.md)。

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
         │ Stage 2：并行 2 个制衡 Agent（输入 = Stage 1 的冻结证据）
         ├──► Risk（风险）      ──┐   BLOCK / WARNING / PASS
         └──► Discipline（纪律）──┘
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
| 09 | [隔离演练](docs/tutorial/09-isolation-drill.md) | `kill -9` 自己，逐项核对邻居毫发无伤 |
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
| 隔离演练（`kill -9` 自己，邻居六项未变） | ✅ |
| 124 条测试全绿 | ✅ |
| 端到端 74.8s（预算 90s） | ✅ |

Phase 1 的验收标准由机器逐条核对，`PENDING` 不计为通过：

```bash
python3 tools/verify/phase1_acceptance.py --baseline data/neighbour-baseline.json --live
```

### 关于「徽章全绿」的说明

这里必须说清楚，否则就是粉饰。

端到端徽章从橙变绿，是因为**把预算从 60s 改成了 90s**。60s 从来不是合理的预算——
它是四个阶段预估**下界**之和，等于要求所有阶段同时命中最优。
实测每个阶段都落在自己的预估区间内，超的是那个不合理的加法，不是系统本身。

完整推导见[第 10 章 · 延迟与成本](docs/tutorial/10-latency-and-cost.md)。

---

## 目录结构

```
.
├── agents/          各 Agent 的 workspace（AGENTS.md = 角色契约唯一载体）
├── skills/
│   ├── _contract/   Evidence / AgentVerdict / DecisionCard（唯一实现）
│   └── _store/      数据访问层（唯一 DB 入口，将来切 PostgreSQL 只改这里）
├── data/            SQLite 事实层（不入库）
├── tools/           cron 调度 + 验证工具
├── tests/           124 条测试
├── docs/
│   ├── design/      架构文档（SSOT）+ 安装指南
│   └── tutorial/    开发教程（10 章，与代码同步）
└── images/          品牌素材（LOGO_BigA01–04 + 透明底变体，含 C2PA 内容凭证）
```

---

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/design/architecture.md`](docs/design/architecture.md) | 系统架构：Agent 拓扑、通信契约、数据架构、延迟预算、路线图 |
| [`docs/design/install-guide.md`](docs/design/install-guide.md) | 环境安装：在已有 OpenClaw 实例的机器上并排装第二套隔离实例 |
| [`docs/tutorial/`](docs/tutorial/README.md) | 开发教程：10 章，真实建造过程 |
| [`CHANGELOG.md`](CHANGELOG.md) | 版本与变更历史（每条写「为什么」，不只是「做了什么」） |

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

<div align="center">

⚠️ **本系统不构成投资建议 · 不自动下单 · 最终决策由人做**

</div>
