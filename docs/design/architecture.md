# EasyUp for BigA 2.0 — 系统架构设计

> 📄 **常青** · 随代码同步
> **覆盖**：Agent 拓扑、通信契约、数据架构、失败模式清单、延迟预算 ｜ **不覆盖**：阶段进度（见 [`phase-2-specialists.md`](phase-2-specialists.md)）、施工过程（见 [`../tutorial/`](../tutorial/README.md)）


> 上游需求：[`../external/2026-09-19-upstream-source-design-v1.md`](../external/2026-09-19-upstream-source-design-v1.md)（只读）
> ⚠️ 本文**不标 status/日期** —— 常青文档的状态就是「现在」，历史在 git 里。
> （原来这里写着「设计中，未开工」，而那时 Phase 2 已经做完两步了）

---

## 〇、已定裁定

### 0.1 小飞 2026-09-19 的四条决定

| # | 议题 | 裁定 | 对架构的影响 |
|---|---|---|---|
| 1 | 与现系统的关系 | **完全独立，自己重新采数据** | 不挂载它的行情库；新系统自建数据层；两套零共享可写状态 |
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
判据在 `tools/verify/isolation.py`（要手工跑），而**这个判据本身**由 `tests/test_isolation.py` 钉住。

⚠️ 原文把这句写成「由 `tests/test_isolation.py` 用路径前缀白名单钉住」，两处都不对：那个文件当时并不存在；「路径前缀白名单」也说反了 —— 那正是造成 64 个误报的前缀陷阱，真实实现用的是 `PurePath.is_relative_to()`。

🔴 **自检工具自己也要被测。** 它一度只有「过 / 不过」两态，于是「什么都没扫到」与「扫了，没问题」写出来一模一样（F2 / F18）。现在是三态，`UNKNOWN` 不计入通过、退出码非零。

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

生产侧有几十个 systemd cron unit，它们的 PATH 由一个共用模块注入，其选法是：

```python
with_oc = [d for d in nvm_versions if (d / "bin" / "openclaw").exists()]
return max(with_oc or with_node, key=_ver_key) / "bin"     # 取「装了 openclaw 的最高版本」
```

⇒ 若 BigA 把 openclaw 装进 `$HOME/.nvm/versions/node/v24.21.0/bin/`：

```
with_oc = [v24.18.0, v24.21.0]  →  max = v24.21.0
```

**生产 cron 的 PATH 会静默切到 BigA 的 binary**，然后用 2026.9.5 去操作生产 state
（cron 命令不带 `--profile`）。这正是那个模块自己注释里记下的那类事故 ——
同型事故实际发生过：某个定时推送任务连续失败数百次，而 cron 状态与审计**全程绿色**。

**做法**：nvm 只提供 node 本体，openclaw 用 `--prefix` 装到 profile 目录内。

```bash
npm i --prefix ~/.openclaw-biga/runtime openclaw@latest
```

于是 `v24.21.0/bin/` 里只有 `node`、没有 `openclaw` ⇒ `with_oc` 仍是 `[v24.18.0]` ⇒ 生产不受影响。

**双保险**：在**生产仓库**里加一条守卫测试，断言它的 PATH 解析结果仍指向 `v24.18.0`。
BigA 哪天不小心装错位置，生产侧当场报红。

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
    │   ├── cron/   registry.yaml  runner.py    ← 单一调度域，⬜ **未建**
    │   ├── git-hooks/ pre-push                 ← 推送前扫本次新增的提交
    │   └── verify/                             ← 全部只读，见下表
    │        isolation.py       I-1/I-2/R-2/端口，三态
    │        spawn_check.py     Specialist 真被 spawn 了吗（接在出卡之后）
    │        agent_trace.py     各 agent 的工具调用序列
    │        latency_report.py  延迟/成本分解 + Stage 1 并行判据
    │        missing_ledger.py  缺失项台账（出口条件 4）
    │        audit_public.sh    公开内容审查（九项）
    │        probe.sh           在一次性库上跑手工探针
    │        sync_test_count.sh 把文档里的测试条数同步成实测
    │        phase1_acceptance.py  Phase 1 验收（I-1 判据转调 isolation.py）
    │                ⚠️ placebo.py / reachability.py **设计中，从未提交过**
    │                   —— 原文把它们与真实文件并排列出，读起来像已建成
    ├── tests/      _scan.py 是三个 AST 扫描器共用的**唯一**文件枚举
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
⬜ **没有任何东西在钉它。** 原文声称由 `tests/test_skill_allowlist.py` 钉住，该文件从未存在过。
🔴 这条风险是真实的：替换语义漏抄一个技能**不报错，只是静默少一个能力**。补这条守卫记在 `TODO.md`。

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
- 删除后生产侧的 PATH 解析仍指向 `v24.18.0`，其相关测试全绿
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

