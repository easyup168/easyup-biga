# 确定性编排升级

> 📄 **阶段** · 进行中，完成后冻结
> **覆盖**：为什么把工作流从 LLM 里拿出来、七批迁移（A–G）的范围/顺序/判据/出口条件
> **不覆盖**：各 Specialist 的判断口径（见 `phase-2-specialists.md`）；
> 已建成架构的现状描述（见 `architecture.md` —— 本文只写**要变成什么样**）

---

## 0. 这份文档是什么

一次**跨阶段的架构迁移**。它不是路线图里的某个 Phase，也不替代任何 Phase ——
Phase 2 剩下的两条出口条件（等一个够极端的交易日、跨天累积缺失项）是**日历问题**，
与本次升级并行，互不阻塞。

### 为什么它没有编号

`docs/README.md` 的命名规约给了两种形状：常青文档按主题、阶段文档按 `phase-<N>-<主题>`。
这次的工作是**第三种**：一次横跨 Phase 2 与 Phase 3 的迁移。

三个选项都试过一遍：

| 方案 | 为什么不用 |
|---|---|
| `phase-2.5-...` | `tests/test_docs_convention.py::test_阶段文档必须带主题` 的判据是 `^phase-\d+-[a-z]`，小数点直接不匹配。放宽它的代价更大：`test_一个阶段只有一份设计文档` 按 `phase-(\d+)-` 计数，`phase-2.5-` **压根不会被计入** —— 于是「一个阶段一份文档」这条守卫对小数阶段**静默失效**。那正是 L-13 |
| 占用 `phase-3-`，旧的 3/4/5 顺延 | 「Phase 3」「Phase 4」「Phase 5」在 `CLAUDE.md` 裁定、`architecture.md` §9 / §12、`TODO.md` 路线图里被**按数字**引用了几十处。重编号 = 一次跨文档的大范围手改 ⇒ L-6 文档漂移的标准起点 |
| 按主题命名，类别仍是「阶段」 | ✅ 采用 |

⇒ 规约里补第三种形状：**跨阶段迁移按主题命名，生命周期仍是「阶段」（完成后冻结）。**

### 与三份外部材料的关系

| 材料 | 状态 |
|---|---|
| `docs/external/easyup-biga-architecture-upgrade.zip` | 本文的输入。**只读，不改** |
| `docs/external/easyup-biga-openclaw-feishu-best-practices.zip` | 尚未处理。与本文 §20/§27 高度重叠 ⇒ **并入批 G**，不单开一份台账 |
| 递归事故 / `ask_user` hardening 两份 | ✅ 已落地（commit `fb1856c` / `3d9ce90`），本文的起点包含它们 |

---

## 1. 为什么现在做

### 一条因果链

```
Supervisor 既是业务判断者，又是工作流控制器
         ↓
工作流只能用提示词表达（ORCHESTRATION.md，18.6 KB）
         ↓
提示词不是契约 —— 它在每一次运行里被重新解释
         ↓
每一次解释偏差 = 一次事故
```

翻一遍 `architecture.md` §9 的失败模式清单，**本项目自己踩出来的五条里有四条同源**：

| # | 事故 | 它其实是什么 |
|---|---|---|
| L-10 | 结构化数据经 LLM 转述，`retrieved_at` 全丢 | LLM 在**搬运**本该由程序传递的数据 |
| L-11 | 身份晚于证据，两次运行的证据合成一张卡 | LLM 决定了**什么时候**分配身份 |
| L-14 | 出卡递归，187 个会话 / \$8.99 | LLM 决定了**下一步跑什么命令** |
| — | 飞书路径 4 spawn 缺 news、`sessions_yield`、占号在 spawn 之后 | LLM 决定了**调用谁、按什么顺序、怎么等** |

加上没进清单的那些：忘传 `--decision-id` ⇒ 卡 014 装着 013 的证据；
`agents_wait` 不带 `collect` ⇒ 每次白花 22 秒；一轮 47 秒零工具调用在重打 JSON。

> 🔴 **这些全部不是提示词写得不好。**
> 每一条的修复都是「把那句话写得更硬一点」，而下一条总会从别的缝里长出来。
> 提示词能表达**意图**，表达不了**不变量**。

### 我们已经做的每一道修复，都是在给 LLM 驱动的状态机加护栏

