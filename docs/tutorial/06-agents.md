# 第 06 章 · 建 Agent：脚手架给你的默认值，多半不是你要的

> 📄 **过程** · 写完即冻结（只追加「⏩ 后续变动」指针）
> **覆盖**：这一段是怎么建起来的 ｜ **不覆盖**：当前设计（见 [`../design/`](../design/architecture.md)）


> **本章目标**：建出 `emotion` Specialist，配好 Supervisor 的委派白名单。
>
> **本章产出**：`agents/emotion/AGENTS.md` + 仓库根的 `AGENTS.md` / `SOUL.md` /
> `IDENTITY.md` + 委派配置。

---

## 1. 建一个 Agent

```bash
BIGA=~/.openclaw-biga/bin/biga

$BIGA agents add emotion --non-interactive \
  --workspace ~/.openclaw-biga/workspace/agents/emotion \
  --agent-dir ~/.openclaw-biga/agents/emotion/agent
```

三个参数各有讲究：

| 参数 | 为什么要显式给 |
|---|---|
| `--workspace` | Specialist 的 cwd 要**窄**。它是有界工人，不需要看到整个仓库 |
| `--agent-dir` | 存认证与会话历史。🔴 **绝不跨 agent 复用** —— 复用会导致会话串台，而串台的症状是「某个 Agent 偶尔知道它不该知道的事」，极难排查 |
| `--non-interactive` | 明确不弹交互 |

### 顺带印证了第 01 章的那个判断

这条命令的输出里有这么几行：

```
◇  Doctor changes ──────────────────────────────╮
│  - Installed missing configured plugin "tavily" │
├─────────────────────────────────────────────────╯
Updated config: ~/.openclaw-biga/openclaw.json
  Backup: ~/.openclaw-biga/openclaw.json.bak
```

「加一个 agent」这个操作，实际上跑了 doctor、装了一个插件、改写了配置文件。

第 01 章说「这个 CLI 从来不是只读的，所以必须用 wrapper 强制 `--profile`」——
这里是它的现场证据。如果刚才漏了 `--profile biga`，被装插件、被改配置的就是**邻居**。

---

## 2. 🔴 脚手架给的默认文件，对 Specialist 是错的

`agents add` 在新 workspace 里生成了五个文件：

```
agents/emotion/
├── .git/            ← ⚠️ 一个嵌套的 git 仓库
├── AGENTS.md
├── BOOTSTRAP.md
├── IDENTITY.md
├── SOUL.md
└── USER.md
```

**跑完立刻 `git status`**（第 02 章的习惯）。逐个看下来，五个里有四个半是错的。

### ① 嵌套 `.git` —— 必须删

```bash
$ git -C agents/emotion log --oneline
fatal: your current branch 'master' does not have any commits yet
```

一个全新的空仓库。留着它，父仓库会把 `agents/emotion/` 当成 **gitlink（子模块）**，
目录里的内容一个都不会被跟踪 —— 而你在 `git status` 里只会看到一行
`?? agents/emotion/`，看不出任何异常。

### ② `BOOTSTRAP.md` —— 对 Specialist 是有害的

它的开头是：

```markdown
# BOOTSTRAP.md - Birth Sequence
_You just woke up. Keep this first conversation short and make it yours._

## 1. Ask What to Call You
Introduce yourself as the user's new assistant, then ask what they would like
to call you.
```

这是给**面向人的助理**准备的「初生仪式」。

而 `emotion` 永远不会和人说话 —— 它只被 Supervisor spawn。
留着这个文件，它第一次被调用时可能会回一句
「你好，我是你的新助手，你想怎么称呼我？」，
**而 Supervisor 等的是一份 `AgentVerdict` JSON。**

### ③ `SOUL.md` / `IDENTITY.md` —— 不会被加载

OpenClaw 明确：**spawned session 不加载 `SOUL.md` / `IDENTITY.md`**。
对一个只会被 spawn 的 Specialist，这两个文件是纯粹的死文件。