🔴 **这句话与本机实测矛盾，暂不能当结论用。** 外部评审 F10：`tools.agentToAgent.allow` 少了一个 agent，而 `agent_trace.py` 显示它当天仍被成功调用了至少 3 次。⇒ 要么这句话不准，要么另有生效路径。
在查清之前**两处名册仍然都要写全** —— 不确定该信哪条时，选代价小的那边。

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

### 4.1 四个数据结构（`skills/_contract/`，唯一实现）

```python
class MissingItem(str):
    """缺失项 = 机器可读代码 + 人话。字符串值就是人话，代码挂在 .code 上。"""
    code: str              # '<域>.<对象>.<原因>'，如 market.turnover.unavailable
                           # 'legacy.unclassified' = Phase 1/2 早期的裸字符串

@dataclass(frozen=True)
class Evidence:
    field: str             # 它支撑 result 的哪个键（铁律 3 靠它执行）
    source: str            # 'sina:kline/sh000001' / 'em:push2ex/limit_up'
    value: Any
    as_of: datetime        # 🔴 数据本身的时间，不是取回时间
    retrieved_at: datetime
    calc_version: str|None = None   # 口径版本；换算法时可清点受影响结论
    label: str|None = None          # 渲染 Card 用的中文名
    raw_hash: str|None = None       # 🔴 指回 raw_market_snapshot.content_sha256
                                    #    派生字段没有单一来源，允许为空

    @property
    def staleness_sec(self) -> int: ...

@dataclass
class AgentVerdict:
    task_id: str           # BIGA-YYYYMMDD-NNN
    agent: str
    status:  Literal['completed','partial','failed']
    verdict: Literal['PASS','WARNING','BLOCK','UNKNOWN']   # 数据完整度
    result:  dict
    confidence: float
    evidence: list[Evidence]
    warnings: list[str]
    missing:  list[MissingItem]   # 🔴 强制：任一必填项算不出来就必须列在这里
    elapsed_ms: int
    stance: str|None = None       # 🔴 方向判断，由 Agent 填，skill 不填
                                  #    取值必须在 STANCE_VOCAB[agent] 里

@dataclass
class DecisionCard:
    decision_id: str
    status: Literal['BUY','WAIT','AVOID','BLOCK']
    headline: str          # 核心矛盾一句话
    verdicts: list[AgentVerdict]
    missing: list[MissingItem]    # 汇总，必须显示
    synthesis: str
    model_ref: str
```

#### 4.1.1 `status` / `verdict` / `stance` —— 三个不能混的问题

| 字段 | 回答 | 谁填 |
|---|---|---|
| `status` | 这次执行**跑完了吗** | skill |
| `verdict` | 这个判断**有效吗**（数据全不全） | skill |
| `stance` | 判断**是什么**（市场偏哪边） | **Agent** |

🔴 `verdict=PASS` 的意思是「数据完整」，**不是「看好」**。
两者混在一起，「没发现问题」与「看多」就再也分不开 —— 而这正是 L-2 的形状。

⚠️ `stance` 必须取自 `STANCE_VOCAB[agent]` 这个固定词表。
今天写「偏强」、明天写「震荡偏强」，三个月后它就是一列自由文本，
Phase 4 拿它做不了任何相关性检验。**它存在的唯一理由就是能被聚合。**

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
| ★ `agent_runs` | 每次 spawn 的 agent/耗时/status（成本与延迟可观测） |
| ★ `raw_market_snapshot` | 采集原样落盘 |
| ★ `agent_verdicts`（v3） | **判定原件** —— skill 写、synthesize 按 id 读 |
| ★ `decision_ids`（v4） | **编号分配器** —— Stage 0 原子占号，见 §5.3.2 |
| `fact_stock_daily` / `fact_index_daily` | 归一化日线 |
| `d_emotion_daily` | 情绪分 |
| `d_sector_strength` | 板块强度 |
| `raw_news` | 带 `published_at` / `source` / `retrieved_at` |

⚠️ **只读打开一个还不存在的库**会抛 `StoreNotInitialised`（v5 加），
而不是裸的 `sqlite3.OperationalError`。它与「schema 建好但零行」是两回事 ——
后者是全新环境的**正常状态**，把它也报成错会有人为了消警告去塞假数据。
巡检工具据此统一退出码 2（判不了），见 §9 的 R-3。

#### 5.3.2 为什么要 `decision_ids`（2.4 前夕加的）

