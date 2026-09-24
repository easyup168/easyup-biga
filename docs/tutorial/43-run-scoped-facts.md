# 第 43 章 · Fact 的身份换成「哪次执行」（外部评审 B 部分收官）

> 📄 **过程** · 写完即冻结
> **覆盖**：B 节剩下的 4 项（B-2/B-3/B-4/B-5）+ 评审 §7.2 第四条，以及**同批**
> 把批 M 那道 C-1 守卫的判据收窄 —— 加约束与拆守卫必须一起做 ｜
> **不覆盖**：评审 A/E/F/G/H 各节（见 `TODO.md`）；B 节到此收官

## 目标 / 产出

| 产出 | 内容 |
|---|---|
| **schema v17** | `ux_fact_per_task_agent` → `ux_fact_per_run_agent` + `ux_legacy_fact_per_task_agent` |
| `load_verdict_ids_for_run()` | 取代按决策号聚合的 `latest_verdict_ids()`（后者已退役，AST 扫描钉住） |
| `save_verdict(run_id=…)` | 否则这条路径落的行归不到任何一次执行尝试 |
| 在线卡 `input_verdict_refs` 必填 | 评审 §7.2 第四条 |
| **C-1 守卫判据收窄** | 从「有 fact 就拒」→「已出过卡就拒」 |
| `tests/test_run_scoped_facts.py` | 18 条探针，9 处 sabotage |

## 为什么这么做

### 1. 一句话：约束说的和该说的，不是一回事了

v11 的 `ux_fact_per_task_agent` 说的是「**一个决策**只能有一份 market 事实」。
真正该成立的是「**一次执行尝试**只能有一份」。

在「一个决策只有一个 run」的年代两句话等价，所以没人看得出差别。批 M 的 C-1
（Trigger 失败终态可重新拉起）**造出了第二个 run**，等价关系当场断掉 —— 而约束
不会因此报错，它只是开始拦错东西。

> 通用原则：**唯一约束表达的是「什么是同一个东西」。**
> 当系统里"同一个东西"的定义变了（decision → run），约束必须跟着改；
> 不改的话它仍然生效、仍然不报错，只是在守一件已经不成立的事。

### 2. 为什么必须 DROP 掉旧那条，而不是「再加一条」

第一版设计里我想的是「加两条新的，旧那条留着也无妨」。不成立：旧那条按
`(task_id, agent)` 管**全部** fact 行，第二个 run 的合法写入照样被它拦下 ——
新加的两条完全不起作用，整批等于没做。

探针 `test_v11那条旧索引已经不在了` 专门钉这一条，sabotage（把 DROP 注释掉）
会让它和「两个 run 各自落 fact」一起变红。

⚠️ DROP 掉它**不是**「改已发布的迁移」：`_V11` 的迁移体一字未动，v17 是用
DROP+CREATE 表达一次演进。回头去改 `_V11` 的 SQL 才是错的 —— 那会让已经迁移过的库
和新建的库长得不一样，而且演进史就此消失。

### 3. 为什么是两条分区索引，不是一条

迁移前有一批 `run_id IS NULL` 的 fact 行（实测生产库 2 条：手工跑 skill 落的、
v10 之前的）。只建 `(run_id, agent)` 的话，对它们退化成 `(NULL, agent)` ——
而 **SQLite 里多行 NULL 不算重复**。

结果是：这批历史行**完全失去唯一约束保护**，v11 当初堵上的那个洞（同一
`(task_id, agent)` 静默产生两份并存原件）对它们重新打开，而且不会有任何报错。

```sql
run_id IS NOT NULL  →  UNIQUE(run_id, agent)
run_id IS NULL      →  UNIQUE(task_id, agent)
```

> 🔴 **「NULL 不算重复」是 SQLite 唯一约束最容易踩的一脚。**
> 它让一条写错的约束**看起来在工作**：建得起来、平时不报错，只在你最需要它的
> 那一类行上静默失效。判据必须有一条反向探针（这里是 `test_历史行仍按task_id管唯一`）。

### 4. 拆守卫：这一批最值得记的一件事

上一批把 C-1 收窄成「`latest_verdict_ids(decision_id)` 非空就拒绝自动重放」。
在当时那是**唯一正确**的选择。B-2 之后前提变了：第二个 run 写自己的 fact 合法、
读也只读得到自己的 —— 继续「有 fact 就拒」会拒掉一次本来安全的重放，把 C-1 想修的
永久中毒原样退回来。

真正剩下的硬约束是 `ux_decision_online`（一个决策只能有一张在线卡）。
⇒ 判据从「这个号名下有没有 fact」收窄成「**这个号出没出过卡**」。**不是删掉守卫。**

