# 确定性编排升级

> 📄 **阶段** · 进行中，完成后冻结
> **覆盖**：为什么把工作流从 LLM 里拿出来、各批迁移（A–H 原案 ＋ 2026-09-23
> 从数据架构材料并入的 I / J / K / L）的范围/顺序/判据/出口条件
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

### 与五份外部材料的关系

| 材料 | 状态 |
|---|---|
| `docs/external/easyup-biga-architecture-upgrade.zip` | 本文的输入。**只读，不改** |
| `docs/external/easyup-biga-openclaw-feishu-best-practices.zip` | 尚未处理。与本文 §20/§27 高度重叠 ⇒ **并入批 G**，不单开一份台账 |
| 递归事故 / `ask_user` hardening 两份 | ✅ 已落地（commit `fb1856c` / `3d9ce90`），本文的起点包含它们 |
| `easyup-biga-multi-agent-data-architecture.zip` + `…-data-platform-development-plan.zip` | 2026-09-23 复核完毕 ⇒ **部分采纳，并入本文 §6**（批 J / I / K / L），不单开一份数据架构文档。采纳与不采纳的分界见下 |
| `baga-full-system-architecture.zip` | 🔴 **总体设计，位阶在以上四份之上。** 2026-09-23 到手。本文是它 §41 Stage 1 的施工文档，不与它平级 —— 冲突时以它为准 |

#### 🔴 总体设计（`baga-full-system-architecture`）对本文的三处影响

**① 本文正在做的事 = 它的 Stage 1，且是它列的第一优先。**
它 §41 的 Stage 1 是「Run Provenance / DecisionOrchestrator / OpenClaw Runtime
Adapter / Frozen EvidenceSet」—— 正是本文的批 B / C / D / J。
它 §43「当前开发短期重点」的前三条：

```
1. Run Provenance Closure
2. EvidenceSet 绑定 Run
3. Verdict / Card 绑定 Run
```

**三条全部落在批 J 里**（① = J 整批，②③ = J-I）。⇒ 批 J 的排序不再只是
本文内部的判断，是总体设计点名的下一步。

**② 当前的 DecisionCard 闭环不是终点，是 Kernel。**
它 §42 写明：多 Agent DecisionCard 闭环是未来整个平台的
**Decision / Agent Kernel**，后续的选股 / 计划 / 盘中 / 风控 / OMS / 组合 /
复盘 / 回测 / Web **不另建系统，而是围着这个 Kernel 长出来**。

⚠️ 这改变了本文很多「有没有消费方」判断的**时间尺度**，但**不改变判据本身**：
L-1 说的仍然是「建的那一刻要有被证明的消费方」，不是「永远别建」。
区别只在于——以前拿不准的东西现在知道它终将有消费方，
⇒ 该问的从「要不要建」变成「跟着哪一批建」。

**③ 推翻了本文此前关于 Parquet 的一条裁定。** 见下表。

#### 数据架构那份：采纳什么、不采纳什么（2026-09-23 裁定）

它与本文是**互补的两半** —— 本文管控制流（程序拥有工作流），它管数据流
（事实世界共享），两者在 `EvidenceSet` 交汇。所以它不另起一份文档，按批次并进 §6。

**已经成立的（它建议的，仓库已经做到）**：一次采集多 Agent 共享（批 D）、
一个 Run 一个 EvidenceSet（批 D-II）、不要每个 agent 一个库（`_store` 单一入口）、
Emotion 迁成标准消费者（批 E-I）。它 §28 建议的「第一个 Vertical Slice 从
`index_daily` 开始」**正是批 D-I/D-II 已经做完的那件事**。

**采纳，已排期**：§35.0 Run Provenance Closure ⇒ **批 J**（本文 §6，下一批）；
§9 RawArtifact ⇒ **批 I**；§17 Pipeline Registry + §16 Agent Registry ⇒ **批 K**；
§29 P0 里的 `cn.trading_calendar` ⇒ **批 L**。

🔴 **RawArtifact 按它 §9 的形状做，不按架构升级指南 §17 的形状做** ——
SQLite 只存 URI + Hash + Metadata，body 落 `.json.gz` 文件。理由不是文件更优雅：
raw 是唯一只增不减、且体积随时间线性膨胀的东西，塞进 control plane 的同一个
SQLite 会让备份 / WAL checkpoint / 将来切 PG 的迁移全部变贵，
而 control plane 恰恰是「数据量有限、需要事务和外键」的那一层。

**明确不采纳 / 推迟**：

| 它的主张 | 裁定 | 理由 |
|---|---|---|
| §5.2 / §22 Parquet + DuckDB 数据面 | 🔴 **2026-09-23 改判：方向已定，但不在本阶段建** | 原判是「推迟，且现在不要写进任何设计文档」，理由是会与 `architecture.md` §5.1 写死的切 PostgreSQL 触发条件并存成第二套口径（L-3）。**总体设计 §33 把这件事定了**：SQLite→Control Plane、Parquet→历史数据面、DuckDB→分析查询、Raw 文件→Provider 归档。⇒ 原判里「不要写下来」那半**作废**（方向由总体设计拥有，不是本文的自由度）；「现在不建」那半**保留**（没有消费方之前建了就是空仓库）。<br>✅ **2026-09-23 已解决**：`architecture.md` §5.1 已经按四个平面重写（控制面 / 历史数据面 / 分析查询 / Provider 归档），各自的选型与触发条件都写清楚了——历史数据面触发于 §45「第一版完整市场数据」开工（不是数据长大了才搬），分析查询跟着历史数据面走、真正需要是 §46 选股闭环开工时，Provider 归档按数据集类型判（全市场/批量抓取从建的那一刻走文件，单标的/小体积继续留 SQLite）。切 PostgreSQL 的三条触发条件不变，且现在明确写清楚了「只管控制面，不管其余三面」。批 I 的这条前置已解除 |
| §29 P0 六个 dataset 一次铺开 | **现在只做 `cn.trading_calendar`（批 L），其余等各自的消费方那一批** | ⚠️ **不是「它们不需要」** —— BigA 的终局是一个全覆盖的个人交易平台，`cn.security_master` / `cn.adjustment_factors` / `cn.equity.daily_bars` 都在那条路上，迟早都要建。这里判的是**时机**：L-1 说的是「建的那一刻要有被证明的消费方」，不是「永远别建」。它们今天还没有消费方（Phase 2 只出 Decision Card），⇒ 跟着各自第一个真实消费方的批次一起建，那时形状也才定得准。`cn.trading_calendar` 是今天**唯一**的例外：[`skills/_sources/tradetime.py`](../../skills/_sources/tradetime.py) 白纸黑字写着「**已知边界：不认节假日。本系统还没有交易日历**」—— 消费方已经在将就着用了 |
| §14 / §15 Dataset + Provider Registry | **推迟到批 K 之后** | 现在只有一个 dataset（`index_daily`）真的走完了全链。注册表会比被注册的东西还大。批 K 只做 Pipeline + Agent Registry，因为那两个有**已发生的事故**在等（见 §6 批 K） |
| 配套计划文档放 `docs/plan/` | **放 `docs/external/`** | `tests/test_docs_convention.py` 的 `PATTERNS` 只认 `design / tutorial / guide / external`，`docs/plan/` 会当场 `assert pat is not None` 报红。这两份是外部输入、只读、文件名已经是 `YYYY-MM-DD-` 前缀 —— 正好符合 external 的命名规则 |

⚠️ 它 §27 的 Primary / Fallback / Validator 三角色**不与裁定 15 冲突**：
裁定 15 约束的是 **agent**，§27 约束的是 **provider**，而且 §27 要求冲突时
`QUARANTINED` 而非静默选一方 —— 那恰恰是裁定 15 想防的「某天悄悄给出两个数」的正解。
⇒ 已在 `CLAUDE.md` 裁定 15 补半句写明，免得将来被读成矛盾。

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

### 追加 5 · 2026-09-22 外部架构复审的断言复核

🔴 **这份评审审的是一份 zip 快照，落在批 D-I 合并之后、批 D-II 开工之前**
（它自己给的测试数是 985，与 D-I 落地那一刻的数字完全对应；它把 D-II、
C-II 的 P1/P2 live 补验都列在「尚未完成」里）。复核这份评审时，D-II
（`e81f94b`）、C-II 的 `agent_runs` 回归修复（`e8518ed`）、C-II 收尾的
P1/P2 live 补验（`71789b5`）**都已经落地**——不照单接收既包括「断言本身
对不对」，也包括「断言现在还成立吗」。

| # | 断言 | 复核 |
|---|---|---|
| §4-6 | `test_isolation.py` 因 `check_namespaces` 漂移而失败 | 🔶 结构性观察成立（mock 清单确实只列了 4/5 个检查函数），但在本仓库当前环境里**不会真的失败**——`check_namespaces` 在装了 systemd 单元的机器上返回 PASS，不会踩进评审描述的 UNKNOWN 分支 |
| §5 | `test_scan_fallback.py` 断言 `scan_mode()=="git"` 会因为 zip 没有 `.git` 而失败 | ❌ 不是本仓库的 bug——这里是真实 git checkout，这条测试跑得过；评审自己在 §3 也承认「ZIP 中没有 `.git`」 |
| §6 | `latency_report.py` 的 UNKNOWN 诊断走 stdout，但有测试要 stderr | ❌ 查无此测试——`test_verify_exit_codes.py` 断言的正是 stdout，且现在跑得过 |
| §8-16 | `run_id` 未贯穿 `agent_verdicts`/`evidence_sets`/`DecisionCard`/`VerdictRef`；`latest_verdict_ids` 按 `decision_id` 不按 `run_id` 聚合；`verify_verdict_refs` 不核对 `ref.agent==存量.agent` | ✅ 事实成立，字段确实都不存在。**但评审据此构造的「重试时跨 run 串读」场景目前打不到**——见追加 5.1 |
| §17-18 | `CARD_PERSISTED` 转移写在 `card_ops.persist()` 之前，`persist()` 抛错会留下「已落库」的假状态 | ✅ 成立，`orchestrator.py` 里 `transition(..., CARD_PERSISTED, ...)` 确实先于 `persist(card)`。真实 bug，值得修——见批 C-III |
| §19-20 | `SNAPSHOT_FROZEN` 的 `detail` 仍写着「SnapshotCoordinator 未接入」，状态名与事实不符 | ⚠️ **已被 D-II 解决**——现在的 `detail` 是真实的 `{"evidence_set_id":..., "symbols":[...], "bars":120}`，不再是假话 |
| §26 | `orchestrator.py` 被直接调用能绕过总闸/预算/单实例锁 | ⚠️ **已被 2026-09-24 那份评审的 D-3 解决**——`main()` 新增 `_preflight_stop_gate`/`_preflight_budget`/`_preflight_lock` 三个函数，三层全部下沉到 Python 层（纵深防御，`bin/biga-card` 自己那道不删）。flock 有真实 POSIX 陷阱：排他资源不能简单"下沉"，靠 `BIGA_CARD_LOCK_HELD` 环境变量让子进程信任父进程已持有的锁，见教程第 40 章 |
| §28 | Stage 1 部分 spawn 成功后没有 cleanup，已启动的会孤儿化 | ✅ 成立，`handles=[ad.start(...) for a in STAGE1_AGENTS]` 中途抛错时，已经 start 成功的 handle 不会被 `cancel()`——真实成本泄漏，见追加 5.2 |
| §24-25 | 没有 stale-run reaper：进程被外部信号杀掉时，run 可能永久卡在非终态 | ⚠️ **已被 2026-09-24 那份评审的 D-2 部分解决**——`tools/maintenance/stale_run_reaper.py` 提供 `find_stale_runs()`/`reap()`；仍然只是纯函数工具，**不接调度**（`~/.openclaw-biga/cron` 依然是空的，`docs/guide/orchestration-kickoff-prompt.md` 早就把调度接入划给"批 G 的地盘"，本次沿用），需要人手工跑或未来某一批接 systemd timer |
| §29-30 | `MissingItem` 的「无响应」code 不分 agent；`AgentVerdict`/`DecisionCard` 只是浅冻结 | ✅ 两条都成立，都是精度/硬化类问题，不是安全洞 |
| §32 | risk 的新鲜度用 `retrieved_at - as_of`（源端延迟），不是「risk 评估那一刻」的年龄 | ✅ 成立，但**评审建议的修法本身没错**——`evaluated_at` 只要在 risk verdict 自己构造时定一次（跟今天 `retrieved_at` 的定法一样），不会破坏回放确定性；这不是「现在的做法错了」，是「现在测的是另一个更早的时间点」 |
| §35 | 各阶段（Stage1/risk/synth）各自固定预算，互相不感知总 deadline 还剩多少 | ✅ 成立，`self.stage1_sec`/`risk_sec`/`synth_sec` 各自读各自的环境变量，`deadline_sec` 只用来算 grant TTL。外层 bash `timeout` 兜底，不是没有防线，但内层三段预算之和可以超过 `deadline_sec` 而不自知 |

