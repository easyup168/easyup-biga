# 第 27 章 · 把事实和判断拆开（契约基础设施 + 一个试点）

> 📚 **过程** · 写完即冻结（后续变动只在末尾追加「⏩」指针）
> **覆盖**：确定性编排批 E-I —— 为什么 `amend_verdict.py` 的存在本身是「事实与判断
> 焊在一起」的证据；三个新类型怎么分；跨型铁律归谁校验；为什么共用一份铁律实现；
> 为什么消费方零改动（`load_verdict` 多态）；读宽写严；为什么只迁一个试点；顺带还了
> D-II 的账（`Evidence.evidence_set_id`）
> **不覆盖**：迁其余五个 skill（E-II）；退役 `amend_verdict.py`（要等全部迁完）；
> Risk 拆两层（批 F）

---

## 目标 / 产出

- `skills/_contract/facts.py`：`FactBundle` / `AgentAssessment` / `AgentOutcome` + `LegacyAdapter`
- `verdict.py`：抽出 `check_fact_invariants` / `check_stance_vocab` / `check_stance_vs_verdict`
  三个共用校验（`AgentVerdict` 与新类型共用一份，防 L-3）
- `Evidence.evidence_set_id`（还 D-II 的账）；三个日线 skill 读冻结时填上；risk CROSS_CHECK 升级
- 存储：schema v8 加 `kind` 列，新旧同住 `agent_verdicts`；`save_fact_bundle` / `save_assessment`
  / `load_outcome`；`load_verdict` 变多态
- 试点 `emotion` 迁到新三型；其余五个 skill 一字未改
- 全套离线测试全绿

---

## 为什么这么做

### `amend_verdict.py` 的存在，就是那条断层的证据

`AgentVerdict` 把两件东西焊在一个 frozen dataclass 里：

- **事实**（skill 算的：`result`/`evidence`/`missing`/`status`/`verdict`）—— skill 跑完就有；
- **判断**（`stance`）—— skill 跑完之后，Agent 才补得上。

问题不是「一个类装了两样东西」这种洁癖，是它**已经在咬人**。第一步不是照抄设计文档
那句「拆开」，而是去查 `amend_verdict.py` 到底在补救什么 —— 它不是一个方便功能，是
「没有它，Agent 想只加一个判断，就只能把整份事实重打一遍」这件事的补丁。三条实测事故：

- **`BIGA-20260920-002`**：补丁路径建成之前，Agent 唯一的办法是把整份 JSON 吐一遍 ——
  15 条 Evidence 的 `retrieved_at` 全部转述丢失（L-10）。
- **F8**：`amend_verdict.py` 曾把契约层的 stance 校验抄了一遍，连盲区一起抄
  （`agent="Market"` 大小写打错也能过）—— L-3，判据本该只有一处。
- **F9**：Agent 把 `task_id` 当 `--ref` 传错、报错后现读源码重试，加 stance 那次
  Stage 1 延迟翻倍（71s→133s）—— 成本全花在「判断必须靠第二条命令后补」上。

三条同源：事实和判断焊在一起，"只加一个判断"就没有一条干净的路。

### 三个类型，一条边界一个类型

| 类型 | 装什么 | 谁产 |
|---|---|---|
| `FactBundle` | 事实 —— **AgentVerdict 减去 stance** | skill |
| `AgentAssessment` | 判断 —— `stance` + 指回哪一份 FactBundle（`fact_ref`，**不抄事实**） | Agent |
| `AgentOutcome` | 组合视图 —— FactBundle + AgentAssessment | 程序 |

`AgentAssessment` 只带 `(task_id, agent, stance, fact_ref)` —— 它**不抄事实**，只用
`fact_ref`（那份 FactBundle 的行号）指回去。这正是补丁路径本该有的样子：Agent 加一个
判断，落一行 stance，事实一个字不重打。

### 跨型铁律归谁校验：拆开之后必须回答的问题

拆开之前，`AgentVerdict.__post_init__` 一口气校验所有铁律。拆开之后，铁律散到哪？

- **只关事实的铁律**（missing 非空⇒不许 PASS；result 字段必须有证据；UNKNOWN⇒missing
  非空）——`FactBundle` 校验。
- **只关判断的**（stance 必须在词表里）——`AgentAssessment` 校验。
- **跨两型的**（UNKNOWN 的事实上不许挂一个方向判断）——它需要同时知道
  `verdict`（事实）和 `stance`（判断），所以由**组合的那一刻**（`AgentOutcome` 构造、
  以及仍然合体的 `AgentVerdict`）校验。

