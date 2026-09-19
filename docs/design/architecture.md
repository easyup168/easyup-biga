# EasyUp for BigA 2.0 — 系统架构设计

> created: 2026-09-19 | author: 小易 | status: **设计中，未开工**（等小飞说「开始」）
> 上游：`$HOME/references/EasyUp_for_BigA_2.0_...docx`（V1.0 参考文档）
> 姊妹文档：`docs/design/easyup-biga-2.0-evaluation.md`（评估，结论被小飞覆盖，见 §0.2）

---

## 〇、已定裁定

### 0.1 小飞 2026-09-19 的四条决定

| # | 议题 | 裁定 | 对架构的影响 |
|---|---|---|---|
| 1 | 与现系统的关系 | **完全独立，自己重新采数据** | 不挂载 它的行情库；新系统自建数据层；两套零共享可写状态 |
| 2 | 交易执行 | **两套互不干扰；将来都有自动下单；最终替代现系统 —— 但那是很久以后，暂不考虑** | Phase 1 不下单；但**架构不得堵死下单路径**（§9 为此预留） |
| 3 | 第一阶段范围 | **先装好新版 OpenClaw，实现最简单的功能验证** | Phase 1 = walking skeleton，不是完整系统（§11） |
| 4 | Agent 形态 | **真八 Agent，完全照参考文档** | Supervisor + 7 Specialist 全部是独立 OpenClaw agent |
| 5 | 仓库位置 | **`~/.openclaw-biga/workspace/`** | 不放 `~/projects/`；与 profile 状态同根，一处即全部 |
| 6 | Supervisor 的 agentId | **复用 `main`** | roster 显示 `main`；其 workspace = 仓库根（OpenClaw 零配置默认） |
| 7 | 陈旧副本 | **删掉 v22.22.0 下的 370M openclaw** | 已执行，见 §2.7 |

### 0.2 关于评估文档的处理

`easyup-biga-2.0-evaluation.md` 的结论是「不重写」。**该结论已被小飞覆盖，本文不再重提。**
但该文的两类内容仍然有效并被本文继承：

- **§5 资产盘点** → 变成本文 §9「必须带进新系统的负面知识」
- **§2 的性能实证**（现仓撤退过 LLM 扇出采集）→ 变成本文 §10 的延迟预算约束

换句话说：**重写这件事我不争了，但我会确保新系统不重踩旧系统踩过的坑。**

---

## 一、系统边界

```
┌──────────────────────────────┐   ┌──────────────────────────────┐
│  现有实例（生产，真钱）        │   │  新系统 BigA 2.0（本设计）     │
│                              │   │                              │
│  node v24.18.0               │   │  node v24.21.0（独立）        │
│  openclaw 2026.7.1-2         │   │  openclaw ≥2026.9.5（独立）   │
│  profile: default            │   │  profile: biga               │
│  port  : 18789               │   │  port  : 19789               │
│  state : ~/.openclaw         │   │  state : ~/.openclaw-biga    │
│  data  : 现有事实层（不共用）  │   │  data  : biga.db（自建）      │
│  agents: main ×1             │   │  agents: ×8                  │
│  IM    : 已接入（独立应用）    │   │  IM    : 需独立应用（§2.6）    │
│  交易  : 自动下单 ✅           │   │  交易  : ❌ Phase 1 不下单     │
└──────────────────────────────┘   └──────────────────────────────┘
        零共享可写状态 —— 两侧唯一的交集是宿主机资源（CPU/内存/磁盘）
```

**不变式 I-1**：BigA 的任何进程**不得以写模式**打开现系统的任何文件。
由 Phase 1 的 `tests/test_isolation.py` 用路径前缀白名单钉住。

**不变式 I-2**：BigA 崩溃、写坏自己的库、把端口占死 —— 现系统必须毫发无伤。
验收方式：Phase 1 结束时做一次 `kill -9` 演练，确认生产 gateway pid 不变。

---

## 二、部署架构

> 🔴 本节 2026-09-19 按小飞裁定改版：仓库位置定为 `~/.openclaw-biga/workspace/`；
> openclaw 程序本体**移出 nvm bin**（理由见 2.2，是一条会静默咬到生产的约束）。

### 2.1 三层隔离

| 层 | 生产 | BigA | 隔离手段 |
|---|---|---|---|
| Node | `v24.18.0` | **`v24.21.0`**（Latest LTS Krypton） | nvm 独立版本目录 |
| OpenClaw | `2026.7.1-2`（装在 nvm bin） | **`≥2026.9.5`（装在 `~/.openclaw-biga/runtime/`，刻意不进 nvm bin）** | 见 2.2 |
| 运行时 | profile `default` / 18789 | **profile `biga` / 19789** | `OPENCLAW_STATE_DIR` + `OPENCLAW_CONFIG_PATH` |

⚠️ **端口间距必须 ≥120**（官方要求）：browser control = base+2，CDP 自动分配 base+11 ~ base+110。
- 生产：18789 / 18791 / 18800–18899
- BigA：**19789 / 19791 / 19800–19899** ← 无重叠