```
契约改文字  →  单实例锁  →  ownership 守卫  →  硬超时  →  ask_user 看门狗
```

五道，全部有效，全部**只能限制损失**，没有一道能让流程本身变成确定的。
外部评审的判断是对的：

> AGENTS.md = Intent / Behavior Guidance
> Code Guard = Safety Invariant

护栏已经建齐了。下一步不是第六道护栏，是**把车道拿回来**。

### 新的核心原则

```text
Program decides workflow.
Agent decides judgment.
```

| 程序决定 | Agent 决定 |
|---|---|
| 什么时候开始 · 下一阶段是什么 · 谁被调用 | 当前市场是什么状态 |
| 超时多久 · 什么算完成 · 什么情况算失败 | 证据意味着什么 |
| 哪些数据属于同一个 Decision | stance 是什么 · rationale 是什么 |

---

## 2. 评审断言的复核 —— 10/10 成立，另有 2 条追加

🔴 **不照单接收。** 每一条可测的断言都在本仓库跑过探针（复核于 745 测试全绿的基线上）。

| 评审 | 断言 | 复核 |
|---|---|---|
| §4 | 构造后改字段可绕过 `__post_init__` | ✅ `v.missing.append(...)` 后 `PASS + missing` 并存；`v.verdict` 可被改成词表外字面量 |
| §5 | `MissingItem(str)` 的身份是文本不是代码 | ✅ 不同 code、同 detail 的两条**相等**，进 `set` 塌成一条 |
| §7 | `confidence` 实际是字段覆盖率 | ✅ 六个 skill 全是 `len(result)/_EXPECTED_FIELDS` |
| §8 | Card 没有 roster 约束 | ✅ 同一个 agent 放两次能构造；只有 1 个 agent 也能构造 |
| §22 | 写边界不重新校验 | ✅ 见下（比评审描述的更严重） |
| §23 | 持久化不是严格 JSON | ✅ `NaN` / `Infinity` 成功写进库 |
| §24 | amendment 血缘无约束 | ✅ 修订可指向**另一个 agent、另一次决策**的原件；一条原件可有多条分叉修订 |
| §25 | `load_card` 同收两个 id 并静默忽略一个 | ✅ `load_card("乱写的号", record_id=真)` 正常返回 |
| §26 | 无显式 `busy_timeout` | ✅ 走默认 5000ms（未显式设置） |
| §15 | 各 Specialist 各自抓同一份数据 | ✅ `fetch_index_daily` 被 market / sector / technical **各抓一次** |

### 追加 1 · §22 的后果是「毒行」，不是「脏数据」

```
save_verdict(非法对象)   →  ✅ 成功落库
load_verdict(同一行)     →  ❌ ValueError（from_dict 复校验，铁律 1）
```

写入不校验、读取校验，而 `agent_verdicts` 有**只追加触发器，删不掉也改不掉**。
⇒ 一次误写让那次决策**永久无法回放**。`decision_records` 同理。

> 这是目前最硬的一条契约缺陷：它把一个可修复的错误变成了不可修复的错误。

### 追加 2 · §5 已经在回放路径上咬人

`card_ops.synthesize()` 按 `(code, text)` 去重，而 `replay.py` 反推 `extra_missing`
时用 `m not in from_verdicts` —— **只按文本**。实测：

```
合成   Card.missing = 2 条（market.turnover.unavailable / supervisor.agent_no_response，文本相同）
回放   反推 extra_missing = 0 条  ⇒  重建出的 Card 只剩 1 条缺失项
```

**回放悄悄让卡变好看** —— 与教程第 8 章 `--check` 当年抓到的那个 bug 同形状，
这次的载体是 `MissingItem` 的 str 身份。
（`replay --check` 仍会报出差异，检测器在 —— 但根因在契约层。）

### 追加 3 · 「唯一的那份」编排文档内部自相矛盾

`ORCHESTRATION.md` 三处说法不一致：

| 位置 | 说法 |
|---|---|
| 顶部 PROMPT 块 | Stage 3「**必须带** `--decision-id`」 |
| Stage 3 正文 | 「`--decision-id` 用 Stage 0 占下的那个号」 |
| Stage 3 参数速查表 | 「`--decision-id`｜**不要填** —— 脚本自动分配，填了反而可能撞号」 |

