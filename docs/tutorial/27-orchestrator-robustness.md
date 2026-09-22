# 第 27 章 · Orchestrator 健壮性四处收尾

> 📚 **过程** · 写完即冻结（后续变动只在末尾追加「⏩」指针）
> **覆盖**：确定性编排批 C-III —— 一份外部架构复审里「值得马上修」的四条独立小
> 修复（落库顺序、部分失败的取消、引用的 agent 核对、总预算收窄）；以及本项目
> **第一次两批真正并行**时，为什么在独立 git worktree 上开工
> **不覆盖**：那四条之外的评审断言（哪些不做、为什么，见设计文档 §2 追加 5）；
> Facts/Assessment 拆分（批 E，与本批并行）；budget/flock 从 bash 挪进 Python（§26，
> 另一批）；stale-run reaper 的调度（批 G）

---

## 目标 / 产出

- `orchestrator.py` 四处（其实是三处 + 一处在 `db.py`）：
  1. `CARD_PERSISTED` 转移挪到 `persist()` 成功之后，`detail` 带真实 `record_id`
  2. Stage 1 部分启动失败时，对已启动的兄弟 handle 逐个 `cancel()`
  3. 各阶段 `ad.wait()` 按总 `deadline` 剩余收窄（`_stage_timeout`）
- `_store/db.py`：`verify_verdict_refs()` 补 `ref.agent == 存量.agent` 核对
- 四道探针 P1–P4 全见过红并已还原；离线全绿 1005 → 1016
  （+7 条 C-III 探针/单测，+4 条**写这一章本身**带出的参数化 docs 测试，见「坑」）
- 教程这一章 + CHANGELOG + TODO 台账

---

## 为什么这么做

### 先讲一件和代码无关、却是本章最值得看的事：为什么开了个 worktree

粘完「通用前置」的环境自检，第四条就红了：

```
git status --short   # 期望干净（DREAMS.md 可忽略）
```

工作区不干净 —— 而且脏的不是 `DREAMS.md`。`_contract/facts.py`（新）、
`verdict.py`（+215）、`_store/db.py`（+192）、外加四个 Specialist skill，都躺在那里
未提交。一跑 `pytest`，红 25 条。

这是**批 E-I 的活。** C-III 的分发提示词第一句就写着「可与批 E-I 并行开工」——
也就是说，另一个会话正在这棵树上做 E-I（Facts/Assessment 契约拆分），它的半成品
就在我脚下。

通用前置那句「任何一项不符就停下来查清楚，不要往下做」在这里不是形式主义。摆在
面前有三条路：

| 选项 | 代价 |
|---|---|
| 就在这棵树里做 | pytest 红 25 条 ⇒ C-III 的「全绿才算完成」验收闸**根本达不成**；提交时还得手挑文件，`db.py` 两批都改，挑不干净 |
| 先把 E-I stash/commit 掉 | 那是**另一个活着的会话**的未提交状态。从外面动它的文件，如果它接下来还要写，极可能对不上 —— 这类「影响别处正在跑的状态」的操作本就该避免 |
| **在干净的 HEAD 上开 worktree** | E-I 的 WIP 原地不动；C-III 拿到绿色基线；两批真正并行，做完各自合回 |

选第三条。`git worktree add -b c-iii .claude/worktrees/c-iii 2e5e8ea`，会话切进去，
`pytest` 立刻是绿的。

> 通用原则：一棵工作树是一个会话的**私有草稿**。两个会话要并行，就各给一棵树，
> 不要在对方的草稿上落笔 —— 哪怕只是「顺手 stash 一下」。

这也是本项目从 A 到 E-I 第一次真的两批同时在跑。之前每一批开工前，上一批都已经
提交合并了（串行）。所以「以前没用过 worktree」不代表这次选它不对 —— 是**情况本身
第一次变了**。

⚠️ 一个容易过度自信的地方：我核对过 C-III 与 E-I 只在 `db.py` 相交，且区域不交叠
（E-I 加新函数在 ~L98–204，C3-3 改 `verify_verdict_refs` 在 L543）。但这是**今天这次
diff 快照**的结论，不是合并时的保证。两支合回 `orchestration` 时得重新核对，不能凭
现在这张快照就认定安全。

### C3-1 · 一条假的「已落库」记录，比没有记录更糟

原来的顺序是这样：

