# 第 35 章 · Agent Registry：把 roster 从五处收成一处

> 📁 **过程文档** · 写完即冻结
> **覆盖**：确定性编排批 K —— 把散在**五处**的 Agent 名册（roster）收编成 `_contract`
> 里一份 `AGENT_REGISTRY`，其余全部派生；给 Card 加一个**生成时冻结**的期望 roster
> 字段，让 `absent_agents` 不再现算现取「今天」的名册。重点在三处判断：**派生要照
> 已验证过的内省形状**、**冻结字段与老卡回退怎么共处**、**`skipif` 缺口修到什么程度**。
> **不覆盖**：Dataset / Provider Registry（裁定表明确推迟，装了就是 L-1 死配置）、
> 运行时配置文件的自动生成（越过 R-1）、`discipline` 上线（Phase 3，没有输入源）。

---

## 目标 / 产出

有一次真实的静默事故（2026-09-21）：`news` 进了契约的 Stage 1 名单、agent 也建好了，
但运行时白名单漏了它 ⇒ 只 spawn 了四个，**没有任何报错**，Card 照常产出、只是少了
一个领域；而 `risk` 如实报「Stage 1 缺席：news」，让排查方向天生指向 news 本身。
当时的应对是 `test_roster_matches_config.py` 做数据驱动**对账** —— 对，但那是对账，
不是单一源。做完这一章：

- `skills/_contract/registry.py`：`AGENT_REGISTRY`（一个 agent 一条 `AgentDefinition`：
  `stage` / `spawned` / `reads_snapshot`）是名册的**唯一手写处**。
- `STAGE1_AGENTS` / `STAGE2_AGENTS` / `RISK_AGENT` / `SNAPSHOT_INDEX_AGENTS` /
  `EXPECTED_ROSTER` 全部**从它派生**，不再手写平行元组。
- `tools/verify/adapter_spike.py` 的 `STAGE1` 也迁到 Registry —— 它曾是第五处、且**零
  测试覆盖**。
- `DecisionCard.expected_roster`：合成那一刻冻结进 `card_json`，`absent_agents` 优先读它。
- `test_roster_matches_config.py` 的 `skipif` 缺口按 R-3 收口。

一句话：**这一批不新增能力，是收编** —— 让「名册」这件事只有一个地方能改。

---

## 为什么这么做

### 一、为什么是「一个类 + `vars()` 内省」，不是几个平行元组

收编前，roster 散在五处（开工前的设计探活普查出来的，比原始事故描述更碎）：

| 位置 | 形态 |
|---|---|
| `_contract/verdict.py` `STAGE1_AGENTS`/`STAGE2_AGENTS` | 权威、但含从不 spawn 的 `discipline` |
| `orchestrator.py` `RISK_AGENT` | 独立字面量 `"risk"` |
| `orchestrator.py` `SNAPSHOT_INDEX_AGENTS` | 独立 `frozenset` |
| `verdict.py` `STANCE_VOCAB` 的 key 集 | 只靠一条测试钉住，`absent_agents` 的权威 |
| `tools/verify/adapter_spike.py` `STAGE1` | 独立字面量，**零测试覆盖**（不 import `_contract`、不在 pytest 下跑） |

关键不是「散」，是**没有谁派生谁** —— 五份各写各的，加一个 agent 要五处都记得改，
漏一处不报错。这正是 `dev-workflow` 第五问（「这份清单在别处有没有孪生？谁派生谁？」）
反复踩过的形状。

解法不发明新写法：`skills/_contract/run.py` 的 `RunState → RUN_STATES` 已经验证过一个
形状 —— 一份权威（类属性）+ `frozenset(v for k, v in vars(...) ...)` 内省派生。那个文件
的 docstring 原话就是「两处各写一份清单必然漂」，跟批 K 治的是同一个病。所以
`AGENT_REGISTRY` 照抄它：`AgentRegistry` 类的属性是一条条 `AgentDefinition`，
`AGENT_REGISTRY` 从 `vars()` 派生，STAGE1/STAGE2/… 再从 `AGENT_REGISTRY` 过滤出来。