第三条正是产出 `BIGA-20260921-014`（卡 014、证据 013）的那条指令。
**L-3 长在了「合并成唯一一份」之后的那一份里** —— 合并消灭了跨文件的第二套口径，
没有消灭同一文件内的第二套口径。

### 追加 4 · 批 A-I 实测：收窄类修法的判据不能建在被校验对象自身状态上

批 A-I 做完 A3/A4（`33fc55a`）之后，评审复核挑出两处独立的收窄式修法，
方向都反了——细节见 `CHANGELOG.md` 与教程第 20 章，这里只留对 A-II
有直接约束力的结论。

**其一（约束 A2）**：写边界重校验最初判断「这是不是历史/回放数据」时，
直接信任 `card.from_store`——`DecisionCard` 一个尚未 `frozen` 的属性。
合法构造对象后 `card.from_store = True` 不会报错，能把刚加的重校验一起
绕开。改法是从 `save_card` 的调用参数 `replay_of` 推导（`replay_of is not
None`），不从对象状态推导——`card_ops.persist()` 是唯一调用点，在线路径
永远不传、`replay.py --store` 永远传原始 `record_id`，调用方的这个决定
不受 `card` 对象本身状态影响。

⇒ A2 把 `DecisionCard` 加 `frozen=True` 之后，`card.from_store = True`
会直接抛 `FrozenInstanceError`——这条路径被 A2 顺带堵死，但**探针清单里
要显式加这一条**（"篡改 `from_store` 抛错"），不能只测 `missing`/`verdicts`
这类列表字段：`from_store` 是这次实测里唯一真被利用过的具体攻击面，
其余字段目前还没有对应的实测攻击路径。

**其二（约束 A6）**：`agent_verdicts.content_sha256` 现在锚定的是
「这次写入时的那段文本」，不是「这个对象的规范形式」——A3 把 `verdict_json`
换成了 `_canonical_dumps`（含 `separators`），同一份 verdict 的存储字节
因此从 206 变成 185，也就是说这个序列化格式**不是冻结的**。如果 A6 的
核对逻辑是「把 `AgentVerdict.from_dict()` 读回的对象重新序列化再比对」，
A-I 之前落库的 239 条原件会集体核对不上，而且是静默的（两串 sha256 都
「看起来正常」）。

⇒ A6 的 `VerdictRef.content_sha256` 核对必须走
`hashlib.sha256(存量 verdict_json 文本)`，不能走「对象重新序列化」。
`tests/test_write_boundary.py::TestVerdictContentShaIsHashOfStoredText`
已经把这条钉住，A6 直接复用这条测试的判据，不要另写一套。

> 两处的共同教训：收窄一条已有判据（无论是安全审查脚本还是契约层校验）时，
> 先确认判据依赖的信号来自**调用方参数/存量数据**还是来自**被校验对象
> 自身**——后者在对象冻结之前永远可以被绕过，冻结之后也只是「更难绕过」，
> 不是「不需要想清楚判据该建在哪」。

### 评审的 drop-in 里有一处会出事

§23 建议统一用 `separators=(",", ":")`。但 `_store/db.py::payload_sha256` 目前**不带**它，
而 `Evidence.raw_hash` 与 raw 层的对应关系建立在这个哈希上。
直接照抄会**改变所有历史哈希**，且失效是静默的（两串 sha 都「看起来正常」）。

⇒ 严格 JSON 分两个函数：**新的**规范序列化（含 separators）用于新增载荷；
`payload_sha256` **只加 `allow_nan=False`**，分隔符维持现状。由测试钉住历史向量。

---

## 3. 目标架构

```text
Feishu / CLI / Cron
        │
        ▼  trigger_id
┌────────────────────────────────┐
│ Trigger Gateway                │  verify / auth / dedup
├────────────────────────────────┤
│ 熔断 → ownership → 单实例锁     │  ← 现有五道守卫，顺序不变
│ → 预算闸门 → 第一次付费调用     │    （L-14 的防护顺序由测试钉住）
├────────────────────────────────┤
│ DecisionOrchestrator           │  ★ 确定性状态机（Python）
│   run_id · 显式状态 · CAS 转移  │
├────────────────────────────────┤
│ SnapshotCoordinator            │  一次抓取 → 冻结 EvidenceSet
├────────────────────────────────┤
│ OpenClawRuntimeAdapter         │  Specialist 生命周期的唯一入口
│   start / wait / cancel / status│
├────────────────────────────────┤
│ RiskPolicy（Python，硬）        │  身份/覆盖/新鲜度/阈值 —— 在付费调用之前
│ RiskAssessment（LLM，软）       │  硬规则全过之后才解释风险
├────────────────────────────────┤
│ CardBuilder → Store + Outbox   │  同事务
└────────────────────────────────┘
        │
        ▼  飞书（由 outbox worker 投递，失败不重跑决策）
```