```python
state = self._to(..., RunState.CARD_PERSISTED, detail={"record": "persisting"})
card_ops.persist(card)          # ← 如果这里抛错？
self._to(..., RunState.COMPLETED)
```

`persist()` 抛错时，`run_events` 里已经留下一条 `CARD_PERSISTED` —— 一条说「这张卡
落库了」的记录，而库里其实什么都没有。

关键在于 **`run_events` 是只追加表**（批 B 建表时就带了只追加触发器）。这条假记录
删不掉、改不掉。于是 `bin/biga-card --status` 从此对着这次运行说假话，而且是**永久**
说假话。

> 一个本来可修复的失败（落库抛错，重跑就是），被记成了一条不可修复的谎。

修法只是换个顺序 —— 先 `persist()`、成功拿到 `record_id`，再转移，`detail` 里带真实
`record_id`（不是占位串）。副作用发生在**记录它之前**，记录才不会撒谎。

> 通用原则：先做副作用，成功了再记「它做成了」。反过来写，副作用一失败，那条记录
> 就成了删不掉的假证据 —— 尤其当记录落在只追加存储里。

### C3-2 · `cancel()` 建好三批了，这是它第一个真调用方

Stage 1 的五路 fan-out 原来是一句列表推导：

```python
handles = [ad.start(a, ...) for a in STAGE1_AGENTS]
```

第三个 `start()` 抛错，Python 会把整个推导式的中间结果一起丢掉 —— 前两个已经
**成功起来**的 Specialist 会话，没人管了。它们会一直跑到各自的 `runTimeoutSeconds`
才被运行时收掉。白烧钱。

`OpenClawRuntimeAdapter.cancel()` 在批 C-I 就建好了，但批 C-I / D-I / D-II 三次评审
都在同一处记着同一句话：它**没有真实调用方**，只在 N=2、无 drain 的 spike 里验过。
一条没有调用方的方法，比没有它更糟 —— 它占着「这件事有人管了」的位置。

这一批的 Stage 1 部分失败，恰好就是那个一直没出现的真调用方（设计文档 §2 追加 5.2
把「修 §28」和「补 cancel() 的残留风险」判定成同一个动作，不是两件事）。改法就是
把列表推导换成显式循环 + 已启动列表，`except` 里逐个取消：

```python
handles: list[SpawnHandle] = []
try:
    for a in STAGE1_AGENTS:
        handles.append(ad.start(a, ...))
except Exception:
    for h in handles:
        with contextlib.suppress(Exception):   # 取消本身失败，别盖住原始异常
            ad.cancel(h)
    raise
```

`contextlib.suppress` 那层不是可有可无：清理阶段里，如果某个 `cancel()` 自己抛了，
它绝不能盖住那个真正让我们走到这里的原始 `start()` 异常 —— 否则排查时看到的是
「取消失败」，而不是「第三个起不来」。

### C3-3 · 「能找到 + hash 对」核不出张冠李戴

`verify_verdict_refs()` 原来核两件事：这个 `verdict_id` 还在不在、它的
`content_sha256` 有没有变。漏了第三件：**这条引用声称的 agent，和它指向那一行的
agent，是不是同一个。**

为什么这是个真洞而不是洁癖：`verdict_id` 是**跨 agent 的全局自增**，不按 agent 分
号段。所以一条手工拼出来的 `VerdictRef` 完全可以声称「这是 market 的原件」，
`verdict_id` 和 `sha` 却全都指向 news 那一行 —— 两者都对得上（因为它真的指向那行），
「能找到 + hash 对」两道检查全过，只有核对 agent 才拦得下。

而 `VerdictRef.agent` 的 docstring 早就写着它「必须与被引用那条 `AgentVerdict.agent`
一致」。也就是说，契约层**声明**了这条不变量，却没有任何地方**强制**它。这一批补上
强制的那一行。

> 通用原则：一个字段的 docstring 说「它必须等于 X」，就得有个地方真的去查它等不等于
> X。否则那句话只是注释，不是不变量 —— 而注释不会在被违反时报错。

### C3-4 · 外层 bash `timeout` 不该是唯一一层防线

三段预算 `stage1_sec` / `risk_sec` / `synth_sec` 原来各读各的环境变量，谁也不知道
总 `deadline_sec` 还剩多少。Stage 1 如果吃满自己那 300s，Risk 和 Synth 仍然各等各的
全额 —— 三段之和可以轻松超过 `deadline_sec`，全靠 `bin/biga-card` 外层那个 bash
`timeout` 硬顶。

