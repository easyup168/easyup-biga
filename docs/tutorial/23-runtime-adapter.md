# 第 23 章 · Python 驱动 spawn：运行时适配层

> 📚 **过程** · 写完即冻结（后续变动只在末尾追加「⏩」指针）
> **覆盖**：确定性编排批 C-I —— 为什么要一层运行时适配、状态归一化值在哪、
> grant 与 groupId 的硬约束、怎么先把真实响应形状抓出来再写解析、cancel 的坑
> **不覆盖**：`DecisionOrchestrator` 与生产入口切换（批 C-II）——
> 这一批**一个字都不改 `bin/biga-card`**，只把地基验实、把入口建好

---

## 目标 / 产出

- `skills/_runtime/mcp.py` —— 传输层：`Grant`（铸/删临时 `.mcp.json`）+ `MCPClient`（JSON-RPC）
- `skills/_runtime/adapter.py` —— `OpenClawRuntimeAdapter.start / wait / cancel / status`，状态归一化
- `tools/verify/adapter_spike.py` —— 对着真实运行时把 spike 的三个未知数测掉，可重跑复验
- 32 条离线测试 + 四项 live 补验（P1 五路并行 / P2 grant 长跑 / P3 失败结构化 / cancel 对应）

---

## 为什么这么做

### 单点风险：Python 到底能不能不经 LLM 驱动 spawn

整个确定性编排升级压在一个假设上：**编排器（Python）能不经过 LLM 轮次，
起一个 Specialist 并拿到跟「真 spawn」一样的运行时证据。** 如果不能，那
「把工作流从 LLM 里拿出来」这件事就是空中楼阁 —— 因为 `sessions_spawn` 是
暴露给 **agent** 的 MCP 工具，CLI 里根本没有对应的子命令。

spike（设计文档 §7）已经证明能：`biga attach --print-config` 铸一个 MCP grant，
再对 `127.0.0.1:<临时端口>/mcp` 做 JSON-RPC。但 spike 只跑了**一次** spawn。
批 C-II（第一次改生产入口）不能带着三个没测过的假设开工：

1. 五路并行 fan-out 是不是**真**并行（还是排队执行完一个再下一个）；
2. grant 撑不撑得住一次完整运行的时长（780s）；
3. spawn 失败时报错是不是**结构化**的（还是一个裸异常从适配器里漏出去）。

> 🔴 **「应该没问题」和「测过了」是两回事。** 这一批的头等大事就是把这三个
> 「应该」变成「测过」——因为下一批要站在它们上面改生产入口。

### 为什么值得单独一层，而不是让 Orchestrator 直接调 MCP

核心理由是**状态归一化**。`sessions_spawn` / `agents_wait` / `subagents`
回的状态是运行时自己的措辞，实测就有一堆：`accepted`（起了）、`queued` /
`running`（在途）、`done`（成了）、`killed`（被取消）、`forbidden`（被拒）。
这些词会随运行时版本变。

如果让 Orchestrator 直接读这些词做分支，运行时哪天把 `done` 改成 `completed`，
整条编排链都要跟着改，而且是**静默失效**（分支不匹配，落进 else）。

⇒ 适配器对外只暴露自己定义的一小组 `SpawnStatus`
（`running` / `succeeded` / `failed` / `timeout` / `cancelled` / `unknown`），
运行时原始词只在这一层翻译。运行时改词，只改这里的一张映射表。

> 通用原则：**跨系统边界的「别人的措辞」，在边界上翻译成「自己的措辞」一次，
> 内部只认自己的。** 否则别人换个说法，你到处都要改。

🔴 **R-3 落在归一化这一步**：认不出来的原始词映射到 `UNKNOWN`，**绝不当
`SUCCEEDED`**。一个没见过的状态被默默当成功，正是本项目最怕的 fail-open ——
所以映射表是白名单，`.get(raw, UNKNOWN)`，不是「不是失败就算成功」。

### grant 与 groupId：两条不是「设计选择」的硬约束

spike 定死的三条约束里，两条落在传输层，它们是 **API 硬约束**，不是我们能
权衡的：

- **`collect=true` 没有 requesting run 时必须带 `groupId`。** Python 客户端
  天然没有「正在进行的 agent 轮次」，所以 fan-out 的一批 spawn 必须自己生成
  一个 groupId 共用。不带直接被拒。
