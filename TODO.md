# TODO

> 全局待办。每次新会话启动必读；完成事项立即更新。
> 格式：`- [ ] <事项>` + **验收** + **命令/路径**

---

## Phase 1 · 最简功能验证

目标：**一条最细的、端到端能走通的线** —— 验证机制（跨 agent spawn / 契约落库 / 回放），
不是覆盖面。机制通了，其余 agent 是复制。

### ✅ 已完成（2026-09-19）

- [x] 装 node v24.21.0（增量；未改 `default`、未卸旧版本）
- [x] 装 openclaw 2026.9.5 到 `~/.openclaw-biga/runtime/` —— **未进 nvm bin**（红线 R-2）
- [x] `bin/biga` wrapper —— 强制注入 `--profile biga`（红线 R-1）
- [x] 隔离实测：跑 BigA 命令后，另一套系统的 state / config mtime 未变
- [x] 守卫测试落在另一套仓库（受害者侧），3 项，断言其 PATH 解析未被改掉
- [x] workspace 骨架 + git init + 首个 commit
- [x] 文档：架构 / 安装指南 / 上游参考 / CLAUDE.md
- [x] 公开化处理：抽掉实盘系统可识别细节（`architecture.md` §9 改写）
- [x] `docs/tutorial/` 开源教程体系启动 —— README 索引 + 第 01/02 章
      纪律写进 `CLAUDE.md`「教程纪律」：**每完成一段先写教程再往下做**（裁定 10）

### ⬜ 待做

- [x] **决定 Phase 1 建几个 agent** —— 已裁定：**2 个**（`main` + `emotion`）
      8 条验收标准无一需要第三个 agent；无 skill 的空 agent 只能编数字，违反契约铁律 3
- [x] `biga setup` —— profile 初始化，端口 **19789**，**未接任何 IM 通道**
      走 `setup --baseline` + `config patch`，不走 onboarding 向导（理由见教程 02）
      验收：`config validate` 绿；`gateway.port=19789`；`model.primary=anthropic/claude-sonnet-5`；
      `subagents.maxConcurrent=6`；`channels` 未设；另一套实例 state mtime 未变
- [x] `skills/_contract/` —— `Evidence` / `AgentVerdict` / `DecisionCard`
      四条铁律全部在 `__post_init__` 里**拒绝构造**，不是记日志
      验收：`pytest` 51 绿（行为 43 + AST 单一实现扫描 8）；
      AST 扫描已用探针验证「真的会红」（重名类 / 近名类 / 字典版契约三种形状）
      ⚠️ 一处对 `architecture.md` §4.1 的偏离：`Evidence` 增加 `field`/`label` 两字段 ——
      不加则铁律 3 无法执行、§7.1 的 Card 样例也渲染不出来。已在教程 03 §3 说明
      教程：`docs/tutorial/03-contract-layer.md`
- [x] `skills/_store/db.py` + `schema.py` + 三张表
      `decision_records` / `agent_runs` / `raw_market_snapshot`
      只追加由 **SQLite 触发器**强制（6 个），不是代码约定；回放用 partial unique index
      支持不覆盖原始记录；读路径一律 `readonly=True` 连接
      验收：`pytest` 75 绿（+ store 行为 20 + 无裸 sqlite3 扫描 4）；扫描已用探针验证会红
      教程：`docs/tutorial/04-store-layer.md`
- [x] `skills/emotion-calc/` —— 真采 A 股情绪数据，输出合法 `AgentVerdict`
      2026-09-18 实测：涨停 78 / 炸板 25 / 跌停 0 / 炸板率 24.27% / 最高 4 板 /
      梯队 {1:66,2:8,3:2,4:2} / 涨跌平 4277·1173·180 / 情绪分 55.52，耗时 18.8s
      🔴 **数据源静默陷阱**：请求非交易日照样 `rc=0` 返回数据（元旦→`tc=0`）。
      因此 `as_of` 一律取自响应里的 `qdate`，绝不取自请求日期；严格模式对不上进 `missing[]`
      ⚠️ 因此**没用现成封装库** —— 它把 `qdate` 整理掉了，那个陷阱就无法防御
      R-3 三条路径实测可复现：PASS / WARNING / UNKNOWN，无一行「PASS + 有缺失」
      验收：`pytest` 102 绿（+ emotion 离线 27）；raw 层落盘 4 条/次
      教程：`docs/tutorial/05-first-skill.md`
- [x] 建 agent `emotion` + 写 `agents/emotion/AGENTS.md`
      ⚠️ 清理了脚手架默认产物：嵌套 `.git`（会让父仓当 submodule）、`BOOTSTRAP.md`
      （会让 Specialist 开口问「我该叫什么名字」）、`SOUL.md`/`IDENTITY.md`（spawned
      session 不加载）、`USER.md`
      契约含「拿单日数据**说不了**什么」一节 —— 防 LLM 把不完整信息补成完整故事
      教程：`docs/tutorial/06-agents.md`
- [x] 配 `main`(Supervisor)：仓库根 `AGENTS.md` / `SOUL.md` / `IDENTITY.md`
      `allowAgents:["emotion"]`（不预填 6 个不存在的 agent = 避免死配置）；
      `emotion.allowAgents:[]`（叶子节点）；`agentToAgent.allow:["main","emotion"]`（两端都要列）
- [x] 端到端跑通：`biga agent --agent main -m "今天市场情绪怎么样？"`
      🔴 **被认证方式卡住 —— 根因已查清**

      **现象**：Supervisor 跑起来了，但它找不到 OpenClaw 的跨 agent spawn 工具，
      退而使用通用子 agent。那个子 agent **契约执行得很对**（读了
      `agents/emotion/AGENTS.md`、跑了 `emotion_calc.py`），但它不是 `emotion` agent ——
      `subagent_runs` 为 0 行，**验收第 1 条过不了**。

      **实测证据**
      | 观察 | 证据 |
      |---|---|
      | MCP server 本身正常 | 直接 HTTP 探测（含 `initialize` 握手）返回 **49 个工具**，含 `sessions_spawn` |
      | Supervisor 看不到它们 | 会话记录：`ToolSearch "sessions_spawn"` 等 4 次查询全部落空 |
      | 退路被走了 | `Agent general-purpose` |
      | 第一次运行卡死 | `activeTool=Agent ... reason=blocked_tool_call`，346s 后 `CLI run aborted` |
      | 真凭据不存在 | `models auth list` → `Profiles: (none)`；claude-cli 是插件自带 synthetic auth |

      **根因（已确证，非推断）**：gateway 日志里有一行警告，说注入的 MCP server
      被本机的一条策略拦下了。登录时被自动选中的桥接模式 ⇒ 工具经 MCP 注入 ⇒
      整体被拦 ⇒ `ToolSearch` 返回 "No matching deferred tools found"。

      ⇒ **只要走那条桥接路径，跨 agent spawn 就不可能工作。** 与提示词无关。
      ⚠️ 该限制不在本项目控制内，**不要去改它**。
      ⚠️ 具体是哪条限制，属于本机情况，记在仓库外的 `AUTH-NOTES.local.md`。

      **已做的修正**：Supervisor 的 `AGENTS.md` 写死工具真名
      `mcp__openclaw__sessions_spawn`，并明令禁止用通用 Agent 顶替（附三条理由）。
      但工具不可见时，契约改得再硬也没用。

      **解法（已实施，两件事缺一不可）**
      1. **运行时**：`agents.defaults.models.*.agentRuntime.id` 由 login 自动写入的
         桥接模式改为 **`openclaw`**（内置 harness，不经外部 CLI，那条限制不适用）。
         fallback 模型同改
      2. **凭据**：见裁定 11 —— 与邻居共享

      **已验证的结果**
      - `agent --agent main -m "只回复两个字：收到"` → 干净返回「收到」
      - 🔴 **Supervisor 真的 spawn 了 emotion**，证据在 OpenClaw 自己的表里：
        `subagent_runs`: `controller_session_key=agent:main:main`,
        `child_session_key=agent:emotion:subagent:deb35b08-…`
      - emotion 子会话 `status=succeeded`，8 次工具调用，耗时 **79.9s**，
        raw 层新增 8 条快照（确实跑了采集）

      **发现的新问题（已修契约，待复测）**
      - 第一次成功 spawn 后，Supervisor 用 `sessions_yield` **提前收工**：
        自己 `status=succeeded`，而 emotion 还在后台跑，**Card 从未产出**。
        典型的「每步日志都绿、事情没做成」。
        已在 `AGENTS.md` 加 Stage 1.5「必须等 + 必须出卡」与回合结束自检
      - ⚠️ 延迟：当时记的 79.9s 来自 **Supervisor 自报**，不可信（实测它两个方向都偏）。
        真实测量与三个 bug 的修复见下方「验收第 8 条」与教程第 10 章

      教程：`docs/tutorial/07-end-to-end.md`