> 🔴 **加约束和拆守卫必须在同一次改动里看见。**
> 分开做的失败是**静默**的：旧守卫有自己的测试，测试照样全绿（它测的是旧判据），
> 只有产品行为悄悄退回去。没有任何东西会报红。

对应那条测试的**结论被翻了过来**，翻的理由写进了 docstring —— 一条测试从「断言 A」
改成「断言 not A」，如果没写清为什么，三个月后没人敢动它。

### 5. 退役要用 AST 扫描，不能用文本匹配

`latest_verdict_ids` 退役的判据是「全仓没有残留引用」。第一版用文本匹配，当场把
**叙述性文字**判成违例 —— `card.py` 的 docstring 里写着「它取代了谁」，`db.py` 的
新函数里写着「为什么换掉它」。

按文本扫的话，唯一的过法是**把说明删掉**：拿「为什么这么改」换一个绿灯。
改用 AST 只认真正的 `Name`/`Attribute`/`ImportFrom` 引用。

> 通用原则：**守卫误伤文档时，先怀疑守卫的判据，不是先删文档。**

## 执行

```bash
# 迁移在生产库副本上先验证
cp ~/.openclaw-biga/workspace/data/biga.db /tmp/prod17.db
python3 -c "import sys;sys.path.insert(0,'skills');from _store import init_schema;init_schema('/tmp/prod17.db')"
```

```
  ✅ 迁移成功
  agent_verdicts 行数: 350
  两条新索引: ['ux_fact_per_run_agent', 'ux_legacy_fact_per_task_agent']
```

## 坑

1. **测试夹具共用一个 run_id ⇒ 第二个决策号撞唯一约束。** `(run_id, agent)` 唯一之后，
   一个固定的 `TEST_RUN_ID` 给两个决策号各落一份 market fact 就会炸 —— 而红的是
   **夹具**不是被测代码，最难查的那种。修法是 `run_id_for(decision_id)` 从决策号
   派生一个确定的 run，`provenance_for()` 统一出口。

2. **`save_verdict` 压根没有 `run_id` 参数。** 只有 `save_fact_bundle` 有（批 J-I 的
   设计：run_id 只从 fact 路径带下来）。于是 `test_orchestrator.py` 的假 Adapter 落的
   Stage 1 原件全是 `run_id=NULL`，按 run 取一条也看不见。补上参数之后，假 Adapter
   还得**知道** run_id —— 它从 `attach` 的 session_key（`agent:main:orchestrator-<run_id>`）
   里学，和真 Adapter 同一个来源。

3. **`input_verdict_refs` 进必填，一次打掉约 40 个测试夹具。** 这是批 N 当时把它留到
   下一批的真实成本。解法不是逐个改调用点（38 处），而是给 `_provenance.py` 加一个
   `provenance_for()` 一次备齐三件套，再让各文件的卡构造器读一个由 `db` fixture
   填的模块级路径 —— 构造器是纯函数、拿不到 fixture，这是本仓库里少数几处
   「模块级可变状态」值得用的地方。

4. **sabotage 脚本里 `-k` 过滤器静默选中 0 条，报成「守卫没起作用」。** 第 42 章记的是
   「sabotage 前先提交」，这次是另一个形状：**破坏打上了、测试没跑到**。单独复跑一次
   才确认那道守卫其实是红的。⇒ sabotage 的输出里要能看出「跑了几条」，不能只看「红了几条」。

## 验证

```bash
python3 -m pytest -q tests/test_run_scoped_facts.py      # 本批探针 18 条
python3 -m pytest -q                                      # 全量
tools/verify/audit_public.sh --worktree
```

9 处 sabotage 逐一确认变红（记录见 CHANGELOG 批 O）。

## 本章要点

| 要点 | 一句话 |
|---|---|
| 唯一约束表达「什么是同一个东西」 | 系统里那个定义变了（decision → run），约束不改会继续生效、继续不报错、只是守错了东西 |
| 旧约束必须 DROP，不能「再加一条」 | 它管着全部行，留着就把新约束架空，整批等于没做 |
| SQLite 多行 NULL 不算重复 | 一条写错的分区约束看起来在工作，只在最需要它的那类行上静默失效 —— 必须有反向探针 |
| 加约束与拆守卫同批做 | 分开做的失败是静默的：旧守卫的测试照样绿，只有产品行为退回去 |
| 翻结论的测试要写清为什么 | 从「断言 A」改成「断言 not A」，不写理由三个月后没人敢动 |
| 退役用 AST 不用文本匹配 | 文本匹配会把「说明它被谁取代」判成违例，唯一过法是删说明 —— 拿理由换绿灯 |
| sabotage 要能看出跑了几条 | 只看「红了几条」的话，`-k` 选中 0 条会被读成「守卫没起作用」 |
