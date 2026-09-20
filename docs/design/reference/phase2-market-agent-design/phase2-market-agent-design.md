# EasyUp BigA Phase 2 与 Market Agent 技术设计

> Status: Draft  
> Scope: Phase 2 / Market Data / Market Agent  
> Project: `easyup168/easyup-biga`  
> Last Updated: 2026-09-21

---

# 1. 文档目的

本文档定义 EasyUp BigA Phase 2 的总体演进方向，以及 Phase 2 第一个新增 Specialist——`Market Agent` 的完整技术设计。

Phase 1 已经完成 Walking Skeleton，核心链路包括：

```text
隔离 OpenClaw
    ↓
Contract Layer
    ↓
Store Layer
    ↓
Evidence
    ↓
Emotion Skill
    ↓
Emotion Agent
    ↓
Supervisor
    ↓
DecisionCard
    ↓
Replay
    ↓
Isolation Test
    ↓
Latency Measurement
```

Phase 1 的目标是证明：

> BigA 的核心架构可以真实运行。

Phase 2 的目标则不同：

> 证明同一套 Contract / Store / Evidence / Agent / Supervisor 架构能够横向扩展到多个 Specialist，而不是只适配 Emotion Agent。

因此 Phase 2 不应优先继续增加新的基础设施，而应优先验证当前架构的可扩展性。

---

# 2. BigA 的核心系统定位

BigA 不应被定义为普通的“AI 炒股 Agent”。

更准确的定义是：

> **Evidence-driven Multi-Agent Decision System**

核心链路为：

```text
Raw Data
    ↓
Deterministic Skill
    ↓
Evidence
    ↓
Specialist Agent
    ↓
AgentVerdict
    ↓
Frozen Evidence
    ↓
Risk / Discipline
    ↓
Supervisor
    ↓
DecisionCard
    ↓
Replay
    ↓
Human
```

BigA 最重要的系统原则不是让模型“知道更多”，而是：

```text
每一个结论
都必须知道：

谁产生的
基于什么数据
什么时候产生
使用了什么算法
有哪些数据缺失
是否可以重放
```

---

# 3. BigA 的核心设计原则

## 3.1 UNKNOWN ≠ PASS

这是 BigA 最重要的系统规则。

```text
没有发现风险
```

和：

```text
没有足够数据判断风险
```

必须是两个完全不同的状态。

错误设计：

```text
missing data
    ↓
没有发现问题
    ↓
PASS
```

BigA 要求：

```text
missing data
    ↓
UNKNOWN
    ↓
missing[] 上浮
```

因此：

```text
UNKNOWN != PASS
UNKNOWN != NEUTRAL
UNKNOWN != ZERO
UNKNOWN != FALSE
```

未知必须作为一等状态存在。

## 3.2 确定性计算不得交给 LLM

凡是存在唯一确定答案的计算，应由 Python Skill 完成。

例如：

```text
成交额
上涨比例
市场宽度
MA5
RSI
MACD
涨跌家数
成交量变化
```

不应让 Agent 在 Prompt 中计算。

正确结构：

```text
Data
  ↓
Python
  ↓
Evidence
  ↓
Agent
  ↓
Interpretation
```

而不是：

```text
Raw Data
   ↓
LLM
   ↓
计算 + 判断 + 输出
```

Agent 的职责是解释 Evidence，而不是重新生成 Evidence。

---

# 4. Phase 2 Agent 范围

Phase 2 目标是补齐到 7 个 Agent 总数。

Phase 1：

```text
main / Supervisor
emotion
```

Phase 2 新增：

```text
market
sector
news
technical
risk
```

因此 Phase 2 完成后：

```text
main
emotion
market
sector
news
technical
risk
```

共 7 个 Agent。

`discipline` 不属于 Phase 2。

Discipline 进入 Phase 3。

原因是 Discipline 应有自己的独立输入源和约束逻辑，而不应为了凑齐 Agent 数量提前上线。

---

# 5. Phase 2 推荐开发顺序

推荐顺序：

```text
Emotion ✅
   │
   ▼
Market
   │
   ▼
Technical
   │
   ▼
Sector
   │
   ▼
News
   │
   ▼
Risk
   │
   ▼
Stage 1 Parallel
   │
   ▼
Frozen Evidence
   │
   ▼
Risk Stage 2
   │
   ▼
Full DecisionCard
```

原因：

```text
确定性较强
    ↓
数据复杂度增加
    ↓
外部依赖增加
    ↓
主观判断增加
    ↓
风险约束
```