⚠️ node 选 v24.21.0 而非 v26.9.0：v26 到 2026-09 仍是 Current 未进 LTS，native 模块 prebuild 有缺口风险。
`openclaw@2026.9.5` 的 engines 是 `>=24.16.0 <25 || >=26.1.0`，v24.21.0 满足。
⚠️ v24.18.0 其实也满足 —— 装新 node **不是版本需要，是隔离需要**。

### 2.2 🔴 约束 D-1：BigA 的 openclaw 不得装进 nvm 的 bin 目录

生产的 几十个 systemd cron unit 经 一个共用的 PATH 注入模块 注入 PATH，其选法是：

```python
with_oc = [d for d in nvm_versions if (d / "bin" / "openclaw").exists()]
return max(with_oc or with_node, key=_ver_key) / "bin"     # 取「装了 openclaw 的最高版本」
```

⇒ 若 BigA 把 openclaw 装进 `$HOME/.nvm/versions/node/v24.21.0/bin/`：

```
with_oc = [v24.18.0, v24.21.0]  →  max = v24.21.0
```

**生产 cron 的 PATH 会静默切到 BigA 的 binary**，然后用 2026.9.5 去操作生产 state
（cron 命令不带 `--profile`）。这正是 那个模块 自己注释里记的那类事故 ——
同型事故实际发生过：某个定时推送任务连续失败数百次，而 cron 状态与审计**全程绿色**。

**做法**：nvm 只提供 node 本体，openclaw 用 `--prefix` 装到 profile 目录内。

```bash
npm i --prefix ~/.openclaw-biga/runtime openclaw@latest
```

于是 `v24.21.0/bin/` 里只有 `node`、没有 `openclaw` ⇒ `with_oc` 仍是 `[v24.18.0]` ⇒ 生产不受影响。

**双保险**：在**生产仓库**加 一条常驻守卫测试，
断言 生产侧的 PATH 解析 解析到 `v24.18.0`。BigA 哪天不小心装错位置，生产侧当场报红。

### 2.3 完整路径表

| 类别 | 路径 | 进 git? | 说明 |
|---|---|---|---|
| profile 状态根 | `~/.openclaw-biga/` | ❌ | `--profile biga` 的标准位置 |
| profile 配置 | `~/.openclaw-biga/openclaw.json` | ❌ | |
| **openclaw 程序本体** | `~/.openclaw-biga/runtime/node_modules/openclaw/` | ❌ | 约束 D-1 |
| 命令 wrapper | `~/.openclaw-biga/bin/biga` | ❌ | 强制注入 `--profile biga` |
| agentDir（auth + 会话） | `~/.openclaw-biga/agents/<id>/agent/` | ❌ | ⚠️ **绝不跨 agent 复用** |
| **代码仓库根 = `main` 的 workspace** | **`~/.openclaw-biga/workspace/`** | ✅ | |
| Node 运行时 | `$HOME/.nvm/versions/node/v24.21.0/` | ❌ | 只提供 node，无 openclaw |

### 2.4 目录结构

```
~/.openclaw-biga/                          ← profile 状态根（不进 git）
├── openclaw.json                          ← profile 配置
├── runtime/node_modules/openclaw/         ← 🔴 程序本体，刻意不进 nvm bin（D-1）
├── bin/biga                               ← wrapper：exec runtime/.bin/openclaw --profile biga "$@"
├── agents/<id>/agent/openclaw-agent.sqlite   ← agentDir：auth + 会话历史
│
└── workspace/                             ← ✅ git repo 根，同时是 main(Supervisor) 的 workspace
    ├── AGENTS.md  SOUL.md  IDENTITY.md    ← Supervisor 的角色契约（它是人的接触点）
    ├── agents/                            ← 7 个 specialist 的 workspace
    │   ├── market/      AGENTS.md
    │   ├── sector/      AGENTS.md
    │   ├── news/        AGENTS.md
    │   ├── technical/   AGENTS.md
    │   ├── emotion/     AGENTS.md         ← Phase 1 唯一要建的 specialist
    │   ├── risk/        AGENTS.md
    │   └── discipline/  AGENTS.md
    ├── skills/
    │   ├── _contract/   evidence.py  verdict.py  card.py    ← 契约唯一实现
    │   ├── _store/      db.py  migrations/                  ← 唯一 DB 入口
    │   ├── emotion-calc/  SKILL.md  scripts/                ← Phase 1 唯一业务技能
    │   ├── market-data/   sector-data/   news-fetch/
    │   ├── technical-calc/  risk-check/  discipline-check/
    ├── data/biga.db                       ← SQLite (WAL)，.gitignore
    ├── tools/
    │   ├── cron/   registry.yaml  runner.py    ← 单一调度域，Phase 1 为空
    │   └── verify/ placebo.py  reachability.py  isolation.py
    ├── tests/
    └── docs/design/
```

**一条符号链接**（让 8 个 agent 共享同一套 OpenClaw 技能）：