🔴 这个问题**必须当场回答，不能「以后再说」**——拆开而不说清铁律去哪，就是把
「missing 非空却给 PASS」这种 fail-open 留了个缝。

### 为什么共用一份铁律实现（而不是 FactBundle 自己再写一遍）

`FactBundle` 和 `AgentVerdict` 都得守事实层的铁律。如果各写一遍，就是 L-3 —— 某天
改了一处、漏了另一处，而漏的时候不报错。F8 就是 `amend_verdict.py` 抄了一遍 stance
校验、连盲区一起抄的实测。

⇒ 把事实层铁律抽成 `verdict.check_fact_invariants` **一份**，`AgentVerdict` 和
`FactBundle` 都调它。探针 D 证明这是一份：把 `check_fact_invariants` 弄坏一处，
`AgentVerdict` 的测试和 `FactBundle` 的测试**一起**红。

### 为什么消费方零改动：`load_verdict` 变多态

`card_ops` / `risk_check` / `DecisionCard` 现在消费 `AgentVerdict`。要让它们不改，
就得让「不管底下存的是新是旧，它们拿到的都是一个 `AgentVerdict`」。

⇒ `_store.load_verdict(vid)` 变多态：旧合体行直接 `from_dict`；新 fact/assessment 行
走 `load_outcome` 拼成 `AgentOutcome`、再 `to_agent_verdict()` 压回。`DecisionCard`
的 `isinstance(v, AgentVerdict)` 严格检查因此不用动，`risk_check` 读 `.stance`/`.result`
也照旧。要看拆开的 FactBundle/AgentAssessment，用 `load_outcome`。

> 🔴 这是一处**刻意的取舍**：分发提示词说 `AgentOutcome` 是「下游消费方实际要读的
> 组合视图」。我让 `AgentOutcome` 成为那个组合视图（`load_outcome` 返回它、探针拿它
> 检查拆分），但**没有**把 `card_ops`/`risk_check`/`DecisionCard` 改成直接读它 ——
> 而是 `load_verdict` 把它压回 `AgentVerdict`，消费方零改动。理由是这是**试点**，
> 血缘面越小越好；让 `DecisionCard` 收 `AgentOutcome` 会涟漪到卡的序列化/回放。
> 若评审坚持消费方必须字面读 `AgentOutcome`，那是 E-II/后续的收敛，不在这一批。

### 读宽写严：旧格式怎么自然清零

`LegacyAdapter` 能把**任何**历史 `AgentVerdict` 拆回新三型（读路径宽）；但新落库
**只收新形状**（`save_fact_bundle` 拒绝 `AgentVerdict`，写路径严）。这样旧格式随时间
自然清零，不会永久驻留成第二套要跟着演进的口径（§9 的成例：历史卡可读、不可再写回）。

### 为什么试点是 emotion

它是 Phase 1 第一个建成的 skill，形状最简单，**不参与 `risk_check.py` 的
`CROSS_CHECK_PAIRS`**（那条只连 market↔technical），出问题影响面最小。迁一个先把
真实类型形状定下来，其余五个（要改口的更多、还牵动 CROSS_CHECK）等 E-II。

### 顺带还 D-II 的账：`Evidence.evidence_set_id`

D-II 的 CROSS_CHECK 改成比 `raw_hash`，但自己承认有盲区：两次独立抓取碰巧逐字节
相同时 `raw_hash` 会碰巧相等、漏报「悄悄退回独立抓取」。真正的修法是给 `Evidence`
加一个字段直接声明「我用的是哪个冻结集」。这一批加了 `evidence_set_id`，CROSS_CHECK
优先比它（结构验证，不看内容），两条都有才用、有一条没有就退回 `raw_hash`（老
Specialist 还没填这个字段，不能因此让检查失效）。

---

## 坑

### 坑 1 · `DecisionCard` 的 `isinstance(AgentVerdict)` 逼出了「边界压回」的设计

一开始想让 `AgentOutcome` 直接流到消费方。但 `DecisionCard.__post_init__` 有一句
`if not isinstance(v, AgentVerdict): raise TypeError` —— 卡严格只收 `AgentVerdict`，
还要序列化它们（回放的原料）。让它改收 `AgentOutcome` 会涟漪到卡的序列化与回放核对。

⇒ 换成 `AgentOutcome.to_agent_verdict()` 在**加载边界**压回 `AgentVerdict`。消费方那
一侧一个字没动。这不是偷懒，是把血缘面收在存储层（`load_verdict`）这一处，而不是散到
卡、回放、risk 三处。

