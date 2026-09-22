# BagA 全系统架构设计文档

> 📄 **只读** · 外部材料，**永不修改**
> **覆盖**：BagA 全系统目标架构与八阶段演进路线（2026-09-23 收到）—— 数据 / 多 Agent / 选股 / 策略 / 回测 / 计划 / 盘中 / 风控 / 执行 / 组合 / 复盘 / Web ｜ **不覆盖**：当前阶段的施工范围与判据 —— 它的 §41 Stage 1 由 [`../design/deterministic-orchestration.md`](../design/deterministic-orchestration.md) 施工，复核与采纳分界见那份的 §0


> Project: BagA / EasyUp BigA
> Date: 2026-09-23
> Status: Target Architecture / Evolution Blueprint
> Foundation: OpenClaw
> Scope: Data / Multi-Agent / Screening / Strategy / Backtest / Planning / Realtime / Risk / Execution / Portfolio / Review / Web / Jobs / Operations

---

# 1. 系统定位

BagA 的最终定位不是一个单纯的多 Agent Demo，也不是一个只会生成 DecisionCard 的 AI 应用。

它的长期目标是：

> **一个建立在 OpenClaw 之上的个人 A 股研究、数据、选股、作战计划、盘中监控、AI 决策、交易执行、仓位管理、复盘、回测和持续改进平台。**

当前正在开发的多 Agent DecisionCard 闭环，是整个系统的第一个完整 Vertical Slice。

```text
当前 BigA
= 整个 BagA 的第一块闭环能力

未来 BagA
= 把所有研究、分析、交易与复盘能力逐步统一到同一个平台
```

# 2. 核心架构原则

```text
OpenClaw 管 Agent 生命周期
BagA 管业务生命周期

程序决定流程
Agent 决定判断

数据先冻结
Agent 后分析

Decision != Order
Order != Fill
Fill != Position

UNKNOWN != PASS

一个 Run
一个 EvidenceSet

历史产物不可覆盖
修订必须版本化

Replay 不重新抓外部数据

Agent 不直接访问 Provider

Agent 不直接写业务数据库

Risk Engine 有权否决 Agent

systemd / Cron 只负责 Trigger
不负责业务编排
```

# 3. OpenClaw 在系统中的定位

OpenClaw 是 BagA 的 Agent Runtime Foundation，负责：

```text
Agent Session
模型调用
Tool Policy
Skill
Subagent
Runtime Run ID
Agent Timeout
Agent Lifecycle
```

BagA 负责：

```text
Trigger
Job
Run
状态机
Data Platform
EvidenceSet
Strategy
Screening
Backtest
Risk
Order
Portfolio
Review
Web
Feishu
Audit
```

最终关系：

```text
                    OpenClaw
             Agent Runtime Foundation
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
      Market        Emotion        Review
       Agent          Agent          Agent
         │             │             │
         └─────────────┼─────────────┘
                       ▼
                Structured Outcomes
                       │
┌──────────────────────┴──────────────────────┐
│                    BagA                     │
│ Data Platform                              │
│ Orchestration                              │
│ Screening                                  │
│ Strategy / Backtest                        │
│ Planning / Tracking                        │
│ Risk                                       │
│ OMS / Portfolio                            │
│ Review                                     │
│ Web / Feishu / Jobs                        │
└────────────────────────────────────────────┘
```

# 4. 系统总体分层

BagA 建议分为 8 个主要平面：

1. Trigger Plane：Feishu / CLI / Cron / systemd / Webhook / Web API，统一转成 TriggerRequest。
2. Control Plane：Job Definition / Run Identity / State Machine / Retry / Concurrency / Budget / Circuit Breaker / Audit。
3. Data Plane：Provider / RawArtifact / Normalize / Quality / Dataset / Snapshot / Point-in-time / Revision / Lineage。
4. Intelligence Plane：FactBundle / EvidenceSet / OpenClaw Agents / AgentAssessment / AgentOutcome / DecisionCard。
5. Research Plane：Feature / Factor / Screening / Strategy / Backtest / Simulation / Performance Evaluation。
6. Trading Plane：TradingPlan / Realtime Monitoring / Signal / Risk / OrderIntent / OMS / Broker / Fill / Portfolio。
7. Review Plane：Decision / Trade / Strategy / Agent / Weekly / Monthly Review。
8. Delivery Plane：Feishu / Web / Realtime UI / Reports / Alerts / Operations Dashboard。

# 5. 全局主业务链

```text
Data
    ↓
Features
    ↓
Screening
    ↓
CandidateSet
    ↓
TradingPlan
    ↓
Realtime Tracking
    ↓
Signal
    ↓
Decision / Agent
    ↓
Risk
    ↓
OrderIntent
    ↓
OMS
    ↓
Broker
    ↓
Fill
    ↓
Portfolio
    ↓
Review
    ↓
Backtest / Strategy Improvement
```