`main` 在这张图里**没有位置** —— 它退回它本来的岗位：与人对话的 Operator。

---

## 4. 身份模型

目前只有 `decision_id` 一个身份。它同时被迫承担五件事。拆开：

| 身份 | 是什么 | 现在由谁顶替 | 为什么必须独立 |
|---|---|---|---|
| `trigger_id` | 一次外部请求（飞书消息 / CLI / cron） | 无 | **飞书事件会重投。** 没有它就没有幂等键，重投 = 重跑一次决策 |
| `decision_id` | 业务上的一次决策 | ✅ 已有 | — |
| `run_id` | 这次决策的**某一次执行尝试** | 无（回放勉强用 `replay_of`） | 硬超时收掉一次运行后重试，两次尝试挤在同一个 `decision_id` 上，事后分不开 |
| `runtime_run_id` | 运行时返回的真实 run / session ref | 只在 `subagent_runs` 里，未被引用 | spawn 证明目前靠 `payload_json LIKE '%<号>%'` 文本匹配（F3 残留）。有了它就是结构化绑定 |
| `evidence_set_id` | 被冻结的数据切片 | 无 | 「所有 Specialist 看的是同一份数据」这句话现在**无法验证** |

```
Trigger
  └─ Decision
       ├─ Run A ── Runtime Run ×6 ── Evidence Set X
       ├─ Run B（重试）
       └─ Run C（回放）
```

---

## 5. 显式状态机

🔴 **只登记「有代码能进入、且有消费方会读」的状态。** 凭空多一个状态就是一条 L-1 死配置。

| 状态 | 谁写 | 谁读 |
|---|---|---|
| `RECEIVED` | Trigger Gateway | `biga-card --status` |
| `PREFLIGHTED` | 五道守卫全过之后 | 同上 |
| `SNAPSHOT_FROZEN` | SnapshotCoordinator | Specialist（取 evidence_set_id） |
| `STAGE1_RUNNING` | Orchestrator | 飞书「跑到哪了」+ 看门狗 |
| `STAGE1_COMPLETED` | Orchestrator | 同上 |
| `RISK_RUNNING` / `SYNTHESIZING` | Orchestrator | 同上 |
| `CARD_PERSISTED` | Repository | outbox worker |
| `COMPLETED` | Orchestrator | 全部 |
| `FAILED` / `TIMEOUT` / `CANCELLED` / `INPUT_REQUIRED` | 各守卫与超时 | 排查 + 退出码 |

评审 §13 列了 15 个，这里留 13 个：

* 去掉 `IDENTITY_RESERVED` —— 与 `PREFLIGHTED` 在本系统里是同一瞬间（Stage 0 占号在预检里）
* 去掉 `SNAPSHOT_COLLECTING` —— 并入 `PREFLIGHTED → SNAPSHOT_FROZEN` 的转移，中间态无人读
* `NOTIFICATION_PENDING` **推迟到批 G**（outbox 存在之前它没有消费方）

`INPUT_REQUIRED` 对应现有退出码 `5`（`ask_user` 死锁），**不与 `FAILED` 合并** ——
一个要人去看提示词，一个要人去等。

所有转移必须走 `transition(run_id, expected_state, next_state)`（compare-and-set）。
不允许任何代码直接 `UPDATE state`。`run_events` 只追加，**建表时就要带触发器** ——
schema v4 建 `decision_ids` 时漏过一次，代价是整套决策身份机制建在可撤销的地基上（F1）。

---

## 6. 七批迁移

每批的出口都是同一句话：**745+ 测试全绿 + 新守卫被探针验证会红 + 一次回放 + 一次真实运行**。

### 批 A · 契约硬化

不改管线，只改类型与写边界。

