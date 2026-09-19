# EasyUp for BigA 2.0

基于 OpenClaw 的 Multi-Agent A 股短线**决策辅助**系统。

> **产品边界**：本系统不自动执行交易，不构成投资建议。
> 最终交易动作由人决定 —— AI 负责扩大认知，人负责最终决策。

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

**Phase 1 · 环境搭建中。**

| 项 | 状态 |
|---|---|
| Node v24.21.0 + OpenClaw 2026.9.5（隔离安装） | ✅ |
| `biga` wrapper（强制 profile 隔离） | ✅ |
| Workspace 骨架 + git | ✅ |
| `biga setup`（profile 初始化，端口 19789） | ⬜ |
| 契约层 `_contract` / 数据层 `_store` | ⬜ |
| 第一个 Specialist Agent + 端到端链路 | ⬜ |

## 运行环境

| 项 | 版本 |
|---|---|
| OS | WSL2 / Linux 6.6 |
| Node | v24.21.0 (LTS Krypton) |
| OpenClaw | 2026.9.5 |
| 数据层 | SQLite (WAL) |
