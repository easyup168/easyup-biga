# Phase 1 启动提示词

> 📄 **操作** · 自包含，可直接粘贴
> **覆盖**：Phase 1 的启动提示词 ｜ **不覆盖**：Phase 1 的实际结果（见 [`../tutorial/`](../tutorial/README.md)）


> 用法：在 `~/.openclaw-biga/workspace/` 打开新会话，把下面「提示词正文」整段粘进去。
> 要求：自包含 —— 不依赖任何先前会话的上下文；引用的文件全部存在。

---

## 提示词正文

你在 `~/.openclaw-biga/workspace/`，这是 **EasyUp for BigA 2.0** 的代码仓库 ——
一套基于 OpenClaw 的 Multi-Agent A 股短线决策辅助系统（不自动下单，产出 Decision Card，人做最终决策）。

### 第一步：读这五个文件，别跳

1. `CLAUDE.md` —— **三条红线 + 四条不变式 + 9 条已定裁定**。已定的事不要重新讨论
2. `TODO.md` —— 当前进度与待办，Phase 1 的 8 条验收标准在里面
3. `docs/design/architecture.md` —— 架构 SSOT。重点看 §三 Agent 拓扑、§四 通信契约、§九 失败模式清单、§十一 Phase 1
4. `docs/external/2026-09-19-upstream-source-design-v1.md` —— 上游需求文档（只读，不改）
5. `agents/README.md` —— roster 规划与 workspace 约定

### 第二步：开工前先跑环境自检

```bash
BIGA=~/.openclaw-biga/bin/biga

$BIGA --version                                          # 期望 OpenClaw 2026.9.5
[ ! -e ~/.nvm/versions/node/v24.21.0/bin/openclaw ] && echo "✅ 红线 R-2 完好"

# 隔离：跑 BigA 命令不得改到同机另一套实例的状态
stat -c '%y' ~/.openclaw/state/openclaw.sqlite
$BIGA agents list
stat -c '%y' ~/.openclaw/state/openclaw.sqlite           # 必须与上一行相同
```

三项有任何一项不符，**先停下来查清楚**，不要继续往下做。

### 现状

**环境已就绪，业务代码为零。**

已完成：隔离安装（node v24.21.0 + openclaw 2026.9.5，刻意不进 nvm bin）、
`bin/biga` wrapper、workspace 骨架、git（4 commits）、架构与安装文档。

未开始：`biga setup`、契约层、数据层、任何 agent。

### 第三步：先问我一个问题，再动手

**Phase 1 建几个 agent？** 这个还没定，需要我拍板。把两边论据摆给我：

- **建 2 个**（`main` + `emotion`）—— Phase 1 要验证的是**机制**
  （跨 agent spawn 通不通 / 契约能不能落库 / 能不能回放），不是覆盖面。
  机制通了，其余 6 个是复制。且一上来建 8 个空 agent = 6 个没有消费方的占位组件，
  正是 `architecture.md` §9 L-1 列为头号失败模式的那个模式。
- **建 8 个** —— roster 从第一天起就是真的，`biga agents list` 直接看到完整拓扑。

⚠️ 注意「真八 Agent」这条裁定（CLAUDE.md 裁定 4）说的是**最终形态**，
不等于 Phase 1 就要 8 个全建。两者不冲突。

### 第四步：我定了之后，按这个顺序做

1. `biga setup` —— profile 初始化，端口 **19789**，**跳过 IM 通道**（Phase 1 只用本地 TUI）
2. `skills/_contract/` —— `Evidence` / `AgentVerdict` / `DecisionCard`
   配 `tests/test_contract_single_impl.py`（AST 扫描，确认全仓无第二份实现）
3. `skills/_store/db.py` + 三张表：`decision_records` / `agent_runs` / `raw_market_snapshot`
   配 `tests/test_no_raw_sqlite.py`（业务代码不许出现裸 `sqlite3.connect`）
4. `skills/emotion-calc/` —— 真采一次 A 股情绪数据，输出合法 `AgentVerdict`
5. 建 agent + 写各自的 `AGENTS.md`
6. 配 `main`(Supervisor)：仓库根的 `AGENTS.md` / `SOUL.md` / `IDENTITY.md`
   \+ `subagents.allowAgents` + `tools.agentToAgent.allow`
7. 端到端：`biga agent --agent main -m "今天市场情绪怎么样？"`
8. `replay <decision_id>` —— 必须与在线路径**共用同一份合成代码**
9. 隔离演练：`kill -9` BigA gateway，确认另一套实例 gateway pid 不变

每完成一项就更新 `TODO.md` 的勾选状态。

### 🔴 三条红线（详见 CLAUDE.md）

- **R-1** 所有 OpenClaw 命令走 `~/.openclaw-biga/bin/biga`。
  直接用 `openclaw` 会读写同机另一套实例的状态目录 —— 该 CLI **会自动跑 doctor 迁移，不是只读**
- **R-2** openclaw 本体绝不装进 nvm 的 bin。升级只能
  `npm i --prefix ~/.openclaw-biga/runtime openclaw@latest`，**不许 `npm i -g`**
- **R-3** `UNKNOWN` ≠ `PASS`。算不出来必须进 `missing[]` 并显示在 Card 上

### 边界：Phase 1 明确不做

接 IM 通道 / 建任何 cron / 任何下单路径 / PostgreSQL / Redis / 回测 /
历史数据回补 / Web UI。

### 两条工作方式上的要求

1. **本仓库将公开。** 提交前自查不含密钥、真实家目录路径，
   以及同机另一套实盘系统的**可识别细节**（内部模块名、文件路径、绩效数字、缺陷百分比）。
   最后一类不是密钥、脱敏扫描不会报，最容易漏。自查命令在 `CLAUDE.md`「公开仓库纪律」一节。
2. **不要替我做已经写在 CLAUDE.md「已定裁定」里的决定。**
   有新的岔路就问我，不要自己选一条往下走。