Market 是 Phase 2 第一优先级。

---

# 6. 为什么首先开发 Market Agent

Market 与现有 Emotion Agent 的结构最接近：

```text
Data
 ↓
Deterministic Calculation
 ↓
Evidence
 ↓
Agent Interpretation
 ↓
AgentVerdict
```

因此 Market Agent 是检验 Phase 1 架构是否真正具有扩展能力的最佳测试对象。

如果新增 Market 时：

```text
_contract 无需大改
_store 无需大改
Supervisor 无需加入 Market 特判
DecisionCard 无需加入 Market 特判
```

说明 Phase 1 的抽象是成功的。

如果加入 Market 后必须大量修改基础设施，则说明 Phase 1 实际构建的是：

```text
Emotion-specific System
```

而不是：

```text
Multi-Agent Framework
```

因此 Market Agent 是 Phase 2 最关键的第二块砖。

---

# 7. Market Agent 的职责

Market Agent 回答的问题是：

> 当前整体市场处于什么状态？

Market Agent 不回答：

```text
应该买哪只股票？
```

不回答：

```text
哪个行业最好？
```

也不回答：

```text
某只股票是否突破？
```

这些属于其他 Specialist。

Market Agent 只负责：

```text
指数状态
成交量状态
市场宽度
上涨 / 下跌结构
涨停 / 跌停结构
整体风险偏好
市场活跃度
```

核心边界：

```text
Market = 整体环境
Sector = 资金方向
Technical = 个股技术结构
News = 信息催化
Emotion = 市场情绪
```

---

# 8. Market Agent 不应该做什么

Market Agent 禁止：

```text
选股
预测具体股票走势
计算技术指标
读取 News
分析行业板块
重新计算原始数据
修改 Evidence
自行补全缺失数据
把 UNKNOWN 转换为 NEUTRAL
```

尤其：

> Market Agent 不应该自行从多个数字推导出新的数值型指标。

所有数值型指标应由 `market-data` Skill 完成。

---

# 9. Market Data Skill 总体结构

建议目录：

```text
skills/
└── market-data/
    ├── SKILL.md
    ├── scripts/
    │   ├── fetch_market.py
    │   ├── normalize.py
    │   ├── calculate.py
    │   ├── snapshot.py
    │   └── __init__.py
    └── fixtures/
        ├── normal_market.json
        ├── weak_market.json
        ├── missing_market.json
        └── stale_market.json
```

职责拆分：

```text
fetch_market.py
    ↓
负责外部数据获取

normalize.py
    ↓
统一字段 / 单位 / 时间

calculate.py
    ↓
确定性计算

snapshot.py
    ↓
生成 Market Snapshot + Evidence
```

---

# 10. Market Data 第一版字段

第一版避免追求“指标越多越好”。

建议只保留高解释力字段。

## 10.1 指数

```text
上证指数
深证成指
创业板指
```

字段示例：

```text
index.shanghai.close
index.shanghai.change_pct

index.shenzhen.close
index.shenzhen.change_pct

index.chinext.close
index.chinext.change_pct
```

后续可以增加：

```text
沪深300
中证500
中证1000
科创50
```

但不建议第一版全部加入。

---

# 11. 成交量与成交额

核心字段：

```text
market.turnover
market.turnover_prev
market.turnover_change_pct
market.volume_state
```

示例：

```json
{
  "market.turnover": 1284300000000,
  "market.turnover_prev": 1092000000000,
  "market.turnover_change_pct": 17.61,
  "market.volume_state": "EXPANDING"
}
```

`volume_state` 必须由 Python 根据明确规则生成。

例如：

```text
EXPANDING
NORMAL
SHRINKING
UNKNOWN
```

规则必须版本化。

---

# 12. 上涨 / 下跌结构

字段：

```text
market.advance_count
market.decline_count
market.flat_count
```

例如：

```json
{
  "market.advance_count": 3312,
  "market.decline_count": 1746,
  "market.flat_count": 91
}
```

---

# 13. 市场宽度

Market Breadth：

```text
breadth =
advance_count /
(advance_count + decline_count)
```

由 Python Skill 计算。

字段：

```text
market.breadth
```

例如：

```json
{
  "market.breadth": 0.6548
}
```

禁止 Agent 自己计算。

---

# 14. 涨停 / 跌停结构

第一版：

```text
market.limit_up_count
market.limit_down_count
```

后续可以增加：

```text
market.limit_up_open_count
market.limit_up_fail_count
market.consecutive_limit_count
```

但 Phase 2 第一版应避免过度扩展。

---