> 通用原则：**同一份清单要在多处用时，先找仓库里有没有已验证过的「从权威派生」写法，
> 照抄它。** 发明第四种写法，就是给下一个人留第四处会漂的地方。

Python 类的 `__dict__` 自 3.7 起保留定义顺序 ⇒ `vars(AgentRegistry)` 迭代顺序 = 定义
顺序 ⇒ `STAGE1_AGENTS` 的**元素顺序**与旧手写元组逐项一致。这条不是洁癖：探针 P1
就靠它证明「收编是空操作，不是顺手重排了扇出顺序」。

### 二、字段只装「已经有消费方」的 —— 不照外部示意稿装 `required_datasets`

`AgentDefinition` 只有四个字段，每个都有一个**已经在跑**的消费方：`stage` → STAGE1/2；
`spawned` → `RISK_AGENT` 与 `EXPECTED_ROSTER`；`reads_snapshot` → `SNAPSHOT_INDEX_AGENTS`。

外部材料的示意稿里还画了 `required_datasets` 之类字段。**没装** —— 它绑定 Dataset
Registry，而裁定表已明确把 Dataset/Provider Registry 推迟（现在只有一个 dataset 走完
全链，注册表会比被注册的东西还大）。装一个没有消费方的字段，就是一条 L-1 死配置：
写进去没人读，三个月后没人敢删。

### 三、`RISK_AGENT` 为什么是一个会「炸」的纯函数

`RISK_AGENT` = 「Stage 2 里唯一会被 spawn 的那个」。`discipline` 也在 Stage 2，但
`spawned=False`，所以今天恰好只有 `risk` 一个。要是哪天有人把 `discipline` 改成
spawned（Phase 3 接上输入源），「Stage 2 且 spawned」就有两个了 —— 那时 `RISK_AGENT`
静默取第一个，是 R-3（算不准了却给个看似正常的答案）。

所以派生写成 `_sole_spawned_stage2(registry)`：数出来不是恰好一个就**当场抛
`RuntimeError`**，在 `import _contract` 那一刻就炸，强迫改名册的人先想清楚「谁是编排器
`ad.start()` 的那个制衡层入口」。抽成纯函数只为**可测** —— 测试拿一个「两个 spawn 的
Stage 2」假名册喂进去、断言它真抛，而不用在测试里重抄一遍判据（L-3）。

### 四、卡级冻结名单：堵一处「同一张历史卡在不同时间给不同答案」

`DecisionCard.absent_agents` 原来是 `@property`，每次读都用**当下**的名册去减：

```python
# 收编前
return tuple(sorted(set(STANCE_VOCAB) - {v.agent for v in self.verdicts}))
```

问题是隐蔽的：一张三个月前的卡今天 `--show` 重新加载，`absent_agents` 用的是**今天**
的 roster。这期间若 roster 变过（加了 agent、或某个一度下线），**同一张历史卡的这个
字段会在不同时间点给出不同答案，而 `card_json` 本身没变**。普查里还没有实例发生，
但机制上成立 —— 属于「回放/历史悄悄变好看」那一类（教程第 8 章、A1 都栽过同形状）。

⇒ 合成那一刻把当时 `AGENT_REGISTRY` 算出的 `EXPECTED_ROSTER` **冻结进 `card_json`**
（新字段 `expected_roster`）。`absent_agents` 优先读它，只有老卡（字段不存在 ⇒ `None`）
才回退到读今天的 Registry。这个「有就用、缺就退回」的形状不是新发明：J-I 的 `run_id`、
E-I 的 `LegacyAdapter`、J-II 的 `spawn_check` 结构化 join 都是它。

两处细节值得记：

- **老卡不回填**（L-8：raw / 历史永不改写）。老卡就是没有这个字段，靠回退兜底，不事后补写。
- **回放原样带过去，不重算**。`replay.py` 把 `expected_roster=original.expected_roster`
  透传给 `synthesize()`，和 `input_verdict_refs` 的处理**一模一样**：老卡 `None` ⇒ 回放
  也 `None`，两边一致；新卡带着它冻结的那份 ⇒ 回放带同一份。于是 `comparable()` 里这个
  字段两边恒等，`--check` 不会因它误报「组装不一致」。这条是这一批**唯一**能悄悄弄坏
  `bin/biga-card --check` 的地方，所以拿三张真卡验过（见「验证」）。

