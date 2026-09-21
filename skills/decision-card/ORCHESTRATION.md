# 出卡编排 —— **唯一**的一份

> 📄 **常青** · 随代码同步
> **覆盖**：从占号到合成的完整步骤 ｜ **不覆盖**：各 Specialist 自己的判断口径（见 `agents/<名>/AGENTS.md`）

## 🔴 为什么单独成文

这段步骤原来有**两份**：

| 在哪 | 状态 |
|---|---|
| `bin/biga-card` 的提示词 | 实测 100% 照做 |
| `AGENTS.md` 的 Stage 0~3 | 内容一模一样，**飞书路径下没被执行** |

2026-09-21 19:31 从飞书说「出一张决策卡片吧」，结果是：

```
sessions_spawn ×4      ← 只有 4 个（少 news）
sessions_yield         ← 契约明令禁止
exec new_decision.py   ← 占号发生在 spawn 之后（L-11）
```

四个 spawn 无归属，约 $0.4 白花。**而这些规则在契约里一条不缺。**

> 🔴 **契约写了不等于会被遵守。** 同一段知识两份实现，
> 弱的那份会悄悄失效 —— 这就是 L-3，只不过这次失效的是提示词而不是代码。

⇒ 合并成这一份。`bin/biga-card` 从下面那个标记块里**抽取**提示词，
不再自己写一遍；`AGENTS.md` 只留一句「跑 `bin/biga-card`」。

## 🔴 正常情况下你不该读这份

**要出卡就跑 `bin/biga-card`。** 它会把下面的步骤喂给一个专用会话。

只有在**它坏了、你必须手工编排**时才照着下面做。
自己编排的实测战绩是：4/5 个 agent、顺序反了、$0.4 白花。

---

## 给 Supervisor 的五步摘要（`bin/biga-card` 抽的就是这段）

<!-- PROMPT:BEGIN -->
请出一张决策卡，严格按以下步骤：
Stage 0 先跑 new_decision.py 占一个决策编号；
Stage 1 在**同一条消息里**并行 spawn market / sector / technical / emotion / news，
  五个一个都不能少，每条 spawn 都要带 collect=true，指令里都要写明本次决策编号；
Stage 1.5 用 agents_wait 同步等待，**不要 sessions_yield**；
Stage 2 全部返回后再 spawn risk（同样 collect=true + 决策编号）；
Stage 3 用 synthesize.py 合成，**必须带 --decision-id**（就是 Stage 0 那个号）。
全部 subagent 结算完再结束本轮。
<!-- PROMPT:END -->

🔴 **这段的每一句都对应一个踩过的坑，不要随手删。**
下面是每一步的完整说明与理由。

---

## Stage 0 · 先占一个决策编号

🔴 **在 spawn 任何 Specialist 之前**，跑这一条：

```bash
cd ~/.openclaw-biga/workspace && python3 skills/decision-card/scripts/new_decision.py
```

它会打印一个编号，例如 `BIGA-20260921-007`。**记住它**，
下面每一步都要原样带上。

### 为什么不能等到合成时再分配

编号原来确实是合成时才分配的。2026-09-21 盘中，两次端到端相隔两分钟：

* 五个 Specialist 的判定原件**全部**写着同一个 task_id
* 合成出来的卡却是另一个号
* 两次运行的证据混进了同一张卡，而卡上**看不出来** ——
  agent 齐全、时间戳都在几十秒内，一切正常

> **一个决策必须在收集证据之前就有身份，否则证据无处归属。**

现在合成脚本会拒绝把不同编号的判定原件合成一张卡，
所以号传错了会当场报错，而不是悄悄出一张混血的卡。

## Stage 1 · 调 Specialist

🔴 **必须用 OpenClaw 的跨 agent spawn 工具，工具真名是：**

```
mcp__openclaw__sessions_spawn
```

参数要点：`agentId: "<specialist>"`、`context: "isolated"`。

### 🔴 多个 Specialist 必须在**同一条消息**里一次性发出

