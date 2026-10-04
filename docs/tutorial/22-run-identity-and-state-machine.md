# 第 22 章 · 运行身份与状态机

> 📚 **过程** · 写完即冻结（后续变动只在末尾追加「⏩」指针）
> **覆盖**：确定性编排批 B —— 为什么把一个 `decision_id` 拆成四个身份；
> 状态机为什么事件溯源；CAS 怎么落在唯一约束上；13 个状态凭什么是 13 个
> **不覆盖**：Orchestrator（批 C）、SnapshotCoordinator（批 D）——
> 本章只建**身份与状态机**这层地基，还没有程序驱动流程

---

## 目标 / 产出

做完这一章，仓库里多了：

- `skills/_contract/run.py` —— `RunContext` 值对象 + 13 个状态 + 合法转移图
- schema **v7** —— `decision_runs` / `run_events` / `evidence_sets` 三张表
- `skills/_store/runs.py` —— `open_run()` / `transition()` / `run_journey()`
- `bin/biga-card --status <run_id>` —— 一条命令说出「这次运行死在哪一步」

而**出卡的行为一个字节都没变**：`bin/biga-card` 仍走老路径，只是顺带记一笔账。

---

## 为什么这么做

### 一个 id 扛五件事

批 B 之前，整个系统只有 `decision_id` 一个身份。它被迫同时回答五个不同的问题，
而这五个问题的生命周期根本不一样。三次实测事故，全长在这个根上：

| 想回答的问题 | 只有 `decision_id` 时的后果 |
|---|---|
| 这是同一次外部请求吗？ | 飞书事件会**重投**，没有幂等键 ⇒ 重投 = 重跑一次决策、再花一次钱 |
| 这是决策的第几次执行尝试？ | 硬超时收掉一次、重试一次，两次尝试挤在同一个 `decision_id` 上，事后**分不开** |
| 这几个 Specialist 看的是同一份数据吗？ | 没有 `evidence_set_id`，这句话**无法验证** |

> 🔴 **一个标识符如果回答不止一个问题，它迟早会在某个问题上给出错误答案** ——
> 而且是静默的，因为另外几个问题它答得都对。

拆开：

```
trigger  一次外部请求（飞书消息 / CLI / cron）—— 幂等键
  └─ decision  一次业务决策
       ├─ run A  一次执行尝试 ── evidence_set  一片冻结数据
       ├─ run B（重试）
       └─ run C（回放）
```

这一批只建 `trigger_id` / `decision_id` / `run_id` / `evidence_set_id` 四个身份的
**载体**（`RunContext` + 三张表）。真正用它们驱动流程是批 C。

### 状态放哪：一个 `state` 列，还是事件序列？

第一直觉是给 `decision_runs` 加一个 `state` 列，`transition()` 就是
`UPDATE decision_runs SET state=? WHERE ...`。但这撞上一条本仓库的硬规矩：

> **只追加，由触发器强制**（L-8）—— 状态被原地 `UPDATE` 之后，
> 「当时看到的是什么」就永久不可重建了。

而 `decision_runs` 建表时就带了只追加触发器（这一点后面会解释为什么**必须**当场带）。
一张只追加的表，上面没有可以反复 `UPDATE` 的 `state` 列。

两条路：

| 方案 | 问题 |
|---|---|
| `decision_runs.state` 可变 + 触发器只保护别的列 | 触发器是**整表**语义，做不到「这列能改那列不能」；而且「状态能原地改」本身就违反 L-8 |
| **状态事件溯源**：`run_events` 每转移一次追加一行，当前状态 = 最新一行的 `to_state` | ✅ 采用 |

事件溯源顺带白送了批 B 判据要的东西：**一条运行的 `run_events` 序列可以完整复述
它走过的路**。`--status` 不用 grep 日志，直接读这张表。

### CAS 落在唯一约束上，不落在「先查再写」上

`transition(run_id, expected, next)` 是 compare-and-set：只有当前状态**正好是**
`expected` 时才推进。天真的写法是：

