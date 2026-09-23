# 第 30 章 · 迁最后一个 Specialist（risk）并退役旧修订路径 —— 收官这一批，机械的部分最不重要

> 📚 **过程** · 写完即冻结（后续变动只在末尾追加「⏩」指针）
> **覆盖**：确定性编排批 E-III —— 为什么 risk 的迁移不能只测「FactBundle 迁成功」就停；
> VETO（否决）怎么从 `AgentAssessment` 穿透到 `DecisionCard` 真正拦 BUY 的那一层；
> 「退役 `amend_verdict.py`」到底退的是什么、`save_verdict` 为什么不能跟着删；
> risk 的 AGENTS.md 为什么修法跟 market 像、但结论不能照搬
> **不覆盖**：新三型本身怎么设计（第 27 章）；前五个的机械迁移（第 27 / 29 章）

---

## 目标 / 产出

- `risk_check.py`：`build_verdict→build_fact_bundle`，产 `FactBundle`。六个 Specialist
  至此全部产 `FactBundle`。
- `amend_verdict.py`：退役操作合体 `AgentVerdict` 的旧路径（删 74 行），`_assess_fact()`
  保留、并扩成也拒 `--verdict`。`save_verdict()` **不删**。
- `agents/risk/AGENTS.md`：删掉 `--verdict … --stance` 组合命令，caveat 走自由文本。
- `tests/test_facts_split_e3.py`：五道探针（P1–P5，P2 = VETO 穿透）+ 三道红灯。

---

## 为什么这么做

### 六个里最危险的那一个，恰恰是形状最普通的那一个

risk 的迁移，代码上跟 market/sector 一模一样：换返回类型、换保存函数、三个 `return`
点从 `AgentVerdict` 改成 `FactBundle`（其中两条 `status='failed'` 的早退路径也一起改）。
读 diff 你会觉得这是这一批里最无聊的一次迁移。

但 risk 有一样别人没有的东西：它的 stance 是 `VETO_STANCE`（`"否决"`）——

> 整个系统里**唯一**一个 Agent 能单方面拦住 BUY 的信号。

前五个迁移里，stance 迁错了，后果是「这句判断不准」。risk 迁错了，后果是
**「一个真该被拦的决策放行了」**。而放行与拦截的日志长得一模一样（R-3 反复讲的那个
失败形状）——错了不会有人当场发现。

所以这一批的核心交付物不是「FactBundle 迁成功」，是一句更硬的话：

> 否决必须能从 `AgentAssessment` 一路穿透到 `DecisionCard` **真正拦截它的那一层**。

### 「看起来机械」正是要格外小心的信号

迁移前，risk 产合体 `AgentVerdict(stance="否决")`，`DecisionCard` 读 `v.stance` 拦 BUY。
迁移后，否决拆成两半：risk 产 `FactBundle`（无 stance），Risk Agent 事后用
`amend_verdict.py --stance 否决` 追加一条 `AgentAssessment`。那么 `DecisionCard` 还读得到
否决吗？

追下去会发现一条**已经存在、不需要改**的链：

```
FactBundle + AgentAssessment(否决)
   → save_fact_bundle + save_assessment（各落一行）
   → card_ops.load_verdicts_and_refs() 里的 load_verdict()（多态）
   → load_outcome() 拼成 AgentOutcome → to_agent_verdict() 压回 AgentVerdict(stance="否决")
   → DecisionCard.__post_init__: blockers = [v for v in verdicts if v.stance == VETO_STANCE]
   → 拒 BUY、且必须 AVOID/BLOCK
```

每一环都是前几批建好的：`load_verdict` 的多态是 E-I 建的，`card_ops` 用它是一直就有的。
所以 risk 迁完之后，**这一批一行拦截代码都不用改** —— 否决自动就穿透了。

这恰恰是最危险的地方。「不用改代码」很容易变成「不用测」。而这条链有五环，任何一环
悄悄断了（比如某天有人「优化」`to_agent_verdict` 忘了带 stance），否决就**静默失效** ——
BUY 照样出，卡面看起来一切正常。

⇒ 所以 P2 不能断在「stance 字段等于否决」。它必须断到**消费方真正用否决做判断的地方**：
构造一个 risk 否决，走完整条链喂给 `DecisionCard`，断言给 BUY 时它**真的抛错拦下**。

> 通用原则：一条「不需要改代码」的迁移，验收标准不是「代码没动所以没事」，是
> **把那条你依赖它没断的链，真的从头到尾跑一遍、并弄断它确认会红**。