它解决的不是「编号好看」，而是 **L-11：身份晚于证据**。

2026-09-21 盘中，两次端到端相隔两分钟。结果两张卡**共用同一条 `technical`
判定原件**（result 哈希相同），且对 `sector` 给出相反结论 ——
而任何一张卡单独看都毫无异常。

两个可以分开修、但只修一个不够的原因：

| 层 | 问题 | 修法 |
|---|---|---|
| 表层 | 五个 specialist 都硬编码 `new_task_id(1)` | 换成临时号，并用 AST 测试钉死 |
| 根本 | 编号在**合成时**才分配 | 提到 Stage 0，沿全链下传 |

🔴 **占号必须原子。** 「先查空位再插入」中间有窗口，两次同时起的运行会拿到
同一个号 —— 那正是本次事故的机制。主键冲突是唯一可靠的并发仲裁。

🔴 **守卫放在 `save_verdict`（唯一写入口），不是五个调用点。**
同一个 bug 能同时活在五个文件里，就是因为每个调用点各写一遍默认值。

> 通用原则：**一个实体必须在它产生数据之前就有身份。**
> 否则那些数据只能事后归属，而事后归属在并发下必然出错。

#### 5.3.1 为什么要 `agent_verdicts`（Phase 2 加的）

它解决的不是「多存一份」，而是**不让 LLM 搬运结构化数据**（见 §9 L-10）。

skill 算完直接把原件落这张表并返回一个 id，Agent 只传 id。
Specialist 要追加缺失项时写**新行**并用 `amends` 指回原行 ——
与 `decision_records.replay_of` 同一套做法：**原件永不改写**。

**五张表全部只追加不修改，由 SQLite 触发器强制**（schema **v5**）。

⚠️ 这句话在 v4 时期是**假的**：原文写「四张表」，而当时已经有五张，
且新加的 `decision_ids` 恰恰是唯一没有触发器的那张（外部评审 F1）。
一条 `DELETE` 就能让同一个号发两次 —— 而 FIX-01 / FIX-02 两道身份闸门
校验的都是「这些判定的 task_id 是不是同一个」，号回收之后两次运行
**真实自洽**，两道闸门会一致放行。

⇒ v5 补上触发器，并把判据从「数几张表」换成
`tests/test_store.py::test_每张表都有只追加触发器` ——
它扫 `sqlite_master` 里**实际有哪些表**，例外要在 `EXEMPT` 里自己举手。
**新表默认就该受保护**，而手写的数字只会在下一次加表时再错一遍。

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

### 6.1 采集层的两条统一接口（`skills/_sources/`）

它们都不是「工具函数」，是**用结构消灭一类判断**——
判断一旦分散到各个调用点，必然有某一处判错。

#### `server_as_of` —— 每个源自己声明有没有服务端时刻

```python
r.server_as_of or now_cn()      # 调用方统一这么写，不必逐处判断
```

| 返回 | 含义 |
|---|---|
| `datetime` | 这个端点自带时刻（日线的交易日、腾讯行情的时间戳、快讯的发布时刻） |
| `None` | **不带任何日期** —— 它说的就是「此刻」（涨跌家数、板块榜） |

🔴 **为什么必须是统一接口而不是各处 `if`**：外部评审 F4 的成因正是
「Evidence 层改对了，几十行外的 raw 落盘层没改」——
两边的测试各自全绿，bug 落在从未被同时检查过的缝隙里。

⚠️ 不带日期的源要**显式写 `server_as_of = None`**，不是不实现 ——
不实现会让人以为是漏了。守卫：`_sources` 里每个带 `raw` 字段的 dataclass
都必须声明它（`tests/test_as_of_attribution.py`），**新加一个源不回答这个问题就红**。

### 6.2 四个数据源，各自的脾气

**每个源都只做一件别人做不了的事**，重叠是为了交叉校验，不是为了冗余。

| 模块 | 拿什么 | 🔴 它自己的坑 |
|---|---|---|
| `sina.py` | 指数日线（脊梁） | 只声明**交易日**，不声明时刻 ⇒ `as_of` 要按收盘推。当天日线发布**晚于收盘约 35 分钟**（实测 n=1） |
| `tencent.py` | 指数实时行情 | 唯一**自带完整时间戳**的源（`quoted_at`），这是它存在的主要理由；成交量单位是**手**，与日线的股差 100 倍 |
| `eastmoney.py` | 涨跌家数 / 板块榜 / 涨停池 | 家数与板块榜**不带任何日期**；同一主机两个端点一个返数组一个返字典；盘前全零 ≠ 全平盘 |
| `sina_news.py` | 7×24 快讯 | 连续事件流，**没有「收盘」概念** ⇒ `as_of` 只能是最新一条的真实时刻（FIX-03） |