问题涉及多个领域时（多数问题都是），把所有 `sessions_spawn` 调用
**放进同一条消息**，让它们真正并行：

```
（同一条消息里）
  mcp__openclaw__sessions_spawn  agentId="market"     context="isolated"  collect=true
  mcp__openclaw__sessions_spawn  agentId="emotion"    context="isolated"  collect=true
  mcp__openclaw__sessions_spawn  agentId="sector"     context="isolated"  collect=true
  mcp__openclaw__sessions_spawn  agentId="technical"  context="isolated"  collect=true
  mcp__openclaw__sessions_spawn  agentId="news"       context="isolated"  collect=true
```

🔴 **每条 spawn 的指令里都必须写上 Stage 0 那个编号**，照这个句式：

```
本次决策编号 BIGA-20260921-007。
跑你的 skill 时必须加 --task-id BIGA-20260921-007。
```

不带这句，Specialist 的 skill 会用临时号（`-000`）而**落库直接报错** ——
这是有意的：它宁可当场失败，也不产生一条无法归属的判定原件。

**分两轮发就是串行。** 而串行的表现是 ——

> 每一步都成功，Card 照常产出，日志全绿，**只是慢了一倍**。

没有任何东西会报错。这与本项目此前抓到的三个 bug 同一族：
**以「慢」表现出来的正确性问题**。

⇒ `tools/verify/latency_report.py --parallel-check` 会核对两个子会话的
   时间区间**是否相交**。不相交就是串行，哪怕总耗时看起来还行。
   判据是区间相交这个**结构性证据**，不是「这次跑得快不快」。

⚠️ 并发上限 `maxConcurrent: 6`，Stage 1 现在正好 **5 个** specialist ——
**已经贴着上限了**。再加就必须先调这个值，否则会静默排队，
表现是「某两个 agent 的时间区间不相交」⇒ `--parallel-check` 会报出来。

### 🔴 「今天」在非交易日意味着什么 —— 这条你已经违反过一次

**照抄这段作为给 Specialist 的指令，一个字都不要加日期：**

```
请给出当前市场状态/情绪的事实与判断。
不要指定日期，走宽松模式，取数据源给出的最近一个交易日，
并在回答里明确写出那是哪一天。
```

🔴 **绝不要在给 Specialist 的指令里出现任何日期**，
除非人**明确说了某一天**（「9 月 18 号情绪如何」）。

人说「今天」**不等于**「请用今天的日期做严格核对」。

#### 实测：2026-09-20（周日）违反这条的后果

你要求两个 Specialist 以 `--date 20260920` 严格模式核对。它们照做了，
于是双双返回 `UNKNOWN`、`confidence=0.0`、**`evidence` 为空**，
产出一张**零证据的空卡**。

而当时 09-18 的完整数据**是拿得到的** —— 涨停 78、炸板率 24.27%、
上证 3911.87 +0.94%、两市成交额 20771 亿，一个都没进卡。

> 周六问「今天情绪怎么样」，有用的回答是
> 「最近一个交易日（周五）是这样的：……」，
> 而不是「今天没开盘，所以我什么都不知道」。
>
> 后者**技术上没错**，但它把一个**可以回答的问题**变成了一张全是缺失项的空卡。
> 诚实不等于无用 —— 标注日期就够诚实了。

⚠️ 但注明日期是**强制**的。可以给周五的数据，不可以让人以为那是今天的。
找不到这个工具名时，先用 `mcp__openclaw__agents_list` 确认 roster，
再试 `mcp__openclaw__sessions_send`。

⚠️ 用 `isolated` 不用 `fork`：Specialist 不需要你的对话历史，
`fork` 会把整段 transcript 复制进去，token 直接翻倍。

### 🔴 绝不允许的替代做法

**不许用通用子 agent 工具（`Agent` / `Task` / `general-purpose`）来「扮演」emotion。**

理由有三条，每条都是硬的：

1. **那不是 emotion。** 通用子 agent 只是读了 `agents/emotion/AGENTS.md` 的另一个你。
   真正的 `emotion` agent 有自己的 agentDir、自己的模型配置、
   自己的 `allowAgents: []` 约束 —— 全部被绕过。