### 红灯证明这条链是真的（而不是凑巧绿）

P2 的红灯演练把 `to_agent_verdict()` 里的 `stance=self.stance` 改成 `stance=None` ——
只断这一环。结果「否决真的拦住 BUY」那条探针报 **`DID NOT RAISE`**：否决没传到，
BUY 没被拦。这证明 P2 真的挂在整条穿透链上：穿透断了它就红，不是「反正 stance 变量
写着否决所以永远绿」。

### 「退役 `amend_verdict.py`」退的是什么

六个 skill 全产 `FactBundle` 之后，`amend_verdict.py` 里那条操作合体 `AgentVerdict`
的老路（复制原件 + 改字段 + `save_verdict(amends=…)`）**再没有活的产出方会喂给它**。
它退役了 —— 删掉那 74 行，历史合体行改成只读（对它跑 amend 明确报「已退役」）。

但有两样东西**不能**跟着删，各有各的理由：

| 东西 | 为什么留 |
|---|---|
| `_assess_fact()`（fact 行加 `--stance`） | 六个 Specialist 以后**永远**走这一条。这是补丁路径最终的样子，不是被退役的对象 |
| `_store.save_verdict()` | 七个测试文件 + `phase1_acceptance.py` 还靠它造**老形状**的数据，来测 `LegacyAdapter` 读路径宽。退役的是「活的 skill 还在写这个形状」，不是「这个形状不该再被测试到」 |

> 🔴 「退役一个功能」和「删掉它依赖的底层函数」是两件事。`amend_verdict.py` 不再 import
> `save_verdict`，但 `save_verdict` 本身留在 `_store` —— 因为「还能不能读回老数据」这件事
> 永远需要能**造出**老数据来测。把底层函数一起删，是把「读路径宽」这条保证的测试地基
> 也拆了。

### risk 的 AGENTS.md：修法像 market，结论不能照搬

E-II 修 market 的 AGENTS.md 时，把 `--add-missing market.trend.no_history --verdict WARNING`
整条删了，因为「趋势」是范围外的判断边界。risk 看起来是同一个形状（也有个
`--verdict UNKNOWN --stance 无法判定` 的旧命令），但**先查了真实数据库**才动手 ——
分发提示词特意叮嘱「别假设它跟 market 同款」。

查出来：risk 历史上 `--add-missing` 只有两个码，`risk.coverage.insufficient` 和
`risk.upstream.trade_date_inconsistent`，**都是 skill 自己已经报的**（后者甚至同名同码）。
所以 risk 没有 market 那种「范围外判断边界」，它的旧 `--add-missing` 纯粹是在重复 skill。

那 `--verdict UNKNOWN` 呢？关键在一条契约细节：`check_stance_vs_verdict` **只**禁止
「UNKNOWN 的事实上挂方向判断」，反过来 **`无法判定` 可以挂在任何 verdict 上**。而覆盖
不足时 skill 报的是 **WARNING**（核心字段齐、只是覆盖不全）。所以 Agent 给
`--stance 无法判定` 挂在这个 WARNING 上，契约层完全接受 —— 根本不需要 `--verdict UNKNOWN`
把完整度事后改成 UNKNOWN。那条 `--verdict` 和 market 的一样，是 agent 越权改 skill 算的
完整度，正是批 E 要焊死的缝。

⇒ 删 `--verdict`，agent 只给 `--stance 无法判定`；上游矛盾这类它自己看出来的东西，
写进回答的「依据 / 未被审阅的面」自由文本。

---

## 执行

```bash
# 1. 查真实数据库：risk 历史上到底用 --add-missing 补过什么（不照搬 market 的结论）
python3 - <<'PY'
import sqlite3, json
con = sqlite3.connect("file:data/biga.db?mode=ro", uri=True)
# 对每条 risk 修订，算相对原件新增的 missing code —— 只有两个，且都已被 skill 自报
PY

# 2. risk_check.py 机械迁移（三个 return 点）
# 3. amend_verdict.py 退役旧路径（199 → 125 行），_assess_fact 扩成也拒 --verdict
# 4. agents/risk/AGENTS.md 删 --verdict 组合命令

# 5. 探针 + 红灯
python3 -m pytest tests/test_facts_split_e3.py -q      # 11 passed
# 3 道红灯（改 to_agent_verdict / risk return / 退役分支）逐个见红后 cp 还原

# 6. 回归 + 计数 + 回放
python3 -m pytest -q                                    # 全绿
bash tools/verify/sync_test_count.sh
bin/biga-card --check <一个已有决策号>                   # 组装一致
```