```
~/.openclaw-biga/skills  →  ~/.openclaw-biga/workspace/skills
```

**为什么 `main` 的 workspace 就是仓库根，而 specialist 在子目录**：
`main` 是人直接对话的对象，cwd 应能看到整个仓库（docs/skills/data 的相对路径可用）；
specialist 是有界工人，窄 cwd 反而是对的。
且这正是 OpenClaw 的**零配置默认**（profile 的默认 workspace = `<stateDir>/workspace`，默认 agent = `main`），
不需要为 `main` 写任何 workspace 配置。

### 2.5 skills 加载的坑

官方：skills 从**每个 agent 的 workspace** + **共享根**加载，再按 allowlist 过滤。
🔴 `agents.entries.*.skills` 是**替换语义，不与 `agents.defaults.skills` 合并**。
⇒ 给某个 agent 配 skills 白名单时，必须把共享基线**整份抄进去**，否则会静默丢技能。
由 `tests/test_skill_allowlist.py` 钉住（读 config，断言每个 entry ⊇ defaults）。

### 2.6 飞书：Phase 1 不接

同机另一套实例已占用一个 IM 机器人应用。两个 gateway 共用同一个应用 ⇒ 同一条消息两个 bot 都回。
**Phase 1 一律走本地 TUI**：`~/.openclaw-biga/bin/biga chat`。
接飞书是 Phase 3 的事，届时需要一个**独立自建应用**。

### 2.7 已完成的前置清理（2026-09-19）

`$HOME/.nvm/versions/node/v22.22.0/lib/node_modules/openclaw`（370M，v2026.7.1-2，
最后修改 2026-07-20）已用 `npm uninstall -g openclaw` 移除。

- 删除前核对：零进程使用 / 不在 PATH / 非 default / 唯一引用它的 `openclaw.service` 已 disabled+inactive
- ⚠️ **v22.22.0 这个 node 本身必须保留** —— 它下面还有 `puppeteer`，仍被另一个脚本硬编码引用。
  只卸 openclaw，不要删整个版本目录
- 删除后 生产侧的 PATH 解析 仍解析到 `v24.18.0`，它的相关测试 7 项全绿
- 遗留：`~/.config/systemd/user/openclaw.service`（现指向已删除的路径，disabled+inactive）
  与 `openclaw-gateway.service.bak` —— 建议一并清理，**待小飞确认**


## 三、Agent 拓扑

### 3.1 Roster（8 个）

| agentId | 角色 | 模型 | `subagents.allowAgents` | 职责一句话 |
|---|---|---|---|---|
| **`main`** | **BigA Supervisor** | Opus | 其余 7 个全部 | 人的唯一接触点；拆任务、收证据、查完整性、组织制衡、出 Card |
| `market` | Market Agent | Sonnet | `[]` | 市场现在是什么状态（指数/成交额/涨跌结构/宽度/量能）。**不选股** |
| `sector` | Sector Agent | Sonnet | `[]` | 资金与共识方向（行业/概念强度、核心股、持续性、扩散） |
| `news` | News Agent | Sonnet | `[]` | 政策/产业/公告/海外；**重点是时间戳、来源、新鲜度核验** |
| `technical` | Technical Agent | Sonnet | `[]` | MA/量/MACD/RSI/突破/压力支撑。**指标由 Python 算，Agent 只解释** |
| `emotion` | Emotion Agent | Haiku | `[]` | 涨跌停/炸板率/最高板/连板/大面 → 情绪周期判断 |
| `risk` | Risk Agent | Sonnet | `[]` | 交易风险，**有否决权**（BLOCK） |
| `discipline` | Trading Discipline Agent | Haiku | `[]` | 人的行为风险：FOMO/追高/连亏翻本/偏离计划/无止损/仓位失控 |

**`allowAgents: []` 是刻意的** —— 对应官方团队预设的
"specialists return artifacts and evidence to the coordinator **without delegating further**"，
也对应参考文档 §6「星型协作，而不是 Agent 之间任意互聊」。
这同时是成本护栏：防止 specialist 递归 spawn 炸开。

**模型分层理由**：`emotion` / `discipline` 的判断高度规则化（阈值+计数），Haiku 够用；
`market` / `sector` / `news` / `technical` / `risk` 需要跨源推理，用 Sonnet；
Supervisor 做最终合成与矛盾裁定，用 Opus。
⚠️ 这是**初始假设，不是结论**。Phase 2 要用同一批问题做 A/B（同题换模型比 Card 差异），再定档。

### 3.2 配置骨架