# 6. 三条核心业务链

## 6.1 批处理研究链

```text
收盘数据采集
→ 数据质量
→ Feature / Factor
→ 全市场选股
→ CandidateSet
→ 策略评估
→ 次日盘前作战计划
```

## 6.2 盘中实时链

```text
Realtime Feed
→ Realtime Snapshot
→ Market / Emotion
→ WatchItem Tracking
→ Signal
→ Risk
→ 提醒 / OrderIntent
```

## 6.3 复盘学习链

```text
Fill / Position
→ Trade Review
→ Decision Review
→ Strategy Metrics
→ Agent Metrics
→ Improvement Proposal
```

# 7. 数据平台设计

标准链路：

```text
Provider
→ RawArtifact
→ Normalizer
→ Quality Validator
→ Published Dataset
→ Snapshot
→ FactBundle
→ EvidenceSet
→ Agent
```

a-stock-data 的定位：

```text
Provider Catalog
接口参考
Fallback 参考
数据源踩坑知识库
```

正确关系：

```text
a-stock-data
→ BagA Provider Adapter
→ RawArtifact
→ Normalizer
→ Quality
→ Snapshot
```

Snapshot 状态：

```text
COLLECTING
VALIDATING
COMPLETE
PARTIAL
QUARANTINED
FAILED
SUPERSEDED
```

EvidenceSet 原则：

```text
Run A → EvidenceSet A
Run B → EvidenceSet B
```

# 8. 多 Agent 架构

平台 Agent 可以不断增加：

```text
Market
Emotion
Sector
News
Technical
Macro
Valuation
CapitalFlow
Event
Risk
Review
Discipline
Portfolio
```

但每个 Pipeline 版本的 Agent roster 必须固定并版本化。

# 9. Agent Registry

每个 Agent 声明：

```text
agent_id
stage
required
required_datasets
input_schema
output_schema
timeout
tool_policy
model_profile
```

Agent 只声明需要哪些 Dataset，不声明 Provider。

# 10. Pipeline Registry

定义：

```text
pipeline_id
pipeline_version
stage
agent roster
required policies
timeout
output contract
```

Orchestrator 根据 Pipeline 汇总 required_datasets，准备 EvidenceSet，再启动 OpenClaw Agents。

# 11. DecisionOrchestrator

核心原则：

```text
Program owns workflow.
Agent owns judgment.
```

建议状态：

```text
RECEIVED
PREFLIGHTED
SNAPSHOT_COLLECTING
SNAPSHOT_FROZEN
STAGE1_RUNNING
STAGE1_COMPLETED
RISK_RUNNING
SYNTHESIZING
CARD_PERSISTED
NOTIFICATION_PENDING
COMPLETED
FAILED
TIMEOUT
CANCELLED
INPUT_REQUIRED
```

# 12. Run Identity

必须明确：

```text
trigger_id
decision_id
run_id
runtime_run_id
evidence_set_id
verdict_id
decision_record_id
```

完整链：

```text
Trigger
→ Decision
→ Run
→ EvidenceSet
→ RuntimeRun
→ AgentOutcome
→ DecisionCard
```

# 13. Screening Engine

选股是一级模块：

```text
Universe
→ 基础过滤
→ Feature / Factor
→ 规则筛选
→ 排序
→ CandidateSet
```

核心对象：

```text
ScreenDefinition
ScreenRun
Candidate
CandidateSet
```

原则：

```text
Python / DuckDB 负责全市场筛选
Agent 负责候选股语义分析与解释
```

# 14. Strategy Engine

核心对象：

```text
StrategyDefinition
StrategyVersion
EntryRules
ExitRules
InvalidationRules
PositionRule
RiskProfile
```

同一 StrategyDefinition 应被选股、回测、盘前计划、盘中监控、模拟交易、实盘共同使用。

# 15. Backtest Engine

```text
StrategyVersion
→ Historical Universe
→ Historical Features
→ Signals
→ Risk Rules
→ SimulationBroker
→ Order / Fill
→ Portfolio Ledger
→ Performance Report
```

必须考虑：

```text
手续费
印花税
滑点
涨跌停
停牌
T+1
最小交易单位
历史股票池
历史板块成分
复权
```

# 16. Planning Engine

盘前计划应结构化。

核心对象：

```text
TradingPlan
PlanTarget
MarketScenario
EntryRule
ExitRule
InvalidationRule
PositionLimit
```

# 17. Realtime Monitoring

盘中热路径：

```text
Realtime Feed
→ LatestQuoteStore
→ Realtime Features
→ Rule Evaluator
→ Signal / Alert
```

Agent 不参与每个 Tick。重要事件发生时，冻结 Intraday Snapshot，再调用 OpenClaw Agent。

# 18. Tracking Engine