留着它们的坏处不是占空间，是**误导**：三个月后有人想调整 emotion 的语气，
会很自然地去改 `SOUL.md`，然后困惑于「改了没反应」。

### ④ `USER.md` —— 用户偏好，与 Specialist 无关

### 结论

```bash
rm -rf agents/emotion/.git \
       agents/emotion/BOOTSTRAP.md \
       agents/emotion/SOUL.md \
       agents/emotion/IDENTITY.md \
       agents/emotion/USER.md
```

只留 `AGENTS.md`，然后把它整个重写。

### ⚠️ 但删是不够的 —— 它们会回来

几小时后准备提交时，`git status` 里又出现了这三个文件。看时间戳：

```
-rw-r--r-- 1 … 17:38 IDENTITY.md
-rw-r--r-- 1 … 17:38 SOUL.md
-rw-r--r-- 1 … 17:38 USER.md
-rw-r--r-- 1 … 17:42 AGENTS.md      ← 我改的那个
```

`17:38` 正是 gateway **第一次 spawn `emotion`** 的时刻。
运行时在 spawn 一个还没「安家」的 agent 时会重新 bootstrap 它的 workspace。

所以 `rm` 只解决了当下，**下一次 spawn 它们又回来了**。正确的做法是 `.gitignore`：

```gitignore
# 运行时**每次 spawn 都会重建**这些文件（不是一次性的，删了还会回来）。
# ⚠️ 只忽略 agents/ 下的 —— 仓库根的那两个是 Supervisor 的，是有效契约。
/agents/*/SOUL.md
/agents/*/IDENTITY.md
/agents/*/BOOTSTRAP.md
```

注意路径前缀 `/agents/*/`：仓库根的 `SOUL.md` / `IDENTITY.md` 属于 Supervisor，
**必须提交**。一个写成 `SOUL.md` 的粗暴规则会把它们一起忽略掉 ——
而那是真正有效的契约文件。

> **通用原则**：清理脚手架产物之前，先判断它是**一次性生成**还是**每次运行都会重建**。
> 对后者，`rm` 只是把问题推到下一次；要用忽略规则，而且规则要精确到路径。

> **通用原则**：脚手架的默认产物是按**最常见的使用场景**设计的。
> 当你的场景不是那个场景时，默认值不只是「多余」，而常常是**反向**的。
> 逐个文件问一遍「这个东西在我的场景里会被谁读？读了会发生什么？」

---

## 3. 角色契约写什么

`AGENTS.md` 是 Specialist 的**唯一**契约载体（因为另外两个文件不会被加载）。

写了三段东西，重要性递增。

### ① 硬约束

```markdown
### 1. 你不做算术
任何数字必须来自 skill 的返回值。**不许在推理里心算、估算、换算。**
理由：LLM 算错不会报错，而且算错的过程不可回放。

### 2. 你不发明数据
`missing[]` 里的每一项，都必须原样出现在你给 Supervisor 的回答里。

### 3. 你不 spawn 其他 Agent
你是叶子节点。
```

每条都带**理由**。只写「不许做 X」，模型在边界情况下不知道该往哪边靠；
写了理由，它能自己推断。

### ② 判断口径 —— 这才是 Agent 存在的意义

第 05 章说过，skill 只给事实，判断归 Agent。那么判断的依据就得写在这里：

| 阶段 | 涨停家数 | 最高板 | 炸板率 | 跌停家数 |
|---|---|---|---|---|
| 🧊 冰点期 | <30 | 1–2 板 | >50% | 多 |
| 🌱 修复期 | 30–80 | 3–5 板 | 30–50% | 偏少 |
| 🔥 亢奋期 | >100 | 6 板+ | <20% | 极少 |
| 📉 衰退期 | 60–100（**下降趋势**） | 板高下降 | 上升 | 增多 |
| 😱 恐慌期 | <30（**急速下滑**） | 崩塌 | 极高 | 爆发 |

