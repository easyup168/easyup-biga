# 第 73 章 · 测量引擎，与一份会把「对不上」说成「样本不够」的词表（Phase 4 · P4-6/P4-7）

> 📄 **过程 · 写完即冻结**（2026-09-26）
> **覆盖**：合 Batch 4（Measurement / Placebo / Robustness）的过程、拒收的三份文档、改掉的两处 ｜ **不覆盖**：Phase 4 的设计裁定（见 [`../design/phase-4-review-measurement.md`](../design/phase-4-review-measurement.md)）、统计方法本身的教学

---

## 目标 / 产出

合入 `easyup-biga-phase4-batch4-measurement-robustness-20260926`：
P4-6（cohort 冻结 + 预注册假设 + 置换/自助 + 多重检验校正）
+ P4-7（label permutation / lagged-label placebo / regime 与 equity-EW 稳健性）。

产出：`review/measurement.py`（546 行）、`review/placebo.py`、`review/robustness.py`，
契约里的 `MeasurementResult` 与三个身份/哈希函数，v31 落库，两条 PIT 查询。

---

## 解压前那一眼，这次救了三份文档

第 72 章记下的规矩是「解第三方归档前先看 `namelist()` 的第一段路径」。
这一批照做，输出是：

```
顶层路径段: {'CHANGED_FILES.md', 'docs', 'src', 'README_FIRST.md',
             'tests', 'README.md', 'TEST_RESULTS.md',
             'IMPLEMENTATION_REPORT.md', 'CLAUDE.md'}
```

`README.md` 和 `CLAUDE.md` 在里面 —— **仓库根那两份**。

`CHANGED_FILES.md` 也老实列了它们。逐条 diff 之后确认三份文档全部来自
**0.9.2 冻结基线**，合进来会：

| 文件 | 会退掉什么 |
|---|---|
| `CLAUDE.md` | 删掉整节「素材库在仓库外 `~/.openclaw-biga/notes/`」 |
| `README.md` | Status 徽章退回 `phase 2 code closed`；删掉整段 Phase 3 实现状态与出口条件；测试数写成 2144 |
| `docs/guide/review-prompt.md` | 同样把测试数写回 2144 |

⇒ **三份全部拒收。**

> 🔴 **源码增量包里出现仓库根的自我描述文档时，默认拒收。**
> 它们描述的是交付方那棵树的状态，不是你这棵树的。
> 而这类回退**一条都不会报错** —— 徽章仍然会渲染，只是说了假话。

⚠️ 顺带：它们要改的正是 `sync_test_count.sh` 维护的那三份。
那个脚本存在的理由（数字有多个出处 = L-3）在这里反过来咬了一口：
**多出处也意味着外部包会同时改到它们。**

---

## 改掉的第一处：一份会把「对不上」说成「样本不够」的词表

`measurement.py` 开头：

```python
_MARKET_POSITIVE = frozenset({"放量上涨", "缩量上涨"})
_MARKET_NEGATIVE = frozenset({"缩量调整", "放量下跌"})
...
return "BULLISH" if stance == "多头" else "BEARISH" if stance == "空头" else None
```

这些字面量是 `domain.STANCE_VOCAB` 的孪生 —— 本仓库外部评审点名过三次的那个形状。

但这一处的**症状**特别值得记下来。假设某天 `technical` 的 `多头` 改名成 `偏多`：

1. `_label()` 对每一张卡都返回 `None`
2. 两个比较组**双双为空**
3. `_result()` 很诚实地给出 `effect=None, ci=None, p_value=None, underpowered=True`
4. 报告上读起来是 **「样本不够」**

而真实原因是**「词表对不上了」**。这两件事的下一步完全相反：
前者要**等**（等够 30 个 anchor 日），后者要**改代码**。

> 🔴 **最坏的失败不是报错，也不是静默 —— 是报出一个「合理但错误」的原因。**
> 它会让人去做一件完全正确、却解决不了问题的事。

⇒ 对账放在**导入期**（与 `review/registry.py` 已有的引用完整性检查同一个位置）：

```python
for _agent, _group in _STANCE_GROUPS.items():
    _unknown = _group - set(STANCE_VOCAB[_agent])
    if _unknown:
        raise RuntimeError(...)
```

只查**子集**不查相等：`分化` / `无法判定` / `震荡` / `轮动分散` 本来就不属于任何一组，
将来新增一个 stance 也应当默认落在组外（那是保守的）。
**这里要挡的是改名与删除。**

错误消息里写了一句关键的下一步：

> 改这里的分组常量**不是**唯一要做的事：历史 `ReviewSubject` 里冻结的是旧词，
> 换词表等于换了比较口径，应当发新的 `MeasurementPolicy` id，
> 而不是让旧 run 悄悄换含义。

探针实测：把 `_TECHNICAL_BULLISH` 改成 `"偏多"`，`import` 当场 `RuntimeError`。

---

## 改掉的第二处：`MeasurementRun` 的身份可以手拼

包里的 `MeasurementRun.__post_init__` 只校验字段非空 + hash 形状。
而包**自带的测试** `test_measurement_run_identity_cannot_be_forged`
期望它拒绝手拼的 id —— 那条测试在合并后当场 `DID NOT RAISE`。