| # | 内容 | 判据 / 探针 |
|---|---|---|
| A1 | `MissingItem` 脱离 `str`，`(code, detail)` 值对象 | 探针：构造两条同文本异代码的缺失项，断言 `!=` 且 `set` 不塌；回放 `extra_missing` 反推改按 `(code, text)`，用追加 2 的场景做回归 |
| A2 | 核心对象 `frozen=True`，`list`/`dict` → `tuple`/`Mapping` | 探针：对每个契约对象尝试赋值，断言抛错 |
| A3 | 🔴 **写边界重校验** —— `save_*` 先规范序列化、再严格重建、再校验、最后才 INSERT | 探针：构造「构造合法 → 事后改成非法」的对象，断言**写不进去**；并断言库里不再出现读不回来的行 |
| A4 | 严格 JSON（`allow_nan=False`）+ 规范序列化 | 探针：`NaN` / `Infinity` 写入被拒；**历史哈希向量不变** |
| A5 | Card roster 结构化 | 复用 `STAGE1_AGENTS`/`STAGE2_AGENTS` + `tests/_consistency.built_agents()`（F8/F10 用的就是这套）。探针：造一个重复 agent、造一个缺席 agent，各自报红 |
| A6 | `VerdictRef(agent, verdict_id, content_sha256, contract_version)` 上卡 | 探针：改掉库里某条原件的 sha，断言回放核对报红 |
| A7 | `confidence` → `data_completeness`；`from_dict` 双键兼容 | 不新增 `assessment_confidence`（没有消费方 ⇒ L-1）。探针：读一张历史卡仍能还原 |
| A8 | amendment / replay 血缘约束 | `new.task_id == old.task_id` 且 `new.agent == old.agent`；线性修订加 DB unique；`load_online_card` / `load_card_by_record_id` **拆成两个 API** |

🔴 **A5 不采用评审 §8 的六个具名字段。** 写死 `DecisionInputs.market/emotion/.../risk`，
`discipline`（裁定 13 故意不建）一上线就要改类型定义 —— 而 roster 是**配置**，
不该是类型。用结构性核对代替具名字段，是本仓库已经验证过的做法。

### 批 B · 运行身份 + 状态机

* schema v7（🔴 原写 v6——批 A-II 的 A8 在 `agent_verdicts` 上加线性修订的
  唯一索引，先落了 v6。迁移列表只许在末尾追加、不许改动已发布的条目
  （`schema.py` 自己的规则），所以这里是把设计文档的编号跟着改一位，
  不是去改 v6 那次迁移本身）：
  `decision_runs` / `run_events` / `evidence_sets`（+ 全部只追加触发器）
* `RunContext` 进契约层
* `transition()` 的 CAS 语义 + 非法转移一律抛错
* `bin/biga-card` 暂时仍走老路径，但**开始写 run 记录** —— 先让新旧并存，不改行为

判据：`biga-card --status <run_id>` 能说出「死在哪一步」，而不是 grep 日志。

### 批 C · Runtime Adapter + DecisionOrchestrator ★

全案的中心。

* `OpenClawRuntimeAdapter`：`start / wait / cancel / status`，**统一状态归一化**
* `DecisionOrchestrator.run(ctx) -> DecisionCard`：Stage 0→3 全部由程序驱动
* `bin/biga-card` 收缩成薄 CLI，**五道守卫的顺序原样保留**并继续由测试钉住
* `ORCHESTRATION.md` 从 18.6 KB 的流程说明收缩为**只剩给 Specialist 的指令文本** ——
  顺序 / 等待 / 传号 / 合成全部变成代码；「为什么」作为设计理由留在本文档
* `main` 失去启动管线的能力 —— 不再是「守卫拦住它」，而是**它没有步骤可执行**

🔴 **这一批之后，`entry_guard.py` 的性质变了**：它从「唯一防线」退成「纵深防御的一层」。
不删除 —— 人仍然可能手工照抄命令。

### 批 D · SnapshotCoordinator

```
Provider → RawArtifact → NormalizedSnapshot → FactBundle → EvidenceSet → Specialists
```

* 先迁共用最多的：`fetch_index_daily`（**当前被三个 skill 各抓一次**）
* 再迁 breadth / pool，最后 news / emotion
* 冻结之后 Stage 1 / Risk / Replay **全部只读**，Specialist 不联网