但光有表格不够，还写了一句更重要的：

> ⚠️ 这张表是**判断依据，不是查表规则**。
> 真实市场很少正好落在某一格里，你要做的是权衡，不是匹配。

### ③ 🔴 最重要的一段：这个 Agent 拿现有数据**说不了**什么

表里有两行带「趋势」字样：衰退期（下降趋势）、恐慌期（急速下滑）。

**而 skill 目前只给单日快照，没有历史序列。**

于是契约里写死：

```markdown
- 当数据看起来像衰退期或恐慌期时，你**只能**说「当日读数与 X 期一致」，
  **不能**断言处在该阶段
- 并且必须在 `missing` 里加一条：
  `情绪周期趋势 —— 只有单日快照，无历史序列，无法区分「衰退期」与「修复期的某一天」`

这不是保守，是诚实。**把「一天的数字」说成「一个阶段」是在编造你没有的信息。**
```

这一段是整份契约里最花心思的部分。

原因：LLM 极其擅长**把不完整的信息补成一个完整的故事**。
给它五个阶段的定义和一天的数据，它会非常自然、非常流畅地告诉你「当前处于修复期」——
听起来完全合理，而它其实无从判断。

**必须明确告诉它「这个结论你的数据支撑不了」，否则它一定会给。**

> **通用原则**：给 LLM 一套判断标准时，要同时告诉它
> **哪些判断是它手上的数据做不出来的**。
> 只给标准不给边界，它会把标准套满，包括套在根本不适用的地方。

### 顺带：关于那个分数

skill 会返回一个 `emotion_score`。契约里专门写了一段：

```markdown
⚠️ **它是一个未经验证的参考值。**
- 可以在推理里参考它
- **不要**把它当成结论呈现（「情绪分 55 分，属于修复期」是错误的表述）
```

一个 0–100 的分数看起来非常权威。**如果不明说它没被验证过，它会被当成结论。**

---

## 4. Supervisor：`main` 的三个文件

`main` 是唯一直接与人对话的 Agent，所以它**确实**需要 `SOUL.md` / `IDENTITY.md`。

| 文件 | 写什么 |
|---|---|
| `AGENTS.md` | 职责、流程、硬约束 |
| `SOUL.md` | **怎么说话**（不是做什么） |
| `IDENTITY.md` | 名字、气质 |

`SOUL.md` 里最值得抄的一条：

```markdown
**2. 不确定就说不确定，而且要说清楚不确定在哪。**

❌「情绪面整体偏暖，可以适度参与。」
✅「涨停 78 家、炸板率 24%，读数与修复期一致。但只有单日数据，
   分不清这是修复中还是衰退途中的某一天 —— 这一条已列进缺失项。」
```

**给正例和反例，比给形容词有效得多。** 写「要具体」不如直接展示什么叫具体。

`IDENTITY.md` 里选了 🧭 而不是 📈：

> 罗盘指的是**你在哪**，不是**会涨到哪**。
> 这正好是本系统的边界 —— 描述当前状态，不做预测。

一个 emoji 能承载多少信息是可以争论的，但选择本身强迫你把边界再想一遍。

---

## 5. 委派配置：两个容易踩的坑

```json5
{
  agents: {
    entries: {
      main: {
        subagents: { allowAgents: ["emotion"], delegationMode: "prefer" },
      },
      emotion: {
        // 🔴 刻意为空：Specialist 是叶子节点，不许再往下 spawn。
        subagents: { allowAgents: [] },
      },
    },
  },
  tools: {
    agentToAgent: { enabled: true, allow: ["main", "emotion"] },
    sessions: { visibility: "tree" },
  },
}
```

### 坑一：`agentToAgent.allow` 要把两端都列上

官方语义：**requester 和 target 都要在 allow 里匹配得上**。

- 空 `allow` = 未设 = **allow-all**
- 只列一半 = **互相够不着**