这是很有意思的一种不一致：**测试写了、实现没跟上**，而交付方自报 11 passed。
原因大概率是他们那棵树的 `contracts.py` 里有这段校验，而给我的 diff 基于
另一个版本 —— 我按新增段合并时，这一段落在了「改动」而不是「新增」里，被我漏掉了。

⇒ 补上派生校验。身份里带 `cohort_manifest_sha256` 的意义是
**换了样本就换 id**；没有校验的话，手拼一个 id 配另一份 cohort 可以直接构造出来，
而那正好让「同一个 run id 指向两份不同的 cohort」成立。

> 通用原则：**合并增量包之后，先把包自带的测试跑一遍。**
> 它们是交付方对自己实现的断言 —— 红的那条往往指向你漏合的东西。

---

## 值得原样保留的

| 做法 | 为什么好 |
|---|---|
| `_result()` 在任一组为空时给 `effect=None, underpowered=True`，**不编数** | 我们今天的 cohort 正好是 `BUY 0` —— 它必须能诚实地说「这组是空的」 |
| 置换检验在 `C(n,k) <= iterations` 时**精确枚举** | 小样本下随机置换的 p 值抖动很大，而我们至少半年内都是小样本 |
| 随机置换的 p 值用 `(extreme+1)/(n+1)` | 避免 p=0 这种「无限显著」的假象 |
| 每个 analysis 的种子由 `sha256(seed|key|salt)` 派生 | 同一次 run 里各分析互不相关，但整体可复现 |
| `resampling_algorithm_id` / `rng_algorithm_id` 进 policy | 换 RNG 会改变历史 p 值 ⇒ 它必须是**可以指认的**，而不是「当时 Python 恰好是什么版本」 |
| cohort 冻结时**先解析时间再过滤类型** | 未来才到的 replay/legacy 行不会改变一个已经可复现的历史 cohort hash |
| 稳健性端点要求三个 dataset 的**每一个 H5 交易日**都 COMPLETE | 一天缺就整体不可用，而不是用剩下的凑一个看起来像样的数 |

最后一条尤其对：`resolve_equity_ew_h5` 在任何一天遇到非 COMPLETE 就返回
`available=False` 并带上 `NON_COMPLETE_<dataset>:<day>:<status>` ——
**理由写在返回值里，不在日志里。**

---

## 一处保留意见（没改）

`_exploratory` 对每个维度做「每个 label vs REST」。当某维度只有两个 label 时，
这会产生两条**互为镜像**的检验（A-vs-B 与 B-vs-A，双尾下 p 值完全相同），
而 BH-FDR 的 `m` 因此被这对重复撑大。

方向上它让校正**更保守**，不是更宽松 ⇒ 不是正确性缺陷。
而改动多重检验的结构属于统计口径变更，应当走新的 `MeasurementPolicy` id，
不适合在一次合并里顺手做。⇒ 记在这里，不改。

---

## 验证

```bash
cd ~/.openclaw-biga/workspace

# ① 三份文档没有被外部包退回去
grep -q "素材库在仓库外" CLAUDE.md && echo "CLAUDE.md 未被退回 ✅"
grep -q "phase%202%20code%20closed" README.md || echo "README 徽章未被退回 ✅"
# ⚠️ 判据写成「不是 phase 2」而不是「等于 phase 3」——徽章本来就会随阶段推进，
#    这一章要守的是「没有被 0.9.2 基线退回去」。原文写死了当时的值，
#    到 Phase 4 收口（第 77 章）徽章一改它就跑不出来了。

# ② stance 分组与词表对账（导入期）
python3 -c "
import sys; sys.path.insert(0,'src')
from easyup_biga.domain import STANCE_VOCAB
from easyup_biga.review import measurement as m
for a, g in m._STANCE_GROUPS.items():
    assert not (g - set(STANCE_VOCAB[a])), a
print('stance 分组全部在词表里 ✅')"

# ③ Batch 4 测试
python3 -m pytest -q tests/test_p4_batch4.py
```

---

## 本章要点

| # | 要点 |
|---|---|
| 1 | 🔴 **源码增量包里出现仓库根的自我描述文档时，默认拒收** —— 它们描述的是交付方那棵树 |
| 2 | 这类回退一条都不会报错：徽章仍然渲染，只是说了假话 |
| 3 | 数字有多个出处（L-3）不只让你漏改一处，还让**外部包能同时改到它们** |
| 4 | 🔴 **最坏的失败不是报错、也不是静默，是报出一个「合理但错误」的原因** |
| 5 | 词表脱节会被报成「样本不够」——而「等」和「改代码」是完全相反的下一步 |
| 6 | 词表对账查**子集**不查相等：新增应当默认落在组外，要挡的是改名与删除 |
| 7 | 错误消息要写「改这个常量**不是**唯一要做的事」——历史冻结的是旧词 |
| 8 | **合并增量包之后先跑包自带的测试** —— 红的那条往往指向你漏合的东西 |
| 9 | 空组时给 `effect=None, underpowered=True` 而不是编数，正是我们今天这份 cohort 需要的 |
| 10 | 保守方向的统计瑕疵可以先记下来不改 —— 改口径属于新 policy 版本，不适合顺手做 |