🔴 **要说准它解决什么。** 它**不**直接消除 §3.11 的交易日分裂 ——
当天日线在收盘后约 33~38 分钟才发布，这是数据源的物理限制。
它做到的是：把「这次决策站在哪个时间基准上」从**六个 Specialist 各自的发现**
变成**冻结集的一个声明属性**，于是「盘中用实时基准 / 盘后用日线基准」
成为一个在一处做出的策略选择。

副作用值得记：`CROSS_CHECK_PAIRS`（market.sh_close ↔ technical.close）
是为了**检测**这个问题而建的。共享快照之后它会恒真 ——
届时要么删掉它、要么改成核对「两者是否引用同一个 evidence_set_id」，
**不能留一条恒真的检查**（L-7 死配置）。

### 批 E · Facts / Assessment 拆分

```
旧 AgentVerdict  →  LegacyAdapter  →  FactBundle + AgentAssessment + AgentOutcome
```

逐个 Specialist 迁，允许一段时间新旧并存。做完之后
`amend_verdict.py`（skill 先写 verdict、agent 再补 stance 的那条补丁路径）可以退役。

⚠️ 这是七批里最贵的一批：六个 skill 的输出结构、契约层、存储层、回放、
以及 745 条测试里相当一部分都会被触及。**不与批 C 并行做。**

### 批 F · Risk 拆两层

* `RiskPolicy`（Python，硬）：身份 / 覆盖率 / 新鲜度 / 硬阈值 / 缺失传播 —— **fail closed**
* `RiskAssessment`（LLM，软）：硬规则全过之后才解释风险

现状其实已经完成了一大半：`risk_check.py` 本来就是 Python、fail-closed、只给事实不给结论。
真正要变的是**位置** —— 它现在跑在一个被 spawn 的 LLM 会话**内部**。
搬进编排器之后，身份错误在**付费调用之前**就被拦下，而不是在里面被算一遍再压成 UNKNOWN。

### 批 G · Outbox + 飞书 + 配置进仓库

* `notification_outbox` 与 Card **同事务**写入；worker 投递；幂等键 `(event_type, aggregate)`
* 飞书从「自由对话要卡」变成 **trigger**（`trigger_id` = 飞书 event id ⇒ 天然幂等）
  —— 这条直接关掉 `TODO.md` 待裁定里那项（实测 4 spawn + yield + 占号在后，\$0.4 白花）
* 自动出卡的 agent `tools.deny: [ask_user]`；交互式 `main` 不受影响
* `deploy/openclaw/`（**未建**）：`agents.yaml` / `tool-policy.yaml` / `profile.template.json` / `apply_config.py`
* 每次运行记录六个指纹：`git_commit` / `openclaw_version` / `agent_config_hash` /
  `tool_policy_hash` / `prompt_hash` / `contract_version`

🔴 **`apply_config.py`（**未建**）撞红线 R-2。** 它必须走 `bin/biga`，
且**绝不允许写出按默认名推导的 systemd 单元名**。装前核对、装后比对 sha256，
判据并入 `tools/verify/isolation.py` 的「共享命名空间」一项。
未做这一步之前不许合并批 G。

批 G 同时吸收第二份未处理的外部材料（飞书最佳实践）——
它的 §17（两种工作模式彻底分开）与 §25（Outbound Only → Preflight → Inbound Trigger）
与本批是同一件事，**不另开台账**。

---

## 7. 全案的单点风险 —— ✅ 已 spike 通过（2026-09-22）

### 原来的问题

`tools/verify/spawn_check.py` 的证据源是运行时的 `subagent_runs`，
而那张表由**跨 agent spawn 机制**写入。CLI 里**没有** `sessions spawn` 子命令 ——
`sessions_spawn` 只是暴露给 agent 的 MCP 工具。

> ⇒ Python 编排器能不能在不经过 LLM 轮次的情况下，产生同等的运行时证据？

### 实测结论：能，五项证据全齐，且零 `main` LLM 轮次

用 `biga attach --print-config` 铸一个 MCP grant，直接对它做 JSON-RPC：

```
[spawn] 0.1s  accepted   runId=8316618a-…
subagent_runs 新增 1 行
  child_session_key       agent:market:subagent:cd94da20-…      ✅
  controller_session_key  agent:main:main                        ✅
  requester_session_key   agent:main:main                        ✅
  payload_json            含传入的决策号                          ✅
[agents_wait] 3.3s → completed，带 result 与 usage(123 in / 5 out)
```

