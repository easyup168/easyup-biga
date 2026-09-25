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
    │   ├── _contract/   evidence.py verdict.py card.py run.py ← 契约唯一实现
    │   ├── _store/      db.py  runs.py  schema.py            ← 唯一 DB 入口
    │   ├── _runtime/    mcp.py  adapter.py                   ← Specialist 生命周期唯一入口（批 C-I）
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
    │        readback_check.py  毒行巡检：agent_verdicts/decision_records 里存在但读不回来的行
    │        audit_public.sh    公开内容审查（十一项）
    │        probe.sh           在一次性库上跑手工探针
    │        budget_report.py   当日出卡用量与闸门状态
    │        sync_test_count.sh 把文档里的测试条数同步成实测
    │        phase1_acceptance.py  Phase 1 验收（I-1 判据转调 isolation.py）
    │        _verdict.py      🔴 退出码的**唯一定义**（0/1/2 = 过/不过/判不了）
    │                ⚠️ placebo.py / reachability.py **设计中，从未提交过**
    │                   —— 原文把它们与真实文件并排列出，读起来像已建成
    ├── tests/      _scan.py    三个 AST 扫描器共用的**唯一**文件枚举
    │               conftest.py 全局禁网围栏（`@pytest.mark.network` 豁免）
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
| **`main`** | **BigA Supervisor** | Opus | 其余 7 个全部 | 人的唯一接触点；对话式答疑与解释。⚠️ **出 Card 批 C-II 起不再由它驱动** —— 那是程序（`orchestrator.py`），它够不到 |
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

**➕ `synthesizer`（编排支撑，不计入业务 8 个）**：批 C-II 新建的**综合判官**，
`allowAgents=[]` 叶子，模型 Sonnet。它只产出整张卡的 `status`/`headline`/`synthesis`，
不采数据、不产 `AgentVerdict`、不搬运证据。综合判断从 `main` 手里移到它这里，正是因为
它在能力上就 spawn 不了任何东西 —— `main` 若兼任判官会把 L-14 的攻击面又带回来。
`tests/_consistency.py` 把它登记为 `SUPPORT_AGENTS`，`built_specialists()` 不含它，
roster 一致性检查要求配置里有它。

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

### 3.3 调用链（参考文档 §12 + 并行化 + 批 C-II 程序驱动）

🔴 **批 C-II 之后 `main` 不在这条链上。** 编排是一段程序
（`skills/decision-card/scripts/orchestrator.py` 的 `DecisionOrchestrator`），
由**人 / cron** 经 `bin/biga-card` 触发。`main` 是个 agent，够不到这个 Python 对象 ——
不是「守卫拦住它」，是根本没有一条工具调用能到达（L-14 那条边界从提示词约定变成程序结构）。

```
人 / cron ──► bin/biga-card ──► DecisionOrchestrator（Python，orchestrator.py）
         │
         │ Stage 0：reserve_decision_id（占号在 open_run 之前，decision_id 从头非空）
         │ Stage 1：并行 spawn 5 个（**共用一个 groupId**，maxConcurrent ≥ 5）
         ├──► market ──┐
         ├──► sector ──┤
         ├──► news   ──┼──► AgentVerdict × 5（含 evidence）
         ├──► technical┤
         └──► emotion ─┘
         │
         │ Stage 2：spawn 制衡层 risk，输入 = Stage 1 的冻结证据（verdict_ref）
         └──► risk ──► AgentVerdict（否决 / 警示 / 放行 / 无法判定）
         │
         │ Stage 3：spawn synthesizer 判官（allowAgents=[] 叶子，无 spawn 能力），
         │          拿结构化 status/headline/synthesis；**证据由程序从冻结 verdict 组装**
         ▼
   BigA Decision Card  ──► decision_records 落库（可回放）
         ▼
   Human-in-the-loop
```

⚠️ **综合判断为什么是 synthesizer 而不是 `main`**：判官若复用 `main`，它带着 `main`
的全套 spawn 能力，一个提示词注入就能让它再拉起一轮编排（L-14）；`allowAgents=[]` 的
叶子 agent 从能力上就做不到。判官只出判断，不采数据、不搬运证据。

⚠️ **`discipline` 按裁定 13 不建**（没有输入源），所以 Stage 2 目前只有 risk。参考文档
§12 画的是 `Risk → Discipline` 串行；将来 discipline 上线时，若它不需要读 risk 的结论
就可并行（两者输入不相交），需要则串行 —— 由 §12 的验收项守着。批 F 可能前移 risk 的调用时机。

#### 3.3.1 运行时适配层 `skills/_runtime/`（确定性编排批 C-I 加的）

上图里「spawn 一个 Specialist」这个动作，批 C-I 之前只有 LLM 轮次能做
（`sessions_spawn` 是暴露给 agent 的 MCP 工具，CLI 里没有对应子命令）。
spike（设计文档 §7）证明了 Python 能不经 LLM 轮次驱动它：`biga attach
--print-config` 铸一个 MCP grant，再对 `127.0.0.1:<临时端口>/mcp` 做 JSON-RPC。

批 C-I 把这条通路收成一层，**Specialist 生命周期的唯一入口**：

* `skills/_runtime/mcp.py` —— 传输层。`Grant`（铸/持/删那个临时 `.mcp.json`，
  context manager）+ `MCPClient`（`initialize` 握手 + `tools/call` 的 JSON-RPC，
  SSE/JSON 两种响应都认）。