# 15. Market Snapshot 建议结构

内部 Domain Object：

```json
{
  "as_of": "2026-09-21T10:30:00+08:00",
  "indices": {
    "shanghai": {
      "close": 3852.12,
      "change_pct": 0.82
    },
    "shenzhen": {
      "close": 12643.10,
      "change_pct": 1.34
    },
    "chinext": {
      "close": 2864.18,
      "change_pct": 1.78
    }
  },
  "turnover": {
    "value": 1284300000000,
    "previous": 1092000000000,
    "change_pct": 17.61,
    "state": "EXPANDING"
  },
  "breadth": {
    "advance": 3312,
    "decline": 1746,
    "flat": 91,
    "ratio": 0.6548
  },
  "limits": {
    "up": 77,
    "down": 8
  }
}
```

Snapshot 是 Domain Model。

Agent 不应直接依赖原始 Provider 数据结构。

---

# 16. Raw Data 与 Evidence 必须分离

推荐数据链：

```text
Provider Response
       ↓
Raw Snapshot
       ↓
Normalized Snapshot
       ↓
Calculated Fields
       ↓
Evidence
```

Raw 数据不能直接成为 Agent 输入。

原因：

```text
Provider 字段可能变化
Provider 单位可能变化
字段命名可能变化
数据结构可能变化
```

BigA 应通过 Normalize 层建立稳定内部协议。

---

# 17. Market Evidence

Market Skill 最终输出 Evidence。

建议字段：

```text
evidence_id
decision_id
evidence_set_id

agent_id
field
value
unit

source
as_of
retrieved_at

calc_version
raw_hash

status
missing
```

例如：

```json
{
  "evidence_id": "ev_market_breadth_001",
  "evidence_set_id": "es_20260921_103000",
  "agent_id": "market",
  "field": "market.breadth",
  "value": 0.6548,
  "unit": "ratio",
  "source": "market-provider",
  "as_of": "2026-09-21T10:30:00+08:00",
  "retrieved_at": "2026-09-21T10:30:03+08:00",
  "calc_version": "market-data@0.1.0",
  "raw_hash": "sha256:...",
  "status": "OK",
  "missing": []
}
```

---

# 18. Evidence Identity

Phase 2 建议正式强化 Evidence Identity：

```text
evidence_id
raw_hash
calc_version
evidence_set_id
```

这样 BigA 才能真正回答：

```text
这个 Decision
到底是基于哪一份数据？
```

---

# 19. Market Agent 输入

Market Agent 不直接读取 Provider。

输入应类似：

```json
{
  "agent": "market",
  "evidence_set_id": "es_20260921_103000_001",
  "evidence": [
    "...",
    "...",
    "..."
  ]
}
```

Market Agent 只允许：

```text
读取 Evidence
解释 Evidence
生成 Verdict
```

---

# 20. Market Agent 输出

统一输出 `AgentVerdict`。

建议：

```json
{
  "agent": "market",
  "status": "PASS",
  "stance": "BULLISH",
  "confidence": 0.78,
  "summary": "市场宽度较强，成交额明显放大，主要指数同步上涨。",
  "evidence_ids": [
    "ev_market_breadth_001",
    "ev_market_turnover_002",
    "ev_market_index_003"
  ],
  "missing": []
}
```

---

# 21. Status 与 Stance 分离

例如：

```text
status = PASS
stance = WEAK
```

表示：

```text
数据完整
判断有效
市场偏弱
```

而：

```text
status = UNKNOWN
stance = UNKNOWN
```

表示：

```text
数据不足
无法判断
```

因此：

```text
status
```

描述判断是否有效。

```text
stance
```

描述判断结果是什么。

两者不得混合。

---

# 22. Missing 设计

所有缺失必须使用机器可读取的代码。

建议：

```text
market.index.shanghai.missing
market.turnover.missing
market.breadth.insufficient_coverage
market.data.stale
market.provider.timeout
```

缺失不得转换为 0、false 或 neutral。

---

# 23. Market 数据新鲜度

Market Data 是强时效数据。

必须定义：

```text
as_of
retrieved_at
```

并定义 freshness policy。

盘中初始示例：

```text
<= 120 seconds → FRESH
```

超过阈值：

```text
STALE
```

具体阈值未来可根据真实运行调整。

关键是阈值必须机器化和版本化。

---

# 24. Provider 失败策略

第一版建议明确区分：

```text
NETWORK_ERROR
TIMEOUT
INVALID_RESPONSE
EMPTY_RESPONSE
RATE_LIMIT
STALE_DATA
PARTIAL_DATA
```