#### 追加 5.1 · 「重试跨 run 串读」目前打不中，但不是因为已经修了

评审的核心叙事（P1，§8-16）建在这个场景上：同一个 `decision_id` 跑第二次
（重试），`latest_verdict_ids(decision_id)` 可能把 Run A 的旧 verdict
和 Run B 的新 verdict混在一起。

复核结论：**这个场景在当前代码里打不中**——不是因为哪个字段挡住了它，
而是因为「同一个 `decision_id` 跑第二次」这条路径根本不存在。
`orchestrator.py::run()` 的 Stage 0 永远 `reserve_decision_id()` 现铸一个
新号（`main()` 传进来的 `ctx.decision_id` 恒为 `None`），`decision_id`
因此天然是「一次执行尝试」的粒度，客观上顶替了本该由 `run_id` 承担的
隔离作用。

⚠️ **这不代表评审的断言是错的，代表它是提前量。** 批 B 当年拆出 `run_id`
本身就是为了给「硬超时收掉一次运行后重试，两次尝试挤在同一个 `decision_id`
上」这个场景铺路（§4 的身份模型表格原话）——也就是说，重试路径迟早会有，
评审指出的这批字段缺口（`agent_verdicts`/`evidence_sets`/`DecisionCard`/
`VerdictRef` 都不认 `run_id`）到那一天就会从「潜在」变成「立即可利用」。

🔴 **2026-09-22 复盘：上面「不建议现在单独开一批」这句判断本身错了一个变量。**
它把两件成本曲线完全不同的事混成了一件：

| | 把字段补上、存下来（capture） | 靠字段做强制校验（enforce，拒绝跨 run 串读） |
|---|---|---|
| 现在有没有消费方 | 有——`run_id` 本身从批 B 就存在并被写入 `decision_runs`/`run_events`，这里只是让 `agent_verdicts` 等**已经在写别的字段**的表顺手多存一列 | 没有——`latest_verdict_ids()` 按 `run_id` 过滤这件事，只有重试路径存在才有意义 |
| 现在不做，以后会怎样 | 越晚做越贵：批 E 系列正在**同一层**（`skills/_contract/verdict.py`、`skills/_store/schema.py`）做迁移，晚一步就要在这层上再开一次刀，还要处理期间新落的历史数据 | 不会变贵——加一个 `WHERE run_id = ?` 不会因为多等几批而变难写 |
| 结论 | **现在排期，紧跟在当前这轮 schema 改动后面** | 维持原判：等真正的重试批次开工前再做 |

分界线是「记账」与「用账」——这个项目里 `Evidence.raw_hash` 就是先例：先有字段
被老老实实填上，很久之后才等到 D-II 给它写出真正严格的消费逻辑，中间那段
「填了但没人查」的时间不是浪费，是在为将来的消费方攒数据。`run_id` 现在要做的
是同一件事，不是提前建枚举。

⇒ **capture 部分排期为独立小批「批 J-I · run_id 贯穿全链」**
（2026-09-23 之前叫「批 E-I 收尾」，扩容并改名的理由见 §6 批 J），
等批 E-II 与 E-III **都**合并之后开工（三批都会碰同一批 skill 脚本与
schema 层，等前面落定再动一次，不是三批人马同时抢同一批文件）——
详见 `orchestration-kickoff-prompt.md`。**enforce 部分维持原判**：
任何引入「同一 decision_id 可以被多次尝试」的批次开工前，
`latest_verdict_ids()` 按 run_id 过滤 + 拒绝跨 run 混読的校验必须先补上，
但这部分现在确实没有消费方，留在那时候再做。

#### 追加 5.2 · Stage 1 部分失败的 cleanup 缺口，恰好是 `cancel()` 一直缺的那个真调用方

批 C-I 建了 `OpenClawRuntimeAdapter.cancel()`，批 C-I/D-I/D-II 三次评审都
记录过同一句话：它只在 N=2、无 drain 场景下验证过，"谁第一个真的让
Orchestrator 调用 cancel()"，谁负责把这条残留风险补掉（见 `TODO.md`
「批 C-II 残留风险」）。

这份评审的 §28 发现的洞——Stage 1 五路 fan-out 中途某一路 `start()` 抛错
时，已经成功启动的兄弟 handle 被直接丢弃、无人取消——**正好就是那个
一直没出现的真调用方**。修这个洞需要在 `except` 分支里对已启动的 handle
挨个调 `cancel()`，这恰好是 N 可以到 5、且天然会有 drain（部分 handle
已经在 active/recent 之间流转）的真实场景。

⇒ 修 §28 与补 `cancel()` 的残留风险验证是同一个动作，不是两件事——
批 C-III 把两者一起收掉。

### 评审的 drop-in 里有一处会出事

§23 建议统一用 `separators=(",", ":")`。但 `_store/db.py::payload_sha256` 目前**不带**它，
而 `Evidence.raw_hash` 与 raw 层的对应关系建立在这个哈希上。
直接照抄会**改变所有历史哈希**，且失效是静默的（两串 sha 都「看起来正常」）。

⇒ 严格 JSON 分两个函数：**新的**规范序列化（含 separators）用于新增载荷；
`payload_sha256` **只加 `allow_nan=False`**，分隔符维持现状。由测试钉住历史向量。

### 追加 6 · 2026-09-24 那份评审的 remediation checklist —— 逐节复核（批 M）

配套文件：`docs/external/biga-latest-deep-review-classified/docs/review/
2026-09-24-biga-remediation-checklist.md`（0 节 + A-H 八个阶段）。这是一份
**行动清单**，不是叙事性断言，逐条核对"做了 / 没做 / 为什么"比追加 5 那种
表格更直接。批 M（`7d30639`）只做了 C、D 两节；这里把 0 节与 A/B/E/F/G/H
也过一遍，不要因为"这次只排了 C/D"就让其余部分停留在"没人看过"的状态。