```python
cur = SELECT 最新状态
if cur != expected: 拒绝
INSERT 新状态
```

这中间有一个竞态窗口 —— 两个并发转移都读到 `expected`、都通过判断、都 INSERT。
本仓库在**占号**（`decision_ids`）上已经踩过一模一样的坑，结论写在那次的代码里：

> 🔴 **主键/唯一约束冲突是唯一可靠的并发仲裁。「先查再写」永远有窗口。**

所以真正的仲裁不是那个 `if`，是 `run_events` 上的 `UNIQUE(run_id, seq)`：
每个 run 内 `seq` 从 1 单调递增，转移就是 `INSERT seq=当前+1`。两个并发转移都
算出 `seq=N+1`、都去 INSERT，唯一约束**只让一个落地**，另一个撞约束报错。

那个 `if cur != expected` 仍然留着 —— 它挡的是**回退**和 **expected 传错**
（顺序错误），不是并发。两道各管一件事。

### 13 个状态，不是 15 个

外部评审列了 15 个状态。照抄进来很省事，但本仓库最贵的一课是
**L-1：零消费方组件**——建了没人用的东西，它不会报错，只会让人以为「这件事有人管了」。

逐个问「谁会写它、谁会读它」，两个状态答不上来：

- `IDENTITY_RESERVED` —— 占号发生在预检里，它和 `PREFLIGHTED` 是**同一瞬间**，
  没有代码能单独进入它。
- `SNAPSHOT_COLLECTING` —— 它是 `PREFLIGHTED → SNAPSHOT_FROZEN` 转移的中间态，无人读。

还有一个 `NOTIFICATION_PENDING`：它的消费方是 outbox worker，而 outbox 要到批 G 才建。
**在它的消费方存在之前登记它，就是提前造一条死配置。** ⇒ 推到批 G。

⇒ 13 个。而且这个「13」本身要被钉住，否则下一个人又会顺手加一个：

```python
# tests/test_run_state_machine.py
assert RUN_STATES == {13 个具体名字}
```

> 🔴 **「一个不多」这句话，只有写成一条会红的测试才算数。**
> 写在注释里的「请勿新增状态」拦不住任何人。

### 每个状态都要能指出「谁写它、谁读它」

这是把上一节的原则做成结构性守卫，分两半：

- **谁写它**（可达性）：从 `RECEIVED` 出发，沿合法转移图 BFS，必须能走到每一个状态。
  走不到 = 没有任何转移序列能产生它 = 它永远不会出现。
- **谁读它**（消费方）：批 B 里所有状态的通用读取方是 `biga-card --status`，
  它的知识就是 `run_ledger.py` 里的 `STATE_MEANING`。一个状态若不在
  `STATE_MEANING` 里，`--status` 就说不清它 —— 那它就是个没有读取方的状态。

🔴 **`STATE_MEANING` 放在消费方（`run_ledger.py`）那一侧，不放契约层。**
如果把「每个状态的含义」也塞进定义状态的那个文件，检查就变成
「定义方自己声明自己被读了」——什么都没验。放在消费方，
「加了状态但没人读」才会被 `set(STATE_MEANING) == RUN_STATES` 当场抓到。

> 通用原则：**验「有没有消费方」的判据，必须从消费方那一侧取材料。**
> 从生产方取，它永远会说「我被消费了」。

### `decision_runs.decision_id` 为什么可空

这条最反直觉。一个 run 属于一个 decision，`decision_id` 怎么会空？

因为批 B 的 `bin/biga-card` 走的是**老路径**：它 spawn 一个 LLM，占号发生在
那个 LLM 的 Stage 0 里，`bin/biga-card` 自己是靠 poll `MAX(decision_id)` 才
**发现**新号的。也就是说，run 开始的那一刻（`RECEIVED`），号还不存在。

能不能让 `bin/biga-card` 提前占号？能，但那会改占号逻辑和出卡流程 ——
而批 B 的硬约束正是「**不改行为、不改占号逻辑**」。