追踪拆成四种：

- Candidate Tracking
- Plan Tracking
- Trade Tracking
- System Tracking

Candidate 状态示例：

```text
DISCOVERED
QUALIFIED
PLANNED
WATCHING
TRIGGERED
REJECTED
EXPIRED
```

Trade 状态示例：

```text
PLANNED
SIGNALLED
APPROVED
ENTERED
PARTIALLY_EXITED
EXITED
REVIEWED
```

# 19. WatchItem

核心字段：

```text
watch_id
instrument_id
candidate_set_id
plan_id
state
thesis
entry_rules
exit_rules
invalidation_rules
valid_until
```

# 20. Risk Engine

三层：

```text
Pre-Decision Risk
Pre-Trade Risk
Post-Trade Risk
```

确定性代码负责：

```text
身份
完整度
数据新鲜度
仓位
资金
停牌
涨跌停
单票上限
行业集中度
每日亏损
订单过期
```

Agent 只负责风险解释。

# 21. OrderIntent

```text
DecisionCard
→ OrderIntent
→ PreTradeRiskCheck
→ Approval
→ OrderRequest
```

# 22. OMS

订单状态：

```text
CREATED
RISK_CHECKING
RISK_REJECTED
AWAITING_APPROVAL
APPROVED
SUBMITTING
SUBMITTED
PARTIALLY_FILLED
FILLED
CANCEL_PENDING
CANCELLED
REJECTED
EXPIRED
UNKNOWN
```

核心身份：

```text
intent_id
client_order_id
broker_order_id
```

# 23. Broker Adapter

统一接口：

```text
get_account
get_positions
submit_order
cancel_order
get_orders
get_fills
```

支持：

```text
SimulationBroker
PaperBroker
LiveBroker
```

# 24. Portfolio & Accounting

核心对象：

```text
AccountSnapshot
Position
PositionLot
CashLedger
Fill
Fee
CorporateAction
```

Broker Fill / Position 是账户事实来源。

# 25. Review Pipeline

```text
DecisionCard
→ ReviewPlan
→ Outcome Snapshot
→ Metrics
→ Optional Review Agent
→ ReviewCard
```

支持：

```text
T+1
T+5
T+20
Weekly
Monthly
```

历史 DecisionCard 永不修改。

# 26. Review 类型

```text
Decision Review
Trade Review
Strategy Review
Agent Review
Operational Review
```

# 27. Feature / Factor Engine

确定性计算：

```text
MA
MACD
RSI
ATR
Volatility
Breadth
Volume/Price
Emotion
Momentum
Valuation
Quality
Capital Flow
```

输出：

```text
FeatureSet
FactorSet
```

# 28. Job 架构

Data Jobs：

```text
security-master-sync
trading-calendar-sync
eod-daily-bars
emotion-close
news-ingestion
```

Research Jobs：

```text
factor-build
screening
backtest
```

Decision Jobs：

```text
premarket-plan
daily-close-decision
symbol-analysis
```

Review Jobs：

```text
due-review
weekly-review
monthly-review
```

Maintenance Jobs：

```text
provider-health
data-quality
backup
stale-run-reaper
outbox-delivery
```

# 29. Trigger 架构

所有 Feishu / CLI / Cron / systemd / Web / Webhook 最终统一成 TriggerRequest。

Trigger 不包含业务编排逻辑。

# 30. Event Architecture

第一版：

```text
SQLite Transactional Outbox
+
In-process Event Dispatcher
```

主要事件：

```text
DatasetPublished
SnapshotCompleted
CandidateSetReady
PlanPublished
SignalTriggered
RiskRejected
OrderSubmitted
FillReceived
PositionChanged
ReviewDue
ReviewCompleted
```

# 31. Web 架构

主要页面：

```text
Market
Screening
Planning
Trading
Research
Review
Operations
```

# 32. 实时数据层

未来 Hot Path：

```text
Realtime Provider
→ Normalizer
→ RealtimeQuoteStore
→ WebSocket / SSE
→ Web
```

初期 In-memory，后续多进程再考虑 Redis。

# 33. 数据库与存储

当前推荐：

```text
SQLite
→ Control Plane

Parquet
→ Historical Data Plane

DuckDB
→ Analytics Query

Raw Files
→ Provider Archive
```

暂不需要 PostgreSQL / Kafka / ClickHouse / Kubernetes。

# 34. Registry 体系

最终建议 6 个 Registry：

```text
Dataset Registry
Provider Registry
Agent Registry
Pipeline Registry
Strategy Registry
Job Registry
```

# 35. Tool Policy

自动 Pipeline Agent：

```text
deny ask_user
deny external fetch
deny direct DB write
deny pipeline entrypoint
deny broker execution
```

Operator Agent 可有不同策略。

# 36. Operator Agent

负责：