| 节 | 条目 | 状态 |
|---|---|---|
| 0 | 创建 Git tag/checkpoint、保存数据库备份、保存 OpenClaw 配置 Hash | ❌ 均未做——本项目走的是"每批独立 commit + 测试全绿才继续"，没有走这份 checklist 假设的"先打 tag 再动手"流程。`git tag -l` 只有 `v0.2.0` |
| 0 | 暂停自动 Retry | N/A——本项目现在没有自动 Retry 机制，无需暂停 |
| 0 | 暂停新增大量 Cron/systemd Job | ✅ 遵守——D-2 的 reaper 明确不接调度，本批没有新增任何 `.service`/`.timer` |
| 0 | 保留 `.biga-card-stop` 可用 | ✅ 一直可用，未改动 |
| A | 消除动态同名模块 monkeypatch 错位；测试不再依赖执行顺序 | 🔶 `test_facts_split_e3.py` 的 `card_ops` 双实例问题是真实存在、已知、**本批未修**的一处（`TODO.md`"既有测试隔离脆弱性"，与 H-I/H-II 的 legacy import 共存是两回事——后者经核实"查无实据说已引发故障"，见批 H-II 独立复核记录） |
| A | Isolation 检查改用统一 Registry | ❌ 未做——`isolation.py::check_namespaces` 仍是硬编码扫描，不是 Registry 模式。追加 5 的 §4-6 复核已经说明"结构性观察成立，但当前环境不会真的失败" |
| A | 统一 stdout/stderr 契约 | ❌ 未做，但对应的具体断言本身"查无此测试"（追加 5 的 §6 复核） |
| A | 测试同时支持 Git worktree 与 ZIP walk 模式 | 🔶 部分——`tests/_scan.py` 已有 git-fallback 机制（`is_external_reference` 等），但没有专门针对"ZIP walk"模式验证过 |
| A | subprocess 测试结束时被回收 | 🔶 各测试各自处理（`Popen`/`subprocess.run` 各自管理生命周期），没有统一的 Registry 式强制回收保障 |
| A | 完整 pytest 通过；更新 README 测试数量 | ✅ 两条都做——1379 条全绿（git checkout 环境下）；每次改动后同步 README/CLAUDE/review-prompt 三处徽章。⚠️ 这个数字本身在批 M 里第一次同步时错写成 1375——`sync_test_count.sh` 在一棵还没加完最后几条测试的树上跑过一次，之后又加了测试没有重新跑；见 CHANGELOG 本条 |
| B | 全部 13 项（`agent_verdicts.run_id` 强制 / Fact 唯一约束改 `(run_id,agent)` / `DecisionCard`/`decision_records`/`agent_runs` 各字段必填 / `VerdictRef` 严格验证 / `evidence_sets.run_id NOT NULL UNIQUE`）与 7 项回归测试 | ❌ 全部未做，且这一行原来的理由是错的——⚠️ **2026-09-24 二轮对抗性复核纠正**：这里曾写"目前没有任何代码路径会对同一 decision 开出第二个 run"，但 C-1（`inbound.py::accept_trigger`）本来就是这样一条路径：`launcher(origin, trigger_id, decision_id)` 重新拉起时，`new_run_context()` 照常铸一个全新 `run_id`，`decision_id` 不变——**relaunch 从批 M 提交的那一刻起就已经是"对同一 decision 开第二个 run"**，不是假设中的未来状态。二轮复核修复的 fact 唯一性检查（`latest_verdict_ids(decision_id)` 非空则拒绝 relaunch）与 B-2 的方向**互为反面**：前者是 fail-closed 收窄（事实已存在 ⇒ 拒绝重放，交回人工），后者是放开重放（唯一约束改 `(run_id,agent)` ⇒ 第二个 run 本来就该能写自己的 fact，不需要拒绝）。两者不能同时是"最终状态"——现在保留前者，因为它是**给当前 `(task_id,agent)` 约束**的正确安全阀，且已经修复了一个真实、可复现的严重 bug（生成一张用陈旧证据合成的卡）；B-2 一旦真正实施（连带 7 项回归测试、`latest_verdict_ids` 的调用方迁移），`inbound.py` 这道 fact 存在性检查需要**同步重新评估**是否还需要、要不要收紧成"检查这个 run_id 自己的 fact"而不是"整个 task_id 的 fact"——不是自动删除，是要有人回来看一眼。⇒ 见 `inbound.py` 里对应注释与本文件追加 6 的交叉引用；`latest_verdict_ids` 现在有四个调用方（Stage 1→Stage 2、Stage 3 合成、`verify_verdict_refs` 一类核对、以及这里新加的 relaunch 守卫）——B-4 计划废弃它时，这是需要一并处理的第四处，不是历史遗留的三处 |
| C | 13 项主清单 + 6 项回归测试 | ✅ 已做 9/13（Trigger 可恢复重启、Worker `max_attempts`、Retryable 分类、失败 exit non-zero、飞书失败不回滚 Card——本来就是；systemd 环境读取 target 已被独立事故修复）。**未采纳**：显式 `RESERVED/LAUNCHING/STARTED/LAUNCH_FAILED` 四态（改用更简单的 reserved_at+超时阈值，目标等价）、`EnvironmentFile`/env 文件权限/内容（已有更好方案，不依赖环境变量注入）。**真遗漏**：`timeout 按退避策略重试`——目前固定 `MAX_ATTEMPTS` 次机会，无指数退避，详见教程第 40 章"未完成的部分" |
| D | 9 项主清单 + 5 项回归测试 | ✅ 已做 7/9（`OrchestratorTimeout`、超时进 TIMEOUT、stale-run-reaper、外部 kill 后收敛、RunPreflight 下沉三项）。**本来就有、未改动**：全局 monotonic deadline 与 `min(stage_budget,remaining)`（批 C-III 已实现）、Stage 1 部分失败 cleanup（同批已实现）。**未做**：CLI/Feishu/Cron/systemd"统一入口"——CLI/Feishu 已统一走 `bin/biga-card`，但 Cron/systemd 目前没有出卡触发入口，N/A 而非真遗漏。回归测试里"SIGTERM 模拟后 Reaper 收尾"是**手工构造过期 run** 再验证收敛逻辑，不是真发信号的端到端集成测试 |
| E | 全部 11 项主清单 + 5 项回归测试 | ❌ 10/11 未做——`deep_freeze`、Fact/Evidence canonical equality、`input_evidence_ids`、Missing Code 按 agent 细分、`age_at(evaluated_at)`、Snapshot 元数据四项均确认缺失（部分已实测复现，见并行核实报告）。**唯一已做**：canonical payload 与 raw HTTP bytes 命名分开（批 I，`raw_text_sha256`，早于这份评审就解决了）。<br>⏩ **此后已变**：拆成批 1-4 做完，`input_evidence_ids` 由裁定 16 的 `OriginRef` 四类来源取代（不是原样补一个字段），`kind` 铁律落地。见 `CLAUDE.md` 裁定 16、`TODO.md`"E 节全部完成" |
| F | 全部 10 项 | ❌ 8/10 未做，含 `pyproject.toml` 目前**只有** `[tool.pytest.ini_options]`——没有 `[build-system]`/`[project]`/`[project.scripts]`，`pip install -e .` 这条验收标准现在必然失败；无 `ruff.toml`/`mypy.ini`/`pyrightconfig.json`；Dataset/Provider/Pipeline Registry 未完成（`TODO.md` 早就承认"后续阶段"）。**部分完成**：`domain`/`persistence`/`providers`/`runtime`/`application` 五个包已从 `skills/_*` 迁出（H-I/H-II），但内部仍是 legacy import（"跨包引用清理"待办，`TODO.md`）。<br>⏩ **此后已变**：当前必需部分（U-I 跨包引用清理 / U-II `pyproject.toml` 正式化 / U-III 移除动态 `sys.path` / U-IV ruff/mypy 基线）四个子批全部完成。console scripts 明确裁定**不做**（`tools/` 不是包，多数脚本靠 `__file__` 回溯仓库根，理由见 U-II）——`pip install -e .` 这条验收标准因此改写为"库代码可装"而非"CLI 脚本可装"，不是遗漏。3 项 Registry 仍未做，评审自己标注"后续阶段"，非当前必需 |
| G | 全部 13 项 Live Acceptance | ❌ 不适用当前阶段——前置的 B/E/F 大量未做，这一节假设的"每个 Run 恰好一个 EvidenceSet"这类断言依赖 B 部分先落地。本次也没有做真实的飞书端到端验证（需要开新会话，见教程第 40 章"未完成的部分"）。<br>⏩ **此后已变**：G 节 13 项证据于 2026-09-24 全部集齐（含真机飞书触发 `BIGA-20260924-009` 成功走完全程），见 `TODO.md` 对应条目、教程第 58/59 章。独立 sign-off 已于 2026-09-24 由新会话完成（建造会话发现并修复了验证过程中暴露的两处真缺陷，不适合同时当裁判，故交新会话复核），**G 节已关闭**——复核做法与结论见 `TODO.md` 对应条目。本行保留批 M 当时的判断不改写——它记录的是那一次的核实范围，不是今天的状态 |
| H | 全部 8 项 Baseline 冻结 | ❌ 未做——前序阶段没有全部完成，`v1-architecture-baseline` tag 不存在，这一节的前提条件不成立。<br>⏩ **此后已变**：8 项已全部做完，见本文件「§14 · H 节 Baseline 冻结」 |

**结论**：批 M 只完成了这份 checklist 里 C、D 两节（且 C/D 内部也各有 1-2 项
真遗漏，见上表），A/B/E/F/G/H 六节基本未动。这不是"忘了"——是核实过范围
之后有意选择优先做 C/D（直接影响当前实际在用的飞书路径），B/E/F/G/H 留给
后续批次，见 `TODO.md`"批 M"条目。

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

### 🔴 `run_id` 这个名字现在指三个互不相同的东西（2026-09-23 实测）

上面那张表是**目标**。实际落地到今天，`run_id` 这个字面量在仓库里有三个含义，
而且它们同住一个数据库、同住一份代码：

| 出现处 | 实际是什么 | 实测值 |
|---|---|---|
| `decision_runs.run_id` / `run_events.run_id` | ✅ 表格里那个：一次执行尝试 | `'cd5af37977fa4db9a5c2f1424cfc8001'` |
| `agent_runs.run_id` | ❌ **`INTEGER PRIMARY KEY AUTOINCREMENT` 账本行号** | `130, 129, 128…` |
| `SpawnHandle.run_id`（`skills/_runtime/adapter.py`） | ❌ 其实是表格里的 **`runtime_run_id`**（OpenClaw `subagent_runs.run_id`），且**从不落库** | 运行时给的 id |

危害不是「某处算错了」，而是**任何一条 join、任何一份报表，都会拿到一个语义正确
但指向错误的数字，且不会报错**。这与 §4 开头说的「`decision_id` 被迫承担五件事」
是同一个病的另一种形态：那次是一个名字扛了五个含义，这次是三个东西共用一个名字。

⚠️ 两者都不是「写得草率」。`agent_runs` 建于 Phase 1（那时只有一种 run），
`SpawnHandle.run_id` 忠实照抄了运行时自己的列名（在 adapter 内部它是对的）。
**是身份模型拆开之后，旧名字没跟着搬家。**

⇒ 批 J 一次清掉，见 §6。三处必须在同一批做完 —— 改一半留下的半新半旧命名空间
比现在更难读（读者无法判断某个 `run_id` 属于已改还是未改的那半）。

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

> ✅ **D-I / D-II 已落地（2026-09-22）。** D-I 建 `skills/_snapshot/`（冻结一次、多处读），
> D-II 把 market/sector/technical 的 `fetch_index_daily` 切到 `read_index_daily`。
> 落地细节冻结在教程第 25 / 26 章；当前状态见 `architecture.md` §6.3。
> breadth / pool / news / emotion 的迁移仍未做（下面第二条）。

```
Provider → RawArtifact → NormalizedSnapshot → FactBundle → EvidenceSet → Specialists
```

* 先迁共用最多的：`fetch_index_daily`（**原本被三个 skill 各抓一次** ✅ 已迁）
* 再迁 breadth / pool，最后 news / emotion（**未做**）
* 冻结之后 Stage 1 / Risk / Replay **全部只读**，Specialist 不联网（日线部分 ✅ 已达成）

🔴 **要说准它解决什么。** 它**不**直接消除 §3.11 的交易日分裂 ——
当天日线在收盘后约 33~38 分钟才发布，这是数据源的物理限制。
它做到的是：把「这次决策站在哪个时间基准上」从**六个 Specialist 各自的发现**
变成**冻结集的一个声明属性**，于是「盘中用实时基准 / 盘后用日线基准」
成为一个在一处做出的策略选择。

副作用值得记：`CROSS_CHECK_PAIRS`（market.sh_close ↔ technical.close）
是为了**检测**这个问题而建的。共享快照之后它会恒真 ——
届时要么删掉它、要么改成核对「两者是否引用同一份数据」，
**不能留一条恒真的检查**（L-7 死配置）。

> ✅ **D-II 采了后者**（`risk_check.py`）：判据从「比值」改成「比 `Evidence.raw_hash`
> 是否相同」——两个 Specialist 读同一份冻结快照 ⇒ raw_hash 都是那份的指纹 ⇒ 相同 ⇒
> 不报；某个悄悄退回独立抓取 ⇒ raw_hash 出自另一份 ⇒ 不同 ⇒ 报红。它因此不是恒真，
> 而是「谁没读冻结快照」的探照灯（探针 P2 钉住它仍会红）。没用 `evidence_set_id`
> 直接比，是因为 `Evidence` 上没有这个字段而 `raw_hash` 已经有 —— 加字段是批 E 的
> 契约改动，不在这一批。

### 批 E · Facts / Assessment 拆分

