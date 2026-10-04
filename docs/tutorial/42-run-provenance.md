# 第 42 章 · Run Provenance（外部评审 B 部分，前 9 项）

> 📄 **过程** · 写完即冻结
> **覆盖**：把「这张卡属于哪次执行、基于哪份冻结切片、用了哪些判定原件」从
> 「解开 card_json 能看到」变成「一句 SQL 能答 + 写边界会拒」——schema v16 三列
> 一索引、契约层血缘检查、库层 ref 严格核对、在线落库必填 ｜
> **不覆盖**：B 节的 B-2/B-3/B-4/B-5（Fact 唯一约束改 `(run_id, agent)`、废弃
> `latest_verdict_ids`）——与同期另一条在途修复正面冲突，理由见「为什么这么做」
> 第 4 节；评审 A/E/F/G/H 各节（见 `TODO.md`）

## 目标 / 产出

外部评审 B 节（Run Provenance Closure）共 13 项。本章做掉 9 项，产出：

| 产出 | 是什么 |
|---|---|
| schema **v16** | `decision_records.run_id` / `.evidence_set_id`、`agent_runs.orchestration_run_id`、`ux_evidence_set_per_run` |
| `DecisionCard.evidence_set_id` | 卡自己记住"看的是哪份切片"——因为没有别的地方记得住 |
| `_check_run_provenance()` | 契约层：ref 必须恰好覆盖每条判定 + 每条 ref 的 run 必须等于卡的 run |
| `verify_verdict_refs()` 补两道 | 库层：原件的 `task_id`/`run_id` 必须与卡对得上 |
| 在线落库必填 | `replay_of is None` 时缺 run_id/evidence_set_id 一律拒绝 |
| `tests/test_run_provenance.py` | 25 条探针，11 处 sabotage 验证 |

## 为什么这么做

### 1. B 不是「可以缓的加固」——它是一个已经发货的改动的前置条件

评审 §6.3 用一段话提前写出了一个**当时还没发生**的 bug：

> 当前 Fact 唯一约束仍可能是 `UNIQUE(task_id, agent)`，这会导致：同一个 Decision
> 的第二个 Run 无法保存同一 Agent 的新 Fact。于是形成：读取时可能混入旧 Run，
> **写入时又可能阻止新 Run 正常产出**。

上一批（批 M）的 C-1 让飞书 Trigger 在"已是失败终态"时可以重新拉起，安全论证是
**"这两种场景都还没写过 fact"**。对抗性复核用真实 PoC 把它打穿了：

```
[2] Stage 1 落下 market 的 fact verdict_ref=1
[3] run 收成 TIMEOUT（TIMEOUT ∈ NOTIFY_FAILURE_STATES）
[4] 重投 accepted=True   launcher 调用两次，decision_id 同一个
[5] 💥 [market] 决策 BIGA-20260924-001 已经有一份 fact 原件了
```

`LEGAL_TRANSITIONS` 允许**任何**在途状态直接进 FAILED/TIMEOUT/CANCELLED，包括
`STAGE1_COMPLETED`——那时五份 fact 早就落库了。

> 🔴 **教训：安全论证不要建在"状态名字长这样"上。**
> 状态名是**推断**，"这个决策号名下有没有 fact"是**事实**。
> 能直接查事实的时候，不要用状态名去推。

而真正的后果比撞约束更坏。第二轮的五个 Specialist 全部落不下 fact，但编排器
**不会因此停**：`latest_verdict_ids(did)` 按 `task_id` 查，查到的仍是上一轮遗留的
旧 ref，非空 ⇒ 零证据那道 fail-fast 不触发 ⇒ 用几小时前的证据合成一张
`generated_at` 是现在的卡。spawn 核验照样通过（它核的是这一轮真的 spawn 过，
不是证据新鲜度）。

> 从「看得出卡住了」变成「看不出任何问题」——这正是本项目最优先防范的形状。

### 2. 没照抄评审的 `NOT NULL`——先读生产库，再决定约束强度

评审给的是 `decision_records.run_id NOT NULL`、`evidence_sets.run_id NOT NULL UNIQUE`。
照抄之前先只读查了一遍生产库：