- **grant 带 TTL、不会自己回收、只会到期。** ⇒ TTL 必须 ≥ 本次运行的总预算
  （`BIGA_CARD_DEADLINE_SEC`，780s），否则长跑到一半 grant 过期；而且用完
  要**主动删**那个临时 `.mcp.json`（`Grant` 做成 context manager，退出即删）——
  不删就是每次运行都在攒的小泄漏。

### token 用量：带出来，但这一批不写库

`agents_wait` 的返回里带 `usage: {inputTokens, outputTokens}`。适配器把它放进
`SpawnResult.usage` 带出来，但**不在这一批写进任何表**。

因为 C-I 不接任何 run —— 没有 `run_events` 可写。写库是 C-II 的事（它有 run
上下文，把 usage 写进 `run_events.detail`，**不写 `agent_runs`**：schema v2 已经
删过 token 列，理由是「唯一真相源是运行时 trajectory，这里再存一份就是滞后的
第二套口径」，那条理由现在仍成立）。

> 🔴 **在有消费方之前建写入路径就是 L-1。** 带出来供 C-II 用，不代表现在就该落库。

---

## 执行

### 先抓真实响应形状，再写解析

写适配器的 `wait()` / `status()` / `cancel()` 之前，得知道运行时到底回什么。
凭想象写解析、再用「我以为的形状」做离线测试，测的就是自己的假货，不是产品
（本仓库的 F3/L-13 教训）。所以先用一次性 grant 把真实形状抓出来：

`sessions_spawn` 的 accepted 响应（在 `content[0].text` 里是一段 JSON）：

```json
{"status":"accepted","runId":"...","childSessionKey":"agent:emotion:subagent:...","context":"isolated",...}
```

`agents_wait` 的完成响应：

```json
{"completed":[{"runId":"...","status":"done","result":"收到。","usage":{"inputTokens":123,"outputTokens":5}}],"pending":[]}
```

这些真实形状被固化进 `tests/test_runtime_adapter.py` 的 fake，离线测试因此验的是
「解析真实形状」，不是「解析我编的形状」。

### 三项 live 补验（对着真实运行时，会花钱）

```
$ python3 tools/verify/adapter_spike.py parallel
  峰值并发 RUNNING = 5 / 5
  成功 5/5；usage 合计 in=615 out=25
  ✅ 区间相交（并行）：True —— 峰值同时 RUNNING 5 个

$ python3 tools/verify/adapter_spike.py failure
  ✅ 不存在的 agent → SpawnStartError（结构化）：... "agentId is not allowed" ...
  ✅ 失败被翻译成可判断的状态，没让裸异常从 Adapter 里漏出去

$ python3 tools/verify/adapter_spike.py cancel
  active[] runIds: ['9716f3f9', 'c6636864']   ← 注意：不是 spawn 顺序
  取消 h0 之后：h0=cancelled  h1=running
  ✅ 取消杀的是对的那一个：True

$ python3 tools/verify/adapter_spike.py grant-longevity   # ~13 分钟
  t=0 起一次：status=succeeded  usage={'input': 123, 'output': 5}
  等了 780s；grant 仍可用：True
  .mcp.json：退出前存在=True，退出后存在=False
  ✅ grant 撑过 780s 且 .mcp.json 被清理：True
```

P1 的判据是**区间相交**，不是「五个都成功了」—— 成功但排队执行看起来完全正常，
那正是本仓库在别处踩过的「延迟其实是正确性 bug 的症状」。实测峰值同时 5 个在
RUNNING，是真并行。

---

## 坑

### 抓形状的脚本自己有个 bug，差点误判 agents_wait

第一版抓形状时，我从 `spawn.result.structuredContent` 里取 `runId` —— 结果它是
**空的**（这个运行时把数据放在 `content[0].text` 的 JSON 里，不放
`structuredContent`）。于是 `runId=None`，`agents_wait(ids=[None])` 回了
`Cannot read properties of null (reading 'trim')`。

一瞬间以为是 `agents_wait` 的 API 有问题。其实是**我的抓取脚本**取错了字段。
适配器的 `MCPClient.call_tool` 是对的（先看 `structuredContent`，没有就把
`content[].text` 当 JSON 解）。

> 教训还是那条：**探针/抓取脚本自己也会有 bug，第一反应先怀疑脚本，再怀疑被测的东西。**

### cancel 只认 taskId，且 active 顺序不是 spawn 顺序

运行时的取消（`subagents action=cancel`）只认 `tasks[].taskId`。我先试了两个
「手上现成的 id」，都被 `Task outside session tree` 拒：