```json5
{
  agents: {
    defaults: {
      // main(Supervisor) 的 workspace 不配 —— 用 OpenClaw 默认 <stateDir>/workspace
      // = ~/.openclaw-biga/workspace，正好是仓库根。
      // 7 个 specialist 在 agents add 时用 --workspace 显式指到 workspace/agents/<id>。
      subagents: {
        maxConcurrent: 6,          // ≥5，保证 Stage 1 五个分析 agent 真并行（§10）
        runTimeoutSeconds: 180,    // 单个 specialist 硬上限
        announceTimeoutMs: 120000,
      },
    },
    entries: {
      main:       { model: "anthropic/claude-opus-5",
                    subagents: { allowAgents: ["market","sector","news","technical",
                                               "emotion","risk","discipline"],
                                 delegationMode: "prefer" } },
      market:     { model: "anthropic/claude-sonnet-5", subagents: { allowAgents: [] } },
      sector:     { model: "anthropic/claude-sonnet-5", subagents: { allowAgents: [] } },
      news:       { model: "anthropic/claude-sonnet-5", subagents: { allowAgents: [] } },
      technical:  { model: "anthropic/claude-sonnet-5", subagents: { allowAgents: [] } },
      emotion:    { model: "anthropic/claude-haiku-4-5-20251001", subagents: { allowAgents: [] } },
      risk:       { model: "anthropic/claude-sonnet-5", subagents: { allowAgents: [] } },
      discipline: { model: "anthropic/claude-haiku-4-5-20251001", subagents: { allowAgents: [] } },
    },
  },
  tools: {
    agentToAgent: { enabled: true,
                    allow: ["main","market","sector","news","technical",
                            "emotion","risk","discipline"] },
    sessions: { visibility: "tree" },   // Supervisor 能看自己 spawn 的子会话，不能乱看
  },
}
```

⚠️ `tools.agentToAgent.allow` **必须把 8 个全列上**（requester 和 target 都要匹配）。
官方：空 allow 等于未设 = allow-all；只列一半 = 互相够不着。

⚠️ `delegationMode: "prefer"` **只是 prompt 引导，不是调度器**。
真正保证 Supervisor 一定去调 specialist 的，是它 AGENTS.md 里的硬性流程约定 + §12 的验收。

### 3.3 调用链（参考文档 §12，加上并行化）

```
人 ──► main (Supervisor)
         │
         │ Stage 1：并行 spawn 5 个（maxConcurrent ≥ 5）
         ├──► market ──┐
         ├──► sector ──┤
         ├──► news   ──┼──► AgentVerdict × 5（含 evidence）
         ├──► technical┤
         └──► emotion ─┘
         │
         │ Stage 2：并行 spawn 2 个「制衡层」，输入 = Stage 1 的冻结证据
         ├──► risk      ──┐
         └──► discipline ─┴──► AgentVerdict × 2（BLOCK / WARNING / PASS）
         │
         │ Stage 3：Supervisor 合成
         ▼
   BigA Decision Card  ──► decision_records 落库（可回放）
         ▼
   Human-in-the-loop
```

⚠️ **Stage 2 两个并行是对文档 §12 的一处偏离**：文档画的是 `Risk → Discipline` 串行。
但两者输入不相交（risk 看市场与个股、discipline 看人的行为史），
且文档 §5 自己就把它们并列为「制衡层」。并行省 10-15s。
**若将来发现 discipline 需要读 risk 的结论，立刻改回串行** —— 由 §12 的验收项守着。

---

## 四、通信契约

### 4.1 三个数据结构（`skills/_contract/`，唯一实现）

```python
@dataclass(frozen=True)
class Evidence:
    source: str            # 'biga.db:market_daily' / 'cls:12345' / 'em:api/xxx'
    as_of: datetime        # 🔴 数据本身的时间，不是取回时间
    retrieved_at: datetime
    value: Any
    calc_version: str|None = None   # 口径版本；换算法时可清点受影响结论

    @property
    def staleness_sec(self) -> int: ...

@dataclass
class AgentVerdict:
    task_id: str           # BIGA-YYYYMMDD-NNN
    agent: str
    status:  Literal['completed','partial','failed']
    verdict: Literal['PASS','WARNING','BLOCK','UNKNOWN']
    result:  dict
    confidence: float
    evidence: list[Evidence]
    warnings: list[str]
    missing:  list[str]    # 🔴 强制：任一必填项算不出来就必须列在这里
    elapsed_ms: int

@dataclass
class DecisionCard:
    decision_id: str
    status: Literal['BUY','WAIT','AVOID','BLOCK']
    headline: str          # 核心矛盾一句话
    verdicts: list[AgentVerdict]
    missing: list[str]     # 汇总，必须显示
    synthesis: str
    model_ref: str
```

### 4.2 🔴 四条契约铁律

1. **`UNKNOWN` ≠ `PASS`。** 算不出来必须说算不出来。
2. **`missing` 非空 ⇒ Supervisor 不得给 `BUY`。** 对应文档 §9「证据时间不一致时触发补充查询，而不是强行汇总」。
3. **每个 `result` 字段必须能追到至少一条 `Evidence`。** 无据之言不入 Card。
4. **契约只有一份实现。** 任何 agent / 脚本不得自建第二套 Verdict 结构。
   由 `tests/test_contract_single_impl.py` 用 AST 全仓扫描钉死。

### 4.3 消息示例

