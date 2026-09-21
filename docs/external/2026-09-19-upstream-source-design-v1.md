# EasyUp for BigA 2.0 — 上游参考设计 V1.0

> 📄 **只读** · 外部材料，**永不修改**
> **覆盖**：上游需求文档 v1（2026-09-19 收到） ｜ **不覆盖**：本项目的任何裁定 —— 与它冲突之处见 [`../design/architecture.md`](../design/architecture.md) 的「刻意偏离」


> 本文是本项目的**上游需求文档**，由 `EasyUp_for_BigA_2.0_OpenClaw_多Agent生产架构设计_V1.0.docx` 转换而来。
> 原样保留，**不做修改** —— 实现与它的每一处偏离都在 `docs/design/architecture.md` 里显式标注并说明理由。

---

文档定位：本方案用于 EasyUp for BigA 2.0 的总体技术架构、Agent 职责划分、通信机制、数据架构及第一阶段落地规划。系统定位为交易决策辅助与技术研究系统，不自动执行交易，不构成投资建议。
# 1. 产品定位与核心理念
EasyUp for BigA 2.0 不以“AI预测某只股票涨跌”为核心，而是构建一个由多个专业 Agent 协同完成市场观察、共识识别、技术分析、事件验证、风险审查和交易纪律检查的 A 股短线决策系统。
产品定位：AI Agent × A股短线决策系统
核心理念：发现共识，锁定核心，让每一笔交易都有逻辑。
技术底座：OpenClaw Agent Runtime / Gateway
决策原则：AI 扩大认知，人类保留最终决策权。
产品边界：不自动下单；不以模型输出的单一分数替代事实证据和风险判断。
# 2. 总体系统架构
User / Feishu / Web / CLI / API
        ↓
OpenClaw Gateway（Session / Routing / Auth）
        ↓
BIGA SUPERVISOR（任务编排 / 证据汇总 / 决策协调）
        ↓
┌────────┬────────┬────────┬────────┬────────┐
Market   Sector   News     Technical Emotion
Agent    Agent    Agent    Agent     Agent
        ↓
Risk Agent → Trading Discipline Agent
        ↓
BigA Decision Engine → Decision Card
        ↓
Human-in-the-loop（最终决策）
        ↓
Daily Review → Skill / Knowledge Evolution
# 3. 核心设计原则
职责隔离：每个 Agent 只负责明确领域，避免一个 Agent 包揽行情、新闻、技术和风控。
Supervisor 编排：Supervisor 负责拆任务、调度 Specialist、汇总证据、触发补充验证及最终协调，不直接承担全部分析。
数据与智能分离：Python/数据服务负责抓取、清洗、指标计算；LLM/Agent 负责理解、解释、关联和推理。
风险与机会分离：Risk Agent 和 Trading Discipline Agent 属于制衡层，专门寻找风险和逻辑漏洞。
结构化通信：Agent 之间通过结构化消息传递 task_id、时间戳、结果、置信度、证据和警告，而不是互相发送无约束长文本。
事实可追溯：关键分析结果必须保留 evidence、数据时间和来源，便于复盘、回放、审计和回测。
Human-in-the-loop：第一阶段不做自动交易，最终交易动作由人决定。
# 4. 七大核心 Agent
BigA Supervisor： 总指挥。接收用户问题，判断需要哪些 Agent，创建任务、收集结果、检查完整性、组织风险审查，并生成最终 Decision。
Market Agent： 回答“市场现在是什么状态”。分析指数、成交额、涨跌结构、涨停/跌停、炸板、连板高度、市场宽度、量能等。不负责选股。
Sector Agent： 识别市场资金与共识方向。分析行业/概念强度、成交额、涨停数量、核心股、持续性和扩散程度。
News Agent： 负责政策、产业、公司公告、海外事件、商品及宏观新闻；重点做时间戳、来源、相关行业/公司、影响方向和新鲜度验证。
Technical Agent： 负责 MA5/10/20、成交量、MACD、RSI、突破、压力位、支撑位、前高、换手及量价关系。指标由 Python 计算，Agent 负责解释。
Emotion Agent： 分析涨停、跌停、炸板率、最高板、连板、昨日强势股表现、大面数量、上涨/下跌家数等，形成情绪周期判断。
Risk Agent： 专门寻找交易风险，并可拥有否决/拦截权。检查市场风险、板块退潮、个股高位、量价异常、流动性、事件不确定性和风险收益结构。
Trading Discipline Agent： 检查 FOMO、追高、冲动交易、连续亏损后的翻本冲动、偏离交易计划、缺少止损和仓位失控等行为风险。
# 5. Agent 分层
分析层
├── Market Agent
├── Sector Agent
├── News Agent
├── Technical Agent
└── Emotion Agent