> ✅ **E-I 已落地（2026-09-22）。** 契约三型（`skills/_contract/facts.py`）+ `LegacyAdapter`
> + 存储（schema v8 `kind` 列，新旧同住 `agent_verdicts`）+ `Evidence.evidence_set_id`
> （顺带还了 D-II 的账）+ risk CROSS_CHECK 升级 + **试点 `emotion`** 已迁到新三型。
> 只剩 `risk` **未迁**，仍产 `AgentVerdict`。
> 落地细节冻结在教程第 27/29 章；当前状态见 `architecture.md` §4.1.2。
> ✅ **批 E-II 已落地（2026-09-23，`359b97c`）**：迁 market/sector/technical/news 四个。
> ⬜ **批 E-III（分发提示词已就绪）**：迁 `risk` + 退役 `amend_verdict.py`——
> `risk` 单独一批的理由是它同样带 `stance`（`VETO_STANCE`，制衡层最安全关键的判断），
> 且它消费其余五个的产出，等那五个形状稳定、评审复核过之后再动最后一个更安全；
> 退役 `amend_verdict.py` 的前提是**全部六个**都迁完，天然只能跟最后一个绑在一起，
> 不是这一批顺手加的范围。

```
旧 AgentVerdict  →  LegacyAdapter  →  FactBundle + AgentAssessment + AgentOutcome
```

逐个 Specialist 迁，允许一段时间新旧并存。做完之后
`amend_verdict.py`（skill 先写 verdict、agent 再补 stance 的那条补丁路径）可以退役
（退役前提是**全部** Specialist 迁完；E-I 只迁了一个，**未退役**）。

⚠️ 这是七批里最贵的一批：六个 skill 的输出结构、契约层、存储层、回放、
以及大量测试都会被触及。**不与批 C 并行做。** ⇒ 分发时拆成 E-I（基础设施 + 一个试点）
/ E-II（market/sector/technical/news）/ E-III（`risk` + 退役 amend_verdict.py），
同 A/C/D 的拆分理由，比原计划多拆一层——理由见上面 risk 那条。

#### E-I 交下来的两个设计问题，E-II 分发时已裁定（2026-09-23）

**①「Agent 追加的限制」缺失项归哪**：老 `amend_verdict.py --add-missing` 反复出现
的真实用例只有一种形状——"这次只抓到单日快照，无法判断趋势/周期"。这不是判断，
是 skill 自己在抓取那一刻就知道的事实（抓到几天数据是抓取的直接输出）。
⇒ **裁定：改成 skill 自己在 `build_fact_bundle` 里检测（阈值判断：实际抓到的
bars 数 < 判断趋势/周期所需的最小值），写进 `FactBundle.missing`，不给
`AgentAssessment` 加字段。** 如果 E-II 实际迁移时找到一条历史实例不符合这个
形状（需要 Agent 主观判断，不是 skill 能查的阈值），停下来回评审，不强行套用。

**② 消费方要不要改成直接读 `AgentOutcome`**：**裁定：不改。** `card_ops`/
`risk_check`/`DecisionCard` 现在靠 `load_verdict` 的兼容垫零改动消费新旧两种形状，
且已被 E-I 的 P3 探针证明过新旧并存不丢数据。没有消费方需要"事实与判断分开看"
这件事本身——提前把这层也收敛掉是 L-1（没有消费方的结构）。

### 批 J · 身份闭环（原「批 E-I 收尾 · run_id 贯穿全链」，2026-09-23 扩容）

> 🔴 **改名的理由**：原名描述的是 E-I 欠的那一笔账（把 `run_id` 存下来）。
> 2026-09-23 复核数据架构那份材料时实测发现，同一个命名空间里还有另外两处
> （见 §4 的三同名表），而它们**必须和第一处一起改** —— 改一半留下的
> 半新半旧命名空间比现在更难读。名字再叫「E-I 收尾」会让开工会话把它
> 当成扫尾活，按那个体量安排验证。**追加 5.1 里的旧名同步改了。**

数据架构那份材料 §35.0 把这件事列为**整个数据平台的第 0 步**，理由与本文一致：
后面每一个 dataset / snapshot / outcome 都要挂在某个 id 上，
id 本身有歧义的时候，挂得越多越贵。

🔴 **总体设计（2026-09-23）把它点成了下一步。** `baga-full-system-architecture`
§43「当前开发短期重点」的前三条是「1. Run Provenance Closure / 2. EvidenceSet
绑定 Run / 3. Verdict / Card 绑定 Run」—— ①是批 J 整批，②③是 J-I。
它 §12 列的七个身份（`trigger_id` / `decision_id` / `run_id` / `runtime_run_id` /
`evidence_set_id` / `verdict_id` / `decision_record_id`）与本文 §4 一致，
而实测闭环度只有 2/7（见 §4 的三同名表）。

拆成两批，理由是**耦合面不同**（同批 A / C / D / E）：

| 会话 | 内容 | 耦合面 |
|---|---|---|
| **J-I** | `run_id` capture 贯穿全链（= 原 E-I 收尾，范围不变） | 六个 skill 脚本 + 契约层 + schema |
| **J-II** | `agent_runs.run_id` → `ledger_id` 改名 ＋ `runtime_run_id` 落库 | `_store` + `_runtime` + `orchestrator` + `tools/verify` |

J-II 的两半合在一起做，是因为它们**动的是同一张表**（`agent_runs`）——
拆开就是对一张只追加表连开两次刀。

**J-I · capture，不 enforce。** 范围与判据见追加 5.1，一字未改：
只让 `run_id` 能被存下来、传下去，**不改任何现有的判定 / 过滤逻辑**。
`latest_verdict_ids()` 按 `run_id` 过滤这件事仍然等真正的重试路径出现。

**J-II · 清掉另外两个同名。**

* `agent_runs.run_id` → `ledger_id`：它是 `INTEGER PRIMARY KEY AUTOINCREMENT`，
  从 Phase 1 起就是账本行号，与编排的 `run_id` 毫无关系。
  🔴 **实测全仓没有任何代码读这一列** ⇒ 现在改是免费的，以后不是。
* `SpawnHandle.run_id` → `runtime_run_id`，**并落库**（`agent_runs` 加一列）。

  落库这一步有一个**现成的、已经在生产路径上的消费方**，不是提前量：
  `tools/verify/spawn_check.py`（`bin/biga-card` 每次出卡都调它，判据是调度命令
  的字面量）现在靠 `payload_json LIKE '%<决策号>%'` **文本匹配**认 spawn ——
  §4 的身份模型表格早就把这条记成「F3 残留」。有了落库的 `runtime_run_id`，
  它就从文本匹配变成结构化 join。

  ⚠️ 改判据的形状**照抄 E-I 已经验证过的那一套**：有 `runtime_run_id` 时优先用它，
  历史行是 NULL 时退回现有的 LIKE ——与 `CROSS_CHECK_PAIRS`「优先比
  `evidence_set_id`，退回 `raw_hash`」逐字同形。不要发明第二种兼容写法。

**开工前提**：批 E-II **与** E-III 都合并之后。J-I 要给六个 skill 脚本各加一个
CLI 参数，而 E-II/E-III 正在改这六个脚本的产出形状 —— 三批人马抢同一批文件是
本文反复避免的事。J-II 不碰 skill，理论上可以先开，但它与 J-I 共用 schema 版本号，
分开做会占掉两个版本号来改同一层，得不偿失。

---

### 批 I · RawArtifact（数据架构 §9 / 架构升级指南 §17）

raw 层现在存的**不是 raw**：链路是 `get_json()` → `json.loads` →
`json.dumps(sort_keys=True)` 落盘，而 `schema.py` 的建表注释写着
「这里存的是当时从数据源拿到的字节，**不做任何归一化**」—— `sort_keys=True`
就是归一化，这句注释是假的（L-3 的标准形状：注释断言了一件没发生的事）。

后果不是哈希算错（它自洽），而是 `content_sha256` 是**我们自己重排后**的指纹，
不能用来向数据源证明任何事；键序 / 空白 / 浮点表示 / 原始编码全部丢失；
上游改序列化而不改数据，看不见。对一个卖点是「证据可追溯、可回放」的系统，
这是证据链**最底层**的问题。

排在批 J 之后、批 F 之前：它给每条 raw 记录加溯源字段，
那些字段要指向哪个 `run_id` —— 得先有一个不含歧义的 `run_id`。
✅ 这个子问题已经解决：`orchestrator.py:201`
`save_fact_bundle(risk_fb, run_id=ctx.run_id)` 确认 risk 的事实包和其余
Stage 1 六个 skill 走同一个 `ctx.run_id`（批 F 复用了 J-I 的 capture
路径），不存在"risk 有没有独立调用点"这个分支。

#### 设计探活（2026-09-23）：损失点比原始描述更早，且有一处会被静默破坏的消费方

开工前把这条链从头跟了一遍，不止是"json.dumps 重新排了序"这一步。

**完整链路（比原始 bullet list 更早出现损失）**：

```text
skills/_sources/http.py::get_text()   HTTP body → str（按 encoding 参数解码，默认 utf-8）
        ↓
skills/_sources/http.py::get_json()   str → Python 对象（json.loads），原始文本就地丢弃
        ↓
各 collector（market_calc.py 等六个 calc/scan skill）
        把 get_json() 的返回值揣进 c.raw: list[(source, payload, src_as_of)]
        ↓
skills/_store/db.py::save_raw_snapshot()
        blob = json.dumps(payload, sort_keys=True)   ← 原始设计描述点名的这一步
        sha  = payload_sha256(payload)                ← 同样基于重排后的对象
        ↓
raw_market_snapshot.payload_json / content_sha256
```

原始设计描述只点名了最后一步（"json.dumps(sort_keys=True) 落盘"），但真正
第一次损失发生在 `get_json()` 内部——原始响应文本被 `json.loads` 解析成
Python 对象后，那段文本**没有被保留到任何地方**，六个 collector 攒进
`c.raw` 的从一开始就是解析后的对象，不是原始文本。⇒ 只改
`save_raw_snapshot` 的序列化方式不够，`c.raw` 这条累积路径本身就已经
丢了原始文本，需要往回追到 `get_json()` 才能补上。

🔴 **`get_text()` 这一层还有一个原始设计描述完全没提到的损失，本批
建议明确排除在范围外**：它按 `encoding` 参数（默认 `utf-8`）把 HTTP 响应
体解码成 `str`，如果某个数据源实际编码不是 utf-8（比如 GBK，国内老牌
财经站常见），这一步解码本身就可能已经出错，且比"键序被打乱"更隐蔽——
错误的解码在多数情况下不会抛异常，只会产出乱码字符串，静默地把错误的
文本当成"原始"存下去。⇒ **本批只解决"文本级"的原始性**（保留
`get_json()` 解析前的那段 `str`，不再重新 `json.dumps`），**不解决
"字节级"的原始性**（`get_text()` 的编码假设是否正确）——除非分发提示词
的建造会话核实后发现某个现用数据源的编码假设确实是错的，那是一个独立
问题，不在本批顺手带过。

**🔴 一个会被静默破坏的现有消费方，普查中才找到**：`skills/_snapshot/
coordinator.py:191-198` 的 `load_raw_snapshot` 消费方——

