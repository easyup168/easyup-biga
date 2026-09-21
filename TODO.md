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
- [ ] 批 A-I · 写边界重校验 + 严格 JSON（A3 / A4）
- [ ] 批 A-II · 值对象与不变量（A1 / A2 / A5 / A6 / A7 / A8）
- [ ] 批 B · 运行身份 + 状态机（schema v6）
- [ ] 批 C · Runtime Adapter + DecisionOrchestrator ★（spike 已通过，可开工）
      ⚠️ 开工第一件事：补验 spike 未覆盖的三项（五个并行 fan-out / grant 长跑稳定性 /
      spawn 失败的结构化错误面）—— 见设计文档 §7 末尾
- [ ] 批 D · SnapshotCoordinator
- [ ] 批 E · Facts / Assessment 拆分
- [ ] 批 F · RiskPolicy 前移
- [ ] 批 G · Outbox + 飞书 trigger + 配置进仓库
- [ ] 批 H · 包结构重组（§29，排最后 —— 它会让期间所有 diff 变脏）

出口条件 12 条 → 见设计文档 §11。

### ⚠️ `.biga-card-stop` 的解除条件看起来已经满足，但没解除

闸门文件写的条件是「在触发源确认停止、且闸门真的接进路径之前，不要 rm」。
`3d9ce90` 已经把预算闸门接进 `bin/biga-card`（在第一个花钱的动作之前）。
⇒ 条件形式上满足，**但解除是人的决定，不自动做**。

---

## 路线图（Phase 3+ 不要提前做）

| Phase | 内容 | 出口条件 |
|---|---|---|
| 2 | 补齐到 **7** 个 agent（不含 `discipline`）+ Stage1/2 并行 + 完整 Card | `missing[]` 在真实缺数据时非空 ≥5 次；延迟预算按实测重推（**不照抄 105s**） |
| 3 | 数据层加厚 + `discipline`（含它的输入源）+ 独立飞书应用 + 第一条 cron | 每条 cron 都有**被证明的**消费方 |
| 4 | 测量层：安慰剂基准 + Card 状态的区分力检验 | `BLOCK/WARNING` 与 T+5 结果按日聚类 t 有区分力 |
| 5 | （很久以后）自动下单 | **硬前提：Phase 4 通过**。在 Card 的 BLOCK 被证明有区分力之前接下单 = 新增一道空转门 |