⇒ run 头开在占号之前，`decision_id` 留空；等卡落库、发现了号，把它记进
`CARD_PERSISTED` 事件的 `detail`。批 C 的 Orchestrator 会在开 run 之前就占号，
那时它非空。这是「新旧并存」的典型形态：新机制先建起来，老路径以它能诚实
提供的粒度接入，不为了好看而假装知道自己不知道的东西。

### 记账为什么是 best-effort

`bin/biga-card` 里每一处 run 记账（`_move`）都吞掉自己的错误，绝不让记账失败
影响出卡的退出码/流程/成本。因为「不改行为」是硬约束。

代价是：记账要是静默失效了，没有告警。这值得吗？值得——因为 run 记录是
**可观测数据**，不是安全守卫。它坏了，`--status` 查不到这次运行，仅此而已；
不会 fail-open 放过一笔不该放的交易。而它的**正确性**由测试保证（测试直接驱动
`run_ledger` / `_store.runs`，不依赖 live 出卡路径）。

> 通用原则：**best-effort 只配给可观测数据，不配给守卫。**
> 分不清的时候，问「它失效时，是少看见一件事，还是放过一件坏事」。

---

## 执行

### 状态机长什么样

```
$ python3 skills/decision-card/scripts/run_ledger.py open --origin cli
21b14508132a4551b0462bf88cb30f3d

$ RL="python3 skills/decision-card/scripts/run_ledger.py"
$ $RL move 21b1… --expect RECEIVED --to PREFLIGHTED
$ $RL move 21b1… --expect PREFLIGHTED --to CARD_PERSISTED --detail '{"decision_id":"BIGA-20260922-007"}'
$ $RL move 21b1… --expect CARD_PERSISTED --to COMPLETED
```

非法转移当场抛错，并把「合法的下一步」告诉你：

```
$ $RL move 21b1… --expect COMPLETED --to FAILED
COMPLETED → FAILED 不是合法转移。
  从 COMPLETED 出发合法的下一步：（无——它是终态）
  跳步 / 回退 / 乱跳都在这里被挡（设计文档 §5 的状态机）。
```

### `--status`：死在哪一步

```
$ bin/biga-card --status 3f4a0abfacf441f5948717aed1cc081c
run     3f4a0abfacf441f5948717aed1cc081c
decision （legacy 路径，占号在 LLM 那侧，见下方事件 detail）
trigger  cli-20260922T145727-7e268e0d   origin=cli   non_interactive=True
created  2026-09-22T14:57:27.932615+08:00

走过的路：
  [ 1] 14:57:27  · → RECEIVED
  [ 2] 14:57:27  RECEIVED → PREFLIGHTED
  [ 3] 14:57:28  PREFLIGHTED → INPUT_REQUIRED   {"tool": "ask_user"}

当前：INPUT_REQUIRED  —— 🔴 死在这一步
      卡在非交互 ask_user 上，需要人去看提示词（退出码 5）。
```

---

## 坑

### 建表顺序：被引用的表要先出现

`decision_runs.evidence_set_id` 外键指向 `evidence_sets`，`run_events.run_id`
指向 `decision_runs`。第一版按「主角先写」的顺序把 `decision_runs` 写在最前，
`evidence_sets` 垫底。SQLite 对 `CREATE TABLE` 里的前向引用其实是容忍的（外键
只在 DML 时校验），但**没必要留这个隐患**。⇒ 按依赖倒序建：
`evidence_sets` → `decision_runs` → `run_events`。

### 设计文档点名文件，要带 `skills/` 前缀

在 `architecture.md` §5.3.4 写落点时，我写的是 `_contract/run.py`。
`test_设计文档点名的文件必须真实存在` 当场报红——它把这个字符串当成从仓库根
出发的路径去 `exists()`，而文件在 `skills/_contract/run.py`。

```
architecture.md 点名了不存在的文件：[(549, '_contract/run.py'), (551, '_store/runs.py')]
```