- [x] `replay <decision_id>` —— 与在线路径共用 `card_ops.synthesize()`（纯函数）
      AST 测试钉死「两个 CLI 都不自己构造 DecisionCard」
      `--check` 当场抓到真 bug：回放丢了 Supervisor 的 3 条 `extra_missing`，
      **让 Card 悄悄变好看** —— 已修（从原卡无损反推）并补了「守卫会红」的测试
      实测：在线 WAIT(#1) + 换 opus 回放 AVOID(#2)，原记录未改动
      教程：`docs/tutorial/08-replay.md`
- [x] 隔离演练：`kill -9` BigA gateway（pid 64645）
      邻居**六项逐位一致**：pid 391 未变、**启动时间未变**（证明没被重启过）、
      state/config mtime 未变、`nvm default=24.18.0`、nvm bin 仅 v24.18.0 带 openclaw
      无孤儿进程残留
      ⚠️ 演练中发现：起 gateway 会**自动创建 4 条 cron**（见「待裁定」）
      教程：`docs/tutorial/09-isolation-drill.md`

### Phase 1 验收（全部满足才算过）

> ✅ **九项全过**（`PASS 9 · FAIL 0 · PENDING 0`，2026-09-19，`BIGA-20260919-010`）
>
> ```bash
> python3 tools/verify/phase1_acceptance.py \
>         --decision-id BIGA-20260919-010 --baseline <基线.json> --live
> ```
> 脚本把「没测」记为 `PENDING` 而不是 `PASS` —— 不带 `--decision-id` 跑只会得到
> `PASS 2 · PENDING 7`。**这是故意的**：R-3 对验收脚本自己同样适用。

1. [x] Supervisor **确实 spawn 了** `emotion`
       ⚠️ 判据已修正（外部评审 P2-3）：`agent_runs` 有该行**不算证明** ——
       我们自己的代码就在写它。真正的判据是运行时自己的 `subagent_runs`
       两份独立记录都核：`agent_runs` + 运行时 `subagent_runs`
2. [x] `emotion` 返回**合法 `AgentVerdict`**，含 ≥1 条带 `as_of` 的 `Evidence` —— 13 条
3. [x] 输出 Decision Card，含 状态 / 证据 / **缺失项** 三段 —— 缺失项 3 条
4. [x] `decision_records` 落库 1 行，`replay --check` 重跑出一致结论
5. [x] 故意打断数据源 → `UNKNOWN` + `missing` 3 条，**不是 PASS**（红线 R-3）
6. [x] 另一套系统：gateway pid / 启动时间 / config / `nvm default` / 18789 监听 五项未变
7. [x] 另一套系统的 PATH 解析仍指向 `v24.18.0`（红线 R-2 未被破坏）
       外加 6b：`/proc/<pid>/fd` 逐个核 —— 无任何 BigA 进程持有邻居目录下的句柄（I-1）
8. [x] 单次端到端 **< 90s**（热缓存，2 个 agent）—— 实测 **74.8s / $0.2179**
       ⚠️ **这一条的预算从 60s 改成了 90s。** 60s 是四个阶段预估「下界之和」，
       不是预算；实测每个阶段都在自己区间内而合计 75s。推导见
       `architecture.md` §10.1 与教程第 10 章。**八 Agent 的 105s 未改**
       判据是 `latency_report.py` 的**等卡墙钟**，不是 Card 上的 `elapsed_ms`

### Phase 1 明确不做

接飞书 / 建任何 cron / 任何下单路径 / 建其余 6 个 agent /
PostgreSQL / Redis / 回测 / 历史数据回补 / Web UI

---

## 已知问题

- ⚠️ **`BIGA-20260920-001` / `-002` 两张卡的 `retrieved_at` 不可信。**
  它们是 LLM 在合成时统一填的「敲命令时刻」，不是采集时刻。
  两张卡的真实采集时刻分别可从 `raw_market_snapshot` 查到
  （`-002` 是 **20:42:48**，卡上写的是 20:44:34，差 106 秒）。
  **按 raw 层永不改写的原则，这两行不修改** —— 在此标注，
  做任何时延分析时跳过它们。schema v3 之后落的卡不再有这个问题

- ⚠️ **`latency_report.py --parallel-check` 的总量测量还按老架构的假设写。**
  批 C-II 之后对 `BIGA-20260922-001` 真跑了一次，暴露两处：① 报告说「窗口内
  没有 main 的任何轮次，这份分解缺了最大的一块」——它假设 Supervisor 会有
  自己的一轮 LLM 调用，但 C-II 之后编排完全由 `orchestrator.py` 驱动，
  `agent:main:orchestrator-<run_id>` 只是一个 grant 会话，不是一次真正的
  main 轮次，这个假设从 C-II 起就不成立了；② 调度器记录的几次执行
  （cli news/sector/market 若干次、subagent risk）在 trajectory 里对不上号，
  报告自己承认「合计偏小」。
  **核心判据（Stage 1 是否真并行）没受影响**——那次实测区间两两相交、
  最小重叠 36.3s，`✅ 真并行` 结论可信,只是"等卡墙钟"与总成本这两个数字
  不能再当真。批 D-II 收尾时没有单独修它，先记在这里，不阻塞批 E——
  这是报告工具的观测口径滞后，不是生产路径的 bug（区别于已修的
  `agent_runs` 那条：那条是生产路径自己的验证闸门坏了）

---

## 待测（有明确判据，缺的是数据）

- [x] ✅ **当天日线几点发布** —— 已实测（2026-09-21）：**15:32:47–15:37:50** 之间，
      收盘后约 33~38 分钟。「收盘后跑就对齐」这条推断因此被证伪并改掉。

      ⚠️ **n=1，仍然不要写成「几点之后跑就行」** —— 那是把一次观测当规律，
      与被证伪的那条是同一个错误。判断方法仍是直接查日线最后一根的日期。

- [ ] **多测几天**，看这个延迟稳不稳定（稳定才谈得上给时刻建议）

## 两份外部评审的合并台账

两份**互不知情**的外部评审，在重叠区域独立做了功：

| | 来源 | 条目 | 状态 |
|---|---|---|---|
| 评审 ① | 评审 ①（`docs/external/`，只读） | P1-1…3 / P2-1…3 | ✅ 全部修完（FIX-01…06） |
| 评审 ② | `2026-09-21-adversarial-review-phase2.md` | F1…F23 | 15 模式级 / 7 实例级 / 1 修不干净 |
| 评审 ③ | 第三轮**深度**评审 | 6 条 | ✅ 全部修完，每条都有探针红灯 |

🔴 **三份外部材料都只读，修复进度只记在这里。**

### 评审 ③：六条是同一个形状

不涉及业务逻辑 —— **全打在守卫本身上**：

> 守卫查的地方，和它声称守的地方，不是同一处。

已编号 `architecture.md` §9 **L-13**（本仓库出现十次，两轮评审里复发率最高的模式）。

| # | 发现 | 修法 | 探针 |
|---|---|---|---|
| 1 | `spawn_check` 跑了但退出码被丢 | `bin/biga-card` `exit 4`；守卫改**行为测试** | 拆掉闸门 → 2 红 |
| 2 | 无 `.git` 时守卫集体 error | `_scan.py` 降级遍历 + `scan_mode()` | 退回 `check=True` → 4 红（含集成那条） |
| 3 | 声称离线的单测真连新浪 | `tests/conftest.py` 进程内拦 socket | 首跑即命中评审那一条，**零误报** |
| 4 | 「判不了」与「不通过」同码（**四处**） | `tools/verify/_verdict.py` 唯一定义 + AST | 两处分别退回 → 红 |
| 5 | 旧口径还活在三处代码里 | 守卫扫活文档，要求自带历史框 | 两个探针（改回旧句 / 新处裸写）→ 红 |
| 6 | risk 的 fail-closed 是**半硬**的 | 检测到即返回，载荷不留派生字段 | 退回半硬 → 5 红 |

⚠️ 第 4 条评审只报了一处（`latency_report`），顺着扫**一共四处**。
最说明问题的是 `phase1_acceptance.py`：注释写着「PENDING 不算 PASS，这是第一条红线」，
下一行 `return 0 if … else 1` —— **PENDING 与 FAIL 退成同一个码**。

详见 `docs/tutorial/19-guards-that-cannot-fail.md`。

### 去重：重叠处并没有被真正覆盖

评审 ② 的附记怀疑「评审 ① 的修复可能没真正盖住 F3」。**已核实，它的怀疑是对的**：

| 看起来重叠 | 实际 |
|---|---|
| F1 ↔ P1-1/P1-2（决策身份） | 两条新防线都建立在「id 字段本身可信」之上，而 F1 恰好在这个前提**之下** ⇒ 不覆盖。已单独修（schema v5） |
| F3 ↔ P2-3（`agent_runs` 不是 spawn 证明） | FIX-06 只修了**教程两处口径矛盾**，机制缺口原样留着 ⇒ **不覆盖，F3 完全成立** |
| F4 ↔ P1-3（`as_of` 语义） | FIX-03 修的是 news 的 Evidence 层；F4 在 market/sector 的 **raw 落盘层**。已核实 `save_raw_snapshot` 仍用全局共享的 `as_of` ⇒ **不覆盖** |

> 🔴 **教训：两份报告指向「同一大类」时，最容易假设已经修过了。**
> 三处重叠，**三处都没被覆盖** —— 因为真正的洞总在上一次修复所依赖的前提里。

### 逐条状态 —— 按**复查**的口径，不是按我自己的

⚠️ 上一版这里写「23 / 23 全部处理完」。**那个说法不成立**，外部复查逐条核过：

| 档 | 数量 | 含义 |
|---|---|---|
| ✅ 模式级修复 | 18 | 根因消除，同形状的下一例也会被挡住（F8/F10/F16 已从下方补齐，见对应小节） |
| 🔶 实例级修复 | 4 | **报出的那个场景堵上了，模式还在**（F2 / F3 / F5 / F7） |
| ⬜ 诚实标注修不干净 | 1 | F13 —— 代码注释里自己写明防不住开放式自然语言 |

🔴 **「治好这一例，模式还在」正是清单类问题最容易踩的坑 ——
而这次修复过程本身又踩了一遍。** 这一档单独列出来，就是不让它被
「23/23」这种数字盖住。

#### 🔴 F3 —— 复查推翻了我的修复，已重修

第一版做了自动化、覆盖了全部 6 个 specialist、接进了出卡流程 ——
**但不按决策号绑定**，只问「这个 agent 名字有没有出现在最近 50 条记录里」。

实测复现（`tools/verify/probe.sh`）：

```
伪造决策号 BIGA-20260921-901，用合法 API 写 6 行 agent_runs
→ spawn 核验：6 个 agent 两份独立记录都齐   ✅ 通过
```

伪造的号**蹭上了别的决策的真实记录**。而 `bin/biga-card` 正常使用就会
反复运行 ⇒ 这个条件在真实机器上**几乎总是成立**，不是刁钻场景。

⚠️ 最讽刺的是：决策号本来就**明文写在 `payload_json` 里**，只是没被用来绑定。

**已重修**：按决策号过滤（去掉 LIMIT —— 按号取不该有条数上限）、
`child_session_key` 按段比而非子串、区分「读不到」（判不了）与
「库能读但本决策零记录」（**伪造**，不是判不了）。
三种情形各有探针，全部验过。

> 🔴 **而第一版的测试结构上不可能发现它** —— 它去 fake
> `_runtime_spawn_records` 本身，正好把「按决策号过滤」那段整个绕过去。
> 现在改成造一份**真的 sqlite** 让被测代码走完整查询路径。
>
> **假的东西造得太靠上，测的就是自己写的假货，不是产品代码。**

🔶 **残留**：绑定仍然是 `payload_json LIKE '%<号>%'` 的文本匹配，
只是从「匹配 agent 名」变成了「匹配决策号」——强度差很多，但性质没变。
运行时没有提供结构化的决策号字段，暂时只能这样。

#### 🔶 F7 —— 两半，此前只修了一半

| | 状态 |
|---|---|
| 文档失真（点名从未存在的 `reachability.py` 等 6 个文件） | ✅ 结构性修复，有机器判据 |
| **风险本体**（6 条阈值里 3 条从未被测试触发过） | 此前 **一条测试都没加** |

上一版台账把整条 F7 标成「✅ 已修」，**容易让人以为风险已经解除** ——
复查指出得对。现已补：`THRESHOLDS` **parametrize 到自己身上**
（新加一条自动纳入，写死六条的话第七条又会是下一个 F7），
正反两向 + 字段生产方的 AST 核对。

探针验过三种失效：阈值侧改名、**上游侧改名**（最隐蔽的那种）、判定整个失效。

🔶 **残留**：这是**构建期**可达性，不是 L-7 原文要的
「每日报告『配了但从未命中』的规则」。生产环境里长期不命中仍然无人察觉。
⇒ `reachability.py` 依然未建，已在 `architecture.md` §9 标注。

#### 🔶 F2 / F5 —— 实例修好，模式还在

复查的原话值得抄下来：

| 条目 | 堵上的 | 没变的根子 |
|---|---|---|
| F2 | 隔离检查两处「无事可查」 | 用**文本/存在性匹配**代替真正的身份验证 |
| F5 | 60 日高低点的量级围栏 | 围栏**压缩了错误的量级，没有消除错误** —— 5 倍以内的坏值照样过 |

⇒ 这两条**不单独再开工**——都需要换一种更权威的判据（真正的进程身份 /
第二个独立信号做交叉校验），不是补一处清单就能解决，先记下来。

#### ✅ F8 / F10 / F16 —— 从「实例级」补成「模式级」

外部评审追问「有什么根本解决手段」之后，把「找一个唯一权威源，其余全部
派生或做结构性比对」这条原则抽成了共用工具 `tests/_consistency.py`
（`assert_matches_source` / `assert_subset_of_source` / `built_agents()`），
三条各自接上：

| 条目 | 之前 | 现在 | 探针 |
|---|---|---|---|
| F8 | `STANCE_VOCAB` 靠人记得同步登记新 agent | 新增结构性测试：`set(STANCE_VOCAB) == built_agents()` | 造一个 `agents/discipline/` 目录（不改 STANCE_VOCAB），当场报红；删掉后恢复绿 |
| F10 | 两个白名单字段各写一条孤立测试 | `_MUST_COVER_BUILT` 登记"所有理应覆盖已建好 agent 的字段"，parametrize 到字典本身；加第三个字段只需加一行 | 复现原始 F10 bug（news 漏出）被抓到；另外**造了一个假想的第三个字段**，同样被抓到——证明泛化是真的，不是嘴上说说 |
| F16 | `_LIVE_FIELDS` 是纯手抄字典，新文件消费无日期源不会被察觉 | 从 `_source_result_classes()` 结构性派生"哪些是无日期源"，反查每个 calc 脚本是否引用了这些源却没登记进 `_LIVE_FIELDS`（字段级仍手写，**文件级**结构性核对） | 让 `technical_calc.py` 引用 `BreadthResult`（不登记），当场报红；撤销后恢复绿 |

⚠️ **过程中自己先踩了一脚**：最初想让 F10 的三个字段"两两完全相等"，
实测直接报错——`agents.entries` 天然比另外两个多一个 `"main"`
（entries 回答"谁注册了"，另外两个回答"main 能碰到谁"，语义不同）。
这不是 bug，是三个字段本来就不是同一个问题的三份答案；而"每个字段都
必须覆盖已建好的 agent"这条本身已经完整复现 F10 要防的事故，不需要
再加一条方向搞错的"彼此相等"。**先测一遍再定稿，这条原则对自己也成立。**

### 🔴 五次被探针抓到假守卫 —— 同一个形状

修这 23 条的过程里，**我自己造了三条通不过探针的守卫**：

| # | 守卫 | 它实际查的 | 探针怎么戳穿的 |
|---|---|---|---|
| 1 | F5 量级围栏 | 围栏函数本身 | 把 `technical_calc` 里那行调用删掉，**一条都没红** |
| 2 | F4 raw 层归属 | 手工 `probe.sh` 验过 | 改回共用 `as_of`，**一条都没红** |
| 3 | F3 spawn 证明 | 表名出现在文件里 | docstring 里也有那个名字，改查询照样绿 |
| 4 | F7 幽灵文件 | 反引号里的路径 | 而 F7 报的两个幽灵写在目录树里，是**裸文件名** |

> 🔴 **守卫查的地方，和它声称守的地方，不是同一处。**
> 四次里有三次「测了函数，没测产品线真的走了这个函数」。
>
> ⇒ 这就是 `dev-workflow` 第 3 条要求「守卫用探针验证会红」的全部理由。
> 四条如果按「写完看着对就交」，会一起变成新的 F 系列。

### 别丢掉「攻了但没攻破」那一节

评审 ② 第二节列了 12 条**经受住真实攻击**的防护（占号算法的并发正确性、
`from_dict()` 读取时二次校验、pre-push hook 两条历史高危路径、
分母为零的守卫、Card 层三条硬否决……）。

🔴 **这一节的价值不亚于发现清单** —— 它标出哪些地方**不用再查**。
重构碰到这些地方时先回去读它，别把已经验证过的防护顺手改薄了。

## 待裁定

- [ ] 🔴 **飞书的「出卡」请求不该直接进 Supervisor 的自由对话**

      实测（`BIGA-20260921-021`）：同一份契约，两条路径执行结果不同。

      | 路径 | 消息 | 结果 |
      |---|---|---|
      | `bin/biga-card` | 提示词里把五步**又复述一遍** | ✅ 5 spawn + `agents_wait` + 顺序正确 |
      | 飞书 | 「出一张决策卡片吧」 | 🔴 4 spawn + `sessions_yield` + **占号在 spawn 之后** |

      后者留下 4 个孤儿 spawn（约 $0.4 白花），缺 `news`，
      并且触发了 L-11「身份晚于证据」—— 而 schema v4 就是为了防它。

      🔴 **`AGENTS.md` 里这些全都写着。** 所以不是契约缺内容，
      是**契约写了不等于会被遵守**。

      ⇒ 修法是架构性的：把「出卡」当成**结构化命令**而不是自由对话，
      触发与 `bin/biga-card` 同一条路径（validate → enqueue → 快速 ACK
      → 跑完推送）。外部设计文档 §35 / §62 正是这么建议的，
      现在有实测证据。

      ⚠️ 在改之前，飞书里**不要用自然语言要卡** —— 会白花钱。
      预算闸门挡得住连点，挡不住这种「跑了但进不了卡」。


- [ ] 🔴 **延迟预算第三次重推：180s 只覆盖盘中，盘后会超**

      首次盘后端到端（`BIGA-20260921-020`，日线发布之后）：

      | | 盘中基准 | 盘后本次 |
      |---|---|---|
      | 墙钟 | 172.6s | **198s** 超预算 |
      | 成本 | $1.20 | **$1.37** |
      | `news` 单轮 | 78.3s / $0.30 | **100.0s / $0.42** |
      | 60 分钟窗口内快讯 | 67~79 条 | **184 条** |

      原因不是「今天慢一点」：窗口同样 60 分钟，条数翻倍，
      而 `news` 的 prompt 长度直接跟着条数走。

      ⚠️ 顺带：184 条超过 `MAX_ITEMS=120`，**64 条没被看过**
      （已如实上浮成 `news.window.truncated`）。盘后不只更慢更贵，**看到的还更少**。

      三个方向各有代价 —— 抬预算会让盘中异常不再报红；分时段两条预算是 L-3 的形状；
      压 `news` 要么漏消息要么更贵。

      🔴 **不在攒够盘后实测之前动它** —— 现在回答不了「什么时候它不该红」，
      而那正是前两次改预算时反复强调的必答问题。


- [ ] 🔴 **换掉共享的 anthropic 凭据**（裁定 11 的退出条件，Phase 3 前必须做）
      当前 BigA 与邻居共享同一份凭据 ——
      **它失效时两套一起停，且排查方向天生指向 BigA（错的那边）**。
      触发条件任一成立即换：① 能够申请到独立凭据
      ② 出现第一次因这条耦合导致的误判排查

- [x] **模型认证方式** —— 已裁定：**与邻居实例共享凭据**（裁定 11）
      过程要点（可迁移的那部分）：桥接模式下工具被本机策略整体拦掉 ⇒
      重登时安装器又自动选回同一种方式 ⇒ 去读「同环境但能用」的邻居配置，
      两行差异直接指出两个根因（运行时被钉死 / 没有 auth profile）。
      ⚠️ 具体用的是哪种凭据、为什么只能用它 —— **属于本机情况，不入库**，
      记在仓库外的 `~/.openclaw-biga/AUTH-NOTES.local.md`。
      耦合本身已在 `CLAUDE.md`「已知耦合」一节记明，含排查顺序与退出条件
- [x] **起 gateway 自动创建的 4 条 cron** —— 已裁定：**暂不动，记录在案**。
      清单：`heartbeat:main` / `memory-core:memory-dreaming-promotion` /
      `skill-collection-review:main` / `skill-collection-review:emotion`

      **已查实这四条是 openclaw 自带的托管任务，不是我们建的**（2026-09-19 复核）：
      - 四条**全部带 `declaration_key`** —— 这个字段正是「系统声明的托管任务」与
        「人手工建的」的分水岭。作为对照，邻居实例 91 条 cron 的该字段**全是 NULL**
      - 声明写死在包里：`server-reload-managed-*.mjs` 的
        `requestActiveCronJobCancellationByDeclarationKeyPrefix("skill-collection-review:")`、
        `resolveSkillCollectionReviewMonitorSpecs`。「reload managed」= 服务启动时对账重建
      - ⚠️ **跨实例对比不能用来证明「是不是默认」** —— BigA 是 2026.9.5，
        邻居是 2026.7.1-2，大版本差。那个对比只说明 `declaration_key` 机制是新加的

      🔴 **之前记的「heartbeat 已失败」是错的，这里更正**：
      `cron_run_receipts.error_text` 写得很清楚 —— `heartbeat skipped: no-route`。
      heartbeat 的活儿是往 IM 频道推状态，而 **Phase 1 明确不接任何 IM**，
      没有投递路由所以跳过。**这是设计内的正确行为，不是故障，没有东西要修。**

      真正的问题在观测面：**同一件事，两份记录说法不一致** ——
      `cron_run_receipts.status = skipped`（对），
      而 `task_runs.status = failed`（错）。
      先前就是读了 `task_runs` 才误报「heartbeat 失败 5 次」。
      > **一个把合法跳过标成失败的指标，会持续制造假警报，
      > 而假警报的终点是所有人都不再看它。** 与 R-3 同源：状态标错和状态缺失一样危险。
      查根因请直接读 `cron_run_receipts.error_text`，不要信 `task_runs.status`。

      与 `architecture.md` §8「只有一个调度域、不用内置 cron」+ Phase 1「不建任何 cron」
      仍有形式上的冲突，但既然是上游默认行为、且当前全部空转（heartbeat 无路由、
      另三条未到期），Phase 3 建自己的调度域时统一处理

- [x] **GitHub 远端** —— 已连通并推送
      仓库：`easyup168/easyup-biga` —— **已于 2026-09-20 转为 Public**
      认证：为 `easyup168` 单独生成密钥 `~/.ssh/id_ed25519_easyup168`，
      用 Host 别名 `github.com-easyup168` 区分 —— 本机另一个账号的默认密钥完全未受影响
      ⚠️ 克隆/remote 必须写别名主机名，写成 `github.com` 会用错密钥
      提交身份：项目账号（仓库级 `git config`，未动全局）
- [x] **`discipline` 放哪个 Phase** —— 已裁定：**推到 Phase 3**（裁定 13）
      它的职责是「人的行为风险：FOMO / 追高 / 连亏翻本 / 偏离计划 / 无止损 / 仓位失控」，
      输入是**人的交易行为史**。而 BigA：不下单、不接账户、按裁定 1 不挂邻居的库
      ⇒ **它现在没有输入源。** 硬建 = L-1（零消费方）+ L-2（无输入却给得出 PASS）的合体
      ⇒ Phase 2 只建 7 个，出口条件按 7 个写，**不假装有第 8 个**
      Phase 3 与「数据层加厚」一起做，届时先定输入源（候选：人每日手填当日计划与持仓，
      Evidence 的 `source` 就写「人工输入」，缺填 ⇒ `UNKNOWN` 进 `missing[]`，不 fail-open）

- [x] **Phase 2 在哪开发** —— 已裁定：**独立分支 `phase2`**（裁定 14）

- [ ] 要不要装 `gh` CLI
      ⚠️ 与「转 Public」相关：没有它就得走 GitHub 网页或 API 改可见性

### 公开可见性（已转 Public，2026-09-20）

- [x] **完整安全审查 —— 八项全过**（2026-09-19）
      凭据/私钥 · Gateway token · 家目录路径 · 个人邮箱 · 邻居可识别细节 ·
      内网公网 IP · 旧用户名 · 密码赋值
      审查脚本见本节末尾
- [x] **commit 作者邮箱改用 GitHub noreply**
      `easyup <easyup168@users.noreply.github.com>`，全历史 16 个 commit 已重写。
      noreply 与真实邮箱在 GitHub 上功能完全等价（关联、头像、贡献图都正常），
      唯一区别是公开后不会被爬虫抓到真实地址
      ⚠️ 审查当场还抓到一个反讽的问题：我在**记录「别泄露邮箱」这件事**的
      CHANGELOG/TODO 条目里，把邮箱本身写进去了。
      **文档里描述一个敏感值时，不要把那个值抄进去。**

- [x] 转 Public 当天（2026-09-20）：八项全绿后转公开（<!-- 冻结：当时确实是八项 -->）

- [x] 🔴 **触发条件已经变了 —— 现在是「每次 push 前」，不再是「转可见性前」**

      仓库已公开 ⇒ **push 即发布**，没有「先推上去、转公开之前再检查」这个窗口了。
      而且 `phase2` 分支一样公开可见 —— 这正是审查脚本必须扫 `--all` 而不是
      `main` 的原因（当时是按「以防万一」改的，现在它是**载重**的）。

      ⚠️ 已公开的内容**撤不回来**：删了分支仍可能留在 fork、缓存与各类镜像里。
      所以顺序只能是「先审查、后 push」，不能反过来。

      ✅ 已接成 `pre-push` hook（`tools/git-hooks/pre-push`）——
      靠人记得跑正是 §9 那张表反复否掉的那种防护。
      hook 扫**增量**（`remote_sha..local_sha`，新分支则 `<sha> --not --remotes`），
      手工体检扫 `--all`：已公开的历史撤不回来，为它每次报红只会训练出忽略

```bash
BIGA_REPO=~/.openclaw-biga/workspace

# 手工体检：全历史口径
$BIGA_REPO/tools/verify/audit_public.sh

# 装 pre-push hook（仓库级 config，克隆后每人执行一次）
git -C $BIGA_REPO config core.hooksPath tools/git-hooks
```

🔴 **这些检查只有一份实现**：`tools/verify/audit_public.sh`。
之前它是 `TODO.md` 里的一段字面量 —— 一旦 hook 里再抄一份，
就有了两套口径，而**改了一份忘了另一份时，剩下那份仍然报绿**（L-3）。

---

## Phase 2 · 补齐到 7 个 agent（当前阶段）

> 🔴 **设计 SSOT 是 [`docs/design/phase-2-specialists.md`](docs/design/phase-2-specialists.md)。**
> 本节只留勾选状态 —— 「为什么这么设计」写在那边，两处都写必然漂。

> 🔴 **在独立分支 `phase2` 上开发**（裁定 14）—— `main` 保持 Phase 1 已验收的状态。
>
> 理由：Phase 1 的 9 项验收是**在 main 的某个具体提交上实测出来的**，
> 那几个数字（74.8s / $0.2179 / 124 测试）只对那个状态成立。
> 把半成品的第 3、4 个 agent 混进 main，README 的徽章就开始描述一个
> **没人验收过的状态** —— 正是 L-6 文档漂移。
>
> 合回 main 的条件 = 本节出口条件全过。

目标：**把 Stage 1 扇出与 Stage 2 制衡层跑通**，并在过程中量出延迟随 agent 数增长的斜率。

⚠️ roadmap 原文是「补齐 8 个 agent」。直译成「一次建 6 个 agent」会正面撞上 L-1 ——
没有 skill 的 agent 只能编数字。Phase 1 已经把成本结构证明了：一个 specialist 的
工作量几乎全在它的 skill（教程 05 是全系列最长一章，23KB），agent 本身只是一份
`AGENTS.md` 加两处配置（`allowAgents` + `agentToAgent.allow`，**两端都要列**）。

### 排序：新机制优先，复制其次

| 步 | 做什么 | 新增的机制 | 为什么排在这个位置 |
|---|---|---|---|
| 2.1 | `market` skill + agent | Stage 1 **第一次真并行** | `maxConcurrent: 6` 配了但从未被验证过 —— 至今只有 1 个 specialist，并行是零次实测 |
| 2.2 | `risk` + Stage 2 | 冻结证据传入 + **BLOCK 否决权** | **唯一的结构性新机制。** BLOCK 正是 Phase 4 要检验区分力、Phase 5 下单要依赖的那个东西 —— 越早端到端落库，样本越多 |
| 2.3 | `sector` / `technical` | 无 | 到这一步才是真正的「复制」，推后不损失任何信息 |
| 2.4 ✅ | `news` | 时间戳 / 来源 / 新鲜度核验；**第一个 skill 算不出结论的 Agent** | **先做数据源 spike。** 这台机器上已出现过一次「工具通路被本机策略整体拦掉」（教程 07），等做到最后才发现拿不到数据源 = 整章白写 |

### 出口条件 → 见 `docs/design/phase-2-specialists.md` §4

🔴 **这里原来有第二份清单，它已经漂了。**

漂的证据（发现于 2.4 收尾）：

| 项 | TODO.md 这份 | 设计文档那份 |
|---|---|---|
| 否决的载体 | 还写着 `BLOCK` | `stance='否决'`（2.2 就改了） |
| 条件 2（冻结证据） | 未勾 | ✅ 结构保证 + AST 测试 |
| 教程章节 | 「第 11 章起」 | 11–16 逐个列出 |

两份清单的典型后果不是「其中一份错」，而是
**看的人各看各的那份，然后对进度产生两个印象**（L-3）。

⇒ 出口条件只在**阶段设计文档**里维护一份。这里只留指针。

### 🔴 「≥5 次真实 missing」攒不快 —— 第一天就要开始记

这条出口条件要的是**真实**缺数据，不是注入故障。而 Phase 2 明确**不建 cron**
（第一条 cron 在 Phase 3）。⇒ 需要手工日跑 + 一份累计台账：
日期 / 哪个源缺 / missing 几条 / Card 状态。
**不从第一天开始记，Phase 2 末尾会卡在这一条上干等。**

### ⚠️ 真实的延迟风险在 Stage 3，不在 Stage 1

只有 1 个 specialist 时，Stage 3（Supervisor 合成）就已经压着 30s 上沿（实测 30.0s）。
verdict 从 1 条变 6 条，它的输入量翻 6 倍 —— 而 Stage 1 是并行，加 agent 只涨「最慢那个」。
⇒ **每加一个 agent 就跑一次 `tools/verify/latency_report.py`**，攒 3–4 个点再推预算。
这是「60s 被证伪」那件事的直接教训：**先量，再定数。**

### 附带的两件小事

- [ ] **模型分层 A/B** —— 裁定 8「Phase 1 不做模型分层」的到期项。
      当前 config 里两个 entry 的 `model` 全是 `null`（跑 defaults 的 sonnet），
      而 §3.1 roster 写的是 `main=Opus` / `emotion=Haiku`。
      §3.1 自己标明那是**初始假设不是结论** —— 用同一批问题做 A/B 再定档
- [ ] `announceTimeoutMs: 120000` 在 §3.2 的配置骨架里，实际 config **没有** ——
      Stage 1 扇出到 5 个之前补上

### Phase 2 明确不做

`discipline`（裁定 13）/ 任何 cron / 飞书 / 任何下单路径 /
PostgreSQL / Redis / 回测 / 历史数据回补 / Web UI

---

## 确定性编排升级（与 Phase 2 收尾并行）

> 🔴 **设计 SSOT 是 [`docs/design/deterministic-orchestration.md`](docs/design/deterministic-orchestration.md)。**
> 本节只留勾选状态 —— 「为什么这么设计」写在那边，两处都写必然漂。
>
> 分发提示词在 [`docs/guide/orchestration-kickoff-prompt.md`](docs/guide/orchestration-kickoff-prompt.md)。
> **批 A 在分发时拆成 A-I / A-II 两个会话** —— 那是分发口径，不是设计变更。

与 Phase 2 剩余的两条出口条件**并行**，互不阻塞：那两条是日历问题
（等一个够极端的交易日、跨天累积），这次是架构问题。

- [x] 方案设计 + 评审断言复核（10/10 成立 + 3 条追加）
- [x] 🔴 **spike：Python 能否不经 LLM 轮次产生同等的 `subagent_runs` 证据** —— ✅ **能**
      实测（2026-09-22）：`biga attach --print-config` 铸 grant → MCP-over-HTTP
      `sessions_spawn` → `subagent_runs` 五项证据全齐、零 `main` LLM 轮次；
      `agents_wait` 3.3s 同步返回，带 usage。结论与三条硬约束见设计文档 §7
- [x] 批 A-I · 写边界重校验 + 严格 JSON（A3 / A4）—— ✅ 评审复核通过（`33fc55a`）
      评审四条：F-1/F-1b（审查判据两次方向反了）· F-2（档位改由调用参数推导）·
      F-3（`content_sha256` 锚存量文本）已修；F-4（`readback_check` 无自动调用方）带进 A-II
- [x] 批 A-II · 值对象与不变量（A1 / A2 / A5 / A6 / A7 / A8 + F-4）—— ✅ 评审复核通过（`884f0fb`）
      三轮评审：F-5（阻塞，`MissingItem.code` 冻结后仍可写）· F-6（中等，Card roster
      「缺席该不该硬拒」与设计表格不一致，裁决为折中方案）· F-8（第二轮复核，
      「非空即放行」太松，收紧为「missing 条数 ≥ 缺席数」）均已修完并独立复现过；
      837 条测试全绿；schema 落到 v6（`ux_verdict_amends_linear`）
- [x] 批 B · 运行身份 + 状态机（schema v7）—— ✅ 评审复核通过（`d401cc2`）
      12 进程真并发 CAS 独立复核过；`bin/biga-card` 的 `_move` 序列连通性
      目前靠人工读代码，没有机器验证——留了一条建议（复用 F-4 的沙盒机制
      跑一次真实脚本再查 run_journey），不阻塞，记在这里免得下次忘了
      `RunContext` 进契约层（第六个契约类型）；schema v7 建 `decision_runs` /
      `run_events` / `evidence_sets`，三张表**建表即带只追加触发器**（不重蹈 F1）；
      `transition()` 用 `UNIQUE(run_id, seq)` 做 CAS（状态事件溯源，不在只追加表上
      原地 UPDATE）；13 个状态照设计文档 §5，一个不多；`biga-card --status <run_id>`
      能说「死在哪一步」；`bin/biga-card` best-effort 记 run（不改行为）。
      四道探针全见过红（P1 并发仲裁 / P2 只追加触发器 / P3 拆 CAS→P1 红 /
      P4 无消费方状态被抓）。899 条测试全绿（837 → 899）。
- [x] 批 C-I · Runtime Adapter + spike 补验 —— ✅ 评审复核通过（`d8e7ea6`）
      建了 `skills/_runtime/`（`mcp.py` 传输层 + `adapter.py`
      `OpenClawRuntimeAdapter.start/wait/cancel/status` + 状态归一化）与
      `tools/verify/adapter_spike.py`（驱动真实 adapter 的 live 补验，可重跑复验）。
      三项 spike 补验全过：**P1 五路并行峰值 5/5 同时 RUNNING**（真并行）、
      **P2 grant 撑过 780s 且 .mcp.json 已清**、**P3 失败翻成结构化 SpawnStartError**；
      外加 cancel 对应实证（active 顺序≠spawn 顺序，靠 active[i]↔tasks[i] 同序映射）。
      P4 状态归一化探针见过红。938 条测试全绿（899 → 938）。**bin/biga-card 一字未改。**
      评审复核：P4 独立复现红（且多抓到一处开工会话没自报的断言一起报红）；
      cancel() 的 active[]/tasks[] 同序映射离线复核 N=5 与 drain 场景，算法本身
      无 N=2 专属 bug——残留风险收窄为「运行时在这些场景下是否真的保持同位
      对应」，线下无法验证，已作为条件性探针 P5 带进批 C-II（只在 Orchestrator
      真的调用 cancel() 时才适用，否则要求原样记进已知问题，见分发提示词）
- [x] 批 C-II · DecisionOrchestrator + 生产入口切换 ★ —— ✅ 评审复核通过（`d35d286`）
      这次升级第一次改动生产入口（`bin/biga-card`）。已落地（离线全绿，956 条）：
      · `DecisionOrchestrator`（`skills/decision-card/scripts/orchestrator.py`）程序
        驱动 8 步细粒度状态链（RECEIVED→…→COMPLETED），占号在 `open_run` 之前
        （`decision_id` 从头非空）；Stage 1 五路 fan-out 共用一个 groupId、Stage 2
        risk、Stage 3 spawn `synthesizer` 判官（**新建**的 `allowAgents=[]` 叶子 agent，
        靠 `outputSchema` 拿结构化 status/headline/synthesis，程序组装 Card，
        数据不经判官搬运）。
      · `bin/biga-card` 收缩成薄 CLI：五道守卫（顺序原样）→ 调 orchestrator →
        spawn_check + readback_check → 退出码。**不再 spawn main、不再抽提示词。**
      · legacy 粗边 `PREFLIGHTED → CARD_PERSISTED` **已删**（唯一使用者消失，L-7）。
      · `ORCHESTRATION.md` 收缩成各角色指令口径（PROMPT 块删除，`--decision-id`
        三处自相矛盾随之消失——代码里 orchestrator 永远显式传号、synthesize.py
        永远优先用证据自带号）；`AGENTS.md` 的「你就是执行者」整段改成「出卡是程序，
        你没有一步可做」（⚠️ 这动了 main 的 AGENTS.md，见评审交接说明的取舍）。
      · 修了一个收缩引入的**生产级 bug**：`echo "…约 $1.2…"` 在 `set -u` 下裸 `$1`
        未绑定会当场终止脚本（总闸开着时撞不到，一解除就咬人）——已转义。
      探针：P3 守卫顺序前后判据不变（既有守卫顺序/拒绝测试逐字节钉住）；
      P4 全仓无 legacy 粗边引用（新增 AST 扫描，非测试文件）。
      **评审回合一 · 两条阻塞项已修**（均改代码）：
        · 阻塞 1 — `orchestrator.py` 的 `main()` 加 ownership 守卫（`entry_guard.
          classify_caller`）：agent 血缘 → `exit 3`，人/cron → 放行。补上了 `main` 用
          shell `exec orchestrator.py` 绕过 bin/biga-card 的活口子 ——「够不到」现在在
          exec 面也成立（这就是 P2 的血缘面）。探针见红：关守卫→桩 run() 炸→红。
        · 阻塞 2 — `bin/biga-card` 加 `trap`（EXIT/TERM/INT/HUP）收养兜底：编排放后台
          +wait，wrapper 一死就 kill 编排子进程。孤儿化事故根因是外层 timeout 杀 shell
          后孙进程孤立（不是 `$1.2` bug），需代码兜底不是「以后小心」。探针见红：
          `_reap_orch` 改空操作→真杀 wrapper 后子进程存活→红。
      评审复核：两条红灯都独立复现过（不是抄交接里的输出）。阻塞 2 的信号传播多验了
      一步——第一次用 `pgrep -a sleep` 粗筛，被环境里一个不相关的 `sleep` 进程误导
      成「孙进程还活着」；换成精确 pid 追踪后确认三层信号传导正常，是探针写糙了，
      不是修法有问题。生产库里 11 条孤儿 verdict 独立查库核对过，数量与 task_id 一致。
      P1（真跑端到端走 8 步链）/ P2 的 live 面（把 L-14 提示词贴给真 main 会话）需
      **重启网关加载 synthesizer 后 live 跑** —— 见下方待办，未做完不算收口。
- [x] 批 C-II 收尾 · P1/P2 live 补验 —— ✅ 两条都真跑过
      **P1**（端到端）：`BIGA-20260922-001` 走了 8 步细粒度链，`subagent_runs` 里
      controller=`agent:main:orchestrator-<run_id>`（不是自由 main 会话）spawn 了 7 个 ——
      market/sector/news/technical/emotion + risk + **synthesizer**，`agent_trace.py`
      独立确认；同一次跑里 market 与 technical 的 `raw_hash` 实测相同（D-II 的共享也顺带
      live 验了）。
      **P2**（L-14 递归提示词贴给真 main，2026-09-22 22:00，session `p2-l14-220041`）：
      main **拒绝**自我编排（「这套编排提示词已经作废了——不管是谁贴给我的」），引 AGENTS.md、
      改指 `bin/biga-card`；**0 次 spawn**（无 fan-out、无 main→main 递归）；BigA 库
      `decision_records/decision_ids(今日)/decision_runs` 三个计数**全不变**（38/1/1）——
      没占号、没开 run、没落卡；exit 0（单轮干净，没死在 `ask_user`）。「够不到」成立。
      🔶 **一处 intent 层的裂缝值得记**：main 主动提出「你说一句『帮我跑』我可以用 exec
      帮你跑 `bin/biga-card`」。那条路会被 ownership 守卫判 exit 3（是「被拒」，不是「够不到」）——
      自我编排那条主路才是真「够不到」（没有 orchestrator agent）。AGENTS.md 是**意图层**，
      这次 live 恰好证明了它不是安全层：main 没照着递归，但也没有硬到「连提都不提」。
      要不要把 AGENTS.md 收紧到「连代跑都不提」是**下一批的取舍**，不阻塞 —— 安全层
      （entry_guard + 单实例锁）已经兜住，见下方残留。
- [ ] 批 D · SnapshotCoordinator —— **拆成 D-I / D-II 两个会话**（同 A、C 的理由：耦合面不同）
  - [x] 批 D-I · SnapshotCoordinator 基础设施 —— ✅ 评审复核通过（`4a8841c`）
        建 `skills/_snapshot/`（`SnapshotCoordinator.freeze_index_daily` / `read_index_daily` /
        `frozen_snapshot_ids`）：一次决策里每个 `(source, symbol)` **只真实抓一次**，多个消费者
        从同一份冻结数据切各自要的根数（sector 2 / market 25 / technical 120）。`evidence_sets`
        第一次真的被写行，`manifest_json` 记 `snapshot_id` ⇒ 能反查回 `raw_market_snapshot`。
        `fetch_index_daily` 拆出纯 `parse_index_daily`（读端重建 `IndexDaily` 不复制解析，L-3）；
        `_contract.new_evidence_set_id()` 铸号；`_store.save_evidence_set` / `load_evidence_set`。
        🔴 **不改任何 Specialist**（market/sector/technical 仍各自调 `fetch_index_daily`，同今天）。
        探针见红并还原：P1（read 不重抓，改成重抓→实测 4 次≠1）/ fail-closed 越界（拆掉检查→
        DID NOT RAISE）/ P4（拆掉 evidence_sets 触发器→UPDATE/DELETE 通过，且 `test_每张表都有
        只追加触发器` 一并抓到）/ P5（manifest 去掉 snapshot_id→反查红）—— 详见 CHANGELOG。
        评审复核：P1、P4 两条独立复现过红（P4 的动态守卫零改动跨批自动生效，值得记）。
        自报的弱点（freeze 跨 decision_id 调用不幂等）复核后**认为不该在这一批修**——
        真正该成立的是「一个 run 只冻一次」，不是「一个 decision 只冻一次」（decision
        允许对应多个 run，重试该拿新数据），coordinator 现在按 decision_id 收参，连表达
        per-run 约束的手段都没有，留给 D-II 按 run_id 认。顺带发现分发提示词把
        technical 的 `bars` 写错成 25（实测 120）——已在 `orchestration-kickoff-prompt.md`
        改正。
  - [x] 批 D-II · Specialist 改口读冻结快照 —— ✅ 评审复核通过（`e81f94b`）
        `orchestrator.py` Stage 1 前冻结一次（sh/sz@**120**，取消费者里最大的 technical），
        evidence_set_id 进转移 detail + `RunContext`（第一次真填这个字段）；只给日线三个
        agent 的任务文本加 `--evidence-set-id`。market/sector/technical 各加可选
        `--evidence-set-id`：给了读冻结（不联网、不重复落盘、`raw_hash` 取冻结集登记的
        `content_sha256`），没给自己抓（调试路径保留），给坏号 fail-closed（`SnapshotReadError`
        上抛，绝不静默退回抓取）。`risk_check.py` 的 `CROSS_CHECK_PAIRS` 判据从「比值」改成
        「比 `raw_hash`」（共享后比值恒真 L-7；比 raw_hash 才是「谁没读冻结」的探照灯）。
        新增只读访问器 `SnapshotCoordinator.frozen_content_sha256`（不改 `read_index_daily` 签名）。
        探针见红并还原：P5（冻结分支改成退回 fetch→`calls==1`≠0）/ P4（sector 对 2 根重算 hash→
        与冻结集 H 不符）/ P2（CROSS_CHECK 退回比值→值相等漏掉 hash 不一致，双红）/
        freeze 跳过→`test_一次决策只冻2行raw` 报 `0≠2`。详见 CHANGELOG。
        评审复核：P5、P2 两条独立复现过红（P5 的还原方式很说明问题——用异常类型判断
        会漏报，靠的是断言 `fetch` 调用次数）。自报的盲区（technical 回退且刚好抓到
        与冻结逐字节相同的数据、raw_hash 碰巧相同）复核后**认为不该在这一批堵**：
        能漏报的场景恰好是漏报了也无害的场景，真正验证「是否走了共享机制」需要把
        `evidence_set_id` 挂上 `Evidence`，是批 E 的契约改动。D 系列（D-I + D-II）到此
        完成——「所有 Specialist 看同一份数据」从批 B 的「无法验证」变成日线部分
        「机制存在 + 真的在用 + 有检查兜底」。
        🔴 **D-I 自报的「freeze 跨 decision_id 不幂等」在这一批自然消解**：orchestrator 每次
        `run()` 只调一次 freeze ⇒ **一个 run 只冻一次**（正是评审复核要的 per-run 语义）。
        一个 decision 被重试 ⇒ 两个 run ⇒ 两次 freeze ⇒ 各自一份新数据（retry 本就该拿新数据），
        各 run 用自己 `run_events.detail` 里记的 evidence_set_id。coordinator 无须按 run_id 收参。
- [x] 修复 · `agent_runs` 记账在批 C-II 之后没人写，`spawn_check.py` 永远判不了
      2026-09-22 对 C-II/D-II 做真实 live 验证时发现：真实出卡（`BIGA-20260922-001`，
      8 步细粒度链、7 个真实 spawn 含 synthesizer，`agent_trace.py` 独立确认；
      market/technical 的 raw_hash 真的相同，D-II 的共享快照生产里成立）之后，
      `bin/biga-card` 却 exit 4——`spawn_check.py` 报「判不了」。根因：写 `agent_runs`
      唯一的调用方是旧 standalone `synthesize.py`（main 提示词驱动时期），批 C-II 把
      合成挪进 `card_ops.persist()` 时没有把这一步搬过来。不是安全洞（没把失败判成
      成功），但每次真实出卡都印一条误导性的红字，且验证闸门名存实亡。
      ⚠️ 这条本该被 `test_store.py::test_我们自己的代码就在写它` 的 AST 扫描挡住，
      但那条扫描只查「repo 里有没有调用点」，`synthesize.py` 作为测试夹具仍满足它——
      判据是「文字存不存在」不是「生产入口会不会走到」，本仓库自己在别处反复强调
      的原则这次没做到（这条本身不算错：那条测试的目的是证明 agent_runs 可被业务
      代码自产而不可信，不是验证生产入口真的在写，两件事不该混）。
      修法：`card_ops.persist()` 在线路径补 `record_verdict_run()` 循环，回放路径
      显式跳过。两条新回归测试独立复现过两个方向的红（去掉记账→在线测试
      `[] == [...]`；回放也记账→回放测试 `12 == 6`）。1003→1005 条。
      本次修复在这个会话里直接做的，没有走单独的开工/评审两会话流程——
      范围小、根因链条已经查实，但没有另一个独立视角复核过，如实记在这里。
- [ ] 批 E · Facts / Assessment 拆分 —— **拆成 E-I / E-II 起**（同 A、C、D 的理由）
  - [~] 批 E-I · 契约基础设施 + 一个试点 Specialist —— **实现完成、离线全绿，
        六道探针（P1–P6）全见过红并已还原，待独立评审**（不自宣通过）。
        已建 `FactBundle`/`AgentAssessment`/`AgentOutcome` + `LegacyAdapter`
        （`skills/_contract/facts.py`），事实层铁律抽成一份共用
        （`verdict.check_fact_invariants` 等，防 L-3）；schema v8 加 `kind` 列
        新旧同住 `agent_verdicts`；`load_verdict` 变多态、消费方零改动；
        `Evidence` 加 `evidence_set_id`（还 D-II 的账，CROSS_CHECK 升级为优先
        比它）；试点迁 `emotion`（`build_fact_bundle`）。市场/板块/技术/新闻/
        风险五个未动，`amend_verdict.py` 未退役。教程 ch 27。
  - [x] 批 E-II · 迁 market/sector/technical/news 四个 —— **评审复核通过、已合并
        （`359b97c`）**。四个 skill `build_verdict→build_fact_bundle`、产 `FactBundle`；
        消费方零改动。教程 ch 29。
        评审复核：独立查库验证了 `market.trend.no_history` 的历史修订（4 条，
        2 条原件确认是 PASS/completed 且 `volume_ratio` 均完整，与报告结论一致）；
        亲手 sabotage 了「skill 源码不该出现 trend 概念」这条守卫；核对 sector/
        technical/news 的历史 amend 记录，确认下面「后续文档收敛」那条的风险
        评估准确（technical 19 条修订从未用过 `--add-missing`，sector/news 用的
        都是 skill 自己的码，模板占位码从未被字面照抄过）。
        **①的裁定落地查了真实数据库**（不照抄例子）：四个 skill 历史 `--add-missing`
        限制全部已由 skill 自检（`market.breadth.*`、`sector.board.pre_session`），
        ①不新增检测代码。唯一例外 `market.trend.no_history` 命中 escape hatch ——
        实测 5 次修订原件都带 `volume_ratio`（有整段序列），是**判断边界**（同 sector
        「板块持续性」）不是数据缺口 ⇒ 裁定**范围外、skill 不产它**，caveat 归 agent
        「需要注意」自由文本。② 维持不改（消费方继续吃 `load_verdict` 兼容垫）。
        🔴 第五交付物：修 `agents/market/AGENTS.md` 模板（设计 owner 授权的 AGENTS.md
        例外）—— 删掉 `--add-missing market.trend.no_history … --verdict WARNING` 组合命令
        （agent 事后覆盖 skill 完整度的旧洞，迁移后会被拒、不改就是 F9 式抖动），
        caveat 改走「需要注意」。
        ⏭ **后续文档收敛（未做，非阻塞）**：sector/technical/news 的 AGENTS.md 里还留着
        **可选**的 `--add-missing X.partial` 示例命令。它们对应 genuine 数据缺口、skill
        已自检，agent 正常只需 `--stance` 转述、不会撞上，破坏概率远低于 market；但示例
        本身迁移后同样会被 fact 行拒 ⇒ 建议随 E-III 或一次文档 pass 一并清掉。
  - [x] 批 E-III · 迁 `risk` + 退役 `amend_verdict.py` —— **评审复核通过、已合并
        （`619d35e`）**。VETO 穿透独立复现：评审自己写脚本，从数据库落库开始走完整
        链路（FactBundle→AgentAssessment(否决)→`load_verdict`→`DecisionCard`），
        不经过报告贴的探针。**Facts/Assessment 拆分至此收官：六个 Specialist 全部产
        `FactBundle`。** 教程 ch 30。
        · risk 机械迁移（三个 return 点，含两条 `status='failed'` 早退），读上游仍用
          `load_verdict`（多态），`AgentVerdict` 仍在 import（它是上游的消费方）。
        · 🔴 **VETO 穿透验证（核心）**：查明拦截链（`load_verdict` 多态 →
          `to_agent_verdict` → `DecisionCard` 读 `.stance`）是前几批建好的，**这一批一行
          拦截代码不改**——P2 因此断到 `DecisionCard` 真拦 BUY 的那一层（不是断
          `stance==否决`），红灯把 `to_agent_verdict` 的 stance 丢成 None → BUY 不被拦
          （`DID NOT RAISE`）证明链是真的。
        · 退役 `amend_verdict.py` 旧路径（删 74 行），`_assess_fact` 保留并扩成也拒
          `--verdict`。🔴 `save_verdict()` **不删**（七个测试 + `phase1_acceptance.py`
          还靠它造老形状测 `LegacyAdapter` 读路径宽）——amend 不再 import 它，函数留 `_store`。
        · risk AGENTS.md：**先查了库**（risk 历史 `--add-missing` 两个码都已被 skill 自报，
          不是 market 那种范围外边界）→ 删 `--verdict` 组合命令；`无法判定` 可挂 WARNING
          （`check_stance_vs_verdict` 只禁 UNKNOWN 上的方向判断）⇒ 不需要事后降级。
        ⏭ **随之解决的后续项**：E-II 交接里记的「sector/technical/news 的 AGENTS.md 仍留
          可选 `--add-missing X.partial` 示例」—— 本批未一并清（它们不涉 risk），仍留作
          一次文档 pass 的候选；退役后那些示例照抄同样会被 fact 行拒。
- [x] 批 C-III · Orchestrator 健壮性收尾（外部评审）—— **实现完成（分支 `c-iii`，
      基于 `2e5e8ea`）：离线全绿 1005→1016、四道探针 P1–P4 全见过红并已还原、
      `biga-card --check` 回放一致、`audit --worktree` 十一项全绿；评审复核通过，
      已合并。** 在独立 git worktree 上做，因为批 E-I 的未提交 WIP 同时在主工作区
      （当时红 25 条）——本项目第一次两批真正并行。
      不依赖 D/E，可与 E-I 并行开工
      来源：2026-09-22 外部架构复审，复核记在设计文档 §2 追加 5。四件独立
      小修复：① `CARD_PERSISTED` 转移挪到 `persist()` 成功之后（现在顺序
      反了，`persist()` 抛错会留一条假的"已落库"记录）；② Stage 1 部分
      启动失败时取消已启动的 handle（给 `cancel()` 第一个真实调用方,
      顺带补上「批 C-II 残留风险」里记的 N=5/drain 验证缺口）；③
      `verify_verdict_refs()` 补 `agent` 字段核对（`verdict_id` 是跨
      agent 全局自增，理论上能伪造一条指错行的 VerdictRef）；④
      Orchestrator 感知总 deadline，各阶段按剩余时间收窄预算（现在三段
      预算互不感知,加总可能超过 `deadline_sec`，只靠外层 bash timeout 硬顶）。
      评审里"run_id 未贯穿 Verdict/EvidenceSet/Card"那条断言是真的，但据此
      构造的"重试跨 run 串读"场景目前打不中（`decision_id` 现在恒为新铸，
      没有重试路径）——按追加 5.1，靠 run_id 做强制校验这部分仍是排期约束，
      留给"任何引入同 decision_id 重试"的批次开工前处理。
      🔴 2026-09-22 复盘：把 run_id **字段补上存下来**这部分不该也一起排期——
      现在就有消费方（batch B 起 run_id 就存在），且批 E 正在同一层做迁移，
      晚做要再开一次刀。已拆成独立小批，见下方「批 J」。
- [x] 批 J · 身份闭环（J-I + J-II 均已评审复核通过）—— **原「批 E-I 收尾 · run_id 贯穿全链」，2026-09-23 扩容并改名**
      （设计见 SSOT §6 批 J；改名理由：实测发现 `run_id` 在库里指**三个**
      互不相同的东西，另外两处必须与第一处一起改，名字再叫「E-I 收尾」
      会让开工会话按扫尾活的体量安排验证）
  - [x] 批 J-II · `agent_runs.run_id`→`ledger_id` ＋ `runtime_run_id` 落库
        —— 已合并（933aa6d）。机制经**独立复核**（2026-09-23，另开会话，未参与
        建造）确认成立：亲手在 shipped 代码里关掉结构化强绑定，复现了 P3 描述的
        确切症状（stdout 印出「1 个 agent 两份独立记录都齐」），还原后绿；亲手对
        迁移后的 `agent_runs` 真跑 UPDATE/DELETE，只追加触发器仍拦得住；干净
        `git clone` 全量跑绿。schema v9。教程第 31 章（原写作 30，与批 E-III
        撞号——两批并行各取下一个空号）。
        ✅ **「live 补验」一度复核复现不出来，已重新补验并确认成立**（详见
        CHANGELOG 两条相邻条目）：原断言称查 `subagent_runs` 能查到某个
        `run_id`，独立复核原样重跑返回 0 行；用户授权后改为**直接走
        `OpenClawRuntimeAdapter.attach()`/`start()`**（与 `orchestrator.py`
        起 Specialist 同一条代码路径，非绕开 Adapter 的裸 MCP 调用）重新 spawn
        一次（123 input/5 output tokens），`subagent_runs` 这次命中 1 行、
        `child_session_key` 逐字一致。⇒ **命名空间共享结论成立，J2-3 的结构化
        join 前提得到真实、可复现的实测支持**。最合理的解释：上一次的验证走的
        不是 Adapter 路径（`task_runs` 行形状与真实历史生产行不同），不是结论
        本身有问题。批 J-II 至此没有未决项。
        （另一条与此无关的既有缺口：强绑定 per-agent 启用、`NULL` 含义会漂移 ——
        正文见下方「两条 enforce 欠账」第 1 条，不在这里重复。）
  - [x] 批 J-I · `run_id` capture 贯穿全链 —— ✅ 评审复核通过（2026-09-23）
        schema v10（`agent_verdicts` / `evidence_sets` 各加 `run_id`）；六个 skill
        各加 `--run-id`；`VerdictRef`/`DecisionCard` 各加字段；`comparable()` 把
        **卡级** `run_id` 一并剥掉（回放不是原来那次执行，不剥就会把无损回放误判
        成不一致），但 `input_verdict_refs[].run_id` **不剥** —— 那是原件血缘，
        串了别的 run 仍抓得到。教程第 32 章。1101 → 1118 条。
        **2b 裁定落地**：`save_assessment` 的 `run_id` 从被 amends 的 fact 行
        **继承**，不加 CLI 参数 —— 评审独立验过结构证明（`save_assessment` 签名无
        此参数、`amend_verdict.py` argparse 也没有 ⇒ Agent 够不到）与行为证明
        （关掉继承当场报红）。
        ✅ 顺带验证了批 J-II 那条**可派生**守卫的回报：`run_id` 列的命名空间检查
        **零改动**自动覆盖了本批新加的两列（实测 checked 含 `agent_verdicts` /
        `evidence_sets`）—— 当初没写成清单式，这次就不用回去改它。
        🔁 **独立复核**（2026-09-23，另开会话，未参与建造）：亲手把 2b 的继承那行
        （`inherited_run_id = meta["run_id"]`）改成硬编码字符串，`TestInheritRunId`
        两条行为证明测试当场翻红（值对不上 / None 场景也对不上），还原后绿——
        commit 信息里那句"关掉继承当场报红"复现成立，不是转述。干净 `git clone`
        全量 1118 条绿，`audit_public.sh` 十一项绿。
        ✅ **live 补验已做（2026-09-23）**：走真正的 `OpenClawRuntimeAdapter`
        （非绕开的裸 MCP 调用）真实 spawn 了一次 `emotion`，任务文本一字不差用
        `_specialist_task()` 的真实文案（`--task-id BIGA-VERIFYNOOP-001 --run-id
        b7c1a2e9d3f4a5b6c7d8e9f0a1b2c3d4`，末尾加 `--no-store` 避免落库）。
        查该 session 的原始 transcript（`~/.openclaw-biga/agents/emotion/agent/
        openclaw-agent.sqlite` 的 `transcript_events`，不是会脱敏的 `sessions
        tail`），**逐字节看到 LLM 真实执行的命令**：
        `python3 skills/emotion-calc/scripts/emotion_calc.py --task-id
        BIGA-VERIFYNOOP-001 --run-id b7c1a2e9d3f4a5b6c7d8e9f0a1b2c3d4 --no-store`
        ——`--run-id` 确实被带上了，与 `--task-id` 同一种提示词机制同样可靠。
        （命令本身因故意造的不合法 `task_id` 格式报错退出——这是预期内、跟
        `--run-id` 无关的副作用，`FactBundle` 的契约校验按设计正常拦截，未落库。）
        成本 $0.097（123 input / 200 output tokens）。批 J-I 至此没有未决项。

### 🔶 孤儿告警分组显示（2026-09-23 回合二评审建议，未做）

`orphan_spawns()` 接上 `task_runs` 之后，信号里混进了刻意的验证/spike spawn
（09-22 那 15 条全是 `只回复两个字：收到` / `取消测试` 这类）。它们**按定义
确实是孤儿**（钱花了、没进卡），但会把生产那一条淹掉。

⇒ **分组显示，不要过滤**：生产会话是 `agent:main:orchestrator-<32 位裸 hex>`，
spike/测试会话自己带标签（`orchestrator-spike-p1-…` / `-cancel-…` / `-capture-…`）。
全库统计：裸 hex 2 个、带标签 12 个。区分是**按次的，不是按天的** ——
不需要「验证日」这种新口径。约 5 行。

🔴 为什么不过滤：过滤是个静音开关，而**验证日恰恰是最容易跑出真孤儿的那天**。
分组不丢任何东西，只是让生产那一条浮出来。
⚠️ 它依赖 spike 会话继续自觉带标签，是**约定不是强制** —— 所以更要分组而非过滤：
约定失效时分组最多归错组（仍看得见），过滤则是直接消失。

### 🔶 spawn 核验的两条余留（2026-09-23 评审，不阻塞）

1. **决策号靠文本匹配，而它会漏进调用方可控的字段。** 两张表的过滤都是
   `LIKE '%<决策号>%'`：`subagent_runs` 查 `payload_json`、`task_runs` 查 `task`。
   实测生产库里 **22 行 exec 的 task 文本带着决策号** —— 决策号确实会进到
   agent 自己可控的文本里。今天打不中（扫了全库，「一行里出现 2 个以上决策号」
   **0 行**，交叉污染不存在），且批 J-II 的强绑定对新行已接管这条路径。
   哪天有个提示词引用了上一张卡的号，那行就会记到那张卡头上。
2. **`tools/verify/budget_report.py --day 20260922` 会崩** —— `--day` 不是 flag
   （位置参数）。与 spawn 核验无关，评审顺手撞到的。

### 🔴 两条 enforce 欠账（都归「引入同 decision_id 重试」的那一批）

两条的根因都在**分发提示词**，不在实现 —— 两批都严格照提示词做了。记在这里
免得随 commit 沉下去。

1. **`runtime_run_id` 的强绑定是 per-agent、按数据有无启用的**（批 J-II）。
   `NULL` 今天的含义是「迁移前的老行」，等六个 agent 都走上新路径之后会悄悄
   变成「也可能是漏填的新行」，而没有任何东西会注意到这个转变。
   ⇒ 加 **per-decision 一致性检查**：同一个 decision 里只要有一行带
   `runtime_run_id`，其余行也必须带，否则那一行按「无法核实」处理（R-3，
   不是退回弱判据）。

2. 🔴 **`save_fact_bundle` 的 `run_id` 零校验**（批 J-I）。它是 Agent 从命令行
   抄下来的字符串，直接进 INSERT —— 那条 INSERT 里其余每个字段都经过
   `FactBundle` 构造 + 规范序列化 + 严格重建，唯独它是挂在旁边的裸参数，
   **绕过了批 A-I 立的「写边界重校验」原则（A3）**。
   对比 `save_assessment`：同一个字段在那边是结构上不可能错的（继承、Agent
   够不到）。同一批里两个写入点，一个结构安全、一个完全不设防。
   ⚠️ 真正难受的不是「Agent 忘了加」（那是 `None`，**看得见**），是**抄错**：
   存进一个合法但指错的 run_id，不报错、无人读，等到 enforce 那一批才发现
   攒了一批脏血缘。先例 `Evidence.raw_hash` 没有这个毛病，因为它是**算出来的**。
   ⇒ 修法便宜：`decision_runs` 有 `(run_id, decision_id)`，而 `fb.task_id` 就是
   decision_id ⇒ 「给了 `run_id` 就必须在 `decision_runs` 里存在、且属于这个
   `task_id`」，一条 SELECT。**这是写边界校验，不是 `latest_verdict_ids()` 过滤** ——
   后者才是追加 5.1 说的那个 enforce，两件事别混。

- [ ] 批 F · RiskPolicy 前移 —— 依赖已清（E 系列 + J-I 都已合并），设计探活
      已完成、分发提示词已就绪（2026-09-23）：`build_fact_bundle()` 本来就是
      纯函数，编排器可以直接调用；两种确定性结论（跨决策证据污染/无上游）
      不必再 spawn risk，其余情况仍 spawn 但只负责解读、不再自己跑
      `risk_check.py`。VETO 穿透必须回归验证，不能因为改了调用路径就松了
- [ ] 批 I · RawArtifact —— raw 层存的不是 raw（`json.loads`→`json.dumps
      (sort_keys=True)`），而建表注释断言「不做任何归一化」。等批 J 的依赖
      已解除（run_id 已消歧义），建议等批 F 落地——它给 raw 加溯源字段，
      那些字段指向哪个调用点，等 risk 搬移落定后再定更清楚
- [ ] 批 G · Outbox + 飞书 trigger + 配置进仓库 —— 设计探活已完成（2026-09-23），
      按外部材料自己的分阶段建议拆成两批：
  - [ ] 批 G-I · Outbox（Outbound Only）—— 分发提示词已就绪。只做推送通知
        （Card 完成/UNKNOWN/Risk BLOCK/失败四类），不接受任何飞书输入，
        没有新攻击面，不涉及 R-2
  - [ ] 批 G-II · Inbound Trigger —— 等 G-I 落地再写分发提示词（需要它的
        `notification_outbox` 表结构与 `NOTIFICATION_PENDING` 状态转移
        先定型）。核心交付物：飞书"出卡"从自由对话变成结构化 trigger，
        main 的 LLM 全程不参与路由决策——这是真正关掉 2026-09-21 那次
        $0.4 白花事故的地方，风险与 E-III/J-II 同量级
- [ ] 批 K · Pipeline Registry + Agent Registry —— 2026-09-21 `news` 没被
      spawn 那次事故的结构性解法（现在只有对账测试，不是单一源）；
      顺带拿到 Pipeline 版本化（历史卡现在说不出「当时用了哪几个 agent」）。
      设计探活已完成（2026-09-23），但会碰 `orchestrator.py` 里 F 也要动的
      常量，建议等 F 落地合并之后再写分发提示词
- [ ] 批 L · `cn.trading_calendar` —— P0 六个 dataset 里**今天**唯一有
      被证明消费方的（`skills/_sources/tradetime.py` 自己写着「不认节假日」）。
      定位是给总体设计 §45「第一版完整市场数据」那一批**打样**：用一个非行情、
      体量小的数据集把 Provider→Raw→Normalize→Quality→Snapshot 走第二遍
      （第一遍是已完成的 index_daily）。
      🔴 其余五个的 schema 形状由 §46 选股闭环决定（FeatureSet 要什么、
      Screening 按什么过滤），那一批没开工之前不要按猜测定 —— raw 只追加
- [ ] 批 H · 包结构重组（§29，排最后 —— 它会让期间所有 diff 变脏）

🔴 **批 I / K / L 来自 2026-09-23 复核的数据架构材料**（`docs/external/` 的
`multi-agent-data-architecture` + `data-platform-development-plan` 两份），
并经同日到手的**总体设计**（`baga-full-system-architecture`，位阶最高）复核过。
采纳/不采纳的分界、Parquet 那条改判、以及批 J 为什么是下一步（总体设计 §43
短期重点前三条全落在它里面），写在设计 SSOT §0 与 §6，**不在这里重复**。

出口条件 12 条 → 见设计文档 §11。

### 🔶 批 C-II 残留风险（评审时一并看）

**P5 · `cancel()` 的 active[]/tasks[] 同序映射仍只在 N=2、无 drain 下实测过。**
C-II 那一批的 `DecisionOrchestrator` **没有调用 `adapter.cancel()`**：部分失败/超时
处理是「缺席的 agent 记 `missing`、照常出卡」，还在跑的兄弟 spawn 由各自的
`runTimeoutSeconds`（C-I 定死的硬约束，运行时到点收）兜住，不主动取消。所以 C-I 评审
留下的那条 —— 「N≥3、且至少一个 spawn 已离场（active→recent）时，取消是否仍命中对的
那一个」—— 在 C-II 时**没有消费方**，原样带了下去。

⇒ **批 C-III 的 C3-2 是那个第一个真实调用方**（Stage 1 部分启动失败 → 对已启动的
兄弟逐个 `cancel()`）。这条残留风险因此**拆成两半**：① 「`cancel()` 缺真实调用方」
——**已解决**；② 「真实运行时 N=5/drain 下同序映射是否仍命中对的那一个」——**仍未
补**：C3-2 的离线探针用桩 adapter，只证明 Orchestrator 会对已启动的 handle 调
`cancel()`、且身份对，没跑活运行时。这条 live 补验随第一次真实 Stage 1 部分失败
（或专门造一次）补上，见 CHANGELOG「已知问题（批 C-III）」。

**stall_watchdog 现在零消费方。** 老 `bin/biga-card` 的 bash 轮询循环（看门狗挂在那里防
非交互 `ask_user` 死锁）被收缩掉了。新路径里每个 spawn 带 `runTimeoutSeconds`，一个卡在
`ask_user` 的会话**理应**被运行时按 timeout 收掉、`agents_wait` 记成缺席 —— 但
「`runTimeoutSeconds` 是否在 `blocked_tool_call` 状态下真的开火」读代码验不了。
⇒ 模块与其单元测试**原样保留**（没验证「运行时确实兜住」之前不删这道安全网，R-3）。
下一批二选一：① 验证运行时会收掉阻塞会话 → 正式退役看门狗；② 把 `find_blocked` 接到
orchestrator 的超时诊断路径（超时时判一下是不是 `ask_user` 死锁、记进 detail/missing），
给它一个真实消费方。这条同 P5 一样，是「这一批用不到但不让它悄悄消失」。

### ✅ 批 D-I 施工空档 + 批 D-II 输入 —— 均由 D-II 消费（保留作台账）

D-I 留的三条都在 D-II 落实了，记在这里免得以后翻出来以为还悬着：
- **施工空档（freeze 没有调用方）** ✅ 消费：`orchestrator.py` 每次 `run()` 冻结一次。
- **输入 1（bars 取 120 不是 25）** ✅ 采纳：`SNAPSHOT_BARS = 120`（分发提示词那个 25 是错的）。
- **输入 2（raw_hash 指向冻结集那份，不对切片重算）** ✅ 采纳：读冻结走
  `frozen_content_sha256`；`CROSS_CHECK_PAIRS` 也随之从「比值」改成「比 raw_hash」（不再恒真）。

### 🔶 批 D-II 残留（评审时一并看）

**`decision_runs.evidence_set_id` 这一列填不进去，是设计使然不是漏。** schema v7 注释写
「批 D 填」，但 `decision_runs` 只追加、它那一行在 `open_run`（RECEIVED）时就写了，而 freeze
在 SNAPSHOT_FROZEN（晚于 open_run）才发生 ⇒ 事后 UPDATE 违反只追加，提前 freeze 到预检前
更糟。⇒ evidence_set_id 的持久记录落在 `run_events`（SNAPSHOT_FROZEN 那次转移的 detail）
+ `evidence_sets` 表；`RunContext` 内存里也更新。这一列保持 NULL。谁将来要「按 run 查它
用了哪个冻结集」，读 run_events.detail，别指望 decision_runs 那一列。

**`SNAPSHOT_INDEX_AGENTS` 是一份会漂的清单。** `orchestrator.py` 里写死
`{market, sector, technical}`——决定给谁的任务文本加 `--evidence-set-id`。它必须与「哪几个
skill 真接了这个参数」一致，没有权威源可派生。加了新日线消费者忘了改它 ⇒ 各自抓、不共享。
靠探针 P1 兜底（三条日线证据反查同一冻结集，漏了就 raw_hash 对不上报红），但那是运行期
才发现。将来 breadth/pool 也迁冻结（批 D 后两段）时，一并想清楚这份清单要不要变成
可派生的（比如各 skill 自报「我读哪些冻结源」）。

### ⚠️ `.biga-card-stop` 的解除条件看起来已经满足，但没解除

闸门文件写的条件是「在触发源确认停止、且闸门真的接进路径之前，不要 rm」。
`3d9ce90` 已经把预算闸门接进 `bin/biga-card`（在第一个花钱的动作之前）。
⇒ 条件形式上满足，**但解除是人的决定，不自动做**。

### 🔶 `bin/biga-card` 的 `_sql()` 在库不存在时会打一屏无害的 traceback

批 A-II 测试 F-4 时在沙盒里发现：`BEFORE=$(_sql "SELECT MAX(decision_id)...")`
那一行用 `readonly=True` 打开一个还不存在的 `data/biga.db`，
`connect()` 按设计会抛 `StoreNotInitialised`——但这里没接住，
异常信息进了 stderr，`BEFORE` 拿到空字符串（恰好是语义正确的兜底值），
**不影响功能**。真实机器上 `data/biga.db` 建库之后就不会再触发。
不在这一批修（与 A1/A2/A5/A6/A7/A8/F-4 都无关）——留着当下一次顺手活。

---

## 路线图（Phase 3+ 不要提前做）

| Phase | 内容 | 出口条件 |
|---|---|---|
| 2 | 补齐到 **7** 个 agent（不含 `discipline`）+ Stage1/2 并行 + 完整 Card | `missing[]` 在真实缺数据时非空 ≥5 次；延迟预算按实测重推（**不照抄 105s**） |
| 3 | 数据层加厚 + `discipline`（含它的输入源）+ 独立飞书应用 + 第一条 cron | 每条 cron 都有**被证明的**消费方 |
| 4 | 测量层：安慰剂基准 + Card 状态的区分力检验 | `BLOCK/WARNING` 与 T+5 结果按日聚类 t 有区分力 |
| 5 | （很久以后）自动下单 | **硬前提：Phase 4 通过**。在 Card 的 BLOCK 被证明有区分力之前接下单 = 新增一道空转门 |