```
decision_records   43 张在线卡，只有  6 张的 card_json 带 run_id
agent_runs        166 行，只有 36 行有 runtime_run_id
evidence_sets       7 行，  1 行 run_id 为空
```

这些行是迁移之前落的，它们**确实不知道**自己属于哪次 run。两条路都比现状糟：
`NOT NULL` 会让迁移当场失败；先回填再加约束等于**给历史数据编一个当时并不存在的
答案**（L-8：raw/历史永不改写）。

⇒ 列可空、不回填，**必填由写边界强制**。档位与 `_check_identity` 那条三段式完全
一致：**读可以宽，写必须严**。

> 通用原则：**约束该放在哪一层，取决于存量数据长什么样，不取决于设计文档写得多漂亮。**
> 同一句话（"这个字段必须有"）在 schema 层是"历史读不出来"，在写边界是"以后不许再犯"。

迁移在**生产库副本**上实跑过一遍（复制到临时目录再 migrate），确认 44/166/7/338/7
行一行不少、新列全 NULL、唯一索引建起来了——同 v11「加索引前先确认存量干净」的先例。

### 3. 分区索引：一条语义错了但不会报错的约束

`UNIQUE(run_id)` 写上去**不会失败**——SQLite 里多行 NULL 不算重复。但它声称的是
"没有 run_id 的切片也受唯一约束"，而那恰恰是唯一不该被约束的一类。

```sql
CREATE UNIQUE INDEX ux_evidence_set_per_run
    ON evidence_sets(run_id) WHERE run_id IS NOT NULL;
```

> 🔴 **"加上去不报错"不等于"加对了"。**
> 这类约束的验证必须包含一条反向探针：`run_id IS NULL` 的两行能不能并存。
> 只测"重复会被拦"的话，不带 `WHERE` 的版本也能过——那条测试证明不了分区子句存在。

### 4. 为什么 B-2/B-3/B-4/B-5 有意不做

同期另一条在途修复把 C-1 收窄成：`latest_verdict_ids(decision_id)` 非空就**拒绝
自动重放**、交回人工。在唯一约束还是 `(task_id, agent)` 的前提下，那是当下**唯一
正确**的选择（fail-closed）。

但 B-2 把唯一约束改成 `(run_id, agent)` 之后前提就变了：第二个 run 本来就能写自己
的 fact，重放是安全的 —— 那道守卫会变成"拒绝一次本来安全的重放"，把 C-1 想修的
永久中毒**原样退回来**。而 B-4 要删掉的 `latest_verdict_ids`，正是那道守卫刚刚
成为第 4 个调用方的函数。

> 🔴 **加约束和拆守卫必须在同一次改动里看见。**
> 分开做就会变成一边加、一边忘了拆 —— 而那种失败是**静默**的：守卫有自己的测试，
> 测试照样绿，只有产品行为悄悄退回去了。

同理留到下一批的还有评审 §7.2 第四条"在线卡 `input_verdict_refs` 不许为空"：
它会同时废掉 `card_ops.synthesize(verdict_refs=None)` 这条**文档里明确允许**的
旧调用路径，属于产品决策不是纯加固；而它要防的危险情形（引用了错的原件）已经由
契约层那道覆盖检查挡住了。

### 5. 判据取库里那一行，不取卡上那份拷贝

`_check_identity` 比的是 `AgentVerdict.task_id` —— 卡上那份**拷贝**里的值。
`verify_verdict_refs` 新加的两道比的是 `agent_verdicts.task_id` / `.run_id` ——
**库里那一行**的值。两者可以不一致：拷贝是合成时序列化进 `card_json` 的。

> 通用原则：**只核对被验证方自己出具的那份数据，等于没核对。**
> 这与 `spawn_check.py` 不信 `agent_runs`、只信运行时自己的 `subagent_runs`
> 是同一条（"被验证方控制不到的地方，才算证据"）。

## 执行

```bash
# 迁移在生产库副本上先验证（绝不直接动真库）
cp ~/.openclaw-biga/workspace/data/biga.db /tmp/prodcopy.db
python3 -c "import sys;sys.path.insert(0,'skills');from _store import init_schema;init_schema('/tmp/prodcopy.db')"
```