支撑层：

| 模块 | 职责 |
|---|---|
| `http.py` | 唯一的取数出口：超时、重试、错误归一成 `SourceError` |
| `tradetime.py` | 交易日 → 时刻的换算（`as_of_for_trade_date`）、此刻是否连续竞价 |
| `sanity.py` | 量级围栏（下一节） |

🔴 **`http.py` 的异常清单是六个 skill 共用的单点。** 实测踩过：
`http.client.IncompleteRead` 继承 `HTTPException` + `ValueError`，
**不继承 `OSError`** —— 于是截断响应绕过了 `except OSError` 的重试层，
一次网络抖动直接冒成未捕获异常，六个 skill 全中。
⇒ 这一层漏一个异常类型，影响面是全系统。

### 6.3 三个契约/存储侧的支撑模块

| 模块 | 为什么单独存在 |
|---|---|
| `skills/_contract/missing.py` | `MissingItem` 带**机器可读代码**（`market.turnover.date_mismatch`）—— 缺失项要能统计「哪个源最常缺」，自由文本做不到 |
| `skills/_store/schema.py` | 按版本号递增的迁移列表。**已发布的条目不许改动** —— 跑过 v4 的库不会重放它，所以补触发器只能开 v5 |
| `skills/_store/runtime.py` | 读 OpenClaw 运行时自己的 trajectory。🔴 **UTC → 北京时间的转换只在这里做一次**，消费方拿到的已经是北京时间 —— 这类 bug 的形状是「差 8 小时但仍是个合法时刻」，不报错 |

#### `sanity.py` —— 量级围栏，抓垃圾值不抓行情

```python
implausible_bars(window)        # 相对窗口**中位**收盘价差 5 倍以上 = 坏数据
INDEX_PCT_LIMIT / BOARD_PCT_LIMIT
```

🔴 **阈值卡在「物理上不可能」，不卡「看着不像」。** 60 日内腰斩是行情，
差 5 倍不是。定紧了会在极端行情里报红，而**永远报警的检查会被忽略**。

🔴 **用中位数不用均值**：一根 `low=0.01` 会把均值拉走，
拉走之后它自己就显得没那么离谱了。

⚠️ 抽成共享模块的理由是 F5：`market_calc` 早就有这类围栏、注释还写着
「不是为了抓行情，是为了抓垃圾值」，但**只长在那一个文件里**。
`technical_calc` 的 60 日高低点因此能吃出 3118 万 %，而 verdict 仍是 PASS。

⚠️ 同一层还有一条**反向**约束：解析层**不许把「字段缺失」吞成 0**
（F6）。缺失返回 `None`，让上层报 `missing` ——
吞成 0 会让「主力净流入前 5」在资金字段失效时给出一个
**任意但格式完整**的第一名，带着「0.0 亿」上卡。

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

**BigA 只有一个调度域**：`tools/cron/registry.yaml` → systemd timer。⬜ **未建**（Phase 3 才有第一条 cron）。
LLM 侧的定时任务也由 systemd 触发 `openclaw --profile biga agent --agent <id> -m "..."`，
**不使用 OpenClaw 内置 cron**。

⚠️ Phase 1 **一条定时任务都不建**。第一条 cron 出现的前提是：
它的产物已有**被证明的消费方**（§9 L-1）。

---

## 九、🔴 必须带进新系统的失败模式清单

L-1 ~ L-9 是从一套**长期运行的量化交易系统**里学到的失败模式，
L-10 是本项目自己踩出来的。
它们的共同点是：**失败时不报错**。测试绿、日志绿、监控绿，而事情已经坏了几个月。

因此每一条都不只是「注意事项」，而是配一个**在 Phase 1 就建立的、会报红的机制** ——
靠人记住是不够的，几个月后没人记得。