### 五、`STANCE_VOCAB` 保持独立，只对 Registry 断言子集

`STANCE_VOCAB` 是各 Specialist 的 stance 词表，**不是 roster**。批 K 没有把它并进 Registry：
它由 `TestStanceVocabMatchesContracts` 钉着「key 集 == 已建 specialist」，是另一条独立
约束。批 K 只加一条：`set(STANCE_VOCAB) ⊆ 名册全体`。

为什么是子集、不是相等：`absent_agents` 的权威从「`STANCE_VOCAB` key 集」换成
`EXPECTED_ROSTER`（spawn 的那几个）。这两者今天相等（都是六个 spawn 的 agent，测试钉住），
所以对今天的卡这次换权威是**空操作**。但关键是：`discipline` 在册（`STAGE2_AGENTS` 含它）
却 `spawned=False` ⇒ 不在 `EXPECTED_ROSTER` ⇒ **永不被判「缺席」**。不能因为 Registry
引入、就把 `discipline` 意外带回 `absent_agents` 的权威里 —— 那会让每张卡都常驻一条它的
缺失噪音，正是裁定 13 要防的。探针 P5 专门守这条。

### 六、`skipif` 缺口：修到什么程度（一个范围判断）

分发提示词把这条留给建造会话自己定范围。现状：整个 `TestRosterConsistency` 挂
`@pytest.mark.skipif(not CONFIG.exists())`，fresh clone / CI 上**静默跳过整组** —— 连
**根本不需要配置**的 `test_契约里的stage名单都建好了`（契约名单 vs 已建 agent）都被
一起跳过了。这正是 R-3 想防的形状：算不出来（配置不在）不该悄悄变成「没查出问题」。

范围**有意收窄**（写进了 CHANGELOG）：

- 不需要配置的那半边**照常跑** —— CI 上也拦得住「契约声明了但没建」的漂移。这是收益
  最大、风险最小的一半：把一个被过度 `skipif` 拖累的检查放出来。
- 需要配置的那半边（配置白名单 vs 已建 agent，读的是仓库**外**、人工维护的
  `~/.openclaw-biga/openclaw.json`）在配置缺席时发一条 `RuntimeConfigUnavailable` 警告再
  skip。skip 是诚实的「这台机器上没有那个外部产物」，警告让它**不再静默**（`-q` 的
  warnings summary 也列得出来）。

没有把它改成「配置不在就 fail」—— CI 上本就不该有那份机器专属配置，fail 是把「环境
差异」误报成「代码错误」。R-3 要的是「算不出来要显式说」，不是「算不出来就当出错」。

---

## 执行

派生一致性（P1 的手动版，改代码前先确认收编是空操作）：

```text
$ python3 -c "import sys; sys.path.insert(0,'skills'); import _contract as c; \
    print(c.STAGE1_AGENTS); print(c.STAGE2_AGENTS, c.RISK_AGENT); \
    print(sorted(c.SNAPSHOT_INDEX_AGENTS)); print(c.EXPECTED_ROSTER)"
('market', 'sector', 'news', 'technical', 'emotion')
('risk', 'discipline') risk
['market', 'sector', 'technical']
('market', 'sector', 'news', 'technical', 'emotion', 'risk')
```

全部与收编前的手写字面量逐项相等。

七道探针，每一道把守的东西真弄坏、确认报红、再还原（`tools/verify/` 外的一次性脚本）：