这条守卫是对的（防「文档说有、其实没有」的幽灵文件）。改法是把路径写全。

> 这一条和第 20、21 章遇到的是同一类：**守卫用陈述句校验文档，文档就得用
> 它认得的形式说话。** 不是守卫太死，是文档在偷懒。

### 并发要排队，不要「database is locked」

CAS 的仲裁应该发生在 `UNIQUE(run_id, seq)` 上（一个赢、一个撞约束）。但 SQLite
默认的忙等超时是 0——两个写者一相遇，晚到的那个立刻拿到 `database is locked`，
根本走不到 INSERT，也就走不到那个唯一约束。⇒ 在写路径显式
`PRAGMA busy_timeout=5000`，让晚到的排队等前一个提交，再去撞约束、干净地失败。

---

## 验证

```bash
# 1. 全套测试绿（批 B 之后 899 条）
python3 -m pytest -q

# 2. 状态机的四道探针都见过红（把守的东西弄坏 → 报红 → 还原）：
#    P1 并发同转移只有一个成功         —— tests/test_run_state_machine.py::TestConcurrentCAS
#    P2 只追加：UPDATE/DELETE 被触发器拒 —— ::TestAppendOnly
#    P3 拆掉 CAS 仲裁 → P1 变红（手工探针，见 CHANGELOG）
#    P4 无消费方的状态被抓到           —— ::TestWhoReadsIt::test_每个状态都有消费方

# 3. --status 能复述一条运行走过的路（用一次性库，别写真库）
export BIGA_DB_PATH=$(mktemp -d)/t.db
RID=$(python3 skills/decision-card/scripts/run_ledger.py open --origin cli)
python3 skills/decision-card/scripts/run_ledger.py move "$RID" --expect RECEIVED --to PREFLIGHTED
bin/biga-card --status "$RID"   # 预期：当前 PREFLIGHTED
unset BIGA_DB_PATH
```

预期：`pytest` 899 绿；四道探针在被弄坏时确实报红；`--status` 打印出事件序列。

🔴 **手工探针一律跑在一次性库上**（`BIGA_DB_PATH` 指向临时文件）——
`_store` 是追加式的，写错的行没有 `DELETE` 也没有 `UPDATE`，会永久留在真库里。

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 一个标识符回答不止一个问题，迟早在某个问题上静默答错 —— `decision_id` 扛五件事，三次事故同根 |
| 2 | 只追加的表上没有可反复 `UPDATE` 的 `state` 列 ⇒ 状态**事件溯源**，当前状态 = 最新一行 |
| 3 | CAS 的并发仲裁落在 `UNIQUE(run_id, seq)`，不落在「先查再写」——后者永远有窗口（占号那次已证） |
| 4 | 状态数「一个不多」只有写成会红的测试才算数；`IDENTITY_RESERVED` 之类无写无读的状态就是死配置 |
| 5 | 「谁读它」的判据要从**消费方**那侧取材料（`STATE_MEANING` 在 `run_ledger`，不在契约层） |
| 6 | 新旧并存时，老路径以它能**诚实观测**的粒度接入，不伪造它没看见的中间态（`decision_id` 宁可留空） |
| 7 | best-effort 只配给可观测数据，不配给守卫 —— 分不清就问「失效时是少看见一件事，还是放过一件坏事」 |
| 8 | 三张新表**建表即带只追加触发器**（F1：v4 漏过一次，代价是身份机制建在可撤销的地基上） |

> ⏩ **后续变动（2026-09-23，批 H-I）**：本章出现的 `skills/_contract` / `skills/_store` /
> `skills/_sources` 三个共享包，其**真实实现**已迁至
> `src/easyup_biga/{domain,persistence,providers}/`。旧路径原地保留 re-export 薄壳 ⇒
> 本章正文里的 `from _contract import ...` 等导入语句与位置描述**照旧成立**，只是代码
> 本体不在那儿了。见 `CHANGELOG.md` 批 H-I。
