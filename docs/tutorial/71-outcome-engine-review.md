# 第 71 章 · Outcome 引擎，以及三处「今天碰巧对」（Phase 4 · P4-3/P4-4）

> 📄 **过程 · 写完即冻结**（2026-09-26）
> **覆盖**：合 Batch 2（Anchor/Target 解析 + OutcomeSet 成熟与修订）的评审过程、改掉的三处及其判据 ｜ **不覆盖**：Phase 4 的设计裁定（见 [`../design/phase-4-review-measurement.md`](../design/phase-4-review-measurement.md)）、后续批次

---

## 目标 / 产出

合入 `easyup-biga-phase4-batch2-outcome-engine-20260926`：P4-3（锚点与目标解析）
+ P4-4（OutcomeSet 的成熟、修订与落库）。

产出：`review/anchors.py`、`review/targets.py`、`review/outcomes.py`，
`persistence/review.py` 的 outcome 落库，`analytics.resolve_dataset_snapshots_as_of`。

---

## 为什么这么做

### 这一批的基线问题与上一章**不同**

第 70 章那份是「基线过期 21 个版本」。这一份的 `README_FIRST.md` 写得很清楚：

```
Apply it only after:
1. Phase 3 Frozen Baseline
2. rebased P4-0 + Batch 1 delta
```

⇒ 它的基线是**未经我修改的 Batch 1**。而我在上一章改了 Batch 1 四处。

于是这一批对四个共享文件的「改动」里，混着两种东西：

| | 例子 |
|---|---|
| 真正的新增 | `contracts.outcome_set_id_for`、`persistence/review.py` 的 outcome 落库 |
| **把我的修改改回去** | `review_subject_id_for` 退回不带 policy、`list_uncaptured_decision_records` 退回按 record_id join |

> 🔴 **增量包是相对某个基线的 diff，不是相对你这棵树的 diff。**
> 基线是「原版上一批」时，它必然带着把你的修改推平的那一半。

### 处理方式按文件分两类

- **新增少**（`contracts.py` / 两个 `__init__.py` / `analytics.py`）⇒ 从包里切出新增段插入
- **新增多**（`persistence/review.py`，多了 200 行 outcome 落库）⇒ **取包的整份，再把我的三处裁定重新贴回去**

后者更安全：手工往自己那份里缝 200 行，漏掉一处不会报错。
取整份 + 重贴三处，每一处都有 `assert s.count(old) == 1` 兜着。

顺手在 `persistence/review.py` 的模块头写了一段「本文件有两处本仓库裁定，
合并外部增量包时容易被整份覆盖掉」—— 下一批再来时，读的人先看见它。

---

## 坑：import 过了，调用才炸

`analytics.py` 只取了一个新函数 `resolve_dataset_snapshots_as_of`。插进去之后：

```bash
python3 -c "from easyup_biga.data.analytics import resolve_dataset_snapshots_as_of"
# 通过 ✅
```

而它内部调用 `_validate_date_range()` —— 那个函数是**外部包的 Batch 1 版本**里有的，
本仓库的 Batch 1 把校验内联进了 `_query_as_of`，根本没有这个名字。

Python 的模块级 `import` 只解析到函数对象，函数体里的自由变量要到**调用时**才查。
于是这个洞在 import 期完全看不见。

> 🔴 **「import 过了」不等于「跑得通」。**
> 切片式合并之后，判据是把新函数**真的调一次**，哪怕只为看它抛哪个错。

```bash
python3 -c "
from easyup_biga.data.analytics import resolve_dataset_snapshots_as_of as r
r('cn.index.eod_bars', db_path='/nonexistent',
  knowledge_cutoff='2026-09-26T12:00:00+08:00', start_date='bad', end_date='20260926')"
# ValueError: start_date must be YYYYMMDD   ← 校验真的跑到了
```

补的时候顺手把 `_query_as_of` 里那两行内联校验也收进来 —— 否则又是两份。

---

## 改掉的三处

### ① Horizon 的语义被手写了两份，而漏 key 时不报错

```python
# outcomes.py
_HORIZON_SIZE = {"H1": 1, "H3": 3, "H5": 5, "H20": 20}     # 几个交易日
# anchors.py
horizon_index  = {"H1": 0, "H3": 2, "H5": 4, "H20": 19}    # 下标 = 天数 - 1
```

两份都是 `policy.horizons` 的孪生。第二份所在的推导式是：

```python
horizon_dates = {h: open_days[i] for h, i in horizon_index.items()
                 if h in policy.horizons and len(open_days) > i}
```

它遍历的是**映射表**，再用 policy 过滤。⇒ policy 里有、表里没有的那个 horizon
会被**静默丢掉**，症状是「它永远不成熟」，而不是任何一条报错。

⇒ 从名字派生：`H<N>` ⇒ N 个交易日 ⇒ 下标 N-1。
`contracts.horizon_trading_days()` 一处定义，两边都用它。
新增 horizon 只要名字合这个形状就自动可用，不需要再改任何映射表。

探针实测「漏掉 H20」的症状正如预期 —— 现有那条成熟度测试报的是一句裸 `assert (False)`：

```
tests/test_p4_batch2.py:398: AssertionError: assert (False)
```

> 一个只在「恰好有人断言过那个值」时才被发现的错误，等于没有守卫。

### ② anchor 日被隔离时会崩，而不是判 QUARANTINED

`market.index_blend.v1` 的每根 bar 都以 **anchor 日的 open** 归一化。
外部实现在拿不到那两行时：

```python
if sh0 is None or sz0 is None:
    raise ValueError("blend target requires both anchor-day index rows")
```