Supervisor → Specialist（沿用参考文档 §6 格式，补 `as_of_required`）：
```json
{ "task_id": "BIGA-20260919-001", "requester": "main", "target": "market",
  "task": "market_snapshot", "timestamp": "2026-09-19T10:05:00+08:00",
  "market": "CN_A_SHARE",
  "required_fields": ["breadth","limit_up","limit_down","broken_board","turnover"],
  "as_of_required": "2026-09-19T10:00:00+08:00" }
```

Specialist → Supervisor：即 `AgentVerdict` 的 JSON 序列化。

---

## 五、数据架构

### 5.1 ⚠️ 对参考文档 §8 的一处偏离：不上 PostgreSQL + Redis

| | 文档主张 | 本设计 | 理由 |
|---|---|---|---|
| 事实层 | PostgreSQL | **SQLite (`biga.db`, WAL)** | Phase 1 是「最简功能验证」，单机单用户单写进程。PG 带来的是运维面而非能力 |
| 实时层 | Redis | **同库 + 进程内 TTL cache** | 无跨机需求；Redis 的用途（缓存/状态）单机下 SQLite+内存即可 |

**切 PostgreSQL 的触发条件**（任一成立即切，写死在这里免得凭感觉）：
1. 出现 **>1 个并发写进程**（例如采集与决策分离部署）
2. 需要**跨机**访问同一份事实层
3. 单表 > **5000 万行**（作为参照：一套跑了半年的日线库，最大表也只在百万行量级）

为此，所有 DB 访问必须经 `skills/_store/db.py`，**业务代码里不许出现裸 `sqlite3.connect`** ——
这样切 PG 只改一个文件。由 `tests/test_no_raw_sqlite.py` 钉住。

### 5.2 分层

```
数据源（东财 / 新浪 / mootdx / 财联社 / akshare …）
   ↓  collectors/          ← 纯采集，不做判断，全部带 retrieved_at
raw 层   biga.db: raw_*    ← 原样落盘，永不改写（可重算的地基）
   ↓  normalizers/
fact 层  biga.db: fact_*   ← 归一化事实（复权、单位、代码→交易所）
   ↓  indicators/          ← 确定性计算：MA/MACD/情绪分/板块强度…
derived  biga.db: d_*      ← 派生指标，带 calc_version
   ↓
Agent（通过 skill 只读查询）
```

**raw 层永不改写**是从现系统学来的：现系统的 `trades` 表被原地 UPDATE，
导致「下单当时的状态」不可重建，血缘归因至今残缺。

### 5.3 核心表（Phase 1 只建带 ★ 的）

| 表 | 用途 |
|---|---|
| ★ `decision_records` | Decision Card + 全部 Verdict + 证据（回放的唯一真相源） |
| ★ `agent_runs` | 每次 spawn 的 agent/耗时/token/status（成本与延迟可观测） |
| ★ `raw_market_snapshot` | 采集原样落盘 |
| `fact_stock_daily` / `fact_index_daily` | 归一化日线 |
| `d_emotion_daily` | 情绪分 |
| `d_sector_strength` | 板块强度 |
| `raw_news` | 带 `published_at` / `source` / `retrieved_at` |

---

## 六、技能层：Agent 与 Python 的分界线

参考文档 §3「数据与智能分离」。本设计把它写成可执行的判据：

| 归 Python（skill） | 归 Agent（LLM） |
|---|---|
| 抓取、清洗、单位换算、代码→交易所路由 | 跨来源的关联与因果推断 |
| 一切**有唯一正确答案**的计算（MA、涨停数、换手、板块涨幅） | 一切**需要取舍**的判断（这算不算共识、风险够不够大） |
| 阈值比较本身 | 阈值该不该在此情境下适用 |
| 结构化输出 | 自然语言解释与矛盾裁定 |

🔴 **硬约束 S-1**：Agent **不得**在 prompt 里做算术。
任何数字必须来自 skill 返回值并附 `Evidence`。
理由：LLM 算错不报错，且不可回放。由 Card 渲染时校验「每个数字有据」来兜底。

🔴 **硬约束 S-2**：skill 不得做判断性归类（"这属于强势板块"）。
skill 返回事实与分数，判断留给 agent。
理由：判断逻辑散进 skill = 产生第二套口径。见 §9 L-3 —— 同一判据散落多处实现时，错误比例可以高得惊人，且错法全是静默的。

---

## 七、Decision Card 与回放

### 7.1 渲染原则（文档 §11）

> 优先展示**证据项、风险项、缺失项、状态**，而不是一个看似精确的分数。

```
BIGA DECISION CARD          BIGA-20260919-001   10:05:32   耗时 78s
────────────────────────────────────────────────────────────
市场环境  中性    板块  强      个股趋势  良好
情绪      分歧    新闻  有催化  风险      中等     纪律  ⚠追高

状态：WAIT

核心矛盾：价格位置与市场情绪不匹配。

⚠ 缺失项（2）
  · sector.扩散度 —— 概念成分股映射覆盖率 61%（<80% 阈值）
  · news.海外  —— 最新一条 as_of 已 19 小时，超出当日窗口

证据
  [Market]     涨跌家数 1842/3105  as_of 10:00  src biga.db:raw_market_snapshot
  [Emotion]    情绪分 48（昨 61）   as_of 10:00  src d_emotion_daily
  [Technical]  MA5 偏离 +7.2%       as_of 10:00  src d_stock_ma
  [Risk]       BLOCK ← 位置风险：60日涨幅 +118%，处三档最差
  [Discipline] WARNING ← 追高：距 MA5 +7.2% > 6% 阈值
```