那层 bash `timeout` 是**纵深防御的最后一层**，不该是**唯一**一层。改法：`run()` 开头
记一个 `deadline = time.monotonic() + self.deadline_sec`，每段 `ad.wait()` 的超时改成
`min(该段自己的预算, deadline - now)`；剩余 ≤ 0 就不再等，抛错进 FAILED。

> 通用原则：如果某个约束「只有最外层那道硬限兜着」，那它其实只有一层防线。把它下沉
> 到真正做事的那一层，最外层才回到它该有的位置 —— 兜底，而不是主力。

（顺带：`orchestrator.py` 里 `import time` 在这一批之前是**死代码**，从没被用过。
C3-4 第一次真的用上了它。）

### 探针纪律：先证明它会红，还要先证明它命中的是目标条件

四道探针都走「未改代码时报红 → 改后绿 → 已还原」。其中 P3 多加了一步**前置断言**，
这一步是从 A-I 评审栽过的车里学来的（当年一条探针因为标点被归一化，从头到尾没触发
它声称要触发的检查，差点把有效修法误判成无效）。

P3 要证明「只有 agent 核对能抓到伪造的 ref」。所以它先断言：一条**agent 也说 news**
的引用（`verdict_id`/`sha` 全指向同一行）核对是**通过**的。这就证明了伪造的那条
唯一的破绽就是 agent 字段 —— 存在性和 hash 两道检查都不会替它报红，报红的只可能是
新加的那道。不加这一步，P3 可能因为别的原因（比如 sha 恰好也不对）而红，那它就没有
证明它声称要证明的东西。

> 通用原则：探针不仅要「会红」，还要「因为**对的原因**红」。加一条前置断言，把「除了
> 我要测的那处，其它条件都不触发」钉死。

---

## 执行

### 环境自检抓出脏树（这是它该干的事）

```console
$ git status --short
 M skills/_contract/verdict.py
 M skills/_store/db.py
?? skills/_contract/facts.py
 ... （共 17 项，批 E-I 的未提交 WIP）

$ python3 -m pytest -q | tail -3
FAILED tests/test_emotion_calc.py ... （23 条）
FAILED tests/test_docs_convention.py ... （2 条）
```

确认干净 HEAD 是绿的（不动 E-I 的 WIP，用一次性 worktree 验），再正式开 worktree：

```console
$ git worktree add -b c-iii .claude/worktrees/c-iii 2e5e8ea
Preparing worktree (new branch 'c-iii')
HEAD is now at 2e5e8ea ...
```

### 四道探针，未改代码时逐一报红

```console
# P3（C3-3）：伪造 agent，未加核对时 verify 返回 []
$ pytest ...::test_agent对不上时报红
E       assert 0 == 1
E        +  where 0 = len([])

# P1（C3-1）：persist 抛错，未换顺序时假记录已在
$ pytest ...::test_persist抛异常时run_events无CARD_PERSISTED
E       AssertionError: assert 'CARD_PERSISTED' not in ['RECEIVED', ..., 'CARD_PERSISTED', ...]

# P2（C3-2）：第 3 个 start 抛错，列表推导不取消
$ pytest ...::test_stage1中途start失败_已启动的handle被cancel
E       assert [] == [SpawnHandle(...), SpawnHandle(...)]

# P4（C3-4）：总预算 50s，未收窄时 stage1 仍拿 300
$ pytest ...::test_各阶段等待被剩余deadline收窄
E       assert (300 <= 50)
```

改完之后，全绿。测试条数变了，用 `sync_test_count.sh` 同步（不手工 sed，理由见 F22）：

```console
$ bash tools/verify/sync_test_count.sh
▸ 已同步为 1012 条    # ← 这时只加了 7 条 C-III 探针/单测
✅ 守卫通过
```

⚠️ 但这还没完 —— 写完**这一章**再跑，条数又变成 1016（见「坑」）。所以又同步了一次：

```console
$ bash tools/verify/sync_test_count.sh
▸ 已同步为 1016 条
✅ 守卫通过
```

### 三道验收闸

```console
$ python3 -m pytest -q | tail -1
（全绿，1016）

$ BIGA_DB_PATH=<生产库副本> ./bin/biga-card --check BIGA-20260922-001
✅ 组装一致：BIGA-20260922-001 用冻结证据重跑，逐字段相同。

$ tools/verify/audit_public.sh --worktree | tail -1
══ 十一项全绿 ══
```