| # | 失败模式 | 为什么它是静默的 | 新系统的防护机制 |
|---|---|---|---|
| **L-1** | **零消费方**：模块在写数据，但没有任何代码读它；或工具被多处引用为权威，却从未被任何调度器执行 | 写入方本身工作正常，没有报错点。"被引用"看起来就像"在运行" | **产消对账测试**：任何写库的模块必须在调度注册表或调用图中存在读取方，否则红。判据是**调度命令的字面量**，不是"谁调用了它" |
| **L-2** | **闸 fail-open**：风控/纪律检查在数据缺失或条件算不出来时**静默放行**；未知配置键被忽略，规则退化成无条件命中 | 放行与通过的日志长得一模一样 | **买入侧一律 fail-closed**；`UNKNOWN` 独立于 `PASS`；算不出来必须进 `missing[]` 并显示在 Card 上 |
| **L-3** | **同一判据多份实现**：代码路由、涨跌停、费率等基础判断在各处各写一遍，其中相当比例是错的 | 错法全是静默的 —— 返回一个看似合理的错值 | **判据单一实现 + AST 扫描钉死**：不许存在第二份。一切走 `_contract` / `_store` |
| **L-4** | **基准有算术偏差**：超额收益的对照基准本身带正偏（右偏截面下均值≠中位、逐日连乘≠买入持有），导致随机策略也"跑赢" | 结论看起来显著，方向也符合预期 | **任何基准上线前必须过安慰剂检验**：拿随机标的喂同一条生产路径，要求 `\|t\| < 1.96`。⬜ **未建**（Phase 4，见 `TODO.md`）|
| **L-5** | **估计量依赖**：同一份数据，换一种标准误算法，显著性就反转 | 只报让结论存活的那一个，读者看不出来 | **报结论必须写明用的哪个估计量**，并同时给出多种；`n < 30` 一律标 underpowered |
| **L-6** | **文档漂移**：文档里的关键数字（表数量、数据量、测试数）与实际相差数倍 | 文档不会自己报错，而人会照着它做判断 | **关键事实由测试生成或校验**，不手抄 |
| **L-7** | **死配置**：因子/规则因命名空间不一致而交集恒空，长期零命中；或阈值判断在其作用域内恒真 | 它照常参与计算，只是永远不生效 | **可达性巡检**：每日报告「配了但从未命中」的规则与因子。⬜ **未建**。6 条阈值的**构建期**可达性已由 `tests/test_risk_check.py` 钉住（parametrize 到 `THRESHOLDS` 自己，新加一条自动纳入），但那不是 L-7 要的东西 —— 生产环境里长期不命中仍然无人察觉 |
| **L-8** | **账本幻影行**：记录了实际没有发生的事件（提交即记账，而提交不等于成交） | 账本自洽、总额对得上，但对应的事实不存在 | **raw 层永不改写**；状态变更一律追加而非 `UPDATE`，让「当时看到的」可重建 |
| **L-9** | **下单末端的时序陷阱**：父进程超时预算与子进程下单耗时倒挂、特定时段必须换委托类型、探针失败时继续执行、止损价贴死价格保护带下限 | 症状是"废单"或"没成交"，看不出是架构问题 | **Phase 1 不下单**，但把这四条冻结进 `docs/design/live-order-rules.md`，将来开下单时直接实现（⬜ **未建**，Phase 4+）|

| **L-10** | **结构化数据经 LLM 转述**：让 Agent「把上游的 JSON 原样带上」，再由下游 Agent 抄进命令 | 它不会拒绝也不会报错，只是**漏掉几个字段**。而漏掉的字段往往正是溯源字段 —— 错误要到几个月后做归因时才暴露 | **数据不经过语言模型**：skill 直接落 `agent_verdicts`，Agent 只传 id；修订走 CLI 而不是重打 JSON。并用测试钉死**契约文档里不许再出现贴 JSON 的写法** |

| **L-11** | **身份晚于证据**：决策编号在合成阶段才分配，于是采证时这次决策还没有身份，每个 Specialist 只好自己编一个 | 两次运行的判定原件写着同一个号 ⇒ **证据被合成进同一张卡**，而卡面上完全正常：agent 齐全、时间戳相近、缺失项照常上浮 | **身份先于证据**：Stage 0 原子占号（`decision_ids`，主键冲突仲裁），编号沿全链下传；临时号 `-000` **自曝身份且不得入账**；合成时拒绝混血 |

| **L-12** | **测试双打得太靠上**：回归测试 mock/fake 的对象，正好是**包含被修 bug 的那段逻辑本身**（或它最直接的调用者） | 测试绿、覆盖率数字也好看——双子从不失败，因为它只会返回测试作者手写的期望值。它验证的是"我有没有正确调用这个双"，被测逻辑本身**从没被真正执行过** | **回归测试必须经过真实数据路径**：真文件、真 sqlite 连接、真子进程，不 mock 掉包含 bug 的函数或它的直接调用者。约定一个可注入的"真实但一次性"的外部状态入口（如 `BIGA_RUNTIME_DB` 环境变量），让测试能构造**真实**的外部依赖，而不是伪造被测函数本身 |