不要统一转换成 `None`。

Provider 与 Domain 必须解耦：

```text
providers/
    ↓
normalizer
    ↓
market domain
    ↓
evidence
```

---

# 25. Market Data 版本化

建议：

```text
market-data@0.1.0
```

所有计算结果记录：

```text
calc_version
```

算法修改后升级版本。

Replay 必须知道历史 Decision 使用的是哪个算法。

---

# 26. Market Agent Prompt 原则

Market Agent 的系统指令应围绕：

```text
1. 只使用提供的 Evidence
2. 不自行搜索数据
3. 不执行算术
4. 不补全缺失数据
5. missing 非空时不得假装数据完整
6. 引用 Evidence ID
7. 输出严格 AgentVerdict
```

核心业务约束尽量由 Contract 强制，而不是只写在 Prompt 中。

---

# 27. Prompt Rule 与 Contract Rule

优先级：

```text
Prompt Rule
    ↓
Runtime Validation
    ↓
Contract Validation
    ↓
Database Invariant
```

越重要的规则越不应仅依赖 Prompt。

---

# 28. Store Layer

Phase 2 不建议采用 `market_agent_table` 这种 Agent-centric 设计。

推荐：

```text
Raw
↓
Derived
↓
Evidence
↓
Agent Run
↓
Decision
```

例如：

```text
raw_market_snapshot
derived_market_snapshot
agent_runs
decision_records
```

Agent 与数据层之间的主要接口继续保持 Evidence Contract。

---

# 29. Market Agent 测试结构

推荐：

```text
tests/

market/
├── test_fetch_market.py
├── test_normalize_market.py
├── test_market_calculation.py
├── test_market_evidence.py
├── test_market_agent_contract.py
├── test_market_missing.py
├── test_market_stale.py
└── test_market_e2e.py
```

重点测试：

```text
breadth 计算
turnover change
volume state
index normalization
limit structure
missing
stale
partial response
Evidence 引用真实性
AgentVerdict 契约
```

---

# 30. Hallucinated Evidence Test

如果 Agent 返回：

```text
ev_market_fake_999
```

而该 Evidence 不存在，系统必须拒绝。

用于防止 LLM 引用不存在的 Evidence。

---

# 31. Market E2E 测试

完整链路：

```text
Fixture
   ↓
fetch / load
   ↓
normalize
   ↓
calculate
   ↓
Evidence
   ↓
Market Agent
   ↓
AgentVerdict
   ↓
Store
```

验证运行、落库、Evidence、Verdict 与引用关系。

---

# 32. OpenClaw Runtime 验证

Supervisor 声称调用 Market Agent 不代表 Market Agent 真运行。

必须继续检查 OpenClaw 自己的 runtime 证据，例如：

```text
subagent_runs
```

---

# 33. Market Agent Exit Criteria

```text
[ ] market-data Skill 可独立运行
[ ] 能产生稳定 Market Snapshot
[ ] 所有确定性指标由 Python 计算
[ ] Snapshot 可以转化为 Evidence
[ ] Evidence 有 source / as_of / retrieved_at
[ ] Evidence 有 calc_version
[ ] Evidence 有 raw_hash
[ ] Market Agent 只消费 Evidence
[ ] Agent 不重新拉取 Market Data
[ ] Agent 输出合法 AgentVerdict
[ ] missing 能产生 UNKNOWN
[ ] stale data 能被识别
[ ] Evidence ID 可追踪
[ ] OpenClaw runtime 能证明真实 spawn
[ ] Verdict 可以落库
[ ] Verdict 可以进入 DecisionCard
[ ] Replay 不重新拉取数据
```

---

# 34. Phase 2 并行架构

Stage 1：

```text
                 ┌── Market
                 ├── Sector
Supervisor ──────┼── News
                 ├── Technical
                 └── Emotion
```

必须并行。

Stage1 Latency 应接近：

```text
max(
  market,
  sector,
  news,
  technical,
  emotion
)
```

而不是所有 Agent 延迟之和。

---

# 35. Frozen Evidence

Stage 1 完成后必须形成 Frozen Evidence Set：

```text
Market
Sector
News
Technical
Emotion
      ↓
Frozen Evidence Set
```

冻结后：

```text
禁止修改
禁止覆盖
禁止 Stage 2 重采
```

Risk 只能读取 Frozen Stage 1 Evidence。

---

# 36. Replay

Replay 必须：

```text
读取历史 Frozen Evidence
        ↓
重新执行 Agent / Supervisor
```