```text
飞书聊天
参数补齐
查询状态
解释结果
```

Specialist Agent 用于自动 Pipeline。

# 37. 数据源策略

每个 Dataset 支持：

```text
Primary
Fallback
Validator
```

来源冲突严重：

```text
Snapshot = QUARANTINED
```

# 38. Point-in-time

必须保留：

```text
event_time
as_of
published_at
available_at
retrieved_at
processed_at
```

Replay / Backtest：

```text
available_at <= knowledge_cutoff
```

# 39. 安全与交易边界

未来实盘必须包括：

```text
Secret Management
Kill Switch
Human Approval
Broker Reconciliation
Audit
Read-only / Trading Permission Separation
```

# 40. 运维与可观测性

监控：

```text
Provider success
Snapshot completeness
Fallback usage
Agent latency
Agent cost
UNKNOWN rate
Job success
Order reject
Fill rate
Slippage
Reconciliation mismatch
Notification success
Stale runs
```

# 41. 架构演进路线

Stage 1：

```text
Run Provenance
DecisionOrchestrator
OpenClaw Runtime Adapter
Frozen EvidenceSet
```

Stage 2：

```text
Data Platform
Dataset / Provider Registry
EOD
Emotion Migration
```

Stage 3：

```text
Screening
CandidateSet
TradingPlan
WatchItem
```

Stage 4：

```text
Realtime Market
Signal Engine
Intraday Snapshot
```

Stage 5：

```text
Portfolio Ledger
Risk Engine
Paper OMS
```

Stage 6：

```text
Backtest
Strategy Registry
SimulationBroker
```

Stage 7：

```text
Review
Agent Evaluation
Discipline
```

Stage 8：

```text
Web
Broker Adapter
Controlled Live Trading
```

# 42. 当前 BigA 与未来 BagA 的关系

当前多 Agent DecisionCard 闭环不是最终产品，它是未来 BagA 的 Decision / Agent Kernel。

未来功能不是另建系统，而是在这个 Kernel 周围逐步加入：

```text
Data
Research
Screening
Planning
Realtime
Risk
OMS
Portfolio
Review
Web
```

# 43. 当前开发短期重点

```text
1. Run Provenance Closure
2. EvidenceSet 绑定 Run
3. Verdict / Card 绑定 Run
4. Runtime Adapter Live 验证
5. Snapshot D-II
6. Dataset / Provider Registry
7. 第一个真实 Data Vertical Slice
```

# 44. 第一版数据 Vertical Slice

推荐：

```text
index_daily
```

完整链：

```text
Provider
→ Raw
→ Normalize
→ Quality
→ Snapshot
→ EvidenceSet
→ Market / Technical / Sector
```

# 45. 第一版完整市场数据

随后：

```text
Security Master
Trading Calendar
Tradability
Adjustment Factors
EOD Daily Bars
Emotion Close
```

# 46. 第一版选股闭环

```text
EOD Snapshot
→ FeatureSet
→ Screening
→ CandidateSet
→ Agent Evaluation
→ TradingPlan
```

# 47. 第一版盘中闭环

```text
Realtime Quote
→ Intraday Snapshot
→ WatchItem
→ Signal
→ Decision / Risk
→ Alert
```

# 48. 第一版交易闭环

先 Paper：

```text
DecisionCard
→ OrderIntent
→ Risk
→ Paper OMS
→ Paper Fill
→ Portfolio
→ Review
```

# 49. 第一版回测闭环

```text
Historical Snapshot
→ Strategy
→ Signal
→ SimulationBroker
→ Portfolio
→ Performance Report
```

# 50. 第一版复盘闭环

```text
Decision / Trade
→ Outcome Snapshot
→ Metrics
→ ReviewCard
→ Weekly Review
```

# 51. 不应提前引入

当前不需要：

```text
Kafka
Kubernetes
微服务
多个数据库服务
复杂分布式锁
高频 Tick 平台
全自动实盘
```

# 52. 最终架构目标

最终 BagA 应成为：

> **一个以 OpenClaw 为智能执行底座、以统一数据平台为事实基础、以确定性编排和风险引擎为控制核心、覆盖研究、选股、计划、盘中跟踪、交易、复盘、回测和 Web 的个人 A 股交易操作系统。**

完整闭环：

```text
Data
→ Research
→ Screening
→ Planning
→ Realtime Tracking
→ AI Decision
→ Risk
→ Execution
→ Portfolio
→ Review
→ Backtest / Improvement
```

# 53. 最终原则

```text
OpenClaw is the intelligence runtime.

BagA is the trading operating system.

Data is shared truth.

Agents are specialized interpreters.

Programs own workflow.

Risk owns veto power.

Snapshots are immutable.

Runs are isolated.

Strategies are versioned.

Orders are auditable.

Reviews never rewrite history.

Every important artifact is traceable.
```