2. **留不下证据。** OpenClaw 的 `subagent_runs` 表只记录真正的跨 agent spawn。
   用通用子 agent，这张表是空的，于是**没有任何东西能证明 Specialist 真的被调用过**。
3. **验收会红。** Phase 1 验收第 1 条查的就是这张表。

**如果 `mcp__openclaw__sessions_spawn` 确实不可用**：
不要绕路，不要自己代做。直接如实回答
「跨 agent spawn 工具不可用，无法调用 emotion Specialist」，
并把它写进 Card 的缺失项。**说不知道是合格的回答，绕路不是。**

## Stage 1.5 · 等 Specialist 返回

🔴 **只有一条路：`collect: true` + `agents_wait`。不要同时用 yield。**

```
（Stage 1 同一条消息里，每个 spawn 都带 collect: true）
  mcp__openclaw__sessions_spawn  agentId="market"  context="isolated"  collect=true
  … 五个都这样

（下一步，立刻）
  mcp__openclaw__agents_wait  ids=[五个 spawn 返回的 runId]  timeoutSeconds=300
```

`agents_wait` 会**同步阻塞**到五个全部返回。它返回了，你就可以直接进 Stage 2。

### 🔴 为什么必须带 `collect: true`

不带的话 `agents_wait` **对每个 id 都返回 `not_found`，而且是瞬时返回** ——
它只认 swarm collector 子会话（运行时源码里的判据是 `if (!entry?.collect)`）。

实测这条 bug 的代价（`BIGA-20260921-015`）：

```
10:45:37  agents_wait   → 全部 not_found，8.2s
10:45:43  subagents     → 自己去查状态，6.2s
10:45:50  sessions_yield→ 退回异步等待，7.4s
          ─────────────────────────────
          22 秒，全是协议开销，一点事没做
```

这段时间里**没有任何东西报错** —— 卡照常产出，只是每次都白花 22 秒。

⚠️ 之前的契约在这里给了**两条路**（「优先用 `agents_wait`」+「`sessions_yield`
也是对的」）。给两条路的结果是你**两条都走**，还自己加了一次 `subagents` 查状态。

> **写契约时，一个动作只给一条路。** 给「优先 A，B 也行」，
> 得到的是 A 然后 B，而不是 A。

### ⚠️ `collect: true` 之后**不许再 yield**

collect 模式**没有完成通知**（运行时明确写了 `no completion notification`）。
你 yield 出去就再也不会被唤醒 —— 这次对话会**静默地永远停在那里**，
既不出卡，也不报错。

⇒ `collect: true` 与 `agents_wait` 是**一对**，不能只用其中一个。

### 不要用 `subagents` 查状态

`agents_wait` 返回的 `completed` / `pending` / `errors` 已经包含全部信息。
再查一次 `subagents list` 只是多一个模型往返（实测 6.2s）。

### 兜底：`agents_wait` 真的失败时

如果它返回 `errors`（`not_found` / `not_owner`）而不是超时，说明 spawn 时
**漏了 `collect: true`**。这时才退回异步路径：

```
sessions_yield → 运行时在 Specialist 返回时唤醒你 → 接着合成
```

⚠️ 这条路每次会多出一段唤醒空档，实测可达 121 秒。**它是兜底，不是备选。**

### 🔴 真正的硬约束：这次对话必须以一张 Card 收尾

> **一次提问，要么产出一张 Decision Card，要么明确说出为什么产不出来。**
> 没有第三种结束方式。

注意这条约束的对象是**整个对话**，不是**你的某一轮**。

⚠️ 一个容易骗过自己的地方：`status=succeeded` 只表示**你这一轮**正常结束，
**不表示任务完成了**。判据只有一个 —— `decision_records` 里有没有新增一行。

如果 Specialist 失败或超时：照样出卡，状态写 `WAIT` 或 `AVOID`，
并把「<agentId> 未返回结果」写进缺失项。**出一张标着「不知道」的卡，
比不出卡强得多** —— 后者让人分不清是系统没跑，还是跑了没说。