```python
snap = load_raw_snapshot(...)
raw = snap["payload"]          # 当前期望：一个可切片的 list（K 线数组）
if bars > len(raw): ...
```

它把 `payload` 当**已解析对象**在用（`len()`、切片）。如果本批把
`payload_json` 的语义从"重新序列化的 JSON"直接换成"原始文本"，这个消费方
会拿到一个 `str`，`len(raw)` 数的是字符数不是 K 线根数，且不会报错——
这正是本仓库最想防的那种"看起来正常、其实错了"的静默破坏。⇒ **这一批
必须是新增字段，不是替换语义**：`payload_json`/`load_raw_snapshot()`
返回的 `payload` 继续是解析后的对象（保持 `coordinator.py` 不用改），
原始文本另存一个新字段（例如 `raw_text`），`content_sha256` 改成基于
这个新字段计算——具体列名、`save_raw_snapshot`/`load_raw_snapshot` 的
签名怎么变，留给分发提示词细化，但"不改 `payload` 现有语义"这条边界要
守住。这与 `E-I` 的 `LegacyAdapter`、`K` 的卡级冻结名单回退是同一个
"新增字段、旧读法继续成立"模式，不发明第四种写法。

**`Evidence.raw_hash` 的语义耦合，需要在实现时留一句话**：
`skills/_contract/evidence.py` 的 `raw_hash` 字段文档写着它指向
`raw_market_snapshot.content_sha256`。本批之后 `content_sha256` 的计算
依据变了（从"重排后的对象"变成"原始文本"），但由于 raw 层只追加、旧行
不改写，这是"新行语义变了、旧行不受影响"的标准形状（同 schema 演进的
惯例），不需要做迁移，只需要在 `payload_sha256` 或建表注释里写清楚
"这一列的计算依据在某个 schema 版本之后变了"，免得将来有人拿新旧两种
`content_sha256` 直接比较。

**六个调用方需要跟着改，不是只改 `skills/_store/db.py` 内部实现**：
`market_calc.py` / `emotion_calc.py` / `technical_calc.py` /
`sector_calc.py` / `news_scan.py` / `skills/_snapshot/coordinator.py` 都调用
`save_raw_snapshot`——`payload` 参数的形状变化（多一个原始文本）是一次
真正的签名变更，六处调用点都要跟着改，普查已经点名，不需要分发提示词
的建造会话重新搜一遍。

### 批 K · Pipeline Registry + Agent Registry（数据架构 §16 / §17）

它要解决的事故**已经发生过一次**：2026-09-21，`news` 进了契约的 Stage 1 名单、
agent 也建好了，但 `agents.entries.main.subagents.allowAgents` 白名单里没有它
⇒ 只 spawn 了四个，**没有任何报错**，Card 照常产出，只是少了一个领域；
而 `risk` 如实报「Stage 1 缺席：news」，让排查方向天生指向 news agent 本身。

现在的应对是 `tests/test_roster_matches_config.py` 做数据驱动**对账**。
那是对的，但它是对账，不是单一源。Pipeline Registry 才是结构性解法：
名单只有一处，配置由它派生。

🔴 **只做 Pipeline + Agent 两个 Registry，不做 Dataset / Provider Registry** ——
见 §0 的裁定表。

#### 设计探活（2026-09-23）：现状比"两处会漂"更碎

开工前对代码库做了一次全量普查（roster 到底在几处、`decision_records` 现在
是否真的答不出"当时用了哪几个 agent"、`orchestrator.py` 读的是不是
`STAGE1_AGENTS`），结论比原始事故描述更细：

**roster 不是"两处"，是至少五处，其中一处完全没人管**：

| 位置 | 是否派生自 `_contract` | 备注 |
|---|---|---|
| `skills/_contract/verdict.py` `STAGE1_AGENTS`/`STAGE2_AGENTS` | （它自己就是源） | 唯一被普遍视为权威的一份，但含从不 spawn 的 `discipline`（裁定 13） |
| `skills/decision-card/scripts/orchestrator.py` `RISK_AGENT`、`SNAPSHOT_INDEX_AGENTS` | ❌ 独立字面量 | 前者是"Stage 2 里真正会被 spawn 的那个"，后者是"消费冻结快照的子集"——两个都是 `AgentDefinition` 该有的字段，现在各自单独硬编码 |
| `skills/_contract/verdict.py` `STANCE_VOCAB` 的 key 集 | 只靠一条测试钉住 | `docs/tutorial/21-value-objects-and-invariants.md` 记录过：`DecisionCard.absent_agents` 的权威**特意选了它**而不是 `STAGE1_AGENTS`，因为后者含 `discipline`——这条裁定 K 必须继续尊重，不能被 Registry 的引入意外推翻 |
| `tools/verify/adapter_spike.py` `STAGE1 = (...)` | ❌ 独立字面量，**零测试覆盖** | 它只 import `_runtime`，从不 import `_contract`；不在 pytest 下跑，`test_roster_matches_config.py` 完全看不到它。今天再加一个 Stage 1 agent，这个文件会静默继续用旧名单——这是普查找到的、原始事故描述里没有的**第三处真实漂移风险** |
| `~/.openclaw-biga/openclaw.json`（仓库外，不进 git） | 人工维护 | 唯一的真配置文件；由人跑 `biga config patch` / `biga agents add` 改，**没有任何仓内脚本生成它**。Registry 没法"变成"这个文件——它是外部系统的配置，只能是patch 的生成源，不能替代人工执行 `biga config patch` 这一步（R-1：仍然只走 `bin/biga`）|

**`test_roster_matches_config.py` 本身还有一条静默口子**：整组检查
`@pytest.mark.skipif(not CONFIG.exists(), ...)`——机器上没有那份运行时配置
（比如全新 clone、CI）⇒ **不报红，直接跳过**。这正是 R-3 想防的形状：算不出来
不该悄悄变成"没查出问题"。

**"Pipeline 版本化"这个诉求，普查之后要拆成两半**：

1. 「这张卡**实际**用了哪几个 agent 回答」——**已经能查，冗余三份**：
   `card_json.verdicts[].agent`、`agent_verdicts.agent`（按 `task_id` 分组）、
   `agent_runs.agent`（按 `decision_id` 分组）。这一半**不需要新表**，
   TODO.md 原描述"现在无法回答"不准确，普查已订正。
2. 「这张卡**当时被期望**用哪几个 agent 回答」——**真的没有被记录**，
   这才是需要动手的那一半：`DecisionCard.absent_agents`（`card.py`）是
   `@property`，现算现取**当下**的 `STANCE_VOCAB` key 集，不是卡生成那一刻
   冻结的名单。⇒ 一张三个月前的卡，今天用 `bin/biga-card --show` 重新加载，
   `absent_agents` 用的是**今天**的 roster，不是当时的——如果这期间 roster
   变过（加了 agent，或者哪个 agent 一度下线），**同一张历史卡的这个字段会
   在不同时间点给出不同答案，而 `card_json` 本身没变**。这是普查中发现的、
   比原始事故更隐蔽的一处潜在静默漂移，还没有实例发生过，但机制上成立，
   应当在 K 里一并堵上。

#### 设计方向（不是最终实现，留给分发提示词细化）

1. **`AGENT_REGISTRY`**：`_contract` 新增一份结构（一个 agent 一条
   `AgentDefinition`：`agent_id` / `stage` / 是否真的会被 spawn / 是否消费
   `SnapshotCoordinator` 冻结的快照），只装**已经在生产路径上有消费方**的字段
   ——不装 `required_datasets` 之类外部材料示意稿里的字段（那些绑定 Dataset
   Registry，裁定表已明确推迟，装了就是 L-1 的死配置）。
   `STAGE1_AGENTS` / `STAGE2_AGENTS` / `RISK_AGENT` / `SNAPSHOT_INDEX_AGENTS`
   全部改成**从它派生**的模块级常量，不再手写字面量——照抄
   `skills/_contract/run.py` `RunState` 已经验证过的形状：`frozenset(v for k, v in vars(...) ...)`
   内省派生，不手抄第二份（那个文件的docstring 原话就是"两处各写一份状态
   清单必然漂"，跟 K 要治的是同一个病）。`STANCE_VOCAB` 的 key 集**保持
   独立**，但改成对 Registry 断言子集关系（不能因为 Registry 存在就把
   `discipline` 意外带回 `absent_agents` 的权威里）。
2. **`tools/verify/adapter_spike.py` 必须迁到 Registry**——这不是顺手，
   是 K 结束时"是否还有消费方在读独立字面量"的验收判据之一，普查已经点名
   这一处。
3. **卡级冻结名单**：`orchestrator.py` 构建 Card 时，把当时的 `AGENT_REGISTRY`
   算出的期望 roster 写进 `card_json`（新字段，回放路径照旧不重算）；
   `DecisionCard.absent_agents` 优先读这个冻结字段，只有老卡（字段不存在）
   才回退到读**今天**的 Registry——同一个"有就用、缺就退回"形状，`E-I` 的
   `LegacyAdapter`、`J-II` 的 `spawn_check` 结构化 join 都是这个模式，
   不发明第四种写法。
4. **`test_roster_matches_config.py` 的 `skipif` 缺口**——是否在 K 里一并
   修（改成 R-3 式的"报 UNKNOWN/missing"而不是静默跳过），还是记成独立的
   已知缺口留给分发提示词自己判断范围，**由建造那批的会话在开工时定**，
   这里不预先拍板范围。
5. 明确不做：不生成/不写 `~/.openclaw-biga/openclaw.json`——Registry 最多
   提供"照 Registry 应该长什么样的 `allowAgents`/`agentToAgent.allow` patch"
   给人工核对着跑 `biga config patch`，不越过 R-1 自己去改外部配置文件。

### 批 L · `cn.trading_calendar`（数据架构 §29 P0 唯一有消费方的那个）

> ✅ **已落地（2026-09-23）。** Provider `skills/_sources/szse.py`（深交所官方 monthList，
> 探活后综合选出：新浪日历要 JS 引擎、东财脏、timor 是办公日历≠交易所口径）+ 第一张真实
> `fact_*` 表 `fact_trading_calendar`（schema **v15**）+ `market_is_open()` 认节假日
> （查表、查不到回退 weekday）。`session_in_progress()` **未改**（理由见下）。
> 🔴 **已知约束**：深交所站点从本项目 WSL 部署连不通 ⇒ 本环境 `fact_trading_calendar`
> 空表、`market_is_open` 恒走安全回退；解析层由离线 fixture 全测、落库链由注入桩 fetcher
> 全测，真实抓取留给能连通交易所的运行环境。落地细节冻结在教程第 38 章；当前状态见
> `architecture.md` §5.3.6。下面是开工前的设计与探活，保留作历史。

`skills/_sources/tradetime.py` 自己写着「已知边界：不认节假日」，
后果是节假日会被当成交易日（朝安全方向，但要靠调用方在缺失项文案里
把「也可能是休市日」一并说出来）。这是 P0 六个 dataset 里**今天**唯一通过
L-1 判据的一个 —— 消费方已经在将就着用了。其余五个不是不建，是等各自的
消费方那一批（见 §0 的裁定表）。