⚠️ **L-10 / L-11 / L-12 是这张表里在本项目自己踩出来的三条**，其余九条继承自那套长期运行的系统。
L-12 的实例：F3 的第一版回归测试 mock 的是 `_runtime_spawn_records`——正是"按决策号过滤"这段
逻辑本身的容器，于是那段逻辑从没被测试真正执行过，外部复查一次真实攻击就把它的漏洞找了出来。
重修时把它换成一份真实构造的 sqlite 文件，走完整查询路径，同一类回归才第一次真正被防住。
实测形状：skill 输出 15 条 evidence 全带 `retrieved_at`，Specialist 转述后**一条不剩**；
落库 Card 上 25 条证据的 `retrieved_at` 被统一填成「Supervisor 敲命令的时刻」，
与真实采集时刻差 106 秒。更值得警惕的是，Agent 为此**给自己写了一份恢复文档**，
把「补一个当前时间当 retrieved_at」写成了标准操作 ——
**当 Agent 开始给自己写「怎么绕过这个错误」的文档时，那是架构在求救。**

⚠️ **L-9 是裁定 2「将来都有自动下单」的直接落点** ——
现在不实现，但先把知识存进去，代价近乎为零；等到要用时再重新踩一遍，代价是真金白银。

> 这张表是本设计里最重要的一节。
> 架构图可以照着任何一篇文章画，这几条只能靠踩出来。

---

## 十、延迟与成本预算

八 Agent 形态的代价是真实的。本节把它量化并给出压制手段。

### 10.1 延迟预算

| Stage | 内容 | 并行度 | 预估 | Phase 1 实测 | Phase 2 休市日 | Phase 2 **盘中** |
|---|---|---|---|---|---|---|
| 0 | Supervisor 拆任务、发出 spawn | — | 5-10s | **8.0s** ✅ | **9.8s** ✅ | 34.7s |
| 1 | market / sector / news / technical / emotion | **×5 并行** | 20-40s（取最慢那个） | **36.0s** ✅（只有 emotion） | **31.2s** ✅（4 个并行） | **113.6s** ❌ |
| — | 子 agent 交回到 Supervisor 的空档 | — | 未估 | 1.0s | 0.0s | 14.0s |
| 2 | risk / discipline | **×2 并行** | 15-25s | 未上线 | **16.4s** ✅ | **23.7s** ✅ |
| 3 | Supervisor 合成 + 渲染 Card | — | 20-30s | **30.0s** ✅（压线） | **13.6s** ✅ | 29.0s ✅ |
| | **合计** | | **60-105s** | **74.8s** | **71.0s** | **214.9s** ❌ |

> 🔴 **「60-105s」是下界之和与上界之和，不是预算区间。**
> 5+20+15+20 = 60，10+40+25+30 = 105。把 60 拿去当及格线，等于要求
> **四个阶段同时命中各自的最好情况**。
>
> Phase 1 实测把这件事证实了：**每个阶段都落在自己的预估区间内**
> （Stage 0 在区间内、Stage 1 在区间内、Stage 3 压着上沿），**合计仍然 74.8s**。
> 没有任何一个阶段「超支」，超的是那个加法。
>
> 另一处推理错误：原验收写「只有 2 个 agent，所以该 < 60s」。但 Stage 1 的下界
> 20s 描述的是「5 个并行 specialist 里**最快**那个」—— Phase 1 只有一个 emotion，
> 它就是 36s，没得挑。**减少 agent 数量不会把 Stage 1 拉到下界**，
> 而 Stage 0 + Stage 3 是与 agent 数量无关的固定开销（实测 38s，占 51%）。
>
> 实测方法见 `tools/verify/latency_report.py`；结论不依赖 Supervisor 自报的
> `elapsed_ms`（那个值实测在两个方向上都偏离过，低报 43%、高报 74%）。
>
> 🔴 **Phase 2 把 Stage 1 从 1 个 agent 加到 4 个，端到端反而少了 3.8s。**
> 这正是上一段那条推理的反面验证：Stage 1 的成本是 `max()` 不是 `sum()`
> （四个串行需 98.5s，实际墙钟 31.2s），而 Stage 0+3 的固定开销才是可压的部分 ——
> Phase 1 占 51%，Phase 2 降到 33%，靠的是把合成阶段的结构化数据改走
> `verdict_ref`（§9 L-10），Supervisor 不再逐条重打 JSON。
>
> ⚠️ **两次实测都在休市日**，Stage 1 的 agent 大多因数据未形成而早退。
> 盘中所有源都活着时这张表要重测 —— 在那之前不要把 71.0s 当成能力上限。
>
> 🔴 **盘中重测（`BIGA-20260921-009`，周一 09:52）：214.9s，超预算 2.4×。**
> 休市日那个 71.0s 不代表任何东西 —— 差了 3 倍。
>
> 差距**几乎全在 `sector` 一个 agent**：休市日 21.6s，盘中 113.6s。
> 盘中板块全部活跃 ⇒ 它要翻的页更多、要说的话更多（输出 1492 tok）。
> Stage 1 是 `max()`，所以一个慢 agent 就决定了整个阶段。
>
> ⇒ 出口条件 5 的结论：**预算 90s 在盘中不成立，但不要据此放宽预算。**
> 先看 `sector` 那 113.6s 里有多少是思考档位、多少是真的在翻页
> （`agent_trace.py --agent sector -n 1`），**再谈改数字**。
> 「指标红了就调指标」是本项目明令禁止的（开发流程第 7 条）。

