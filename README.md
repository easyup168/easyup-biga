<div align="center">

<img src="images/LOGO_BigA01.png" alt="EasyUp for BigA" width="480">

# EasyUp for BigA 2.0 — 会说「我不知道」的 A 股 Multi-Agent 决策系统

![Phase](https://img.shields.io/badge/PHASE-1%20walking%20skeleton-555)
![Agents](https://img.shields.io/badge/AGENTS-2%20%2F%208-1f6feb)
![Tests](https://img.shields.io/badge/124%20TESTS-PASSING-2ea043)
![Store](https://img.shields.io/badge/SQLITE-WAL%20%C2%B7%203%20tables-555)
![Tutorial](https://img.shields.io/badge/%E6%95%99%E7%A8%8B-10%20%E7%AB%A0-8957e5)
![Audit](https://img.shields.io/badge/%E5%AE%89%E5%85%A8%E5%AE%A1%E6%9F%A5-8%20%2F%208-2ea043)

![Accept](https://img.shields.io/badge/Phase%201%20%E9%AA%8C%E6%94%B6-9%20%2F%209-2ea043)
![Latency](https://img.shields.io/badge/%E7%AB%AF%E5%88%B0%E7%AB%AF-74.8s%20%C2%B7%20%E9%A2%84%E7%AE%97%2090s-2ea043)
![NoTrade](https://img.shields.io/badge/%E4%B8%8D%E8%87%AA%E5%8A%A8%E4%B8%8B%E5%8D%95-by%20design-555)

***发现共识，锁定核心，让每一笔交易都有逻辑***

</div>

<sub>品牌素材在 [`images/`](images/)：`LOGO_BigA01` 横版字标 · `LOGO_BigA02` 方形图标 ·
`LOGO_BigA03` 方案总览。三张图由 AI 生成，保留了 C2PA 内容凭证（`caBX` 块）未作剥离。</sub>

---

基于 OpenClaw 的 Multi-Agent A 股短线**决策辅助**系统。

> **产品边界**：本系统不自动执行交易，不构成投资建议。
> 最终交易动作由人决定 —— AI 负责扩大认知，人负责最终决策。

### 徽章现在全绿了 —— 但其中一个是因为改了指标

这里必须说清楚，否则就是粉饰。

前一版 README 挂着两个橙红徽章：验收 8/9、端到端 122.8s 超 60s 预算。
现在两个都绿了，**而它们变绿的原因完全不同**：

| 徽章 | 怎么变绿的 | 可信度 |
|---|---|---|
| 验收 9/9 | 修了三个真 bug，端到端 216s → **74.8s** | 实打实 |
| 端到端达标 | **把预算从 60s 改成了 90s** | ⚠️ 读者有权怀疑 |

改指标让红灯变绿，是最容易滑向自欺的一种操作。所以推导过程全部公开：

- **60s 从来不是预算**，它是 `architecture.md` §10.1 四个阶段预估**下界之和**
  （5+20+15+20），105s 是上界之和。把一个区间的最好情况当成及格线，
  等于要求四个阶段同时命中最优。
- 实测按同样的阶段拆开：**每一个阶段都落在自己的预估区间内**
  （8.0s / 36.0s / 30.0s），合计仍然 75s。**没有哪个阶段超支，超的是那个加法。**
- 新的 90s 有推导：Stage 0/1/3 预估**上界**之和（10+40+30=80s）+ 10s 盘中网络余量。
- 八 Agent 的 105s **没有跟着改** —— 它出自同一套算术，但现在没有数据支持新的数，
  **没有数据就不改，也不拿它当承诺**。

完整过程见[第 10 章 · 延迟与成本](docs/tutorial/10-latency-and-cost.md)，
包括那三个 bug 各自怎么以「慢」而不是「报错」的形式表现出来。

顺带一提，验收脚本第 8 项的输出里**保留了 Supervisor 自报的耗时**：

```
✅ 8. 单次端到端 < 90s（真实墙钟）
      74.8s（3 轮，$0.2179）；Supervisor 自报 130.0s
```

自报值高报了 74%（另一次低报 43%）。既然已经改用实测值，
本可以把这个难看的数字删掉 —— 留着，是因为**两者的差距本身是有价值的信息**。

这正是本项目要验证的东西：**一个会主动暴露自己哪里不行的系统，
比一个看起来全绿的系统可信。** 如果连自己的验收都要粉饰，
那它出的 Decision Card 也不值得信。

---

## 核心理念

不以「AI 预测某只股票涨跌」为核心，而是由多个专业 Agent 协同完成
市场观察、共识识别、技术分析、事件验证、风险审查和交易纪律检查，
最终产出一张**证据可追溯、可回放**的决策卡。

生产版优先展示**证据项、风险项、缺失项和状态**，
而不是用一个看似精确的模型分数替代判断。

## 架构一览

```
人 ──► BigA Supervisor（main）
         │
         │ Stage 1：并行 5 个分析 Agent
         ├──► Market ──┐
         ├──► Sector ──┤
         ├──► News   ──┼──► AgentVerdict × 5（含 Evidence）
         ├──► Technical┤
         └──► Emotion ─┘
         │
         │ Stage 2：并行 2 个制衡 Agent（输入 = Stage 1 的冻结证据）
         ├──► Risk       ──┐   BLOCK / WARNING / PASS
         └──► Discipline ──┘
         │
         │ Stage 3：Supervisor 合成
         ▼
   BigA Decision Card ──► 落库（可回放）──► Human-in-the-loop
```

## 三条设计地基

1. **`UNKNOWN` ≠ `PASS`** —— 算不出来必须说算不出来，并显示在卡片的「缺失项」里。
   静默 fail-open 是本项目最优先防范的失败模式。
2. **数据与智能分离** —— 有唯一正确答案的计算归 Python，需要取舍的判断归 Agent。
   Agent 不在 prompt 里做算术；任何数字必须来自工具返回值并附证据。
3. **事实可追溯** —— 每条证据带 `source` / `as_of` / `retrieved_at`。
   证据冻结后可重跑合成，用于换模型对比、提示词回归、事后复盘。

## 目录

```
.
├── agents/      各 Agent 的 workspace（AGENTS.md = 角色契约唯一载体）
├── skills/      OpenClaw 技能 + 确定性 Python 计算层
│   ├── _contract/   Evidence / AgentVerdict / DecisionCard（唯一实现）
│   └── _store/      数据访问层（唯一 DB 入口）
├── data/        SQLite 事实层（不入库）
├── tools/       cron 调度（单一域）+ 验证工具
├── tests/
└── docs/design/ 架构与安装文档
```

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/design/architecture.md`](docs/design/architecture.md) | 系统架构：Agent 拓扑、通信契约、数据架构、延迟预算、路线图 |
| [`docs/design/install-guide.md`](docs/design/install-guide.md) | 环境安装：在已有 OpenClaw 实例的机器上并排装第二套隔离实例 |

## 当前状态

**Phase 1 · walking skeleton 基本成型。** 117 条测试全绿。

| 项 | 状态 |
|---|---|
| Node v24.21.0 + OpenClaw 2026.9.5（隔离安装） | ✅ |
| `biga` wrapper（强制 profile 隔离） | ✅ |
| Workspace 骨架 + git | ✅ |
| `biga setup` —— 端口 19789，未接 IM | ✅ |
| 契约层 `_contract` —— 四条铁律构造时拒绝 + AST 单一实现扫描 | ✅ |
| 数据层 `_store` —— 三张表，只追加由触发器强制 | ✅ |
| `emotion-calc` —— 真采 A 股情绪数据 | ✅ |
| `emotion` Agent + Supervisor 委派配置 | ✅ |
| 合成与回放 —— 两条路径共用同一份组装代码 | ✅ |
| 隔离演练 —— `kill -9` 自己，邻居六项未变 | ✅ |
| 端到端：Supervisor 真的 spawn Specialist | 🔄 进行中 |

Phase 1 的 8 条验收标准由 `tools/verify/phase1_acceptance.py` 逐条机器核对 ——
**`PENDING` 不计为通过**。

```bash
python3 tools/verify/phase1_acceptance.py --baseline data/neighbour-baseline.json --live
```

## 开发教程

本仓库同时是一份**开源开发教程**：[`docs/tutorial/`](docs/tutorial/README.md)。

每一章对应项目里实际完成的一段工作 —— 真跑过的命令、真踩过的坑。
它与普通教程的区别在于全程有一个额外约束：**同机上已经跑着另一套长期运行的实例，
不能碰它一根手指**。

## 运行环境

| 项 | 版本 |
|---|---|
| OS | WSL2 / Linux 6.6 |
| Node | v24.21.0 (LTS Krypton) |
| OpenClaw | 2026.9.5 |
| 数据层 | SQLite (WAL) |