制衡层
├── Risk Agent
└── Trading Discipline Agent

协调层
└── BigA Supervisor

决策层
└── BigA Decision Engine
# 6. Agent 通信机制
推荐采用“Supervisor → Specialist → Evidence → Risk → Discipline → Supervisor”的星型协作模式，而不是 Agent 之间任意互聊。这样可以控制通信复杂度、责任边界和审计难度。
示例任务消息：
{
  "task_id": "BIGA-20260918-001",
  "requester": "supervisor",
  "target": "market",
  "task": "market_snapshot",
  "timestamp": "2026-09-18T09:35:00+09:00",
  "market": "CN_A_SHARE",
  "required_fields": [
    "breadth", "limit_up", "limit_down",
    "broken_board", "turnover", "emotion"
  ]
}
示例返回消息：
{
  "task_id": "BIGA-20260918-001",
  "agent": "market",
  "status": "completed",
  "result": {
    "regime": "neutral",
    "breadth": "weak",
    "liquidity": "medium",
    "emotion": "divergence"
  },
  "confidence": 0.82,
  "evidence": [
    "market_snapshot:xxx",
    "emotion_snapshot:xxx"
  ],
  "warnings": []
}
# 7. OpenClaw 中的通信与编排
OpenClaw 负责 Agent Runtime、Gateway、Session、Routing 和 Agent 间协作。根据当前 OpenClaw 的能力，第一版可使用跨 Agent session 能力进行任务发送与历史读取，并在需要复杂、临时研究任务时创建临时子 Agent。
推荐模式：Supervisor 使用 sessions_send 调用固定 Specialist；对临时的验证、研究或数据核验任务使用 sessions_spawn。生产环境不建议把 OpenClaw Session 当作唯一数据库。
# 8. 数据架构
数据源
  ↓
Data Collector / Python
  ↓
Data Normalizer
  ↓
BigA Data Store
  ├── PostgreSQL：历史事实、行情快照、分析结果、Evidence、复盘记录
  └── Redis：实时状态、缓存、Agent 状态、事件

BigA Data Store
  ↓
OpenClaw Tools / Skills
  ↓
Specialist Agents
  ↓
Supervisor
关键原则：AI 不直接负责所有数据抓取；Python/Data Service 负责确定性数据处理，Agent 负责语义分析和跨来源推理。
# 9. 新闻与数据新鲜度机制
所有新闻保存 published_at / source / retrieved_at。
News Agent 必须先判断新闻是否属于当前交易窗口。
重要事件必须进行来源核验，避免旧新闻被当成最新催化。
行情指标必须记录计算时间，避免盘中数据与收盘数据混用。
Supervisor 在证据时间不一致时触发补充查询，而不是强行汇总。
# 10. Risk Agent 与 Trading Discipline Agent
Risk Agent 负责“市场与交易风险”，Trading Discipline Agent 负责“人的行为与纪律风险”。两者均具有制衡性质。
Risk Check
├── 市场环境风险
├── 板块退潮风险
├── 个股位置风险
├── 量价异常
├── 流动性
├── 事件不确定性
└── 风险收益结构

Discipline Check
├── FOMO
├── 追高
├── 连亏翻本
├── 偏离计划
├── 无止损
└── 仓位失控
两者均可以输出 BLOCK / WARNING / PASS 等结构化状态。最终是否执行交易仍由人决定。
# 11. BigA Decision Card
BIGA DECISION CARD