🔴 **spawn 了几个就要等几个。** 只要有一个还没回来，你就还没到合成的时候。
少等一个而照常出卡，那张卡会**看起来完整**，而它少了一整个领域的证据 ——
且缺失项里什么都不会写，因为你根本没意识到少了。

## Stage 2 · 制衡层

Stage 1 全部返回之后，**必须**再 spawn 一次 `risk`。它是本系统唯一有否决权的 Agent。

### 🔴 传给它的是 Stage 1 的编号，不是新任务

```
mcp__openclaw__sessions_spawn  agentId="risk"  context="isolated"  collect=true
（然后同样用 agents_wait 等它，不要 yield）
```

指令里**必须**带上 Stage 1 各 Specialist 给你的那串 `verdict_ref`，照抄这段：

```
请依据 Stage 1 的冻结证据做风险审查。
本次决策编号 <Stage 0 那个号>，跑 skill 时必须加 --task-id <同一个号>。
Stage 1 的 verdict_ref：<逗号分隔的编号，例如 22,23>
不要自己重新采集数据。
```

#### 为什么必须是「冻结证据」而不是让它自己看一遍

制衡的意义在于审**别人据以下结论的那份证据**。risk 自己重采一遍，
看到的就可能是另一个市场 —— 那时候它审的是自己的幻觉，不是这次决策的依据。
而且回放时两边对不上，「当时为什么放行」就永远查不清了。

⚠️ Stage 1 的结果**不许在这一步被修改**。你不能因为 risk 有意见就回头改
Stage 1 的判定 —— 那些原件是只追加的，改不了，也不该想去改。

### 🔴 risk 说「否决」时你必须照办

| risk 的 stance | 你的 Card 状态 |
|---|---|
| `放行` | 按 Stage 1 的证据正常判断 |
| `警示` | 正常判断，但必须在 `--synthesis` 里说明它警示了什么 |
| `否决` | **只能是 `AVOID` 或 `BLOCK`** |
| `无法判定` | **不许 `BUY`**（它没看清，不等于没风险） |

契约层会强制这几条：给 `BUY` 或 `WAIT` 会被直接拒绝，报错会告诉你原因。
**不要试图绕过** —— 制衡层的否决权不可被合成阶段软化。

⚠️ 特别地：`无法判定` **不是**放行。一个说不上话的风控，
必须让人在卡上看出它说不上话。

## Stage 3 · 合成

**不要手写 Card 的 JSON。** 调合成脚本。

### 照抄这段，不要去读源码

实测过：不给范例时，你会花 8 次工具调用去读 `SKILL.md`、`synthesize.py`、
`_contract/verdict.py`、`_contract/evidence.py` 来反推参数格式，
再试错 3 次才调对 —— 一轮多花一百多秒。**这些信息本来就该写在这里。**

🔴 **你不搬运判定数据，只传它们的编号。**

每个 Specialist 会在回答里给你一行 `verdict_ref=NN` —— 那是它的判定原件在库里的
编号。你要做的就是把这些编号交给合成脚本。

```bash
# 🔴 --decision-id 用 Stage 0 占下的那个号，不要另起一个
cd ~/.openclaw-biga/workspace && python3 skills/decision-card/scripts/synthesize.py \
  --decision-id BIGA-20260921-007 \
  --verdict-ids 17,18 \
  --status WAIT \
  --headline "核心矛盾一句话" \
  --synthesis "两三句说明，数字必须来自上面的 evidence" \
  --extra-missing supervisor.agent_offline "discipline agent 尚未上线" \
  --model-ref anthropic/claude-sonnet-5
```

⚠️ **不要传 `--elapsed-ms`。** 它需要你做时间减法，而你不做算术（这份契约的第一条）。
实测你会直接省略它，于是 Card 上留下 `elapsed_ms=0`。
延迟由 `tools/verify/latency_report.py` 从运行时轨迹里独立测量 ——
**被考核方不自己报成绩**，这是 Phase 1 学到的。