「只列一半」这个错误尤其讨厌，因为它看起来像是配了。

### 坑二：`delegationMode: "prefer"` 不是调度器

它只是 prompt 引导。**真正保证 Supervisor 一定去调 Specialist 的，
是它 `AGENTS.md` 里的硬性流程约定，加上验收时查 `agent_runs` 表。**

配了 `prefer` 就以为委派有保障，是典型的**把提示当成机制**。

### 为什么 `allowAgents` 只列了 `emotion`

其余 6 个 Specialist 还没建。把它们提前写进白名单，
就会在配置里留下 6 个**指向不存在对象的引用** —— 配了但永远不生效，
而且不会报错。这正是本项目重点防范的「死配置」。

同理，`agents/` 下也没有预建 6 个空目录。

---

## 6. 验证

```bash
$BIGA agents list
```

```
- main (default)
  Workspace: ~/.openclaw-biga/workspace
  Agent dir: ~/.openclaw-biga/agents/main/agent
  Model: anthropic/claude-sonnet-5
- emotion
  Workspace: ~/.openclaw-biga/workspace/agents/emotion
  Agent dir: ~/.openclaw-biga/agents/emotion/agent
  Model: anthropic/claude-sonnet-5
```

两个 agent 的 workspace 与 agentDir 都在 `.openclaw-biga` 下，没有一个指向别处。

```bash
for k in agents.entries.main.subagents.allowAgents \
         agents.entries.emotion.subagents.allowAgents \
         tools.agentToAgent.allow; do
  printf '%-52s = %s\n' "$k" "$($BIGA config get $k | tr -d '\n ')"
done
```

```
agents.entries.main.subagents.allowAgents            = ["emotion"]
agents.entries.emotion.subagents.allowAgents         = []
tools.agentToAgent.allow                             = ["main","emotion"]
```

确认 `agents/emotion/` 不再是 gitlink：

```bash
$ git status --porcelain agents/
?? agents/emotion/
```

（如果嵌套 `.git` 还在，这里看到的是一样的输出 —— 所以真正的验证是
`ls -a agents/emotion/` 里没有 `.git`。**看起来一样的两种状态，要用能区分它们的方法去验。**）

---

## 7. 本章要点

| 要点 | 一句话 |
|---|---|
| `agents add` 不是只读操作 | 它跑 doctor、装插件、改配置 —— 印证了 wrapper 的必要性 |
| 脚手架默认值可能是**反向**的 | `BOOTSTRAP.md` 会让 Specialist 开口问「我该叫什么名字」 |
| 嵌套 `.git` 必须删 | 否则父仓把整个目录当子模块，且 `git status` 看不出异常 |
| **脚手架产物可能会重建** | `rm` 只解决当下；每次 spawn 都会回来的东西要 `.gitignore`，且规则要精确到路径 |
| 契约写在会被加载的文件里 | Specialist 不加载 `SOUL.md`，写在那里的约束等于没写 |
| 每条约束都带理由 | 只说「不许」，模型在边界情况下不知道往哪边靠 |
| **告诉模型它做不出什么判断** | LLM 会把不完整的信息补成完整的故事，而且非常流畅 |
| 未验证的分数要明说 | 0–100 的数字看起来很权威，不明说就会被当结论 |
| 给正例反例，别给形容词 | 「要具体」不如直接展示什么叫具体 |
| `agentToAgent.allow` 两端都要列 | 空=allow-all，列一半=够不着 |
| `delegationMode` 是提示不是机制 | 真正的保障是流程约定 + 查 `agent_runs` |
| 不预先配置不存在的对象 | 指向不存在 agent 的白名单 = 死配置，不报错 |
| 用能区分状态的方法去验证 | 两种状态的 `git status` 输出一样，就别用它来验 |

---

上一章：[05 · 第一个技能](05-first-skill.md)　|　下一章：[07 · 端到端](07-end-to-end.md)