### 坑 2 · 重构最受测的类，必须逐字保留报错文案

抽 `check_fact_invariants` 要改 `AgentVerdict.__post_init__` —— 全仓最受测的类，很多
测试是 `pytest.raises(match="铁律 1")` 这种按报错子串匹配的。抽的时候把每一条 `raise`
的文案**逐字**搬进helper，一个字都没改，`test_contract_behavior` 才能原样绿。改文案
= 悄悄改判据，下游按子串匹配的测试会跟着变，那就不是「空操作重构」了。

### 坑 3 · Agent 追加缺失项（限制）在新形状里还没有落点

老 `amend_verdict.py` 让 Agent 一条命令同时做两件事：加 `stance`（判断）+ 加
`--add-missing`（比如「只有单日快照，无法判断趋势」这类 Agent 观察到的限制）。

拆开之后，`stance` 有家（`AgentAssessment`），但**Agent 追加的缺失项没有** ——
它既不是 skill 的事实（skill 不知道），也不是一个 stance。给 fact 行加 `--add-missing`
在 E-I 里**明确拒绝**（指路到 E-II），不静默吞掉。这是一个真实的范围边界：拆「事实 vs
判断」这条线时，"Agent 观察到的数据限制"落在两者之间，需要 E-II 想清楚它归哪
（改 skill 自产、还是给 AgentAssessment 加一类字段）。记进 `TODO.md`。

> 通用原则：拆一个焊在一起的东西，最先暴露的不是「怎么拆」，是**那些一直骑在缝上的
> 用法**（这里是 amend 同时改事实和判断）。它们逼你回答「这到底算哪边」，而那正是
> 焊在一起时被糊过去的问题。

---

## 验证

```bash
cd ~/.openclaw-biga/workspace

# 1. E-I 六道探针（P1–P6）+ 契约/存储回归
python3 -m pytest tests/test_facts_split.py tests/test_contract_behavior.py \
                  tests/test_store.py tests/test_emotion_calc.py -q   # 期望全绿

# 2. 新旧并存 + 一份历史 verdict 走 LegacyAdapter 逐字段还原
python3 - <<'PY'
import sys; sys.path.insert(0,"skills")
from datetime import timedelta
from _contract import AgentVerdict, Evidence, LegacyAdapter, now_cn
t=now_cn(); ev=[Evidence(field="x",source="em:x",value=1,as_of=t-timedelta(seconds=60),retrieved_at=t)]
v=AgentVerdict(task_id="BIGA-20260918-001",agent="emotion",status="completed",verdict="PASS",
               result={"x":1},data_completeness=1.0,evidence=ev,stance="亢奋",elapsed_ms=10)
back=LegacyAdapter.to_outcome(v).to_agent_verdict()
print("LegacyAdapter 往返逐字段一致：", back.to_dict()==v.to_dict())
PY
```

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 先查 `amend_verdict.py` 在补救什么，别照抄设计文档的管线图 —— 它的存在就是「事实与判断焊在一起」的证据 |
| 2 | 三个类型：FactBundle（事实/skill）、AgentAssessment（判断/Agent，不抄事实只 fact_ref）、AgentOutcome（组合视图/程序） |
| 3 | 跨型铁律（UNKNOWN⇒无法判定）归 AgentOutcome 校验 —— 拆开必须当场回答铁律去哪，不能「以后再说」 |
| 4 | 事实层铁律共用一份 `check_fact_invariants`（防 L-3）；探针 D：弄坏一处，两个类型一起红 |
| 5 | `load_verdict` 多态把新旧都压回 AgentVerdict —— 消费方（card_ops/risk_check/DecisionCard）零改动 |
| 6 | 读宽（LegacyAdapter 拆任何历史 verdict）写严（新落库只收新形状），旧格式随时间自然清零 |
| 7 | 只迁 emotion 一个试点（影响面最小、不进 CROSS_CHECK）；其余五个 E-II 再迁；amend 未退役 |
| 8 | 顺带还 D-II 的账：`Evidence.evidence_set_id`，CROSS_CHECK 优先比它、缺失才退回 raw_hash |
| 9 | 坑：DecisionCard 严格收 AgentVerdict → 用 to_agent_verdict() 在加载边界压回；血缘面收在一处 |
| 10 | 坑：Agent 追加的「限制」缺失项在新形状里还没有落点，明确拒绝并指路 E-II，不静默吞 |