* `skills/_runtime/adapter.py` —— `OpenClawRuntimeAdapter.start / wait / cancel /
  status`。🔴 **状态归一化**：对外只暴露自定义的 `SpawnStatus`
  （running/succeeded/failed/timeout/cancelled/**unknown**），运行时原始措辞
  （accepted/queued/done/killed/forbidden…）只在这一层翻译 —— 运行时改一个词，
  只改这里的映射表。认不出来的状态落 `UNKNOWN`，**绝不当 SUCCEEDED**（R-3）。
* `tools/verify/adapter_spike.py` —— 对着**真实运行时**把 spike 的三个未知数
  测掉（五路并行相交 / grant 撑过 780s / 失败结构化），运行时升级后可重跑复验。
  🔴 它驱动真实的 `OpenClawRuntimeAdapter`（不另写一套 MCP 调用），且**会花钱**。

**批 C-II 已把 Adapter 接进 `DecisionOrchestrator`**：`orchestrator.py` 用它驱动
Stage 0→3（占号 / 五路 fan-out / risk / 判官 / 合成落库），走 8 步细粒度状态链
（批 B 的 `LEGAL_TRANSITIONS`）。`bin/biga-card` 收缩成薄 CLI：五道守卫 → 调
orchestrator → `spawn_check` + `readback_check` → 退出码，不再 spawn `main`、不再抽提示词。
usage 落 `run_events.detail`（唯一真相源是运行时 trajectory，不在 `agent_runs` 建第二套）。

---

## 四、通信契约

### 4.1 四个数据结构（`src/easyup_biga/domain/`，唯一实现）

🔴 批 H-I（2026-09-23）：本节及本文档其余处提到的 `skills/_contract/`、
`skills/_store/`、`skills/_sources/`，真实实现已迁至
`src/easyup_biga/{domain,persistence,providers}/`。旧路径原地保留 re-export
薄壳（`from _contract import ...` 等全仓导入语句一字不改），但本文档描述的是
**现状**（裁定见 `deterministic-orchestration.md` §13），下面统一改指新位置。

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
    evidence_set_id: str|None = None  # 🔴 批 E-I：指回 evidence_sets.evidence_set_id
                                      #    （读冻结快照的 Specialist 填；risk CROSS_CHECK 直接比它）

# 🔴 上面这个 raw_hash「派生字段允许为空」是**现状**，不是终局。
#    实测：派生证据占全部证据的 55%（1721/3152），而它们 raw_hash 与
#    evidence_set_id **全部为空** —— 溯源链停在 `derived:` 那个字符串上。
#    该怎么办已由**裁定 16** 定死（kind 三分类 + 派生值必须声明输入，粒度按字段定），
#    规格在 `TODO.md` 的「批 1 / 批 2 输入」。⚠️ **尚未实现**，所以本节不写它的字段 ——
#    常青文档描述现状，未实现的设计放进来就会变成长期挂着的「设计中」（Phase 1 §11 的教训）。

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

#### 4.1.2 事实与判断拆开（`src/easyup_biga/domain/facts.py`，批 E-I 起）

`status`/`verdict`/`stance` 是「三个不能混的问题」，但它们还焊在**一个** frozen
`AgentVerdict` 里 —— 事实（skill 跑完就有）与判断（Agent 事后补的 `stance`）产生方
不同、时机不同。焊在一起的代价是实测事故：Agent 想只加一个判断，就得把整份事实重打
一遍（`BIGA-20260920-002` 那次丢了 15 条 evidence 的 `retrieved_at`，L-10）。

批 E-I 起把它拆成三个类型（`src/easyup_biga/domain/facts.py`）：

| 类型 | 装什么 | 谁产 |
|---|---|---|
| `FactBundle` | 事实：`result`/`evidence`/`missing`/`status`/`verdict`/… —— **AgentVerdict 减去 stance** | skill |
| `AgentAssessment` | 判断：`stance` + 指回哪一份 FactBundle（`fact_ref`，**不抄事实**） | Agent |
| `AgentOutcome` | 组合视图：FactBundle + AgentAssessment，跨型铁律（UNKNOWN⇒无法判定）在此校验 | 程序 |

🔴 **铁律不因为拆了就松**：事实层三条铁律走 `verdict.check_fact_invariants` **唯一实现**
（FactBundle 与 AgentVerdict 共用，防 L-3）；stance 走 `check_stance_vocab` +
`check_stance_vs_verdict`。`LegacyAdapter` 把任何历史 `AgentVerdict` 拆回新三型
（**读路径宽**）；新落库只收新形状（**写路径严**，§9），旧格式随时间自然清零。

⚠️ **迁移是渐进的**：E-I 只迁 `emotion` 一个试点（其余五个 skill 仍产 `AgentVerdict`，
E-II 起再迁）。过渡期新旧同住 `agent_verdicts`（`kind` 列区分），`_store.load_verdict`
把两种形状都压回旧消费者认识的 `AgentVerdict` —— `card_ops`/`risk_check`/`DecisionCard`
因此零改动。`amend_verdict.py` 未退役（退役前提是**全部** Specialist 迁完）。

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

### 5.1 存储分四个平面：控制面现在建，其余三个选型已定、触发条件已定、暂不建

总体设计 §33 与数据架构 §22 把存储定成四个平面，各自选型不同。
**只有控制面现在真的建**——其余三个平面「选型已定、触发条件已定、现在
没有消费方」，按 §9 L-1（没有消费方之前建了就是空仓库）暂不建。

⚠️ 这一节曾经只写控制面一个平面（历史上就叫「不上 PostgreSQL + Redis」），
批 I（RawArtifact）开工前发现这样写会被误读成「切 PostgreSQL 的触发条件
管着全部存储决策」——它从来不管 Parquet/DuckDB/Raw 文件那三面，所以拆开写。

#### 控制面（Control Plane）—— 现在唯一建的一个

对参考文档 §8 的一处偏离：它建议 PostgreSQL + Redis，本设计用 SQLite。

| | 文档主张 | 本设计 | 理由 |
|---|---|---|---|
| 事实层 | PostgreSQL | **SQLite (`biga.db`, WAL)** | Phase 1 是「最简功能验证」，单机单用户单写进程。PG 带来的是运维面而非能力 |
| 实时层 | Redis | **同库 + 进程内 TTL cache** | 无跨机需求；Redis 的用途（缓存/状态）单机下 SQLite+内存即可 |

装的是什么：事务性、状态机性质的表——现有 `decision_runs`/`run_events`/
`agent_verdicts`/`decision_records`/`notification_outbox` 等十张表全部是
这一类（数据架构 §22 把这几类表点名归为控制面）。

**切 PostgreSQL 的触发条件**（任一成立即切，写死在这里免得凭感觉）：
1. 出现 **>1 个并发写进程**（例如采集与决策分离部署）
2. 需要**跨机**访问同一份事实层
3. 单表 > **5000 万行**（作为参照：一套跑了半年的日线库，最大表也只在百万行量级）
4. **多人同时使用**（不是 1 的子集：可以每人一个进程、进程数不变，
   而冲突来自人不在同一时刻协调）
5. **SQLite 长期锁冲突**（唯一一条**按观察到的症状**触发的 —— 1~4 都是结构判据，
   结构没变但实际就是在锁，说明判据漏了一种形状，此时以症状为准）

⚠️ 4、5 两条是 2026-09-25 从外部 Phase 3 设计包合并进来的增量。
那份包另起了一份 ADR 记同一套四平面选型 —— **没有落它**（同一判据两个出处就是
L-3，改了一份忘另一份，剩下那份仍然看起来权威），只把它真正新增的这两条搬进来。

为此，所有 DB 访问必须经 `src/easyup_biga/persistence/db.py`，**业务代码里不许出现裸
`sqlite3.connect`** —— 这样切 PG 只改一个文件。由 `tests/test_no_raw_sqlite.py`
钉住。

#### 历史数据面（Historical Data Plane）—— 选型已定，未建

技术选型：**Parquet**（总体设计 §33、数据架构 §22）。

装的是什么：批量时间序列型数据集——`daily_bars`/`minute_bars`/
`financial_facts`/`news_items`/`announcements`/`sector_constituents`
这一类（数据架构 §22），不是控制面的事务性表。

🔴 **触发条件不是「数据长大了才搬」，是「这一类数据集第一次被建的时候
就该用 Parquet」**——数据架构 §35 步骤 4 把这条钉得很具体：「全市场
EOD Daily Bars → 接入 Parquet 数据面」。对应本仓库语境：**§45「第一版
完整市场数据」那一批（批 L 之后）开工时**触发，不是今天。批 L 现在只做
`cn.trading_calendar` 一个——小、结构简单、单一时点查询为主，可以先留
SQLite「打样」，不必为了一张日历表就先建 Parquet 管线。

#### 分析查询（Analytics Query）—— 选型已定，未建

技术选型：**DuckDB**（总体设计 §33、数据架构 §22）。

🔴 **这一面不是独立触发的**——DuckDB 是查 Parquet 用的引擎，历史数据面
一旦真的有 Parquet 文件，分析查询自然就是 DuckDB，不是另一个单独判据。
真正有意义的触发点是 **§46「第一版选股闭环」开工**（`EOD Snapshot →
FeatureSet → Screening → CandidateSet`，需要对全市场做批量特征计算/筛选，
这是 SQLite 单机单进程模型从未打算覆盖的查询形状）。

#### Provider 归档（Provider Archive）—— 选型已定，未建

技术选型：**Raw 文件**（`.json.gz`/`.csv.gz`/`.zip`/`.bin`，数据架构 §9）。

装的是什么：Provider 返回的原始响应体本身；SQLite 侧的 `raw_*` 表只留
`body_uri`/`body_sha256`/元数据，不再把整个 body 塞进一列。

🔴 **触发条件按数据集类型判，不按体积判**（体积门槛数据架构材料里没给，
本仓库也没有历史数据量做参照，拍一个字节数出来是拍脑袋——数据集类型
才是材料实际给出的判据）：
- **全市场/批量性质的抓取**（例如全市场日 K、新闻批量窗口）——从建的
  那一刻就走文件，不等它长大。这与历史数据面的触发条件是同一个判断：
  批量时间序列数据集第一次被建时，raw 与归一化后的存储形态一起换。
- **单标的/小体积的抓取**（例如单个指数、单只个股，本仓库现有的
  `raw_market_snapshot` 都是这一类）——继续留在 SQLite 的 `raw_*` 表里，
  不因为「未来某天可能变大」而提前搬。

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

⚠️ 这张图是**控制面现在的实况**，不是永久形状——批量时间序列数据集
（历史数据面那一批，见 §5.1）建成后，`raw_*`/`fact_*`/`d_*` 不会整体
搬家，是**新数据集从建的那一刻起就走 Parquet + Raw 文件**，`raw_market_
snapshot` 这类单标的小体积数据集会继续留在这里——`raw_*` 这个前缀今后
指的是「控制面里那部分 raw」，不是「全部 raw」。

### 5.3 核心表（Phase 1 只建带 ★ 的）

| 表 | 用途 |
|---|---|
| ★ `decision_records` | Decision Card + 全部 Verdict + 证据（回放的唯一真相源） |
| ★ `agent_runs` | 每次 spawn 的 agent/耗时/status（成本与延迟可观测） |
| ★ `raw_market_snapshot` | 采集原样落盘 |
| ★ `agent_verdicts`（v3） | **判定原件** —— skill 写、synthesize 按 id 读 |
| ★ `decision_ids`（v4） | **编号分配器** —— Stage 0 原子占号，见 §5.3.2 |
| `decision_runs`（v7） | **运行身份头** —— 一次执行尝试的不可变身份，见 §5.3.4 |
| `run_events`（v7） | **状态转移日志** —— 当前状态 = 最新一行；CAS 靠它，见 §5.3.4 |
| `evidence_sets`（v7） | **冻结数据切片登记**（批 D 起有生产方，批 B 只建表带触发器） |
| `notification_outbox`（v12） | **外发通知队列**（批 G-I，第 9 张表）—— 与 Card 同事务入队，幂等键 `(event_type, aggregate)`，见 §5.3.5 |
| `notification_deliveries`（v12） | **投递尝试日志**（批 G-I，第 10 张表）—— 「投没投成」是派生查询，不给 outbox 开原地改例外 |
| `fact_trading_calendar`（v15） | **这个仓库第一张真实的 `fact_*` 表**（批 L）—— A 股交易日历，`market_is_open()` 查它。🔴 生产方 2026-09-25 换成 `src/easyup_biga/providers/sina_calendar.py`（见 §5.3.7），此前走深交所端点、本机连不通，表**长期为 0 行** |
| `fact_stock_daily` / `fact_index_daily` | 归一化日线（**尚未建** —— 等 §45 的市场数据那一批，形状由选股闭环定） |
| `d_emotion_daily` | 情绪分 |
| `d_sector_strength` | 板块强度 |
| `raw_news` | 带 `published_at` / `source` / `retrieved_at` |

v16 到 v20 **都不新增表** —— 只给已有表加列与改约束，所以上表没有对应新行：

| | 加了什么 | 为什么 |
|---|---|---|
| v16 | Run Provenance 三列 + `ux_evidence_set_per_run` | 「这张卡属于哪次执行」变成一句 SQL |
| v17 | Fact 唯一约束按 **run** 分区 | 同一决策的第二个 run 能写自己那份事实 |
| v18 | `ux_evidence_sets_run` | ⚠️ **多余的**，见下 |
| v19 | `agent_runs.provenance_mode` | 区分在线执行行与历史行，是 Run 级 spawn 核验的三态判据 |
| v20 | 撤掉 v18 | 一条不变量不许有两个名字 |
| v21 | `ux_online_agent_run_once` | 一次 run 一个 agent 至多一条在线账本行——多条时一条真 `runtime_run_id` 会把另一条伪造的**盖住** |
| v22 | `decision_runs.expected_spawn_agents` + `ux_online_runtime_run_id` | 冻结「这次该启动谁」；一个运行时 id 不许给两行背书（见 §5.3.8）|

🔴 v18 / v20 这一来一回值得留在这里，因为它是 **L-3 的一个新鲜样本**：
v18 照抄外部评审给的建议索引，**没有先查这条不变量是不是已经有人在守** ——
它与 v16 的 `ux_evidence_set_per_run` 同表、同列、同 `WHERE`，逐字相同。
两个名字守同一条规则的后果不是浪费，是**改规则时会漏掉一个，而且不报错**。
⇒ 评审给的是**形状**，不是「你缺这个」；照抄之前先 grep 一遍。
由 `tests/test_run_provenance.py::test_一个run至多一个切片的约束只应有一条` 钉住。

完整 22 个版本各一句话摘要见 [`schema-rollback.md`](../guide/schema-rollback.md)，
权威说明仍是 `schema.py` 逐条迁移体正上方的注释。

⚠️ **只读打开一个还不存在的库**会抛 `StoreNotInitialised`（v5 加），
而不是裸的 `sqlite3.OperationalError`。它与「schema 建好但零行」是两回事 ——
后者是全新环境的**正常状态**，把它也报成错会有人为了消警告去塞假数据。
巡检工具据此统一退出码 2（判不了），见 §9 的 R-3。

#### 5.3.8 Spawn 证明为什么必须是**三元组**

要证的不是「这个 runtime id 在运行时库里出现过」，是：

```text
(BigA orchestration_run_id, agent, OpenClaw runtime_run_id)
```

少任何一条边，都有一条真实可走的路绕过去（外部评审 2026092503 实测两条 PoC）：

| 少哪条边 | 后果 |
|---|---|
| 不比 agent | 一次真 spawn 可以给**另一个 agent** 背书（news 的 run 证明 market 的行）|
| 不比 BigA run | 同一 decision 下**另一次编排**的 spawn 可以给这次背书 |
| 不查期望名单 | 只证明了一个 agent，`spawn_check` 照样退 0 |

🔴 把两边绑在一起的那根线，是**运行时记下的任务文本里带着 BigA 的 run id**
（`_contract.RUN_MARKER_PREFIX`）。这段文本是 OpenClaw 抄下来的，BigA 事后改不了它
—— 这正是「被验证方写不到的地方才算证据」那条前提的落点。

⚠️ **不要拿提示词里那句 `--run-id …` 当判据**：那是给 agent 的指令，措辞会改
（已经改过几次），拿它解析就是按字符串形状写判据（L-13）。

🔴 **期望 Verdict 名单 ≠ 期望 spawn 名单。** 卡上的 `expected_roster` 回答
「该有谁的判定」；`decision_runs.expected_spawn_agents`（v22）回答「该**启动**谁」。
`risk` 可以在确定性早退里由编排器直接算出事实、根本不被 spawn —— 它有 Verdict
但不该有运行时记录。两份名单合并，那两条正确路径就会被核成红的。

计划必须在**开 run 时**冻死：将来加 / 删 agent 之后，用今天的 Registry 去核一张
老卡会得出错的期望集。没冻过计划的老 run **整体降级为 UNKNOWN**，不退回今天的
Registry 报 PASS。

#### 5.3.9 执行账本的三个写入口

裸插入接口是私有的（`_record_agent_run`）。公开面上只有三个，名字就说清了
它写的是**哪一档证据**：

| 入口 | provenance_mode | 什么时候用 |
|---|---|---|
| `record_online_agent_run` | `online` | 三字段齐、`task_id==decision_id`、runtime id 未被别的在线行占用 |
| `record_unproven_spawn_attempt` | `online_unproven` | 确实 spawn 过，但没捞回 runtime id |
| `record_legacy_agent_run` | `legacy` | 历史 / 手工路径，不带 run |

🔴 **`runtime_run_ids is None` ⇒ `persist()` 一行都不记。** 映射**就是**执行溯源
本身。旧语义「不给映射就给所有 verdict 记账」会让 standalone 合成写出一串自称
在线、却证不了任何事的幽灵行 —— 它根本没 spawn 过任何东西。

🔴 **`online_unproven` 为什么要单独一档**：漏账比记一条判不了的账更糟，但
**把判不了的账记成「已证明」比漏账还糟**。核验侧把它判成 UNKNOWN。

#### 5.3.7 交易日历：为什么换源、以及那三层

**2026-09-25（中秋）实测撞到的事**：系统在**休市日**出了一张卡。六个 agent 里
`news` 按「今天」报交易日 `20260925`，日线类报 `20260924`，`risk` 因交易日不一致
判 `stance=无法判定` —— 而 `20260925` 根本不是交易日。

根因：`fact_trading_calendar` **0 行**。原生产方走深交所官方端点，而本机连不通它
（`src/easyup_biga/providers/szse.py` 模块头写明的网络事实）。于是 `market_is_open()` 恒走 weekday 回退，
对任何工作日上的法定节假日都答「开市」。

🔴 **用指数日线反推交易日是不够的** —— 盘中日线**不含当天** bar（就是
`phase-2-specialists.md` §3.10 记的那条「盘中日线类报上一交易日」）。它只能回答
过去，回答不了「今天开不开」，而那正是出问题的那个问题。必须要**前瞻**日历。

实测可达的前瞻源只有一个：新浪那份 `klc_td_sh.txt`（1990-12-19 → 次年年末，
含交易所已公布的排期）。它是**压缩编码**的 —— 508 字节压 8796 个日期。
通行做法是引一个封装库、把一段 ~17KB 的混淆 JS 丢进 JS 引擎跑；本项目读懂那段
编码之后自己重写了**日历那一支**（`src/easyup_biga/providers/sina_calendar.py`），
解码结果与通行实现**逐一比对完全相同**。那个编码是一族格式（日 OHLCV / 分时 /
收盘序列……），只实现了日历那支，撞到别的支会报出 format id —— 其余五支现在
没有消费方，预先写出来就是 L-1。

`market_is_open()` 因此变成三层：

| 层 | 判据 | 答不出时 |
|---|---|---|
| 1 | 周末短路 | — |
| 2 | `fact_trading_calendar`（由 `bin/biga-calendar` 刷新） | 落到第 3 层 |
| 3 | 硬编码节假日兜底，**带覆盖区间守卫** | 落到第 4 层 |
| 4 | 工作日 ⇒ 当作可能开市（R-3 的安全方向） | — |

🔴 **第 3 层的覆盖区间守卫（两端）是这次唯一真正的新东西。** 硬编码假期表会烂，
而且是**静默**地烂：表停更之后，每个新节假日都被悄悄判成交易日。守卫把它换成
「区间外一律弃权」——绝不乐观地答 True。并且：

- 表**从权威数据导出**，不手抄（`tools/verify/dump_holiday_fallback.py`）。
  ⚠️ 第一版是手抄的，照着另一份同类系统的假期表把国庆后的复市日 `20261008`
  抄成了休市日 —— 而那发生在写完「硬编码表会烂」这段注释之后十分钟内。
  判据只能是「从数据导出」，不能是「仔细一点」。
- `tests/test_sina_calendar.py` 有一条**到期前 45 天会变红**的测试，
  把「静默地烂」换成「按期响亮地提醒」。

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

**所有表全部只追加不修改，由 SQLite 触发器强制**（schema **v7**）。
判据不数表的张数（数字会漂），而是
`tests/test_store.py::test_每张表都有只追加触发器` 扫 `sqlite_master`
实际有哪些表。

⚠️ 这句话在 v4 时期是**假的**：原文写「四张表」，而当时已经有五张，
且新加的 `decision_ids` 恰恰是唯一没有触发器的那张（外部评审 F1）。
一条 `DELETE` 就能让同一个号发两次 —— 而 FIX-01 / FIX-02 两道身份闸门
校验的都是「这些判定的 task_id 是不是同一个」，号回收之后两次运行
**真实自洽**，两道闸门会一致放行。

⇒ v5 补上触发器，并把判据从「数几张表」换成
`tests/test_store.py::test_每张表都有只追加触发器` ——
它扫 `sqlite_master` 里**实际有哪些表**，例外要在 `EXEMPT` 里自己举手。
**新表默认就该受保护**，而手写的数字只会在下一次加表时再错一遍。

#### 5.3.3 写边界重校验 + 严格 JSON（确定性编排批 A-I 加的）

`save_verdict` / `save_card` 曾经只在**读**的时候校验（`from_dict()` 重跑
`__post_init__`），写的时候不校验：`v.missing.append(...)` 这类构造后直接
改字段的写法能绕过契约层，`save_verdict(非法对象)` 会成功落库，
只有下一次 `load_verdict()` 才炸出 `ValueError`——而 `agent_verdicts` /
`decision_records` 都是只追加表，**一次误写就让那次决策永久无法回放**。

⇒ 三个写函数在 INSERT 之前都先走一遍：
`Domain Object → 规范序列化（拒绝 NaN/Infinity）→ 严格重建 → 不变量校验 → DB`。
`save_card` 的重建显式传回 `card.from_store`，不能让它被 `DecisionCard.from_dict()`
的默认值悄悄改成「历史卡」对待，否则「新卡严、旧卡宽」的三段式语义就被削平了。

`payload_sha256`（raw 层的内容哈希）与这条新的规范序列化是**两个函数**，
不能合并：历史哈希建立在 `payload_sha256` 不带 `separators` 的输出上，
合并会静默改变所有历史哈希。`tests/fixtures/payload-sha256-vectors.json`
钉死这一点。

配套巡检：`tools/verify/readback_check.py` 只读遍历两张表，
统计「存在但读不回来」的行数——写边界只能挡住**新写入**，
巡检负责发现历史上是否已经存在这类行（当前生产库：0 条）。

#### 5.3.4 运行身份 + 显式状态机（确定性编排批 B 加的，schema v7）

设计 SSOT 在 `deterministic-orchestration.md` §4 / §5，这里只记它在现状里的落点。

**为什么**：此前只有 `decision_id` 一个身份，它被迫同时承担五件事，实测踩过三次
（飞书重投无幂等键、硬超时重试的两次尝试分不开、「所有 Specialist 看同一份数据」
无法验证）。⇒ 拆成 `trigger_id`（一次外部请求）/ `decision_id`（一次业务决策）/
`run_id`（一次执行尝试）/ `evidence_set_id`（一片冻结数据）。

**落点**：

* `src/easyup_biga/domain/run.py` —— `RunContext` 值对象（运行身份的唯一定义）+ 14 个状态
  `RunState` + 合法转移图 `LEGAL_TRANSITIONS`。状态清单从类属性派生，不手抄第二份。
* `src/easyup_biga/persistence/runs.py` —— `open_run()` 写身份头 + 初始事件；`transition(run_id,
  expected, next)` 做 **compare-and-set**：读到最新 `seq`、断言当前状态 == expected、
  `INSERT seq+1`；`UNIQUE(run_id, seq)` 是真正的并发仲裁（与 `decision_ids`
  用主键冲突占号同一招）。**状态不在 `decision_runs` 上原地 UPDATE** —— 那张表
  只追加，当前状态由 `run_events` 最新一行给出。
* `skills/decision-card/scripts/run_ledger.py` —— bash 与状态机之间的桥
  （`bin/biga-card` 调它开 run / 推状态 / `--status` 复述），并持有
  `STATE_MEANING`：`--status` 的读取知识，也是「每个状态都有消费方」判据的落点。

**14 个状态，一个不多**：评审原文 15 个，去掉 `IDENTITY_RESERVED` /
`SNAPSHOT_COLLECTING`（本系统里没有代码能进入、没有消费方会读 —— 多一个就是
L-1 死配置）；`NOTIFICATION_PENDING` 原本推到批 G，**批 G-I 加回来** —— 它现在有
生产方（编排器在 `CARD_PERSISTED` 之后入队 `notification_outbox`）与消费方
（`STATE_MEANING` + `notify_worker` 投递），不再是「建了没人读」，插在 `CARD_PERSISTED`
与 `COMPLETED` 之间。每个状态都要能指出「谁写它」（能从 `RECEIVED` 经合法转移到达）
与「谁读它」（`--status` 说得清），两条都有结构性测试钉死。

🔴 **批 B 建的状态机，批 C-II 已真正启用**：`DecisionOrchestrator`
（`orchestrator.py`）自己 `open_run` 并驱动全部 9 步转移（`RECEIVED` → `PREFLIGHTED`
→ `SNAPSHOT_FROZEN` → `STAGE1_RUNNING` → `STAGE1_COMPLETED` → `RISK_RUNNING` →
`SYNTHESIZING` → `CARD_PERSISTED` → `NOTIFICATION_PENDING`（批 G-I）→ `COMPLETED`），
走**细粒度链**而不是批 B 那条
legacy 粗边（`PREFLIGHTED → CARD_PERSISTED` 已随 C-II 删除，L-7）。批 B 当时是过渡态
「新旧并存」：`bin/biga-card` 还走老路径（spawn `main`、LLM 内部编排）、只 best-effort
记账；C-II 把老路径整段换成程序驱动，记账变成流程本身而不再是旁挂。

#### 5.3.5 外发通知 outbox（确定性编排批 G-I，schema v11）

四类事件（Card 完成 / UNKNOWN / risk 否决 / 运行失败）经一条 outbox 通道推出去，
让人不必守着终端等一次 170~200s 的同步出卡。**只做「推」**（Outbound Only）——
不接受任何飞书方向的输入（那是批 G-II）。

* `src/easyup_biga/domain/notify.py` —— `NOTIFICATION_EVENT_TYPES`（四类白名单）+
  `card_event_type()`（把一张已产出的卡分到 `risk_block`/`card_unknown`/`card_completed`，
  否决优先、判据是 `stance==VETO_STANCE` 不是 status 猜）+ `NOTIFY_FAILURE_STATES`。
* `notification_outbox`（队列，幂等键 `(event_type, aggregate)`）+ `notification_deliveries`
  （投递尝试日志），**两张都只追加**。「投没投成」= deliveries 里有没有一条
  `status='delivered'` —— 一条派生查询，**不给 outbox 开 `delivered_at` 原地改的例外**
  （与 `run_events`/`amends`/`replay_of` 同一条 L-8 先例：状态变更一律追加）。
* 入队与写库的原子性：`save_card_with_notifications(card, notifications)` 把 Card 与
  outbox 行放**同一个事务** —— 要么一起进库、要么一起回滚，不会「卡进去了、通知没进去」。
  `run_failed` 走 `enqueue_run_failed(run_id)`，在失败终态转移**之后**尽力而为地入队
  （幂等 `aggregate=run_id`）——通知入队失败绝不回滚「这次运行失败了」这条记录。
* `RunState.NOTIFICATION_PENDING`（`CARD_PERSISTED → NOTIFICATION_PENDING → COMPLETED`）：
  只代表「已入队」，**不代表「已投递」**。`COMPLETED` 紧接其后、纯 DB，不等 worker ——
  一次飞书 API 抽风不会把 Run 卡在非终态。
* `skills/decision-card/scripts/notify_worker.py` —— outbox 的读取方：扫没投成的行、
  经一个**可替换的投递接口**（`Deliverer` 协议）投出、往 deliveries 追加一条尝试。
  这一批只有桩实现 `StdoutDeliverer`；真飞书 adapter 是批 G-II 的生产方。挂进 cron
  调度域也是 Phase 3 / G-II 的事（`tools/cron` 现在是空的）。

#### 5.3.6 交易日历 fact 层（确定性编排批 L，schema v15）

`fact_trading_calendar` 是**这个仓库第一张真正落地的 `fact_*` 表** —— 在它之前，§5.2
那张 `raw → fact → derived` 分层图里只有 raw 层被实例化过，「归一化事实层」只存在于
文档。批 L 用一个非行情、体量小、判据清楚的数据集把这一层第一次做成真实 schema，
既补一个既有缺陷，也给 §45「第一版完整市场数据」那一批打样。

* **Provider**：`src/easyup_biga/providers/szse.py` —— 深交所官方 monthList（免鉴权），
  `fetch_trading_calendar`（联网薄函数）+ `parse_trading_calendar`（不联网纯函数，
  能对已存 raw 重放）+ `refresh_trading_calendar`（抓取→原样落 `raw_market_snapshot`
  →归一化进 `fact_trading_calendar`）。分层照 `sina.py` 的既定形状，**不接
  `SnapshotCoordinator`**：那套解决「同一次运行内多消费方看同一份易变数据」，
  日历是低频只读参考表，不是那个形状。
* **完整性 fail-closed（R-3）**：`parse` 要求响应覆盖该月每一天，缺日/未发布当场抛错 ——
  宁可整月拒绝，也不把「没数据」和「休市」混成一谈。
* **只追加**：交易所补发调整（临时增/删交易日）写更晚 `retrieved_at` 的新行，
  `is_trading_day()` 按 `retrieved_at` 取最新一条，不覆盖旧行（L-8）。
* **消费方**：`market_is_open()`（`tradetime.py`）—— 有日历数据以它为准（法定节假日
  正确判成休市），查不到回退到 weekday 判据，结果与批 L 之前逐一相同。回退是朝安全
  方向：查不到当「可能开市」，顶多多报一条缺失项，绝不把「查不到」当「休市」。
  `session_in_progress()` **不改** —— 它回答「这批数据声明的交易日过完了没」（纯时间
  比较），加节假日感知会把 emotion 推向「把节假日的 0 当成真冰点」的危险方向。
* 🔴 **已知部署约束**：`www.szse.cn` 从当前 WSL 部署连不通（TCP 握手后挂死）。
  于是本环境里 `fact_trading_calendar` 保持空表、`market_is_open()` 恒走 weekday 回退
  （安全方向）。`parse` 由离线 fixture 全测、落库链由注入桩 fetcher 全测；真实抓取会在
  能连通深交所的运行环境里把日历填进来。消费关系真实且被测，只是数据写入取决于网络
  可达性 —— 不是 L-1 的「零消费方」。

---


#### 5.3.10 数据平台地基七张表（Phase 3 · P3-1，schema v23–v26）

控制面第一次装下**采集**这条生命周期。七张表分四步进来，全部追加式（触发器强制）：

| 版本 | 表 | 装的是什么 |
|---|---|---|
| v23 | `data_job_runs` / `data_run_events` | 一次采集的身份与状态流水 |
| v24 | `provider_attempts` / `raw_artifacts` | 取数出处：每一跳尝试（含失败的）+ 原始响应的**元数据** |
| v25 | `dataset_partitions` / `quality_reports` / `dataset_snapshots` | 通用发布单位 + 质量裁定 |
| v26 | `evidence_set_datasets` | EvidenceSet → DatasetSnapshot 的冻结血缘 |

🔴 **为什么 Data Run 不复用 `decision_runs`。** 两者形状极像（追加式事件、CAS、
`UNIQUE(run_id, seq)`），但生命周期不同：一次**采集**失败不该和一次**决策**失败
长成同一件事。共用一套状态机，「昨晚日线没取到」和「这次出卡失败了」在库里就
分不开了。

🔴 **`raw_artifacts` 只存 URI / 哈希 / 元数据，正文在文件里** ——
而现有的 `raw_market_snapshot`（单标的、小体积）**不迁移**。这与 §5.1
「Provider 归档」那一节的触发条件一致：按数据集类型判，不按体积判。

🔴 **`evidence_set_datasets` 存在的理由是「关键血缘要能用 SQL 查」。**
一张卡用了哪几份数据快照，此前只能解 manifest JSON —— 与 v16 加 Run Provenance
三列时学到的是同一件事。

⚠️ **`dataset_partitions` / `dataset_snapshots` 的唯一约束都是
`(dataset_id, partition_key_json, data_version)`** ⇒ 修订出新版本，旧版本永不
覆盖。而它能成立的前提是分区键**归一过**（`canonical_partition_key` 按键排序）：
键序不同的同一个分区会变成两行，且唯一约束**不会报错**。


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

### 6.1 采集层的两条统一接口（`src/easyup_biga/providers/`）

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

### 6.3 契约/存储侧的支撑模块

| 模块 | 为什么单独存在 |
|---|---|
| `src/easyup_biga/domain/missing.py` | `MissingItem` 带**机器可读代码**（`market.turnover.date_mismatch`）—— 缺失项要能统计「哪个源最常缺」，自由文本做不到 |
| `src/easyup_biga/domain/verdict_ref.py` | 确定性编排升级批 A-II（A6）新增。`VerdictRef(agent, verdict_id, content_sha256, contract_version)`——Card 记下自己用的每条判定原件指向 `agent_verdicts` 哪一行、当时长什么样，`_store.verify_verdict_refs()` 据此核对「现在还认不认」。核对必须比对**存量 `content_sha256` 列**，不能把 `AgentVerdict` 对象重新序列化再算一遍——`_canonical_dumps` 的格式不是冻结的（A-I 就改过一次分隔符），走后者会让序列化格式一变，之前落库的原件集体核对不上且不报错 |
| `src/easyup_biga/domain/registry.py` | 确定性编排升级批 K 新增。`AGENT_REGISTRY`（一个 agent 一条 `AgentDefinition`：`stage`/`spawned`/`reads_snapshot`）是 **Agent 名册的唯一源**——`STAGE1_AGENTS`/`STAGE2_AGENTS`/`RISK_AGENT`/`SNAPSHOT_INDEX_AGENTS`/`EXPECTED_ROSTER` 全部从它派生（照 `run.py::RUN_STATES` 的 `vars()` 内省形状），不再手写平行清单。**它解决的是 2026-09-21 那次静默事故的根**：`news` 进了契约名单、agent 也建好了，但运行时白名单漏了它 ⇒ 只 spawn 四个、无任何报错、Card 照常出只是少一个领域。收编前普查发现 roster 实际散在**五处**（含 `orchestrator.py` 两个独立字面量、`adapter_spike.py` 一处零测试覆盖的字面量）。`discipline` 在册但 `spawned=False`（裁定 13：无输入源、从不 spawn）⇒ 不进 `EXPECTED_ROSTER`（`DecisionCard.absent_agents` 的权威）⇒ 永不被判「缺席」。`STANCE_VOCAB` 保持独立、只对本表断言子集关系 |
| `src/easyup_biga/data/` | Phase 3 · P3-0 新增。`DATASETS` 与 `PROVIDERS` 是**数据集/数据源名册的唯一源**，照 `AGENT_REGISTRY` 的形状（一处手写、其余派生、反向测试）。`bin/biga-data list|providers` 读它。🔴 **名册答的是「Data Platform 管着哪些数据集」，不是「系统里有哪些」** —— 条目按 Milestone 激活，有真实生产者或消费者才进册（`consumers` 字段是必填的，空元组当场红：想注册就得先答出谁读它）。第一版按「库里真实出现过的 7 组」全收，那是把两个问题混成一个 —— 其中 5 个是 agent 直连 provider 的遗留路径，收进来等于让名册声称管着它完全没接手的东西。`provider_id` 是**适配器级**的（与 `providers/` 下模块同名），不是站点前缀 —— 第一版取前缀把新浪的日线/日历/快讯合成一个 `sina`，于是「它挂了会影响什么」**系统性高估影响面**，而那正是这份名册的主要用途；「别让同一个源有三套名字」这个关切挪到 `source_prefix`。「谁支持哪些 dataset」**派生**不手写。🔴 手写的是**元组**、dict 另外派生：dict 推导会静默吃掉重复 id，唯一性守卫打在 `.values()` 上结构性不可能红（L-13，探针当场抓到）。契约里 `TIMEOUT` 退 **2** 不是 1 —— 对齐 `tools/verify/_verdict.py` 的「2 去查数据、1 去查代码」；`DataRunStatus` 名字里的 `Run` 不是装饰，它与 P3-1 要来的 `DatasetStatus` 是两层东西 |
| `src/easyup_biga/data/snapshots.py` + `data/datasets/` | Phase 3 · P3-2 新增。`DatasetSnapshotService` 把一次发布走完 `DataRun → RawArtifact → ProviderAttempt → Partition → Quality → Snapshot`，按**逻辑分区**幂等；各 dataset 的适配器（今天只有 `src/easyup_biga/data/datasets/index_daily.py`）准备已核验的 RawArtifact 再调它。🔴 `raw_artifact_id` 只在**恰好一个** artifact 时才填 —— 一个 bundle 有多个 raw 时留空，由 `provider_attempts` 承担一对多的血缘边，**不拿一个随便选的外键撒谎**。🔴 桥接器发布前逐个 symbol 核对冻结 raw 的 `content_sha256` 与 manifest **逐字相同**：一份「登记了但对不上原始字节」的血缘比没有血缘更糟。不二次抓取，`body_uri` 指向既有不可变行（`biga+sqlite://raw_market_snapshot/<id>`）|
| `src/easyup_biga/persistence/data.py` | Phase 3 · P3-1 新增。v23–v26 那七张表的**唯一写入面**，走同一个 `connect()` 边界（I-4 对它同样成立），不另起第二个库。🔴 写入前在**代码里**再核一遍引用完整性：SQLite 外键默认是关的，而这一层的错配大多不是「指向不存在的行」而是「指向了存在但不该指的那一行」——快照引用了另一个 dataset 的分区、质量报告挂在别的 run 上。那类错外键管不着，且**一旦落库就永久留在追加式存储里**。`_check_partition_keys()` 让 `DatasetDefinition.partition_keys` 从装饰品变成承重件：实际写入的分区键必须恰好是注册表声明的那几个，否则 `{symbol,as_of}` 与 `{trade_date}` 可以同时写进同一个 dataset 而唯一约束不报错 —— 那是「同一份数据悄悄存了两份」的入口 |
| `src/easyup_biga/persistence/schema.py` | 按版本号递增的迁移列表。**已发布的条目不许改动** —— 跑过 v4 的库不会重放它，所以补触发器只能开 v5 |
| `src/easyup_biga/persistence/runtime.py` | 读 OpenClaw 运行时自己的 trajectory。🔴 **UTC → 北京时间的转换只在这里做一次**，消费方拿到的已经是北京时间 —— 这类 bug 的形状是「差 8 小时但仍是个合法时刻」，不报错 |
| `skills/_snapshot/coordinator.py` | 确定性编排升级批 D-I 新增。`SnapshotCoordinator.freeze_index_daily()` 把一次决策要用的指数日线**只真实抓一次**、原样落 `raw_market_snapshot` 并登记一行 `evidence_sets`；`read_index_daily()` 让多个消费者从**同一份**冻结数据切出各自要的根数（sector 2 / market 25 / technical 120），而不是各自联网。它把「所有 Specialist 看同一份数据」从**六个 skill 各自的发现**变成**冻结集的一个可核对属性**（§4 `evidence_set_id`）。`fetch_index_daily` 拆成 `fetch`（网络）+ `parse_index_daily`（纯解析）就是为了让读端能从冻结的 raw 重建 `IndexDaily`，不必第二次实现解析（L-3）。🔴 **批 D-II 已接进生产**：`orchestrator.py` 在 Stage 1 之前冻结一次（sh/sz@**120** 根 —— 取消费者里最大的 technical），market/sector/technical 各带 `--evidence-set-id` 读同一份、不再各自联网抓日线；读端 `Evidence.raw_hash` 取冻结集登记的 `content_sha256`（整份 raw 的指纹，不对切片重算），于是 risk 的 `CROSS_CHECK_PAIRS` 从「比值」改成「比 `raw_hash` 是否相同」——共享后比值恒真（L-7），比 `raw_hash` 才是「谁没读冻结快照」的探照灯。手工单跑某个 skill 不传 `--evidence-set-id` 仍自己抓（调试路径保留，fail-closed：给了坏号直接报错，不静默退回抓取）|

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

| **L-13** | **守卫查的地方，和它声称守的地方，不是同一处**：断言落在函数上而调用点没被覆盖；落在字符串存在性上而语义（退出码、是否被使用）没被覆盖；落在名字上而实现可以改名 | 守卫**照常报绿**。它确实在查一件事，只是不是它声称在查的那件事 —— 而它的名字让人以为已经被守住了 | **每一条守卫上线前先跑探针**：把被守的东西**真的弄坏**，确认它会红。红不了就说明判据落错了地方。判据优先用 AST / 行为 / 退出码，避免用「字符串在不在」与「文件名像不像」 |

| **L-14** | **入口与契约互相指**：系统提供一个「做这件事就跑这一条」的唯一入口，而那条入口做的事是**把指令喂给某个 agent**；同时那个 agent 的角色契约里也写着「做这件事就跑这一条」 | **每一层都在照文档做，没有任何一层会察觉异常。** 没有错误、没有超时、没有告警 —— 只有进程数和账单在涨。而下游闸门会把绝大多数拦掉，于是**业务指标看起来完全正常**（当天占号数一个都没多） | ① 角色契约里**不许出现可直接照抄的入口命令**（判据只禁代码块里的裸调用 —— 要写「不要做 X」就必须写出 X）；② 入口自己带**单实例锁**，用文件锁不用环境变量（env 传不过网关）；③ 花钱的闸门要放在**第一次花钱之前**，不是第一次产生事实之前 |
| **L-15** | **超时收敛只挂在一条退出路径上**：`ad.wait(handles, budget)` 的 `budget` 参数在预算耗尽时会抛错，而 Python 先求值参数再调用——抛错发生在 `wait()` 真正执行之前，此前已启动的 handle 从未被取消；`wait()` 正常返回、但某个 handle 状态是**本地判定**的 `TIMEOUT`（Adapter 自己的轮询到期，不代表运行时确认收到过停止信号）时，同样没有后续动作 | 两条路径都不经过任何错误处理分支——一条是参数求值顺序的副作用（`wait()` 从未被调用，不算"异常"），另一条是"函数正常返回"；业务代码习惯只在异常分支挂清理逻辑，这两条都不是异常分支 | 取消收敛写成一个无条件的收尾函数，在触发预算异常**之前**和收到 `TIMEOUT` 状态**之后**都显式调一次，不依赖某一条分支"顺便"经过它 |
| **L-16** | **ACK 承诺的是尚未确定的未来**：对外确认消息在"异步任务已提交"这一步就发出，而真正决定这件事会不会发生的判断（如预算闸门）在**另一个进程**里、**之后**才执行；两件事看起来几乎同时发生，容易把"提交成功"当成"会成功" | 用户收到的确认消息本身不报错、格式完全正常——它就是一句关于未来的承诺，而这个承诺可能在发出的同一秒已经不成立，用户没有任何办法从这条消息本身看出差异 | 对外承诺前，自己先问一遍那个决定"这件事会不会发生"的只读判断；能现在就知道结果的就不说将来时。判断本身保持独立纯函数（只读、无副作用），多问一遍的代价只是一次查询，不是新增一道能拦人的关卡 |

⚠️ **L-10 ~ L-16 是这张表里在本项目自己踩出来的七条**，其余九条继承自那套长期运行的系统。
L-15/L-16 出自 2026-09-24 G 节 Live Acceptance 真机验证过程中撞见的两处真缺陷，
均已修复，见[教程第 58 章](../tutorial/58-cancel-on-timeout.md)、
[第 59 章](../tutorial/59-ack-before-gate.md)。

🔴 **L-13 是出现次数最多的一条 —— 到 2026-09-21 已经十次。**
它在两次外部评审里都是复发率最高的模式，而每一次的表现都一样：
守卫存在、命名正确、测试全绿，坏掉的东西没被抓到。已记录的十处包括
围栏测了函数没测调用点、表名检查被 docstring 满足、幽灵文件检查只匹配反引号路径、
计数守卫被 markdown 星号挡住、`spawn_check` 的退出码被丢掉、
「不许联网」只写在 docstring 里没有任何执行者、以及退出码三态在渲染层对了在调用层塌回两态。

> 一条**不会红**的守卫，比没有守卫更糟：它占着「这件事已经有人管」的位置。

⇒ 落成流程：`.claude/skills/dev-workflow/` 的「守卫用探针验证会红」。

🔴 **L-14 的实例：2026-09-21 21:03–21:50 的出卡递归**

`AGENTS.md` 那一节当天被改成「🔴 要出卡？跑这一条，不要自己 spawn」并贴出了
`bin/biga-card`。而那条命令做的事就是把编排提示词喂给 `main` ——
读到那句话的**正是它**：

```
bin/biga-card  →  agent --agent main  →  新的 main 会话
                                            ↓ 读角色契约
                                      bin/biga-card  →  …
```

实测 **187 个 main 会话 / 约 195 次调用**，其中 92 次是契约里那行命令的
**逐字复制**（其余是 `2>&1` / `nohup` / `| tail -100` 这类即兴变体）。

⚠️ 最值得记的是**它为什么没被发现**：
Stage 0 的预算闸门拦下了约 58 次，当日占号数**一个都没多**（16 → 16），
出卡张数也没变。所有业务指标都正常 —— 唯一的异常信号是进程数与 token 账单。

> 🔴 闸门装在**身份**的咽喉点（占号），而**成本**的边界比它早一步。
> 「唯一入口」原则是对的；选错的是「哪一处才算入口」。
> 对花钱的事，入口是**第一次花钱的地方**，不是第一次产生事实的地方。

实账：闸门省下约 \$48（58 × 一次 fan-out），漏掉的是每次被拒**之前**
已付掉的那个 `main` 轮次 —— 32 轮 **\$8.99**。

#### 事故当天那两道修复**不够**，外部评审说得对

当天做的是「改契约文字」加「单实例锁」。评审的判断是：

> AGENTS.md = Intent / Behavior Guidance
> Code Guard = Safety Invariant   —— 两者职责必须分开。

🔴 **锁拦的是并发，不是越权。** 一次**串行**递归（上一层退完下一层才起）
锁根本拦不住，而那同样是无限递归，只是慢一点、更难看出来。

⇒ 补了一条代码判据：`skills/decision-card/scripts/entry_guard.py`。
  顶层入口只能由 人 / CLI / cron / 外部 Trigger 启动，**agent 会话不得启动它**。
  两个信号，各自补对方的漏：

============  ====================================  ============================
信号          判据                                   它单独漏掉什么
============  ====================================  ============================
进程血缘      祖先里有 OpenClaw 运行时                `nohup cmd &` 被 reparent
                                                     到 `systemd --user`，血缘断
                                                     （事故 195 次里有 15 次）
环境变量      `OPENCLAW_SERVICE_*` 在 env 里          网关手工起的话这些压根没有
============  ====================================  ============================

实测对照：网关进程 env 有 `OPENCLAW_SERVICE_KIND` 等，**人类 shell 一个都没有**。
⚠️ 不要用 `OPENCLAW_PREPEND_PATH` —— exec 工具注入它，但那条命令第一件事就是 `unset`。

#### 防护顺序（这一点比每一道本身更重要）

```
Trigger → 熔断 → ownership → 单实例锁 → 预算闸门 → 第一次付费调用
```

顺序错了等于没有：守卫排在付费调用**后面**，每次拒绝仍要付一个 `main` 轮次 ——
事故里那 \$8.99 就是这么来的。顺序由测试钉住。

#### 锁必须与硬超时**配套**

如果持锁的那一次永久卡死，锁会正确地拦住后续所有调用 ——
**于是整个入口永久不可用**。实测过这个卡死场景：

```
stalled session: state=processing age=886s
  reason=blocked_tool_call activeTool=ask_user recovery=none
```

`agent` 是非交互调用 ⇒ 没有人能回答 `ask_user` ⇒ 确定性死锁，运行时自己不救。

⇒ 出卡入口有**一个**总预算数（`BIGA_CARD_DEADLINE_SEC`），而不是
`--timeout 900` 加 `seq 60` 乘 5s 这种「真实上限 1200s、而没有一处写着 1200」。
超时后主动 `sessions delete` 收掉那个会话，进程退出，OS 自动释放锁。

#### `ask_user` 的处理：不禁用，但不许它造成永久死锁

**禁不掉。** 探查结论：`agent` 子命令没有任何 tool 参数；`agent exec`
（隔离 headless）spawn 不了 specialist，而出卡需要网关；配置级 `tools.deny`
的粒度是**按 agent**，会把交互式 `main`（飞书、TUI）的这个能力一起砍掉。

🔴 所以目标改了 —— 这一步是有意识的降级，不是妥协：

> 不要求 `ask_user` 不出现，而是保证它**即使出现也只能造成有限、可检测、
> 可审计的失败**，不能造成永久死锁。

三层，职责不重叠：

==============  ==========================================================
硬超时          `BIGA_CARD_DEADLINE_SEC` —— 覆盖**所有**阻塞工具，
                不依赖日志格式。兜底最可靠的一层
看门狗          `skills/decision-card/scripts/stall_watchdog.py` ——
                30s 内认出 `ask_user` 死锁，比等满硬超时快一个数量级
契约            `AGENTS.md` 明写「这条路是非交互的」+ 该怎么做替代 ——
                **降低概率，不是保证**
==============  ==========================================================

⚠️ 看门狗的数据来源**只有日志**。三处都找过：

==============================  ========================================
`session_state_events`           只有 `created` / `child_spawned` /
                                 `run_completed`，没有 blocked 状态
`sessions list --json`           22 个字段里没有 `state` / `activeTool`
journal 的 `[diagnostic]` 行      ✅ 唯一带 `reason` / `activeTool` 的
==============================  ========================================

明知解析日志脆弱。代价是**朝安全方向**的：格式变了 ⇒ 解析不出来 ⇒
看门狗不开火 ⇒ 退化到硬超时。**不会误杀。**

🔴 而正因为脆弱，判据对着**真实那一行**测（`tests/fixtures/stalled-session.log`
是事故当天 journal 的原文，字段名/顺序/格式一字未改）。
本项目刚在「按我以为的形状写检测器」上栽过一次 —— 那就是 L-13。

退出码 `5` = `INTERACTION_UNAVAILABLE`，与超时的 `1` **分开**：
一个要人去看提示词，一个要人去等。**不自动重试** ——
同一个请求很可能再次 `ask_user`，那是成本循环。

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
| **3** | 数据层加厚：Dataset/Provider Registry 扩展、EOD 日线、Emotion 统一数据迁移 + **`discipline` 及其输入源** + Screening + Review + 独立飞书应用 + 更多 Cron/systemd | 每条 cron 都有被证明的消费方（L-1） |
| **4** | 测量层：安慰剂基准 + Card 状态的区分力检验 | `BLOCK/WARNING` 与 T+5 结果按日聚类 t 有区分力 |
| **5** | （很久以后）自动下单 —— 前提是 Phase 4 通过 | 另起设计文档；L-9 四铁律先实现 |

⏩ **2026-09-24/25**：与 Phase 2 并行的「确定性编排升级」（把工作流从提示词
搬进程序，A–H 批，见 [`deterministic-orchestration.md`](deterministic-orchestration.md)）
完成 Baseline 冻结，`v1-architecture-baseline` tag 落在这次收尾提交上。
**这不等于 Phase 2 本身已收口**——Phase 2 §4 的出口条件是独立判据，
仍按 [`phase-2-specialists.md`](phase-2-specialists.md) 追踪，两者并行、
互不代表对方。Phase 3 的范围（上表）已吸收该升级配套评审清单里
「完成后再进入」列出的具体项，不再是笼统的「数据层加厚」。

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