#### 🔴 预算重推（Phase 2 出口条件 5）

四次盘中实测的分解（单位：秒）：

| 运行 | Stage 1 墙钟 | 最慢的那个 | risk | main 模型时间 |
|---|---|---|---|---|
| 09:52 · 4 agent | 113.6 | sector 113.6 | 23.7 | 94.4 |
| 10:40 · 6 agent | 64.2 | news 63.4 | 21.0 | 120.7 |
| 10:45 · 6 agent | 80.5 | news 80.1 | 22.4 | 94.2 |
| 11:01 · collect | 78.8 | news 78.3 | 19.9 | **73.9** |

**结论：90s 在当前架构下做不到，而且原因是结构性的。**

即使每一项都取实测最好值：

```
Stage 1 最好 64.2  +  risk 最好 19.9  +  main 最好 73.9  =  158s
```

⇒ 新预算 **180s**（留 14% 余量）。

⚠️ **这不是「把红灯调绿」。** 改预算必须同时说清三件事，否则就是作弊：

**1. 旧数字错在哪** —— `60-105s` 是在**一个 agent 都还没有**的时候，
把四个阶段各拍一个区间再相加得到的。它是一个愿望，不是一个预测。
（同一段早就记过另一个加法错误：下界之和不是预算下界。）

**2. 哪些部分不在我们控制内** ——

| 组成 | 可控性 |
|---|---|
| Stage 1 最慢的那个 | 🔴 **不可控**。它是第三方接口的延迟，而且实测会渐进限流 |
| `main` 的合成 | ✅ 可控，是当前最大的可优化项 |
| `risk` | 稳定在 20s 上下，无优化空间 |

**3. 什么时候这条预算该红** —— 超过 180s 时，它说的是
「某个组成异常了」而不是「今天慢一点」：
实测四次的最大值是 214.9s（那次 sector 还没优化），
优化后三次都在 158–173s。180s 会红，意味着有东西真的变了。

#### ⏩ 2026-09-21 17:18 补：**180s 只覆盖盘中，盘后会超**

首次盘后端到端（`BIGA-20260921-020`，日线发布之后）：

| | 盘中（四次实测的基准） | 盘后（本次） |
|---|---|---|
| 端到端墙钟 | 172.6s | **198s** 🔴 超预算 |
| 成本 | $1.20 | **$1.37** |
| `news` 单轮 | 78.3s / $0.30 | **100.0s / $0.42** ← 最大的一笔 |
| 60 分钟窗口内快讯 | 67~79 条 | **184 条** |

🔴 **原因不是「今天慢一点」，是收盘后那一小时的快讯量翻倍。**
窗口同样是 60 分钟，条数从 ~70 涨到 184 —— 而 `news` 的 prompt 长度
直接跟着条数走。180s 这条预算是用**四次盘中实测**推出来的，
而当时所有实测都在 15:00 之前（与 F4/FIX-03 躲过一整天是同一个原因：
**测试窗口系统性地避开了某个时段**）。

⚠️ 顺带暴露第二个问题：184 条超过 `MAX_ITEMS=120` 上限，
**64 条没有被看过**，如实上浮成缺失项 `news.window.truncated`。
所以盘后不只是更慢更贵，**看到的还更少**。

⬜ **这条预算怎么改，留作待裁定** —— 不在这里顺手把它调到 210s。
三个方向各有代价，需要先有更多盘后实测：

| 方向 | 代价 |
|---|---|
| 抬高预算到覆盖盘后 | 盘中的异常会不再报红 |
| 分时段两条预算 | 「什么时候该红」要写两遍，L-3 的形状 |
| 压 `news`（缩窗口 / 提上限） | 缩窗口会漏消息；提上限更慢更贵 |

> 🔴 **改预算之前先问「什么时候它不该红」。** 现在只有一次盘后实测，
> 回答不了这个问题 —— 而这正是旧预算 `60-105s` 当初犯的错：
> 拿一个愿望当预测。

#### 还能往哪压