内部可以保留评分用于实验与回测，**但不上 Card**（文档 §11 原话）。

### 7.2 回放

`decision_records.verdicts_json` 冻结了全部证据 ⇒
```bash
biga replay BIGA-20260919-001 [--model <other>]
```
在不重新采集的前提下重跑 Supervisor 合成。用途：
- 换模型对比合成质量（§3.1 的模型分层 A/B 靠它）
- prompt 改动的回归测试
- 事后复盘「当时看到的证据是否足以得出这个结论」

⚠️ **回放必须与在线路径共用同一份合成代码**，不许另写一套。

---

## 八、调度：单一域

🔴 现系统有 **systemd（90 条）** 与 **LLM cron（91 条/39 启用）** 两个互不引用的调度域，
反复导致「查一个域就断言没有 cron 跑它」的误判。

**BigA 只有一个调度域**：`tools/cron/registry.yaml` → systemd timer。
LLM 侧的定时任务也由 systemd 触发 `openclaw --profile biga agent --agent <id> -m "..."`，
**不使用 OpenClaw 内置 cron**。

⚠️ Phase 1 **一条定时任务都不建**。第一条 cron 出现的前提是：
它的产物已有**被证明的消费方**（§9 L-1）。

---

## 九、🔴 必须带进新系统的失败模式清单

下面九条是从一套**长期运行的量化交易系统**里学到的失败模式。
它们的共同点是：**失败时不报错**。测试绿、日志绿、监控绿，而事情已经坏了几个月。

因此每一条都不只是「注意事项」，而是配一个**在 Phase 1 就建立的、会报红的机制** ——
靠人记住是不够的，几个月后没人记得。

| # | 失败模式 | 为什么它是静默的 | 新系统的防护机制 |
|---|---|---|---|
| **L-1** | **零消费方**：模块在写数据，但没有任何代码读它；或工具被多处引用为权威，却从未被任何调度器执行 | 写入方本身工作正常，没有报错点。"被引用"看起来就像"在运行" | **产消对账测试**：任何写库的模块必须在调度注册表或调用图中存在读取方，否则红。判据是**调度命令的字面量**，不是"谁调用了它" |
| **L-2** | **闸 fail-open**：风控/纪律检查在数据缺失或条件算不出来时**静默放行**；未知配置键被忽略，规则退化成无条件命中 | 放行与通过的日志长得一模一样 | **买入侧一律 fail-closed**；`UNKNOWN` 独立于 `PASS`；算不出来必须进 `missing[]` 并显示在 Card 上 |
| **L-3** | **同一判据多份实现**：代码路由、涨跌停、费率等基础判断在各处各写一遍，其中相当比例是错的 | 错法全是静默的 —— 返回一个看似合理的错值 | **判据单一实现 + AST 扫描钉死**：不许存在第二份。一切走 `_contract` / `_store` |
| **L-4** | **基准有算术偏差**：超额收益的对照基准本身带正偏（右偏截面下均值≠中位、逐日连乘≠买入持有），导致随机策略也"跑赢" | 结论看起来显著，方向也符合预期 | **任何基准上线前必须过安慰剂检验**：拿随机标的喂同一条生产路径，要求 `\|t\| < 1.96`。`tools/verify/placebo.py` |
| **L-5** | **估计量依赖**：同一份数据，换一种标准误算法，显著性就反转 | 只报让结论存活的那一个，读者看不出来 | **报结论必须写明用的哪个估计量**，并同时给出多种；`n < 30` 一律标 underpowered |
| **L-6** | **文档漂移**：文档里的关键数字（表数量、数据量、测试数）与实际相差数倍 | 文档不会自己报错，而人会照着它做判断 | **关键事实由测试生成或校验**，不手抄 |
| **L-7** | **死配置**：因子/规则因命名空间不一致而交集恒空，长期零命中；或阈值判断在其作用域内恒真 | 它照常参与计算，只是永远不生效 | **可达性巡检**：每日报告「配了但从未命中」的规则与因子 |
| **L-8** | **账本幻影行**：记录了实际没有发生的事件（提交即记账，而提交不等于成交） | 账本自洽、总额对得上，但对应的事实不存在 | **raw 层永不改写**；状态变更一律追加而非 `UPDATE`，让「当时看到的」可重建 |
| **L-9** | **下单末端的时序陷阱**：父进程超时预算与子进程下单耗时倒挂、特定时段必须换委托类型、探针失败时继续执行、止损价贴死价格保护带下限 | 症状是"废单"或"没成交"，看不出是架构问题 | **Phase 1 不下单**，但把这四条冻结进 `docs/design/live-order-rules.md`，将来开下单时直接实现 |