`--check` 跑的是**生产库的副本**（拷到 scratch），不是生产库本体 —— `--check` 虽是
只读，但拿副本跑对生产零风险，还顺便让 C3-3 的新核对在真实 37 张卡上过了一遍
（1 张带 refs，报 0 问题，零误伤）。

---

## 坑

- **最大的坑不在代码里，在环境里。** 差点就「E-I 的红 25 条先不管，我做我的 C-III」
  往下走了 —— 而那样 C-III 的验收闸根本达不成，且提交时会把 E-I 的半成品扫进来。
  通用前置那条「不符就停下」拦住了它。教训：环境自检红了要**当真**，别急着绕过去。
- **`import time` 是死代码，但没人发现。** 它在 `orchestrator.py` 里挂了好几批没被
  用过，也没有 lint 报它。C3-4 第一次用上它才「合法化」。小提醒：未使用的 import
  是无声的，靠人眼很难发现。
- **P3 差点没证明它要证明的东西。** 第一版 P3 只断言「伪造的 ref 会报红」，没断言
  「同 agent 的 ref 会通过」。那样即使报红，也说不清是 agent 核对抓的、还是别的原因。
  补了前置断言才补严。
- **写这一章本身又加了 4 条测试。** `test_docs_convention.py` 里有一批**按章节
  参数化**的检查（每章一份实例）。新增一章 `27-*.md`，collected 从 1012 跳到 1016 ——
  于是刚同步好的 `README`/`CLAUDE` 徽章又变旧了，`sync_test_count.sh` 得再跑一遍。
  教训：**`sync_test_count.sh` 要在所有会影响 collected 的改动（含新增教程章节）
  都落地之后再跑**，否则跑了也白跑。这也顺带印证了这条守卫为什么不能靠「记得同步」——
  连「加一章文档」都会悄悄改动那个数。

---

## 验证

```bash
cd ~/.openclaw-biga/workspace     # 或本批的 worktree
# 1) 全绿
python3 -m pytest -q | tail -1
# 2) 四道探针对应的测试都在
python3 -m pytest \
  "tests/test_verdict_provenance.py::TestVerifyVerdictRefs::test_agent对不上时报红" \
  "tests/test_orchestrator.py::TestPartialAndFailure::test_persist抛异常时run_events无CARD_PERSISTED" \
  "tests/test_orchestrator.py::TestPartialAndFailure::test_stage1中途start失败_已启动的handle被cancel" \
  "tests/test_orchestrator.py::TestDeadlineBudget::test_各阶段等待被剩余deadline收窄" -q | tail -1
# 3) 回放一致 + 公开审查
BIGA_DB_PATH=<生产库副本> ./bin/biga-card --check <一个已有决策号>
tools/verify/audit_public.sh --worktree | tail -1
```

预期：全绿；四条测试全过；`--check` 报「组装一致」；audit 十一项全绿。

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 两个会话要并行，各给一棵 git worktree；别在对方的未提交草稿上落笔 —— 哪怕只是「顺手 stash」 |
| 2 | 环境自检红了要当真。差点绕过 E-I 的红 25 条往下做，那样 C-III 的验收闸根本达不成 |
| 3 | 「无冲突」是今天这次 diff 快照的结论，不是合并时的保证；合并前重新核对 |
| 4 | 先做副作用、成功了再记「它做成了」；反过来写，副作用一失败就是删不掉的假证据（尤其在只追加表里）|
| 5 | 一条没有真实调用方的方法比没有它更糟 —— 它占着「有人管了」的位置。C3-2 是 `cancel()` 三批以来第一个真调用方 |
| 6 | 清理阶段的失败要 suppress，不能盖住把你带到这里的原始异常 |
| 7 | 字段 docstring 说「必须等于 X」，就得有地方真去查；否则那是注释不是不变量 |
| 8 | 只有最外层硬限兜着的约束，其实只有一层防线；把它下沉，最外层才回到兜底位 |
| 9 | 探针不仅要会红，还要因**对的原因**红 —— 加前置断言，钉死「除目标外其它条件都不触发」|
| 10 | 离线探针能证明「Orchestrator 会调 cancel() 且身份对」，证明不了「真实运行时 N=5/drain 下取消命中对的那一个」—— 后者是 live 补验，别混为一谈 |