```
=== 迁移后：数据没少 ===
  decision_records       44 行
  agent_runs            166 行
  evidence_sets           7 行
  agent_verdicts        338 行
  decision_runs           7 行
=== 新列全是 NULL（未回填，L-8）===
  decision_records.run_id 非空: 0
  agent_runs.orchestration_run_id 非空: 0
```

## 坑

1. **🔴 sabotage 脚本用 `git checkout --` 还原，把没提交的工作一起删了。**
   本批的改动全在工作区未提交，而 `git checkout -- <file>` 还原的是 **HEAD**，
   不是"sabotage 之前"。一轮 11 处 sabotage 跑完，5 个文件被回滚到上一个 commit，
   schema v16、契约检查、库层核对、CLI 参数、守卫扩展全部丢失，只能照着补丁脚本
   重新打一遍。
   ⇒ 教训：**sabotage 之前先 commit，或者用文件副本还原，不要用 `git checkout`。**
   第二轮改成 `cp` 到临时目录再 `cp` 回来，同样的脚本结构，不会误伤。

2. **sabotage 本身写错，报了一次假阴性。** 第一轮 S2 把
   `lack = [n for n, v in (...) if not v]` 改成 `lack = [] or [n for n, v in (("run_id", None),...)]`
   —— `[] or [...]` 求值成后面那个非空列表，守卫照样拒绝，测试照样绿，于是"这道
   守卫没被验证到"被记成了"这道守卫不起作用"。改成 `lack = []` 才是真 fail-open。
   ⚠️ 这是第 40 章「坑 2」的同一个形状**又踩了一次**：破坏点要确认**真的把行为改了**，
   不能只看代码改了。

3. **新测试文件撞上三道仓库自己的守卫。** `import sqlite3`（I-4：一切 DB 访问走
   `_store`）、`base = dict(decision_id=..., status=...)`（铁律 4：手搓字典版契约，
   要加 `# contract-exempt:` 注释）、以及连带的 `test_scan_fallback`。
   ⇒ 这是好事：**新加组件要向一批自己没写的守卫报到**（第 38 章同款教训）。

4. **`save_verdict()` 没有 `run_id` 参数。** 只有 `save_fact_bundle()` 有——批 J-I
   的设计就是"run_id 只从 fact 路径带下来，assessment 从它 amends 的 fact 行继承"。
   测试里想造一条带 run_id 的原件，必须走 FactBundle，不能走 AgentVerdict。

## 验证

```bash
# 本批探针（25 条）
python3 -m pytest -q tests/test_run_provenance.py

# 受影响的既有文件
python3 -m pytest -q tests/test_store.py tests/test_decision_card.py \
  tests/test_notification_outbox.py tests/test_write_boundary.py \
  tests/test_verdict_provenance.py tests/test_spawn_proof.py \
  tests/test_decision_id_ownership.py

# 全量
python3 -m pytest -q

# 公开仓库审查
tools/verify/audit_public.sh --worktree
```

每一处守卫都做过 sabotage 验证（改坏被守的东西 ⇒ 必须变红），11 处全部确认。

## 本章要点

| 要点 | 一句话 |
|---|---|
| 安全论证别建在状态名上 | 状态名是推断，"这个号名下有没有 fact"是事实——能查事实就别推 |
| 约束强度先读存量再定 | 评审写 `NOT NULL`，生产库只有 6/43 行有值——照抄会让迁移当场失败 |
| 可空列 + 写边界必填 | 读可以宽、写必须严；回填历史 = 给它编一个当时不存在的答案 |
| 分区唯一索引 | 不加 `WHERE ... IS NOT NULL` 也不会报错，只是语义错了——要有反向探针 |
| 判据取库里那行 | 只核对被验证方自己出具的拷贝，等于没核对 |
| 加约束与拆守卫同批做 | 分开做的失败是静默的：测试照样绿，只有产品行为退回去 |
| 🔴 sabotage 前先提交 | `git checkout --` 还原到 HEAD，会把未提交的工作一起删掉 |
| sabotage 要确认真改了行为 | `[] or [...]` 那种"改了但没改到"会把假阴性记成结论 |