### 🔴 缺失项要带代码

`--extra-missing` 收**两个**参数：`<代码> <人话>`。代码形如 `<域>.<对象>.<原因>`，
常用的就这几个，直接抄：

| 代码 | 什么时候用 |
|---|---|
| `supervisor.agent_offline` | 某个 Specialist 尚未上线 |
| `supervisor.agent_no_response` | spawn 了但没返回 |
| `supervisor.evidence_conflict` | 两个 Specialist 给的事实互相矛盾 |

没有代码，缺失项就只能数次数，说不出是哪一类 —— 而「哪一类」正是
将来判断「系统到底缺什么数据」时唯一有用的信息。

### 🔴 三个 Stage 的判定要一起交

`--verdict-ids` 里要包含 **Stage 1 的全部 + Stage 2 的 risk**。
漏掉 risk，那张卡上就看不到风险审查这一段 —— 而契约层拦不住这种「少交一个」，
它只能检查交上来的那些。**这一条只有你自己守得住。**

### 🔴 Specialist 必须带 stance，否则出不了卡

每个给得出判断的 Specialist，除了 `verdict`（数据完整度）还要有
`stance`（方向判断）。缺了 `synthesize.py` 会直接拒绝，并告诉你让谁去补。

遇到这个报错**不要自己代填** —— 方向判断是那个 Specialist 的职责，
你替它填，就等于你自己当了一次 market agent。

### 🔴 绝不要把 Specialist 的 JSON 抄进命令里

实测（2026-09-20，`BIGA-20260920-002`）抄写的代价：

| 观察 | 数字 |
|---|---|
| 你那一轮里**零工具调用**、纯粹在生成 6460 字符 JSON 的时间 | **47 秒** |
| 之后 `synthesize.py` 因为缺字段失败、重试、读文档诊断 | **49 秒** |
| Specialist 转述后 15 条 evidence 里剩下的 `retrieved_at` | **0 条** |
| 落库 Card 上的 `retrieved_at` 与真实采集时刻的偏差 | **106 秒** |

最后一行是最严重的：`retrieved_at` 本该回答「我们什么时候看到这个数的」，
抄写之后它变成了「你敲命令的时刻」。**事实可追溯这条地基，就是在这一步塌的。**

走 `--verdict-ids`，数据根本不经过你，也就不可能被改。

**参数速查**（不用去翻源码）：

| 参数 | 必填 | 说明 |
|---|---|---|
| `--verdict-ids` | ✅ | Specialist 给你的编号，逗号分隔，例如 `17,18` |
| `--status` | ✅ | `BUY` / `WAIT` / `AVOID` / `BLOCK` |
| `--headline` | ✅ | 核心矛盾，一句话 |
| `--model-ref` | ✅ | 例如 `anthropic/claude-sonnet-5` |
| `--synthesis` | | 合成说明 |
| `--extra-missing` | | 你自己发现的缺失项：**两个参数**，机器可读代码 + 人话，可重复 |
| `--decision-id` | | **不要填** —— 脚本自动分配，填了反而可能撞号 |

⚠️ 没有 `--elapsed-ms`，也不要去找它。理由见上。

⚠️ 数据库在 `data/biga.db`，但**不要自己去 sqlite3 查**（外部 sqlite3 会被运行时拒绝）。
需要看库就用 `python3 -c "import sys; sys.path.insert(0,'skills'); from _store import ..."`。

你提供的是**判断**（`status` / `headline` / `synthesis`）；
数据、组装、契约校验、落库、渲染都不经过你。

这样做的另一个原因：回放走的是**同一份合成代码**。
你手写一份 JSON，回放就无法复现你当时的组装逻辑。

### 回合结束前的自检

结束之前问自己两句：

1. `decision_records` 里**新增**了一行吗？（没有 ⇒ 这一轮等于没做）
2. 我给人的回答里，有没有一张完整的 Card？

两条有任何一条是否定的，就还没做完。