---

## 坑

### 坑 1 · 手写 CLI 冒烟测试差点误报「fact 行加 stance 失败」

收尾时想快跑一条冒烟：造一个 fact 行、subprocess 跑 `amend --stance`，结果 `rc=2`。
一瞬间以为迁移把 fact 行的正常路径也弄坏了。

真相是冒烟脚本的 bug：`save_fact_bundle(fb)` 没传 `path=`、父进程也没设 `BIGA_DB_PATH`，
于是**存进了默认库**，而 subprocess 带着 `BIGA_DB_PATH=<tmp库>` 去读 —— 那个 id 在 tmp
库里根本不存在，报的是「找不到，是不是加了 --no-store」。设对了库，三条冒烟全绿。

> 通用原则：subprocess 跨进程测 CLI 时，父进程造数据用的库和子进程读的库必须是**同一个**。
> `BIGA_DB_PATH` 要么两边都设、要么 `save_*(path=…)` 显式传。真实测试靠 `db` fixture
> （`monkeypatch.setenv` + `path=db`）避开了这个坑，是手写脚本图快才踩的。

### 坑 2 · `--verdict` 在 fact 行上原本是**静默忽略**，不是报错

E-II 的 `_assess_fact` 只拒 `--add-missing`/`--add-warning`。`--verdict UNKNOWN --stance X`
在 fact 行上会**创建 assessment、悄悄丢掉 `--verdict`**（rc=0）。这一批把 `--verdict` 也
加进拒绝条件 —— 否则一个照抄旧命令的 agent 会以为自己降级了 verdict，其实没有。
「静默成功」比「明确报错」更坏，因为它让人以为做成了一件根本没做的事。

---

## 验证

```bash
cd ~/.openclaw-biga/workspace

# 五道探针全绿（P2 = VETO 穿透）
python3 -m pytest tests/test_facts_split_e3.py -q          # 期望 11 passed

# 六个 skill 全部产 FactBundle（一个 AgentVerdict 产出方都不剩）
grep -L "build_fact_bundle" skills/*/scripts/*_calc.py skills/risk-check/scripts/risk_check.py

# 退役干净：amend 里没有 dataclasses / save_verdict 的调用
python3 - <<'PY'
import ast
t = ast.parse(open("skills/decision-card/scripts/amend_verdict.py").read())
called = {n.func.id for n in ast.walk(t) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
assert "save_verdict" not in called and "save_assessment" in called
print("✅ 旧路径已退役，_assess_fact 保留")
PY

# 全套回归 + 回放一致
python3 -m pytest -q
bin/biga-card --check <决策号>
```

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | risk 的迁移代码上跟前五个一模一样，但它的 stance 是 `VETO_STANCE` —— 迁错的后果是「真该被拦的决策放行了」，不是「一句话不准」 |
| 2 | 核心交付物是 **VETO 穿透验证**，不是「FactBundle 迁成功」：否决要从 `AgentAssessment` 穿到 `DecisionCard` 真正拦 BUY 的那一层 |
| 3 | 这条穿透链（load_verdict 多态 → to_agent_verdict → card 读 .stance）是前几批建好的，**这一批一行拦截代码不用改** —— 而「不用改」正是最该测的地方 |
| 4 | P2 断到消费方真拦 BUY，不断「stance==否决」；红灯把 `to_agent_verdict` 的 stance 丢成 None，BUY 就不被拦（`DID NOT RAISE`），证明链是真的 |
| 5 | 「退役 `amend_verdict.py`」退的是操作合体 AgentVerdict 的老路（74 行）；`_assess_fact` 保留（六个以后都走它） |
| 6 | 🔴 退役旧路径 ≠ 删 `save_verdict`：七个测试还靠它造老形状测 `LegacyAdapter` 读路径宽。删了底层函数 = 拆了「读路径宽」的测试地基 |
| 7 | risk 的 AGENTS.md 修法像 market 但**先查了库**才动：risk 的旧 `--add-missing` 都已被 skill 自报，没有 market 那种范围外判断边界 |
| 8 | `无法判定` 允许挂在 WARNING 上（`check_stance_vs_verdict` 只禁 UNKNOWN 上的方向判断）⇒ risk 不再需要 `--verdict` 事后降级完整度 |
| 9 | `--verdict` 在 fact 行上原本静默忽略；这一批把它也加进拒绝，因为「静默成功」比「明确报错」更坏 |