这条路**可达**：anchor 日最新修订是 QUARANTINED、其后某天是 COMPLETE 时就会走到。
而它发生在 `build_outcome()` 判隔离**之前** ——
一个本该被如实记成 `QUARANTINED` 的场景，变成了未捕获异常。

设计文档 §14.1 对这种场景要的是「QUARANTINED + reason」，不是崩。

⇒ 在 `resolve()` 里先判一次 `blend_available`，拿不到 anchor 两行就**一根 blend 都不产**，
让下游的成熟度/隔离判定照常工作。

⚠️ 写这条测试时被**落库边界**拦了一次：

```
ValueError: blocking snapshot does not exist: s1-bad
```

`save_outcome_bundle()` 会核对「隔离所指的那份快照真的存在且不是 COMPLETE」。
我的夹具编了一个 snapshot id。**那条不变量是对的**，改夹具去种真快照 ——
被自己的守卫拦一次是好事，它证明那道检查不是摆设。

### ③ 同一张表上出现了第二条选择器

这一批新增的 `resolve_dataset_snapshots_as_of` 与既有的 `_snapshot_uris_as_of`
查的是同一张 `dataset_snapshots`，口径不同：

| | `_snapshot_uris_as_of` | `resolve_dataset_snapshots_as_of` |
|---|---|---|
| 过滤 | `status = COMPLETE` | 不过滤状态 |
| 一天有 v1=COMPLETE、v2=QUARANTINED | 返回 **v1**（静默降级） | 返回 **v2**（并带上状态） |

这正是裁定 15 要防的形状：**两个消费方各算一遍，某天悄悄给出两个数。**

但这次**没有**把旧的那条改严，理由是代价：那会改变 Phase 3 已上线的
`query_eod_as_of` 语义，而 Phase 3 正在走 Live Acceptance。

实际的调和发生在调用点：`TargetResolver` 用严格版选出「哪一份是最新的」，
再拿它的 `snapshot_id` 与行上的 `_snapshot_id` 对账 —— 对不上就整天不用。
一次新的 QUARANTINED 修订因此**挡住**旧的 COMPLETE。

⇒ 差异写进两边的 docstring，并由 `test_两条快照选择器的口径差异是有意的` 钉住。

> **无法收敛的两套口径，至少要让它是被看见的、被测试钉住的。**
> 「读的人应该知道」不是机制。

---

## 值得原样保留的三处

| 做法 | 为什么好 |
|---|---|
| 锚点只用**决策当时可见**的日历事实（`knowledge_cutoff = decision_at`） | 出卡三天后再算 outcome，不会用后来才公布的排期改写锚点 |
| 日历时间比较解析成 aware datetime 再比，不靠 SQLite 字面序 | 两行 `retrieved_at` 的时区偏移不同时，字面序会给出错的答案 |
| content hash 里**剔掉** `knowledge_cutoff` | 否则每次重试都因为时钟往前走而「制造」一次修订 |

第三条尤其值钱：它把「幂等」和「修订」这两件事的判据分开了 ——
**修订由上游数据变化触发，不由重跑时刻触发。**

---

## 验证

```bash
cd ~/.openclaw-biga/workspace

# ① Batch 1 的裁定没有被这一批推平
python3 -c "
import sys; sys.path.insert(0,'src')
import inspect
from easyup_biga.review.contracts import review_subject_id_for, REVIEW_LABELS
from easyup_biga.persistence.review import list_uncaptured_decision_records as f
sig = inspect.signature(f).parameters
assert 'target_policy_id' in sig and 'anchor_policy_id' in sig
assert 'ABSTAIN_SYSTEM' in REVIEW_LABELS
assert 'target_policy_id' in inspect.signature(review_subject_id_for).parameters
print('Batch 1 的裁定仍在 ✅')"

# ② 新函数真的跑得到（不是只 import 得通）
python3 -c "
import sys; sys.path.insert(0,'src')
from easyup_biga.data.analytics import resolve_dataset_snapshots_as_of as r
try: r('cn.index.eod_bars', db_path='/nonexistent',
       knowledge_cutoff='2026-09-26T12:00:00+08:00', start_date='bad', end_date='20260926')
except ValueError as e: print('校验跑到了 ✅', e)"

# ③ Phase 4 三个测试文件
python3 -m pytest -q tests/test_p4_0.py tests/test_p4_batch1.py tests/test_p4_batch2.py
```

---

## 本章要点

| # | 要点 |
|---|---|
| 1 | **增量包是相对某个基线的 diff，不是相对你这棵树的 diff** —— 基线是「原版上一批」时，它必然带着推平你修改的那一半 |
| 2 | 新增少的文件切片插入；新增多的文件**取整份再重贴自己的裁定**，每处配 `count(old) == 1` |
| 3 | 在被反复覆盖的文件头写一段「本文件有哪几处本仓库裁定」，给下一批的读者 |
| 4 | 🔴 **「import 过了」不等于「跑得通」** —— 函数体里的自由变量到调用时才查 |
| 5 | 遍历映射表再用权威源过滤 ⇒ 表里漏的那一项被**静默丢掉**；反过来（遍历权威源）才会报错 |
| 6 | 从名字派生比手写映射表好，因为新增项自动可用、漏项不可能发生 |
| 7 | 异常不是 fail-closed —— 一个本该记成 QUARANTINED 的场景抛异常，等于丢掉那条证据 |
| 8 | 被自己的守卫拦一次是好事；先问「是夹具不现实，还是行为错了」 |
| 9 | 两套口径无法收敛时，**至少让它是被看见的、被测试钉住的** |
| 10 | content hash 剔掉重跑时刻 —— 修订由上游数据变化触发，不由时钟触发 |