🔴 **总体设计已到（2026-09-23），这条排期据此收紧。** 它 §45「第一版完整市场
数据」把这六个列成**一个批次**：Security Master / Trading Calendar / Tradability /
Adjustment Factors / EOD Daily Bars / Emotion Close；§41 把它们放在 **Stage 2**，
第一个真实消费方是 §46 的选股闭环（`EOD Snapshot → FeatureSet → Screening →
CandidateSet`）。

⇒ 批 L **只做 `cn.trading_calendar` 一个**，理由不变（它今天就有一个正在将就的
消费方），但定位变了：它不是「P0 里挑一个先做」，而是**给 §45 那一批打样**——
用一个非行情、体量小、判据清楚的数据集，把「Provider → Raw → Normalize →
Quality → Snapshot」这条链在**第二次**走通（第一次是已完成的 `index_daily`）。

🔴 **其余五个仍然不要按今天的猜测定 schema。** 它们的形状由 §46 的选股闭环
决定（FeatureSet 要什么、Screening 按什么过滤），而那一批还没开工。
raw 层只追加，改形状的代价全在后面。

同时它是数据架构那份材料的**第二个 vertical slice**（第一个是已完成的
`index_daily`），用来验证「Provider → Raw → Normalize → Quality → Snapshot」
这条链在一个**非行情**数据集上是否同样成立。

#### 设计探活（2026-09-23）：不能照抄 `SnapshotCoordinator`，且真实消费方零测试覆盖

开工前查了三件事：现有 raw 层批 I 之后长什么样、`market_is_open()` 真实
被谁用、`SnapshotCoordinator`（D-I/D-II 那套）这次能不能直接复用。

**raw 层不用改，批 I 已经把它铺好了**：`save_raw_snapshot(payload=, raw_text=)`
（schema v13）对任意来源都成立，日历这类数据源不需要新的 raw 层字段。
`content_sha256` 已经基于原始响应文本算，不用再操心批 I 之前那套"重排后
指纹"的问题。

🔴 **`SnapshotCoordinator`（D-I/D-II）解决的是另一个问题，不该照抄**：它的
存在理由是"一次决策运行内，多个 Specialist 必须看到同一份网络抓取结果"
（同一个 `evidence_set_id` 冻一次、大家从冻结的切片读）——这解决的是
**同一次 Run 内的一致性**。交易日历完全不是这个形状：它不是每次决策运行
都要重新抓一次的东西（一年抓一次、按需刷新即可），"两个 Specialist 会不会
看到不同版本的日历"根本不是这批要防的风险。⇒ 批 L **不建 EvidenceSet /
不接 SnapshotCoordinator**，那是给"同一次运行内、易变、多消费方"的数据设计
的，日历是"低频更新、只读查表"的形状，硬套会引入一套不必要的每次-运行
开销。

**"归一化事实层"（`fact_*`）目前只存在于文档，schema 里一张都没有**——
`grep CREATE TABLE skills/_store/schema.py` 十张表，命名都是
`decision_records`/`agent_verdicts`/`raw_market_snapshot`/`evidence_sets`/
`notification_outbox` 这类，`architecture.md` §5.2 画的"raw → fact → derived"
三层分层图里，**只有 raw 层真的被实例化过**。批 L 如果按这个数据集的形状
（归一化后是"某天开不开市"这种简单查表事实）建一张真正的 `fact_trading_
calendar` 表，会是这个仓库**第一次**把文档描述的分层图落成真实 schema——
这本身就是"打样"这个定位的一部分，不只是加一个数据集。

🔴 **真实消费方 `market_is_open()` 现在零测试覆盖**（普查发现，不是这一批
造成的既有缺口）：`grep -rl market_is_open tests/` 零命中，没有
`test_tradetime.py`（**未建**），`skills/_sources/tradetime.py` 自己也没有内嵌测试。
它被 `news-scan` 用来判定 `market_open` 这个派生 evidence（喂给"此刻是否
连续竞价"这条判据），`emotion-calc` 的 `session_in_progress` 用同一个
模块判定"这个数是真零还是还没产生"——**两个真实生产路径**依赖这个模块，
但改坏它今天不会有任何测试报红。⇒ 批 L 必须先给现状（纯 weekday 判据）
补一批特征测试（characterization tests：固定几个已知的历史交易日/周末，
断言现在的行为），再改，否则"改完之后行为变了没变"没有基线可比。

**Provider 选型是开放问题，留给建造会话，但方向已经查过**：现有四个
`_sources/` 适配器（sina/eastmoney/tencent/sina_news）全部是**免鉴权**的
公开端点抓取，没有一个走 token/API key。数据架构材料 §8 点名的
`a-stock-data`（`github.com/t4ol1n/a-stock-data`）在这个仓库的定位一直是
"Provider Catalog / 接口实现参考"（架构文档 §7、数据架构材料同名小节），
不是拿来直接 import——WebSearch 核实过它确实在维护一份交易日历实现，可以
作为参考去找免鉴权端点，但具体端点、字段形状都还没选，留给建造会话按
同一条"免鉴权优先"的既有惯例去挑。Tushare 的 `trade_cal` 需要 token，这类
需要凭据管理的源如果最终选了它，是一次**新增复杂度**（批 G-II 刚踩过
凭据管理的坑），要在正文里写清楚为什么值得，不要静默引入。

---

### 批 F · Risk 拆两层

> ✅ **已落地（2026-09-23）。** `build_fact_bundle()` 的调用从「risk 被 spawn 后自己在
> LLM 会话里跑」挪到「编排器在 spawn 之前直接 import、免费地跑」。两种确定性早退
> （`foreign` / 无上游，判据 `fb.status=='failed'`）不再 spawn risk；其余仍 spawn，但
> risk 只解读编排器算好的 `verdict_ref`。落地时**独立发现并一并修掉两处设计没点名的
> 交互**：① schema **v11** `ux_fact_per_task_agent` 分区唯一索引堵「同一 (task_id, agent)
> 双写 fact 的静默并存」（fact 行 amends 恒 NULL，v6 线性索引管不到它）；② `persist()`
> 只给真被 spawn 的 agent 记账本行，修早退场景下 `spawn_check` 把 risk 误判成 forged。
> 行为变化：全员 Stage 1 缺席不再判「零证据 FAILED」，改出一张满是缺失的卡。
> 落地细节冻结在教程第 33 章；探针红灯与理由见 `CHANGELOG.md`。

* `RiskPolicy`（Python，硬）：身份 / 覆盖率 / 新鲜度 / 硬阈值 / 缺失传播 —— **fail closed**
* `RiskAssessment`（LLM，软）：硬规则全过之后才解释风险

现状其实已经完成了一大半：`risk_check.py` 本来就是 Python、fail-closed、只给事实不给结论。
真正要变的是**位置** —— 它现在跑在一个被 spawn 的 LLM 会话**内部**。
搬进编排器之后，身份错误在**付费调用之前**就被拦下，而不是在里面被算一遍再压成 UNKNOWN。

#### 设计探活（2026-09-23）：`build_fact_bundle()` 已经是纯 Python，不用先拆再搬

开工前读了 `skills/risk-check/scripts/risk_check.py` 全文（414 行，只有三个顶层函数）。
结论比"拆两层"听起来更简单：**`build_fact_bundle(*, verdict_ids, store, task_id)`
本来就是一个不碰 LLM、不碰 spawn 的纯函数**——`main()` 只是给它套了一层 argparse +
`save_fact_bundle()`。不需要先把硬规则从 LLM 会话里"拆出来"，因为它压根不在 LLM
会话里算——**编排器现在就能直接 `import` 它，今天就能调**。

**当前流程的浪费不在"算得慢"，在"为了触发这次算，先花一次 LLM 调用"**：
编排器 spawn `risk` → LLM 读提示词 → LLM 跑 `exec` 调 `risk_check.py`（这一步本身
免费、快，是本地子进程）→ LLM 读 JSON 输出、按 `agents/risk/AGENTS.md` 的判断表
决定 stance → LLM 跑 `amend_verdict.py --stance`。真正花钱、耗时的是那两次 LLM
turn，不是中间那次脚本调用。

**不是所有 `build_fact_bundle()` 的结果都值得省这次 LLM 调用**——读完函数体，
返回路径分三种：

| 路径 | 触发条件 | LLM 解读还有没有信息增量 |
|---|---|---|
| 提前 return（归属） | `foreign`：上游证据来自别的决策 | **没有**——`verdict=UNKNOWN` 是机械判定，`missing` 里已经写清楚是哪几个决策串了进来，stance 只能是「无法判定」，没有第二种可能 |
| 提前 return（无上游） | 一条上游判定都没拿到 | **没有**——同上，`无法判定`是唯一合法结论 |
| 走到底 | 覆盖不足 / 交易日不一致 / 阈值命中 / stance 冲突 / 正常 PASS | **有**——即使最终 stance 已经被 `agents/risk/AGENTS.md` 的判断表钉死成确定值（比如 `覆盖不足 ⇒ 无法判定`），Card 上呈现给人看的解释性文字仍需要 LLM 组织；且这条路径本身在计算意义上就没有"提前退出"的空间可省 |

⇒ **只有前两条提前 return 的路径值得完全跳过 risk 的 spawn**——它们在 `build_fact_bundle()`
的返回值里就已经是确定的、不需要任何解读的终局结论。第三条路径不省 spawn，
但仍然全部改成"编排器先算好、risk 只解读"（见下）。

**"present 但没有 stance"不会被误判成"没跑"**：查过 `_store.load_verdict()`
的多态转换——一行只有 `kind='fact'`、从未被 `save_assessment()` 追加过 assessment
的 `agent_verdicts` 记录，`load_outcome()`/`to_agent_verdict()` 依然能把它转成一个
合法的 `AgentVerdict`（`stance=None`），会正常出现在 `card_ops.load_verdicts_and_refs()`
的结果里、正常计入"present"。⇒ 编排器可以放心地**只写 FactBundle、不追加
assessment**，卡上呈现的是"risk 给出了事实、判定是 UNKNOWN、没有给 stance"，
不会被误读成"risk 没有响应"（那是另一个失败模式，`absent_agents` 单独管）。

#### 设计方向

1. **编排器直接 `import build_fact_bundle, save_fact_bundle`**，在原本 `ad.start(RISK_AGENT, ...)`
   的位置之前，用 Stage 1 的 `verdict_ref` 列表**在本地、免费地**算一遍，`run_id`
   走批 J-I 已经打通的 capture 路径存进去。
2. **提前 return 的两种情况 ⇒ 不 spawn risk**，编排器自己把这份 FactBundle 落库
   （不追加 assessment），直接拿它的 `verdict_ref` 参与合成。