市场环境：中性
板块：强
个股趋势：良好
情绪：分歧
新闻：存在催化
风险：中等
纪律：追高风险

状态：WAIT

核心原因：
当前主要矛盾是价格位置与市场情绪不匹配。

证据：
[Market] ...
[Sector] ...
[Technical] ...
[News] ...
[Emotion] ...
[Risk] ...
[Discipline] ...
生产版建议优先展示证据项、风险项、缺失项和状态，而不是用一个看似精确的模型分数替代判断。内部可以保留评分用于实验、回测和 Agent 评估。
# 12. 一次交易问题的完整调用链
用户：今天 10:05，XX 能不能买？
        ↓
Supervisor
        ├→ Market Agent
        ├→ Sector Agent
        ├→ Technical Agent
        ├→ News Agent
        └→ Emotion Agent
                 ↓
              Evidence
                 ↓
            Risk Agent
                 ↓
      Trading Discipline Agent
                 ↓
             Supervisor
                 ↓
          BIGA Decision
                 ↓
        Human-in-the-loop
# 13. OpenClaw 项目目录建议
agents/
├── biga-supervisor/
│   ├── AGENTS.md
│   ├── SOUL.md
│   └── skills/
├── market/
│   ├── AGENTS.md
│   └── skills/
├── sector/
│   ├── AGENTS.md
│   └── skills/
├── news/
│   ├── AGENTS.md
│   └── skills/
├── technical/
│   ├── AGENTS.md
│   └── skills/
├── emotion/
│   ├── AGENTS.md
│   └── skills/
├── risk/
│   ├── AGENTS.md
│   └── skills/
└── discipline/
    ├── AGENTS.md
    └── skills/

skills/
├── market-data
├── a-share-emotion
├── sector-analysis
├── technical-analysis
├── news-analysis
├── risk-control
├── trading-discipline
├── decision-card
├── daily-review
└── douyin-content
# 14. 核心交易决策流水线
OBSERVE → ANALYZE → CONSENSUS → VERIFY → RISK CHECK → DISCIPLINE CHECK → DECIDE → HUMAN DECISION → REVIEW → LEARN
# 15. 第一阶段 7 天落地计划
Day 1：OpenClaw 环境确认、项目仓库、基础 Agent 配置
Day 2：BigA Skill 基础框架、结构化消息契约
Day 3：Market Agent + A股基础数据链路
Day 4：Emotion Agent + 情绪指标计算
Day 5：Sector Agent + 板块强弱分析
Day 6：Risk Agent + Trading Discipline Agent
Day 7：Supervisor 串联全部 Agent，生成第一版 BigA Decision Card
# 16. 第一阶段验收标准
可以通过 Feishu / CLI / Web 等入口向 BigA 提出一个市场或个股问题。
Supervisor 能根据任务类型调用至少 3 个 Specialist Agent。
各 Agent 返回统一结构化结果，包含时间、状态、结果、证据和警告。
Market / Sector / Technical / News / Emotion 五类分析能够汇总。
Risk Agent 和 Trading Discipline Agent 能执行独立审查。
Supervisor 能生成可读的 BigA Decision Card。
整个流程可记录、可回放、可复盘。
不执行自动下单。
# 17. 长期演进方向
真实数据
  ↓
Multi-Agent 分析
  ↓
Evidence
  ↓
Risk + Discipline
  ↓
Supervisor
  ↓
Human Decision
  ↓
Daily Review
  ↓
Skill / Knowledge Evolution
  ↺ 下一交易日
长期目标不是简单增加 Agent 数量，而是建立一个可观测、可回放、可评估、可持续迭代的 Agent 决策系统。
# 18. 产品原则
EasyUp for BigA 的核心不是“让 AI 替人交易”，而是“让 AI 把复杂市场信息组织成可验证的交易逻辑，同时主动暴露风险和认知盲区”。
最终产品哲学：AI 负责扩大认知，人负责最终决策。

EasyUp for BigA 2.0 ｜ OpenClaw Multi-Agent Architecture ｜ V1.0