⚠️ **L-9 是裁定 2「将来都有自动下单」的直接落点** ——
现在不实现，但先把知识存进去，代价近乎为零；等到要用时再重新踩一遍，代价是真金白银。

> 这张表是本设计里最重要的一节。
> 架构图可以照着任何一篇文章画，这九条只能靠踩出来。

---

## 十、延迟与成本预算

八 Agent 形态的代价是真实的。本节把它量化并给出压制手段。

### 10.1 延迟预算

| Stage | 内容 | 并行度 | 预估 |
|---|---|---|---|
| 0 | Supervisor 拆任务 | — | 5-10s |
| 1 | market / sector / news / technical / emotion | **×5 并行** | 20-40s（取最慢那个） |
| 2 | risk / discipline | **×2 并行** | 15-25s |
| 3 | Supervisor 合成 + 渲染 Card | — | 20-30s |
| | **合计** | | **60-105s** |

**串行做法会是 2-5 分钟** —— 差距全在 Stage 1/2 的并行。
因此 `agents.defaults.subagents.maxConcurrent ≥ 6` 是**功能要求不是调优**。

三条压制手段：
1. **并行 spawn**，不用串行 `sessions_send`
2. **`context: "isolated"`**，不用 `fork` —— specialist 不需要 Supervisor 的对话历史，
   fork 会把整段 transcript 复制进去，token 直接翻倍
3. **数字全部由 Python 算好再进 prompt** —— agent 读的是结论不是原始表

### 10.2 成本可观测

`agent_runs` 表逐次记录 agent / model / 耗时 / token。
Phase 2 结束时出一张「单次决策成本分解」，据此决定要不要降档某个 agent 的模型。
**不预先优化** —— 但要保证测得出来。

---

## 十一、Phase 1：最简功能验证

> 小飞的原话：「先安装好新版 openclaw，实现最简单的功能验证」。
> 本节把「最简单」定义清楚，避免范围漂移。

### 11.1 目标：一条最细的、端到端能走通的线

**只建 2 个 agent**（`main` + `emotion`），不是 8 个。

理由：Phase 1 要验证的是**机制**（跨 agent spawn 能不能通、契约能不能落、能不能回放），
不是**覆盖面**。机制通了，其余 6 个是复制。
而且一上来建 8 个空 agent = 6 个零消费方组件，正是 L-1 要防的。

选 `emotion` 做第一个 specialist：它最确定性（阈值+计数），最容易判断「答得对不对」。

### 11.2 步骤

| # | 动作 | 产物 / 验证 |
|---|---|---|
| 1 | 装 node **v24.21.0**（增量；不改 `default`、不卸 v24.18.0） | `nvm ls` 见到它；生产 gateway pid 不变 |
| 2 | `npm i --prefix ~/.openclaw-biga/runtime openclaw@latest` —— 🔴 **不装进 nvm bin**（约束 D-1） | `runtime/node_modules/.bin/openclaw --version` ≥2026.9.5；且 `v24.21.0/bin/` 里**没有** openclaw |
| 3 | 写 `~/.openclaw-biga/bin/biga` wrapper（强制 `--profile biga`）并加执行位 | `biga --version` 可用 |
| 4 | 🔴 **生产侧**加 一条常驻守卫测试，断言 生产侧的 PATH 解析 → `v24.18.0` | 绿（这是 D-1 的常驻守卫） |
| 5 | `biga setup`：端口 **19789**，**跳过飞书** | `~/.openclaw-biga/openclaw.json` 生成 |
| 6 | 在 `~/.openclaw-biga/workspace/` 建 git 仓库 + §2.4 骨架 + `.gitignore`（`data/`） | `git log` 有首个 commit |
| 7 | 写 `skills/_contract/`（Evidence / AgentVerdict / DecisionCard） | `tests/test_contract_single_impl.py` 绿 |
| 8 | 写 `skills/_store/db.py` + 三张表（`decision_records` / `agent_runs` / `raw_market_snapshot`） | `tests/test_no_raw_sqlite.py` 绿 |
| 9 | 写 `skills/emotion-calc/` —— 真采一次 A 股情绪数据，输出 `AgentVerdict` | 命令行跑出带 `as_of` 的 JSON |
| 10 | 建 agent `emotion`（`--workspace ~/.openclaw-biga/workspace/agents/emotion`）+ 写它的 AGENTS.md | `biga agents list` 见到它 |
| 11 | 配 `main`(Supervisor)：AGENTS.md / SOUL.md / IDENTITY.md 落在**仓库根** + `allowAgents:["emotion"]` + `agentToAgent.allow` | |
| 12 | 跑通：`biga agent --agent main -m "今天市场情绪怎么样？"` | 见 §11.3 |
| 13 | 实现 `replay <decision_id>`（与在线路径共用同一份合成代码） | 同一 verdicts 重跑出一致结论 |
| 14 | 隔离演练：`kill -9` BigA gateway 进程 | 生产 gateway pid 不变（不变式 I-2） |