3. **其余情况 ⇒ 仍然 spawn risk，但提示词整个换掉**：不再让它自己跑
   `risk_check.py`，改成把编排器已经算好的 `verdict_ref` 直接告诉它：
   「事实已经算好，编号 verdict_ref=NN，不要重新跑 skill，读它、按判断表给
   stance、跑 `amend_verdict.py --ref NN --stance <词>`」。
   ⚠️ **一个未解的边界情况，留给分发提示词的建造会话核实、不预先假设**：
   如果 risk 没听话、还是自己跑了一遍 `risk_check.py`（提示词失效、或走到某条
   还没被想到的兜底路径），它会对同一个 `(task_id, agent)` 再产一条 `fact` 行——
   这会不会撞上 `save_fact_bundle`/线性修订唯一索引，撞上时报的错够不够
   明确，需要真的跑一次这个场景验证，不能只看代码猜。
4. **`risk_check.py` 的 CLI 保留、不删**——手工调试/人工复核仍然需要能独立跑它；
   变的只是编排器现在多了一条不经 CLI 的调用路径，两条路径共用同一个
   `build_fact_bundle`/`save_fact_bundle`，不发明第二套计算逻辑。
5. **`agents/risk/AGENTS.md` 必须同步改**——现在的「四条硬约束」第 1 条整段在教
   risk 怎么跑 `risk_check.py`；改完之后大部分场景下它根本不会跑这个脚本了，
   契约文本继续这么写就是 L-6（契约与实际行为对不上）。具体怎么改，包括「万一
   真的还需要它自己跑（比如编排器这次因为某种原因没能算出来）要不要保留一条
   兜底路径」，留给分发提示词的建造会话定，这里不预先拍板。

### 批 G · Outbox + 飞书 + 配置进仓库

> ✅ **批 G-I（Outbound Only）已落地（2026-09-23）。** 只做「推」，不接受任何飞书方向
> 的输入。建的东西：
> * `skills/_contract/notify.py` —— 事件类型词表（`NOTIFICATION_EVENT_TYPES` 四类）
>   + `card_event_type()` 分类判据（否决 / UNKNOWN·缺失 / 正常）+ `NOTIFY_FAILURE_STATES`。
> * schema **v12** 两张只追加表：`notification_outbox`（幂等键 `(event_type, aggregate)`）
>   + `notification_deliveries`（投递尝试日志）。「投没投成」是派生查询，不给 outbox 开
>   `delivered_at` 的 UPDATE 例外 —— 与 `run_events`/`amends`/`replay_of` 同一条「状态变更
>   一律追加」的先例（理由见 `schema.py` `_V12` 注释与 `CHANGELOG`）。
> * `_store.db` 的 `save_card_with_notifications`（Card + outbox **同事务**）/
>   `enqueue_run_failed` / `record_delivery` / `undelivered_notifications`。
> * `RunState.NOTIFICATION_PENDING`（插在 `CARD_PERSISTED` 与 `COMPLETED` 之间，只代表
>   「已入队」不代表「已投递」）；跳过它直达 `COMPLETED` 现在是非法转移。
> * `skills/decision-card/scripts/notify_worker.py` —— outbox 的读取方，桩投递
>   `StdoutDeliverer`（真飞书 adapter 是 G-II 的生产方）。
>
> 落地细节冻结在教程第 34 章（与批 F 各自独立选中"第 33 章"撞车，按落地
> 先后顺序处理，F 先落地保住 33，本批改记 34）；探针记录见 `CHANGELOG`。
> **批 G-II（Inbound Trigger）
> 未开工** —— 异步执行、飞书 trigger、`deploy/openclaw/` + R-2、`main` 移出路由，都在那批。

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

#### 设计探活（2026-09-23）：比原始 bullet list 里的东西更多，也更少

开工前普查了代码库现状，结论有几处纠正了原始 bullet list 的隐含假设。

**仓库里现在一行飞书代码都没有**——`grep` 全仓 `lark_oapi`/`larksuite`/
`open.feishu.cn` 零命中。所有真实的飞书接入（`channels.feishu` 配置块、
credential、装好的插件包）都在仓库外的 `~/.openclaw-biga/openclaw.json`
与 OpenClaw 运行时里。`skills/_contract/run.py` 的 `RUN_ORIGINS` 已经预留了
`"feishu"` 这个值，但**没有任何生产方**在用它——批 G 要做的第一件事是
**造出第一个把 `origin="feishu"` 真正填上的调用点**，不是接一个已有半成品。

**入站幂等的地基已经在 v7 建好了，只是没人用**：`decision_runs.trigger_id`
（`schema.py` v7）的建表注释原话就是"一次外部请求的幂等键（飞书 event id /
CLI 每次一个 / cron）"——跟 bullet list 里"trigger_id = 飞书 event id ⇒
天然幂等"这句话**逐字对应**，只是至今没有生产方填过这一列。⇒ 不要另造一个
入站幂等表，这一列已经在等着被用。

**出站的 `notification_outbox` 确实是全新的（第 9 张表）**——当前 schema
（v10，8 张表）里没有任何东西承担这个角色，且这一点项目自己已经预料到了：
`skills/_contract/run.py` 的 `RunState`（13 个状态）**故意没有** `NOTIFICATION_PENDING`，
文档字符串原话是"同理推迟到批 G（outbox 存在之前它没有消费方）"——也就是说
批 G 不只是"加一张表"，还要**给状态机加一个新状态**，这是原始 bullet list
没提到、但已经被项目自己提前留好位置的一块。

🔴 **`bin/biga-card` 今天是完全同步的——没有任何"快速 ACK、稍后完成"的
半成品可以复用。** 全文一次 `wait "$_ORCH_PID"`，`RunState` 里也没有任何
"已确认、后台跑着"性质的状态。这意味着 Phase 3（Inbound Trigger）里"Event
Verify → Dedup → Allowlist → Preflight → Cost/Lock Guards → **Pipeline**"
这条链，最后一步接的是一次要跑 170-200 秒的同步调用——**批 G 真正要发明的
基础设施是异步执行本身**，不是把已有的异步机制接上飞书。这是这一批最大的
一块新增复杂度，原始 bullet list 用一个词（"worker 投递"）轻描淡写带过了。

**真正的根因修复点不是"让 main 更听话"，是"飞书触发根本不该经过 main 的
自由判断"**。2026-09-21 的两次事故（19:31 的 4 个孤儿 spawn 与 21:03 的
出卡递归 L-14）根子相同：**入口在 prompt 层面，不在代码层面**。L-14 已经
用代码守卫堵上了一半——`entry_guard.py::classify_caller()` 挡住了「入口
脚本被递归调用」，批 C-II 更进一步，把 `main` **整个移出了转发路径**
（"不是守卫拦住它，是够不到"）。但这道守卫只保护 `bin/biga-card`/
`orchestrator.py` 这两个**脚本入口**，挡不住 `main` 直接调用通用的
`sessions_spawn` 工具去 ad hoc 拼一整套 Stage 1 spawn——而这**正是** 19:31
那次事故的真实形状（4 次独立 spawn + `sessions_yield`，全程没有 orchestrator
参与，占号在 spawn 之后）。今天唯一挡着这条路的，是根 `AGENTS.md` 里的
文字指示——跟 L-14 修复前的防护是**同一个量级**，而 L-14 自己的事后总结
已经明确说过这个量级不够。
⇒ 结构性修法：飞书的"出卡"事件必须**直接**从 gateway/adapter 层调用
`bin/biga-card` 等价的入口，`main` 的 LLM 全程不参与这条路径的路由决策——
不是"教 main 认出这是出卡请求再转发"，是"main 压根不会看到这类请求"。
这比外部材料 §18 自己的推荐拓扑（`External Trigger → Pipeline Entrypoint
→ Main Agent → Specialists`，仍然让 Main Agent 留在转发链路上）更保守——
但与批 C-II 已经采用的、比 §18 更严格的立场（`main` 移出转发路径）一致，
不是新裁定，是同一条已有裁定的延伸应用。

> 🔴 **落地修正（G-II 实施时，2026-09-23）**：上面「main 压根不会看到这类请求」
> 这个激进形态**没能落地**，如实记在此。原计划用 `command-dispatch: tool` 让 `/card`
> 绕过 model 直达一个 MCP 工具——但 `command-dispatch` 在建 `toolSchema` 时**够不到
> 会话内才连接**的 MCP stdio 工具（live 报 `Tool not available`；main 在会话里反而
> 调得到）。加上运营者的部署现实（**一个飞书机器人**，`bindings` 按 peer 路由、单个
> 私聊无法按内容分流；**LLM 可用、不追求 0 LLM**），最终落地形态是：**main 认出出卡
> 请求 → 用 shell 跑 `skills/card/scripts/inbound.py`（发起）**。
> ⇒ 这一批真正钉住的不变式**下沉**成更本质的一条：**出卡编排绝不在 agent 会话进程树
> 里跑**（谁按按钮次要，编排在哪跑才是命门）。它靠 `inbound.py` 用 `systemd-run` 把
> `bin/biga-card` 脱离进程树拉起来兑现（entry_guard 判 HUMAN）——与「谁发起」解耦，
> 因此即便 main 的 LLM 发起，19:31/21:03 那种「会话自己拼 ad-hoc spawn 递归出卡」
> 也到不了。全过程与死路的诊断见教程第 37 章 §三/§四。

**`apply_config.py`（未建）要过的 R-2 关卡，机关本身已经在，且已经测过**：
`tools/verify/isolation.py::check_namespaces()` 已经实现"systemd 单元名必须
带 `-biga`"这条判据，`bin/biga`（本仓库内，`~/.openclaw-biga/bin/biga` 软链
到它）已经强制 `--profile biga`。批 G 要做的是**让 `apply_config.py`（未建）
走这条已有的路**，不是新发明一套 R-2 检查。

**六个指纹只有一个是真的**：`contract_version`（`CONTRACT_VERSION =
"contract/1"`，`verdict_ref.py`）已经存在，但落在**每条 `VerdictRef`** 的
粒度上，不是一条独立的按次运行记录。其余五个（`git_commit`/
`openclaw_version`/`agent_config_hash`/`tool_policy_hash`/`prompt_hash`）
在代码里零出现，纯粹是这条 bullet 的愿望，没有任何列或表在等着它们。

**`tools.deny:[ask_user]` 这条今天可以做了，半年前不行**：`stall_watchdog.py`
的注释显式记录过一次已经做过的调查——配置级 `tools.deny` 的粒度是按 agent，
当时 `main` **同时是**交互入口和出卡入口，禁了 `ask_user` 会连累飞书/TUI
的正常交互，所以否决了这个方案。批 C-II 之后 `main` 已经不再是出卡入口
（`DecisionOrchestrator` 直接驱动 Stage 1-3），这条否决的前提已经不成立——
但这只是从两个独立事实推出来的，仓库里没有地方明说，也没有测试钉住
"main 确实不会被这条配置误伤"。⇒ 批 G 落地时**必须补一条测试**验证这一点，
不能只凭推理就认为安全。

#### 设计方向：按外部材料自己的分阶段建议拆批，不要一次把四个阶段都做完