- 传 spawn 返回的 `runId` —— 拒；
- 传我自己设的 `taskName`（stable alias）—— 也拒。

而那个 `taskId` 与 `runId` **没有直接关联字段**：`subagents list` 的 `active[]`
带 `runId`、`tasks[]` 带 `taskId`，两个数组各说各的。只能靠**同序对应**
（`active[i]` ↔ `tasks[i]` 是同一个 subagent）把 runId 映射到 taskId。

🔴 而且 `active[]` 的顺序**不是** spawn 顺序 —— 实测我先 spawn market 再
spawn emotion，`active[]` 里却是 `[emotion, market]`。所以绝不能按「我第几个
spawn 的」去 index，必须按 `active[i].runId == 目标 runId` 定位 i 再取
`tasks[i]`。cancel 补验就是来钉死这一点的：取消 h0（market），死的正是
market、emotion 还在跑 ✅。如果同序对应是错的，死的会是 emotion，当场露馅。

### 「不调用任何付费模型」与这一批天生冲突

分发提示词的「通用前置」写着「不出新卡、不调用任何付费模型」——那是给批 A
（纯 Python 探针）写的。而 C-I 的三项补验**天然要真实 spawn**：验并行、验 grant
长跑、验失败错误面，没有一项能不 spawn 就测。这条冲突是分发文档的 bug，不是
产品决策；补验就是这一批存在的理由。成本约 $1–3，比一次常规出卡（~$1.2）还低。

> 通用原则：**当「省钱的默认约束」和「这件事的本质」冲突时，先确认那条约束是不是
> 从别处照抄来的。** A 批不 spawn 是对的，C 批不 spawn 就没法做。

---

## 验证

```bash
# 1. 离线全绿（批 C-I 之后 938 条）
python3 -m pytest -q

# 2. 状态归一化的探针见过红（P4）：把 status() 改成返回运行时原始词，
#    tests/test_runtime_adapter.py::TestStatus::test_对外永远是归一化状态而非原始词
#    会断言 'killed' == 'cancelled' 失败 —— 见 CHANGELOG 的探针记录。

# 3. 四项 live 补验（会花钱，走真实运行时）：
python3 tools/verify/adapter_spike.py parallel        # 五路并行相交
python3 tools/verify/adapter_spike.py failure         # 失败结构化
python3 tools/verify/adapter_spike.py cancel          # 取消命中对的那一个
python3 tools/verify/adapter_spike.py grant-longevity # grant 撑过 780s（~13 分钟）
```

预期：`pytest` 938 绿；P4 探针弄坏时报红；四项 live 补验各自 `✅`。

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 整个升级压在「Python 能不经 LLM 驱动 spawn」这个假设上；spike 只验了 1 次，C-I 把三个未知数变成测过的事实 |
| 2 | 跨系统边界的「别人的措辞」在边界翻译成「自己的措辞」一次，内部只认自己的 —— 运行时改词只改一张映射表 |
| 3 | 🔴 状态归一化里认不出来的落 UNKNOWN，绝不当 SUCCEEDED（R-3 的 fail-open 防线） |
| 4 | grant 带 TTL≥总预算、用完主动删 `.mcp.json`；fan-out 一批共用一个 groupId —— 都是 API 硬约束不是设计选择 |
| 5 | 先抓真实响应形状再写解析，离线测试用真实形状做 fake —— 否则测的是自己的假货（F3/L-13） |
| 6 | cancel 只认 tasks[].taskId，靠 active[i]↔tasks[i] 同序对应映射；而 active 顺序≠spawn 顺序，必须按 runId 定位 |
| 7 | token 用量带出来供 C-II 写 run_events，但这一批不写库 —— 没有消费方就先不建写入路径（L-1） |
| 8 | 「省钱的默认约束」和「这件事的本质」冲突时，先查那条约束是不是从别处照抄的（A 批不 spawn 对，C 批不 spawn 没法做） |

> ⏩ **后续变动（2026-09-24，批 H-II）**：本章的 `skills/_runtime`（`OpenClawRuntimeAdapter`
> / `MCPClient`），其**真实实现**已迁至 `src/easyup_biga/runtime/`。旧路径原地保留
> re-export 薄壳（子模块壳用 `sys.modules[__name__] = 真实模块` 做身份等同）⇒ 本章正文里的
> `from _runtime import ...` / `from _runtime.mcp import ...` 照旧成立，只是代码本体不在
> 那儿了。见 `CHANGELOG.md` 批 H-II 与教程第 39 章。