`spawn_check` 现有的四条判据**逐条满足**，无需重新定义。
两条退路（逐个 `$BIGA agent` / 极薄 spawn-only agent 轮次）**都不需要了**。

### 🔴 spike 顺带定死了三件事，批 C 必须按这个来

**1. `collect=true` 在没有 requesting run id 时必须带 `groupId`**

第一次调用直接被拒：

```
sessions_spawn collect=true requires a requesting run id when groupId is omitted.
```

Python 客户端**天然没有 requesting run**（它不是一次 agent 轮次）。
⇒ Adapter 每次 fan-out 自己生成一个 `groupId`，Stage 1 的五个共用它。
这不是可选项，是 API 硬约束。

**2. grant 是一次性的、带 TTL、绑定到一个会话键**

* `biga attach --print-config [--session <key>] [--ttl <ms>]` 输出 JSON，
  含 `env.OPENCLAW_MCP_TOKEN` 与 `configPath`；端点在那个文件里，
  是 **127.0.0.1 上的临时端口 + `/mcp`**（不是网关的 19789）
* `--session agent:main:orchestrator-<run_id>` 实测可用 ⇒
  `controller_session_key` 可以**按 run 区分**，比现在挂在 `agent:main:main` 上强得多
* 🔴 TTL 必须 ≥ 本次运行的总预算（`BIGA_CARD_DEADLINE_SEC`，当前 780s），
  否则长跑到一半 grant 过期。grant **不会被 attach 自己回收**，只会到期
* CLI 自己说了「用完删掉那个 `.mcp.json`」⇒ Adapter 负责删

**3. `agents_wait` 在编排器层就能拿到 token 用量**

返回里带 `usage: {inputTokens, outputTokens}`。
现在成本核算要事后读运行时 trajectory（`skills/_store/runtime.py`），
而那份数据**会随运行时清理旧 trajectory 而消失**（`skills/_store/schema.py` 的 v2 注释里记着这个代价）。

⚠️ 但**不要**顺手把它写进 `agent_runs` —— v2 删掉 token 列的理由仍然成立：
唯一真相源是运行时。先只在 `run_events` 里留一条，等真有消费方再说（L-1）。

### 仍然未验的部分

| 项 | 状态 |
|---|---|
| 五个并行 fan-out（同一个 `groupId`） | ⬜ 只验了 1 个。批 C 要验区间相交 |
| grant 在 780s 长跑里的稳定性 | ⬜ 本次只跑了 3.3s |
| `sessions_spawn` 失败/超时的结构化错误面 | ⬜ 只见过一种（`collect` 缺 groupId） |

⚠️ **这三项不是「应该没问题」，是「没测过」。** 批 C 的第一件事是把它们测掉。

## 8. §29 包结构重组 —— 采纳，并记下代价

评审建议长期迁到 `src/easyup_biga/{domain,application,providers,runtime,persistence,integrations,cli}`。

**已裁定采纳（全量档）。** 本节记录我提出过的反对意见与对应的缓解，
以便将来复盘时知道代价是**被预见过**的，不是被忽略的。

| 代价 | 缓解 |
|---|---|
| 作废 19 章教程里的路径 | 教程本来就是**冻结**文档，规约允许追加「⏩ 后续变动」指针。批 A–G 每批结束时，在受影响章节末尾追加一行指针，**不回改正文** |
| `sys.path.insert(0, "skills")` 与 OpenClaw skill 加载约定是承重的 | skill 目录结构**保持不动**，只把 `_contract` / `_store` / `_sources` 迁进包，用薄 re-export 维持旧导入路径；`tests/test_contract_single_impl.py` 的 AST 扫描要同步扩到新路径，否则「唯一实现」守卫会**在新目录上静默失效**（L-13） |
| 纯结构改动没有行为判据 ⇒ 无法用探针验证 | 判据改成「迁移前后 `replay --check` 逐字段相同」+「测试条数不减」。**结构重组唯一可信的验收是行为不变** |

🔴 **排在最后做。** 结构重组不产生任何可测的改进，而它会让**期间所有其他批次的 diff 变脏**
—— 一个 diff 里同时有「搬文件」和「改逻辑」，评审就失去意义。

---

## 9. 兼容策略

**每次只迁一个边界。**

```
旧 AgentVerdict  →  LegacyAdapter  →  新 AgentOutcome
```