外部材料自己的建议（§25）是 Outbound Only → Preflight Interaction →
Inbound Trigger → 可选的 Question Bridge，风险依次升高。这与本项目一贯的
"低风险基础批 + 高风险切换批"分批法（C/D/E/J 都这么拆过）吻合，建议照此
拆成两批，**不要在一批里从零建到 Inbound Trigger**：

* **批 G-I（Outbound Only，低风险）**：`notification_outbox` 新表 + `RunState`
  加 `NOTIFICATION_PENDING`、worker 投递、幂等键 `(event_type, aggregate)`。
  只做"Card 完成/UNKNOWN/Risk BLOCK/运行失败"四类通知**推送**出去，**不接受
  任何飞书方向的输入**——没有新的攻击面，也不涉及 R-2。这一批单独就有交付
  价值（人不用再守着终端等卡跑完）。
* **批 G-II（Inbound Trigger，高风险）**：真正关掉 `TODO.md` 那条待裁定——
  异步执行模型（快速 ACK + 后台完成，`bin/biga-card` 今天没有这个半成品，
  是这一批要新建的基础设施）、`decision_ids.trigger_id` 真正接上生产方、
  **出卡编排脱离 main 进程树**（`inbound.py` 用 `systemd-run`，main 只发起——
  见上「落地修正」）、`deploy/openclaw/` 配置即代码 + R-2 合规、`tools.deny`
  那条测试。**这是这一批的核心交付物，风险与批 E-III/J-II 同一量级**（动的是
  "钱怎么被花掉"这条链的入口，不是内部实现）。

**明确不做**：Preflight Interaction（阶段二）与 Question Bridge（阶段四）
都推迟——外部材料自己说阶段四"只有出现真实需求后才实现"，阶段二在没有
阶段三之前没有意义。六个指纹里剩下的五个（`contract_version` 之外）不在
这两批的范围内，它们没有已证明的消费方（L-1），留给以后真正需要审计这些
维度的时候再建。

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

### 仍然未验的部分 —— ✅ 批 C-I 已全部测掉（2026-09-22）

| 项 | 状态 |
|---|---|
| 五个并行 fan-out（同一个 `groupId`） | ✅ 实测**峰值同时 5/5 在 RUNNING**（真并行，非排队）。`adapter_spike.py parallel` |
| grant 在 780s 长跑里的稳定性 | ✅ 实测等 780s 后同一 grant 复验仍可用、`.mcp.json` 退出即删。`adapter_spike.py grant-longevity` |
| `sessions_spawn` 失败/超时的结构化错误面 | ✅ 不存在的 agent → `{status:forbidden}` → 翻成 `SpawnStartError`。`adapter_spike.py failure` |

批 C-I 把这三项从「没测过」变成「测过」，落成 `skills/_runtime/` 的
`OpenClawRuntimeAdapter`。`adapter_spike.py` 可在运行时升级后重跑复验 ——
这三条 API 约束哪条破了，当场就知道。

⚠️ 顺带实测出一条 spike 没提的 cancel 约束：运行时取消只认
`subagents.tasks[].taskId`（传 runId / taskName 都被 `Task outside session
tree` 拒），且 `active[]` 顺序不是 spawn 顺序 —— 详见 `architecture.md` §3.3.1
与教程第 23 章。

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

### 8.1 批 H-I 落地后的设计探活：H-II 该做什么（2026-09-24）

批 H-I 只搬了 `_contract`/`_store`/`_sources`——本节自己说得很清楚，`_runtime`/
`_snapshot` 往哪迁、`application`/`integrations`/`cli` 三个命名空间装什么，
当时**没讨论过**。轮到 H-II 时先探活这四件事，而不是直接照 §29 的目录骨架
硬填，结论是：**H-II 本身还要再拆一层**，理由与 H 拆成 H-I/H-II 完全一样——
四件事里只有两件现在就有把握做，另外两件仍然没有设计依据。

**`_runtime` → `runtime`：有把握，规模比 H-I 任何一个包都小。**
`adapter.py`（352 行）+ `mcp.py`（253 行），真实消费方确认
（`market_calc`/`sector_calc`/`technical_calc`/`orchestrator.py`/
`adapter_spike.py`）。探活确认三处 H-I 撞过的坑，这里**都不存在**：
零 `__file__` 用法（不会有 `db.py`/`tradetime.py` 那种深度陷阱）；只有 1 处
按子模块路径直接导入（`test_runtime_adapter.py` 的 `from _runtime.mcp
import`，H-I 是 10+ 处）；零处 AST 守卫硬编码 `skills/_runtime` 路径（H-I
有 4 处 `CONTRACT_DIR`/`STORE_DIR` 字面量，这里一个都没有，因为「唯一实现」
类守卫本来就只盯 `_contract`/`_store`，跟 `_runtime` 无关）。教程只有第 23
章一处指针要追加（H-I 是 15 章）。`pyproject.toml` 的 `pythonpath` 已经在
H-I 挂了 `src/`，薄壳写法直接照抄 H-I 定的两种形状（包级壳自挂
`sys.path`、子模块壳 `sys.modules[__name__] = 真实模块`），**不需要重新
设计可达性机制**。

**`_snapshot` → `application`：有把握，但要先证明它属于这一层。**
`coordinator.py`（266 行）同时 `from _contract import`、`from _sources
import`、`from _store import` 三个 H-I 已迁移的包——它不产生新的域类型、
不打外部接口、不建新表，**协调另外三层**（先经 providers 抓一次、用 domain
类型登记、落 persistence）——这正是 §29 骨架里 `application/` 那三个示意
文件共同的角色：跨层协调，不是某一层本身。
本仓库**从未存在**过叫 `state_machine.py` 或 `card_builder.py` 的文件，那两个只是 §29 骨架的示意名。
第三个示意文件 `orchestrator.py` 真实存在，见下段——但它不在 H-II 范围内。
`SnapshotCoordinator` 本身零 `__file__`、零直接子模块导入、零硬编码守卫路径、只有第 25 章一处教程指针。

**`application/` 目前唯一有资格放进去的是 `SnapshotCoordinator`，不是
`orchestrator.py`。** §29 的骨架示意稿把 `orchestrator.py` 也画进
`application/`，但探活发现：真实的 `orchestrator.py`（29597 字节，
`DecisionOrchestrator`）活在 `skills/decision-card/scripts/` 里，是
**decision-card 这一个 skill 自己的编排脚本**，从来不是一个被多个 skill
共同消费的共享包——跟 H-I 的三个包（第二个消费方出现才抽取，裁定 15 的
同源理由）完全不是一回事。H-I 自己的缓解表已经裁定「skill 目录结构保持
不动」，`decision-card/scripts/` 整个目录（含 `orchestrator.py` 也含
`feishu_deliverer.py`/`notify_worker.py`/`budget.py`/`entry_guard.py` 等
十余个脚本）不在这次搬迁范围内——现在把 `orchestrator.py` 单独挖出来放进
`application/`，既违反那条已裁定的边界，也没有第二个消费方能证明"这该是
共享包"。

**`integrations/`（Feishu）与 `cli/` 目前没有设计依据，理由与
`orchestrator.py` 完全相同。** `feishu_deliverer.py`/`notify_worker.py`
同样活在 `skills/decision-card/scripts/` 里，只被 `decision-card` 一个
skill 消费；仓库里也没有独立于各 skill `scripts/` 之外的「cli 层」——
`bin/biga-card`/`bin/biga-notify` 是 bash 派发脚本，直接调进各 skill 的
`scripts/*.py`，不是一层可以被搬迁的 Python 包。硬建这两个命名空间，
放的东西只能是从 `decision-card` 挖出来的孤例——不是「按需创建」，是
「造一个只有一个消费方的共享包」，跟 L-1 反的是同一条。

⇒ **H-II 收窄成**：只搬 `_runtime` → `runtime`、`_snapshot` →
`application`（两个已经是共享包、已有多消费方、跟 H-I 三个包同一形状的
东西）。`integrations`/`cli`，以及把 `orchestrator.py` 之类 skill 内部
脚本挖出来的问题，继续留白，改称 **H-III**——等 `decision-card` 之外
真的出现第二个需要飞书投递/需要独立 cli 层的 skill，那一刻才是抽取的
时刻，现在不是。

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

---

## 14. H 节 Baseline 冻结（2026-09-24/25）

追加 6 的表格里「H | 全部 8 项 Baseline 冻结」原来判 ❌，理由是"前序阶段
没有全部完成"。E/F（当前必需部分）/G 三节此后陆续做完（TODO.md 有完整
记录，追加 6 表格对应行已同步更正），前提条件不再成立——8 项逐条做完：

| # | 项 | 结果 |
|---|---|---|
| 1 | 创建 `v1-architecture-baseline` tag | ✅ 打在本节所在的这次提交上 |
| 2 | Schema 版本说明 | ✅ [`docs/guide/schema-rollback.md`](../guide/schema-rollback.md)——17 个版本各一句话，权威说明仍在 `schema.py` 逐条迁移体注释里，这份文档只提供入口，不重复叙事 |
| 3 | Migration 回滚说明 | ✅ 同上一份文档。老实记录了一个真缺口：**没有自动化的迁移前快照机制**，回滚今天只能靠文件级备份；17 条迁移全是 additive 这一点让"不备份也大体能退"成立，但那是运气不是设计 |
| 4 | 记录 OpenClaw Version | ✅ `OpenClaw 2026.9.5 (ec9c1a1)` |
| 5 | 记录 Tool Policy Hash | ✅ `sha256:536212eafaedb6177b5325bef17267f5e9edb04a0299452ed5786fbf0f0eafd1` |
| 6 | 记录 Agent Config Hash | ✅ `sha256:d72fcc48fc78b4a7c691dbbd98baa67c6e75a4d197715c3d511e1473c44aa169` |
| 7 | 更新 Architecture 文档 | ✅ `architecture.md` 现状描述与 §12 路线图同批更新，见下一行 |
| 8 | 更新 Roadmap | ✅ `architecture.md` §12——Phase 2 标记基本完成，Phase 3 范围吸收了这份 remediation checklist 自己列的"完成后再进入"清单（Dataset/Provider Registry、EOD、Emotion 统一迁移、Screening、Review、更多 Cron/systemd、Web） |

🔴 **4/5/6 三项为什么是 hash，不是原文**：`openclaw.json` 的 `agents` 子树
全是真实 `$HOME` 绝对路径，公开仓库纪律不允许抄进文档。计算方法固定在
`tools/verify/config_baseline.py`（`tests/test_config_baseline.py` 钉住：
同一份逻辑配置换个用户名跑，hash 必须不变——否则这个 hash 只能证明"在
这台机器上没变"，证不了"配置本身没变"）：

```bash
python3 tools/verify/config_baseline.py
```

✅ **G 节独立 sign-off 已由用户另开的新会话完成**（见追加 6 表格 G 行、
`TODO.md`、`CHANGELOG.md` 对应条目）——本节 H 节 8 项产出本身不依赖那次
复核是否已经发生，两者可以并行，但记在这里避免读者以为还悬而未决。