```text
[P1 派生一致性：把 technical 的 reads_snapshot 关掉]      🔴 FAILED …test_SNAPSHOT_INDEX_AGENTS等于旧frozenset
[P2 冻结名单：让 absent_agents 无视冻结字段]              🔴 FAILED …TestP2FrozenRoster
[P3 老卡兼容：去掉 None 兜底，老卡会崩]                   🔴 FAILED …TestP3OldCardCompat
[P4 adapter_spike 迁移：STAGE1 改回独立字面量]           🔴 FAILED …test_adapter_spike的STAGE1派生自contract
[P5 discipline 不进权威：EXPECTED_ROSTER 收全部 agent]   🔴 FAILED …TestP5DisciplineNeverAbsent
[P6 skipif 缺口：配置缺席时静默 skip（删警告）]          🔴 FAILED …test_config缺席时发…警告而非静默skip
[RISK_AGENT fail-closed：discipline 改 spawned=True]     🔴 import 当场炸（RuntimeError）
=== 还原校验 ===  所有被探针改过的文件已逐字节还原 ✅
```

---

## 坑

**坑 1 · 探针自己会有 bug —— 它第一次报「没红」是它自己看错了。**
探针脚本的判据一开始写成 `"failed" in out`（小写），而 pytest 的短摘要打的是大写
`FAILED`。于是六道明明报了红的探针，脚本一律汇报「⚠️ 没红（探针失效！）」。`dev-workflow`
§3 早写过这句：「探针第一次红的时候，先怀疑探针，再怀疑代码」——这次是反过来的同一件事，
先怀疑探针的**判据**，而不是急着改被测代码。判据从 `"failed"` 改成 `"FAILED" or "ERROR"`
（或干脆看返回码），六道全绿地报红。

**坑 2 · P1 探针第一版没打在自己声称的地方 —— 它触发了另一道守卫。**
P1 探针第一版是「把 `news` 从 Stage 1 挪到 Stage 2」。结果 Stage 2 里就有了 `news` 和
`risk` 两个 `spawned=True`，`RISK_AGENT` 的 fail-closed 派生**在 import 时先炸了** ——
整个测试文件 `found no collectors`，而不是 P1 的断言干净地失败。这是好事（说明 fail-closed
守卫是 load-bearing 的），但探针没验到它声称要验的东西（L-13 的形状）。改成「关掉
`technical` 的 `reads_snapshot`」——只动 `SNAPSHOT_INDEX_AGENTS`、不碰 stage/spawned，
P1 的断言这才干净地报红。

**坑 3 · 新建的 `registry.py` 一落地，两条 docs 守卫当场红。**
① `test_每个入口都在设计文档里被提过`：`skills/_*/*.py` 都算「入口」，必须在
`docs/design/` 里被点名并**说清解决什么问题**（不是补一行文件名）。⇒ 在 `architecture.md`
§6.3 的契约模块表里加了一行。② `test_C_不许手搓字典版契约`：测试里我手写了一个 card 形状
的 dict 来模拟「老 card_json」，被铁律 4 的 AST 扫描判成第二套契约。⇒ 改成用真
`DecisionCard(...).to_dict()` 再 `pop("expected_roster")`，不手搓。两条都不是误报，是守卫
在正确地工作。

**坑 4 · 并行批次撞同一棵工作树（第二次）。** 开工不久发现工作树里冒出了不属于这一批的
改动（另一个会话在做批 G-II，动了 `_store/*` 与 `orchestrator.py`）。本项目第一次遇到是
批 C-III（见第 28 章「为什么开 worktree 而不是动对方的未提交草稿」），这是第二次。处理一样：
把批 K 挪进一个独立 `git worktree` 做，先把已经落在共享树上的自己那部分**逐个还原**（尤其
`orchestrator.py` 那处 import：它耦合着还没进共享树的 `registry.py`，不一起还原会让共享树
`ImportError`，直接卡住对方会话），再在 worktree 里从头做。判据：`git diff` 摘要必须干净得
能交给评审，而这在一棵被两个会话同时写的树上做不到。