禁止重新 fetch 外部数据。

否则不是 Replay，而是重新进行一次新的分析。

---

# 37. Failure-First Testing

Phase 2 测试不只证明正常数据下系统能运行，更应该证明：

> 数据异常时系统不会说谎。

至少主动构造：

```text
Market provider timeout
Sector coverage 低
News stale
Technical K-line 缺失
Emotion source unavailable
Risk input incomplete
```

真实运行过程中应确保 `missing[]` 确实能够出现，而不是永远只存在于单元测试中。

---

# 38. Phase 2 Latency 与 Cost

完成 5 个 Specialist 后：

```text
真实运行
     ↓
收集足够样本
     ↓
P50 / P95
     ↓
制定正式 latency budget
```

同时记录：

```text
agent_name
model
input_tokens
output_tokens
latency
cost
decision_id
```

用于判断不同 Agent 的性能与成本。

---

# 39. Phase 2 Acceptance

建议新增：

```text
tools/verify/phase2_acceptance.py
```

运行：

```bash
python3 tools/verify/phase2_acceptance.py --live
```

验收：

```text
[ ] main + 6 specialists = 7 agents
[ ] Market Agent 正常运行
[ ] Technical Agent 正常运行
[ ] Sector Agent 正常运行
[ ] News Agent 正常运行
[ ] Risk Agent 正常运行
[ ] Stage 1 五 Agent 真并行
[ ] Stage 1 Evidence 被冻结
[ ] Risk 只消费 Frozen Evidence
[ ] 每个 Agent 输出 AgentVerdict
[ ] 每个数字可追溯 Evidence
[ ] UNKNOWN 在真实失败场景出现
[ ] missing[] 真实出现 >= 5 次
[ ] Full DecisionCard 可生成
[ ] DecisionCard 可落库
[ ] DecisionCard 可 replay
[ ] Replay 不重新采集数据
[ ] subagent_runs 能证明真实 Agent spawn
[ ] P50 / P95 latency 已测量
[ ] token / cost 已测量
[ ] production OpenClaw isolation test 仍通过
```

---

# 40. Market Agent 第一阶段实施顺序

建议 Claude Code 按以下顺序开发。

## M1 — Domain Definition

定义：

```text
MarketSnapshot
MarketEvidence
MarketStance
MarketStatus
MissingCode
```

不连接真实 Provider。

## M2 — Fixtures

创建：

```text
normal_market.json
weak_market.json
missing_market.json
stale_market.json
```

## M3 — Normalizer

实现：

```text
Provider Payload
      ↓
Canonical Market Snapshot
```

## M4 — Calculations

实现：

```text
breadth
turnover_change_pct
volume_state
```

全部配套单元测试。

## M5 — Evidence Builder

```text
MarketSnapshot
      ↓
Evidence[]
```

加入：

```text
evidence_id
raw_hash
calc_version
```

## M6 — Market Agent

建立 OpenClaw Market Agent。

只接 Fixture Evidence。

暂时不接真实行情。

## M7 — Contract Validation

验证：

```text
UNKNOWN
missing
Evidence reference
AgentVerdict
```

## M8 — Runtime Spawn

由 Supervisor 真实 spawn Market Agent，并检查 OpenClaw runtime。

## M9 — Real Provider

最后连接真实 Market Provider。

开发原则：

> **Offline First, Live Last.**

---

# 41. Phase 2 的真正目标

Phase 2 的重点不是再增加 5 个 Agent。

而是验证：

> 新增 Specialist 不需要修改 BigA 的核心架构。

理想状态：

```text
新增 Agent
    =
新增 Skill
+ 新增 Agent
+ 新增 Domain Tests
```

而不是：

```text
新增 Agent
    =
修改 Contract
+ 修改 Store
+ 修改 Supervisor
+ 修改 DecisionCard
+ 修改 Replay
+ 大量特殊逻辑
```

前者说明架构可扩展。

后者说明架构仍然 Agent-specific。

---

# 42. 最终原则

BigA Phase 2 开发应始终坚持：

```text
UNKNOWN != PASS

Missing != Zero

Agent != Calculator

Evidence != Prompt Text

Supervisor != Data Source

Replay != Re-fetch

Risk != Re-analysis

Raw Data != Agent Input

LLM Output != Evidence

Agent Claim != Runtime Proof
```

BigA 的目标不是最大化 Agent 数量。

而是建立：

> 一个任何结论都可以被追问、验证、回放和解释的决策系统。

Market Agent 是 Phase 2 验证这一目标的第一步。