⚠️ 第 1 步只装 **node**；openclaw 在第 2 步用 `--prefix` 装到 profile 目录内。
把这两步合成「在新 node 下 `npm i -g openclaw`」就会直接踩中 D-1。

### 11.3 Phase 1 验收（全部满足才算过）

1. Supervisor 收到问题后**确实 spawn 了 `emotion`**（`agent_runs` 有该行，不是自己编的）
2. `emotion` 返回的是**合法 `AgentVerdict`**，含 ≥1 条带 `as_of` 的 `Evidence`
3. 输出一张 **Decision Card**，含状态 + 证据 + 缺失项三段
4. `decision_records` 落库 1 行，`biga replay` 能重跑出一致结论
5. 故意把情绪数据源打断 → Card 显示 `UNKNOWN` + `missing` 非空，**不是 PASS**（L-2）
6. 生产侧：gateway pid、openclaw 版本、`default` alias、PATH、18789 监听 —— 五项全部未变
6b. 🔴 生产侧的 PATH 解析 仍解析到 **v24.18.0**（约束 D-1 未被破坏）
7. 单次端到端 **< 60s**（只有 2 个 agent，8 个时才允许到 105s）

### 11.4 Phase 1 明确不做

装飞书 / 建任何 cron / 接任何下单路径 / 建其余 6 个 agent /
PostgreSQL / Redis / 回测 / 历史数据回补 / Web UI。

---

## 十二、路线图

| Phase | 内容 | 出口条件 |
|---|---|---|
| **1** | walking skeleton（§11） | §11.3 七条 |
| **2** | 补齐 8 个 agent + Stage1/2 并行 + 完整 Card | 端到端 <105s；`missing[]` 在真实缺数据时非空 ≥5 次 |
| **3** | 数据层加厚（历史回补、更多 collector）+ 独立飞书应用 + 第一条 cron | 每条 cron 都有被证明的消费方（L-1） |
| **4** | 测量层：安慰剂基准 + Card 状态的区分力检验 | `BLOCK/WARNING` 与 T+5 结果按日聚类 t 有区分力 |
| **5** | （很久以后）自动下单 —— 前提是 Phase 4 通过 | 另起设计文档；L-9 四铁律先实现 |

🔴 **Phase 4 是 Phase 5 的硬前提。**
在 Card 的 `BLOCK` 被证明有区分力之前接下单，等于新增一道空转门 ——
现系统的 `discipline` 两层门不对齐、空转 16 轮 22 笔成交 0，就是这么来的。

---

## 十三、风险登记

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 八 Agent 延迟超预算（>105s），盘中不可用 | 中 | 高 | §10 三条压制手段；Phase 2 出口条件卡死 105s；真超了就把最慢的 specialist 降级为 Python 适配器（**只此一处偏离形态**，且需小飞确认） |
| token 成本失控 | 中 | 中 | `agent_runs` 逐次记账；Phase 2 出成本分解再决定降档 |
| 两套系统抢 CPU/内存（宿主 15G，生产已占） | 中 | 中 | BigA 的 `maxConcurrent` 封顶 6；Phase 1 不建 cron，无后台负载 |
| 误用不带 `--profile` 的命令改到生产 state | **高** | 中 | 做 `biga` wrapper 脚本强制注入 `--profile biga`；**本次会话已实证这个风险真实发生过** |
| 生产 feishu 插件双装（既有问题）在升级路径上爆 | 低 | 中 | BigA Phase 1 不接飞书，绕开；生产侧另行 `doctor --fix` |
| 重新采数据导致历史不足，Phase 4 无法做统计检验 | **高** | 高 | 已知代价（§0.1 裁定 1）。Phase 3 起并行回补历史；Phase 4 的出口条件写明 n<30 标 underpowered |
| 「完全照文档」与实证冲突时的处理 | 中 | 中 | 本文已标出 **2 处刻意偏离**（§3.3 Stage2 并行、§5.1 不上 PG），各附理由与回退条件。再有偏离一律先问小飞 |

---

## 十四、待确认

已定（§0.1 裁定 5/6/7）：仓库位置、Supervisor 用 `main`、删除 v22 陈旧副本。

仍开放：

1. **Phase 1 建几个 agent** —— 我建议 2 个（`main` + `emotion`）先验证机制；
   一上来 8 个全建 = 6 个零消费方组件，正是 §9 L-1 要防的。**等你定。**
2. **数据层 SQLite vs PostgreSQL** —— §5.1 我按「SQLite + 写死切换触发条件」设计，
   与参考文档 §8 不同。一步到位上 PG 的话 Phase 1 多 1-2 天。
3. **模型分层**（§3.1）是初始假设。对某个角色有偏好（如 risk 也上 Opus）现在说比 Phase 2 再改便宜。
4. **遗留 unit 清理** —— `~/.config/systemd/user/openclaw.service`（已指向被删路径）
   与 `openclaw-gateway.service.bak`，要不要一并删。