不要求一次修改「所有 Agent + 所有测试 + 所有 Store + 所有 Replay」。

🔴 **兼容层必须自带退役判据。** 本仓库已有一条「读可以宽、写必须严」的成例
（历史卡可读、不可再写回库）。所有 LegacyAdapter 沿用它：
读路径接受旧格式，**写路径只接受新格式**，于是旧格式会随时间自然清零，
而不是永久驻留成第二套口径（L-3）。

---

## 10. 迁移闸门

每批完成、进入下一批之前，五条全过：

1. 现有回归测试全绿（本升级开工时的基线 **745** <!-- 冻结：开工快照，不随测试增长 -->，
   当前实测值由 `tools/verify/sync_test_count.sh` 同步进 README / CLAUDE 徽章）
2. 新增契约/不变量测试全绿
3. 🔴 **每一道新守卫先用探针验证会红**（`dev-workflow` 第 3 条 / L-13）
4. 一次 `bin/biga-card --check <决策号>` 回放一致
5. 一次真实运行 + `tools/verify/latency_report.py --parallel-check`

以及本仓库的两条纪律：
教程章节与代码同 commit（裁定 10）、`CHANGELOG.md` 写「为什么」（裁定 12）。

---

## 11. 出口条件

全部满足才算这次升级完成。

| # | 条件 | 判据 |
|---|---|---|
| 1 | `main` **不可能**启动管线 | 不是「守卫拦住它」，而是编排步骤全在程序里；探针：让 main 尝试，断言无路可走 |
| 2 | 每次运行恰好一个 `run_id`，且终态可达 | `decision_runs` 无悬挂的非终态行（超过硬超时即判 `TIMEOUT`） |
| 3 | 每张 Card 属于恰好一个 `decision_id`，每个必需 agent 恰好一条 outcome | 契约层拒绝构造 + 落库拒绝 |
| 4 | 每条 outcome 不可变；每条 `MissingItem` 身份基于 code | 探针：赋值抛错；同文本异代码不相等 |
| 5 | 每张 Card 记录它用的 `VerdictRef` | 改掉原件 sha ⇒ 回放核对报红 |
| 6 | 每个 EvidenceSet 在分析前冻结，全部 Specialist 读同一片 | `evidence_set_id` 在六条 outcome 上一致 |
| 7 | 每条持久化 JSON 是严格 JSON | `NaN`/`Infinity` 写入被拒；历史哈希向量不变 |
| 8 | amendment 不跨决策不跨 agent；replay 不跨决策血缘 | DB 约束 + 探针 |
| 9 | 每条外发通知幂等 | 同一 `(event_type, aggregate)` 重投不产生第二条消息 |
| 10 | 每次付费调用发生在预算批准之后 | 守卫顺序测试（已有，扩到新入口） |
| 11 | 飞书触发与 CLI 触发走**同一条**代码路径 | 探针：两种 trigger 产生的 `run_events` 序列相同 |
| 12 | 延迟与成本不劣于基线 | 与批 A 之前记下的墙钟/成本对比；劣化要有解释 |

---

## 12. 明确不做

```
换 PostgreSQL（只加显式 busy_timeout 与锁冲突指标）
Redis / Kafka / 微服务 / Kubernetes
大量新 Agent（discipline 仍按裁定 13 等它的输入源）
复杂 Dashboard
Question Bridge（飞书承接 ask_user）—— 批 G 只做 Outbound + Trigger
自动下单（裁定 2：硬前提是 Phase 4 通过）
```

---

## 13. 对既有文档的影响

| 文档 | 影响 |
|---|---|
| `architecture.md` | §3.3 调用链、§5.3 核心表、§9（新增失败模式）随批次更新。**它描述现状，不描述本计划** |
| `phase-2-specialists.md` | 不动。Phase 2 的两条出口条件与本升级并行 |
| `ORCHESTRATION.md` | 批 C 收缩为只剩 Specialist 指令文本。🔴 收缩时先修正它现在的三处自相矛盾，否则会把矛盾原样搬进代码 |
| `docs/tutorial/` | 冻结，只追加「⏩ 后续变动」指针；新章节从第 20 章起 |
| `docs/README.md` | 补第三种文档形状：跨阶段迁移按主题命名 |
| `CLAUDE.md` | 批 C 之后「Agent 不做算术」要扩写成「Agent 也不做编排」 |