按可控性排序：

1. **`main` 的合成**（73.9s，其中约 22s 零工具调用在组合 synthesize 命令）——
   命令模板已经写进契约了，剩下的是 headline/synthesis 的文字生成。
2. **`news` 的 prompt**（79 条原文 ≈ 6k token，它是最慢的 Specialist）——
   但缩小窗口就缩小了发言范围，见 `phase-2-specialists.md` §3.12 的权衡。
   **这是一个诚实性与速度的交换，不是单纯的优化。**
3. Stage 1 的其余 agent 都在 30s 上下，压它们对 `max()` 没有意义。

**串行做法会是 2-5 分钟** —— 差距全在 Stage 1/2 的并行。
因此 `agents.defaults.subagents.maxConcurrent ≥ 6` 是**功能要求不是调优**。

三条压制手段：
1. **并行 spawn**，不用串行 `sessions_send`
2. **`context: "isolated"`**，不用 `fork` —— specialist 不需要 Supervisor 的对话历史，
   fork 会把整段 transcript 复制进去，token 直接翻倍
3. **数字全部由 Python 算好再进 prompt** —— agent 读的是结论不是原始表

### 10.2 成本可观测

`agent_runs` 表逐次记录 agent / model / 耗时 / token。
**不预先优化** —— 但要保证测得出来。

#### Phase 2 成本分解（`BIGA-20260921-016`，盘中六 Agent，全部 sonnet-5）

| agent | 耗时 | 输出 tok | 缓存读 | 缓存写 | 成本 |
|---|---|---|---|---|---|
| `main` | 172.6s | 5665 | 746k | 91k | **$0.4373** |
| `news` | 78.3s | 2444 | 293k | 54k | $0.2189 |
| `sector` | 61.9s | 3015 | 96k | 37k | $0.1421 |
| `emotion` | 35.4s | 1349 | 67k | 36k | $0.1166 |
| `market` | 32.7s | 997 | 68k | 36k | $0.1133 |
| `technical` | 30.0s | 1287 | 64k | 34k | $0.1114 |
| `risk` | 19.9s | 846 | 86k | 12k | $0.0560 |
| | | | | **合计** | **$1.20** |

三条读法：

**1. `main` 一个占 37%。** 它的缓存读 746k 是第二名的 2.5 倍 ——
因为 Supervisor 要把所有 Specialist 的回答读进上下文。
Stage 1 再加 agent，涨的主要是这一项。

**2. `news` 是最贵的 Specialist**（$0.22，缓存读 293k）。
它的 prompt 里有 79 条快讯原文 ≈ 6k token —— 这是**设计选择**
（见 `phase-2-specialists.md` §3.12 的额度权衡），不是意外。

**3. 🔴 这一轮是冷缓存。** 缓存写 91k 偏高说明刚改过配置。
**冷热缓存的成本不可横向比较** —— 热缓存下 `main` 通常在 $0.36 左右。
`latency_report.py` 会自动标注这一点。

#### 要不要降档某个 agent 的模型

**现在不降。** 判据还不够：

- $1.20/次 × 一天几次 = 可接受，还没到要优化的量级
- 降档会改变结论质量，而**结论质量目前没有度量**（那是 Phase 4 的事）
- 唯一明显的候选是 `main`，但它恰恰是最不该降档的 —— 合成是判断最密集的一步

⇒ 记录，不动手。**先有区分力检验，再谈降档。**

---

## 十一、Phase 1：最简功能验证

> 📄 **已完成并冻结** —— 完整内容见
> [`phase-1-walking-skeleton.md`](phase-1-walking-skeleton.md)。
>
> 这里只留指针。本文是**常青**文档（永远描述当前状态），
> 而 Phase 1 的设计描述的是一个**已经结束的阶段** ——
> 生命周期不同的东西放在一起，读者就无法判断哪些还作数。

结论：`main` + `emotion` 两个 agent，九项验收全过，端到端 74.8s / $0.2179。

Phase 2 及以后的设计见 [`phase-2-specialists.md`](phase-2-specialists.md)。

---

## 十二、路线图

| Phase | 内容 | 出口条件 |
|---|---|---|
| **1** | walking skeleton（§11） | §11.3 七条 |
| **2** | 补齐到 **7** 个 agent（不含 `discipline`，见下）+ Stage1/2 并行 + 完整 Card | `missing[]` 在真实缺数据时非空 ≥5 次；延迟预算按实测重推 |
| **3** | 数据层加厚（历史回补、更多 collector）+ **`discipline` 及其输入源** + 独立飞书应用 + 第一条 cron | 每条 cron 都有被证明的消费方（L-1） |
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