**坑 5（不是我的，但记一笔）· 一个子集顺序下的既有 flaky 测试。**
用一个手挑的十文件子集跑回归时，`test_orchestrator.py::test_persist抛异常时…` 报
`DID NOT RAISE`。一度怀疑是自己改坏了 —— 但换上**未改动的** `orchestrator.py`（只留
`_contract` 改动）它照样红，再 `git stash -u` 退到**纯净基线**跑同一个子集，还是红。
结论：这是基线上一个**子集顺序依赖**的既有污染（前面某个文件漏了全局状态），**跑全量套件
时不出现**（全量 1198 条全绿）。不是这一批的账，没在这一批动它，记进这里免得下次又查一遍。

> 通用原则：**判断「这次改动是否让套件变红」，要跟纯净基线在同一环境下比**，别拿一个
> 手挑子集的红当自己的锅 —— 子集顺序会放大既有的测试间污染。

---

## 验证

```text
# 1) 派生 == 收编前的手写字面量，且 STANCE_VOCAB ⊆ 名册、discipline 不进权威
$ python3 -m pytest tests/test_agent_registry.py -q
23 passed

# 2) 全量套件（worktree 干净 checkout，不含共享树里那批 gitignore 的外部材料）
$ python3 -m pytest -q
1198 passed

# 3) 七道探针都见过红、且文件逐字节还原 —— 逐条手动弄坏、跑对应测试、还原
#    （批 K 自己的 sabotage 脚本躺在开工会话自己的 scratchpad 里，没进仓库；
#    下面这条是可复现的等价步骤，照「探针记录」表格挨个做）
$ git checkout -- <被改的文件> && python3 -m pytest tests/test_agent_registry.py -q
23 passed   # 每弄坏一处，先看对应测试 FAILED，再还原、确认这里重新变绿

# 4) 三张真卡（都是批 K 之前落库、card_json 里没有 expected_roster）回放逐字段相同
$ BIGA_DB_PATH=<真库副本> bin/biga-card --check BIGA-20260922-001         # 09-22
$ BIGA_DB_PATH=<真库副本> bin/biga-card --check BIGA-20260921-025         # 09-21
$ BIGA_DB_PATH=<真库副本> bin/biga-card --check BIGA-20260921-024         # 09-21
✅ 组装一致：… 用冻结证据重跑，逐字段相同。

# 5) 公开仓库审查十一项
$ tools/verify/audit_public.sh --worktree
══ 十一项全绿 ══
```

预期全部如上。第 4 步用真库的**副本**（`--check` 只读，但副本彻底隔离，且不动正在被别的
会话使用的真库）。

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | roster 收编不是新增能力，是让「名册」只有一个地方能改；派生照抄 `RUN_STATES` 的 `vars()` 内省形状，不发明第四种写法 |
| 2 | 字段只装有消费方的（`stage`/`spawned`/`reads_snapshot`）；`required_datasets` 绑定被推迟的 Dataset Registry，装了就是 L-1 死配置 |
| 3 | `RISK_AGENT` 是会「炸」的纯函数：Stage 2 的 spawn 不是恰好一个就 import 时 `RuntimeError`，逼人想清楚谁是制衡层入口（R-3） |
| 4 | 卡级冻结名单堵「同一张历史卡不同时间给不同答案」：合成时冻进 `card_json`，老卡回退到今天的 Registry（有就用、缺就退回，同 J-I/E-I 形状） |
| 5 | 回放把 `expected_roster` **原样透传**、不重算，和 `input_verdict_refs` 一样 ⇒ `--check` 不会误报组装不一致（拿三张真卡验过） |
| 6 | `STANCE_VOCAB` 保持独立、只对 Registry 断言子集；`discipline` 在册但不 spawn ⇒ 永不进 `absent_agents` 的权威（裁定 13，探针 P5） |
| 7 | `skipif` 缺口按 R-3 收窄：不需配置的半边照常跑，需配置的半边发**可见**警告再 skip；没改成「配置不在就 fail」（那是把环境差异误报成代码错误） |
| 8 | 探针自己会有 bug —— 判据看错大小写、或打在别的守卫上；先怀疑探针的判据，再怀疑被测代码 |
| 9 | 并行批次撞同一棵树 ⇒ 挪进独立 worktree，先干净还原自己在共享树的足迹（尤其耦合着新文件的那处 import），别让对方会话 `ImportError` |
