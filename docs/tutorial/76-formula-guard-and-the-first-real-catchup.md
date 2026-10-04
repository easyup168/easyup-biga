# 第 76 章 · 公式指纹，与第一次真实 catch-up（Phase 4 · P4-R1.1）

> 📄 **过程 · 写完即冻结**（2026-09-27）
> **覆盖**：合 P4-R1.1（Formula Guard + catch-up 工具）的过程、指纹里的那个洞、以及在生产库上真的跑完第一次 catch-up ｜ **不覆盖**：Phase 4 设计裁定（见 [`../design/phase-4-review-measurement.md`](../design/phase-4-review-measurement.md)）、操作步骤本身（见 [`../guide/phase4-first-catchup.md`](../guide/phase4-first-catchup.md)）

---

## 这一批回答的是上一章留下的问题

第 75 章末尾记着一条：

> F07 是一次改变历史值的公式变更。按设计文档 §7.3 本该发 `OutcomePolicy` v2；
> 能原地改 v1，只因为生产库里一条 `OutcomeSet` 都还没有。**下一次改公式就不行了。**

那句「下一次就不行了」当时只是一句话 —— **没有任何机制**在兑现它。
P4-R1.1 把它做成了机制：

| 层 | 做什么 |
|---|---|
| `metric_algorithms.py` | 算法进注册表，每个带 `formula_sha256` + `implementation_sha256` |
| `OutcomePolicy` | 多四个字段，构造时与算法注册表对账 |
| Phase 4 闸门 | 每个 policy 的指纹与注册表必须一致，否则报「formula fingerprint drift」 |
| 写边界 | 同一个 `outcome_policy_id` 的**第一条持久化记录**全局冻结指纹 |

最后一层是关键：它不是代码约定，是 `save_outcome_bundle()` 里的一次 SQL 查询 +
一次比对。改了公式又想往 v1 里写，**写不进去**。

---

## 指纹里的那个洞

`_implementation_sha256()` 把函数源码做 token 流哈希（剔掉注释与格式）。
思路对，但它**只哈希 calculator 自己**。

而那个 calculator 调用了 `_daily_returns()` —— `positive_day_ratio` 与
`realized_vol` 两个指标完全由它决定。

实测（临时模块，改一个字符再重算）：

```
calc 的 implementation_sha256（calc 自己的源码一个字没动）：
  改 _daily_returns 前: 1ff20ea9c58acb204951
  改 _daily_returns 后: 1ff20ea9c58acb204951
  → 🔴 指纹没变
而它算出来的数变了： 1.0    （原来是 0.5）
```

> 🔴 **守卫确实在查一件事，只是不是它声称在查的那件事。**
> 它声称守「公式没变」，实际守的是「这一个函数的源码没变」。

⇒ 改成哈希**同模块的传递闭包**：用 AST 找出函数体里引用到的、
且 `__module__` 相同的可调用对象，递归收集，按名字排序后一起哈希。

这是**派生**不是手写名单 —— 将来 calculator 多调一个同模块 helper，它自动进指纹。

### 边界是选择，不是疏漏

闭包**只跟同模块**。`horizon_trading_days` 来自 `.contracts`、`OutcomeMetric` 是契约类，
两者都不在指纹里。理由：把整个 import 图拉进来，任何无关改动都会逼出一次 policy 升版。

这条边界写进了 docstring，也写成了判据（`test_跨模块依赖不在指纹内是有意的`）——
否则读的人会以为跨模块也在里面。

⚠️ 同一条边界还有另一面：闭包只跟**可调用对象**，**模块级常量不在指纹内**。
今天的 calculator 里没有模块常量（`252.0` 之类都是字面量写在函数体内），所以不留洞。
但「今天不留洞」是需要被守住的 —— 于是那条测试同时断言
「calculator 没有用到任何模块级 `UPPER_CASE` 常量」。

探针把 `252.0` 提成 `TRADING_DAYS_PER_YEAR`，它当场红。

---

## 🔴 又一次：探针没红，是判据落错了地方

给「指纹要覆盖辅助函数」配探针时，把 `_implementation_sha256` 改回
「只哈希 calculator 自己」，测试**照样绿**。

原因：我的第一版判据写的是

```python
assert "_daily_returns" in ma._same_module_callees(calculator)
```

—— 它查的是**那个辅助函数存在于闭包里**，而不是**指纹真的用了闭包**。
把 `_implementation_sha256` 换掉，`_same_module_callees` 照样返回正确结果。

改成行为判据：造两个真实模块，`caller` 逐 token 相同、只有 `helper` 的函数体不同，
断言两者的指纹不同。

第一版夹具还写错了一次：我让两个模块只差一个模块级常量 `FACTOR`。
测试红了 —— 而**红得对**：闭包只跟函数，常量本来就不在里面。
改成 helper 函数体不同之后才是我要测的那件事。

> 这是第三章连着记同一件事了（74 / 75 / 76）。三次的原因各不相同：
> **场景触发不到** → **守卫不存在** → **判据查了「它用到的东西」而不是「它自己的行为」**。

---

## 第一次真实 catch-up

上一章结束时，生产库刚迁到 v31，`catchup` 还没跑过 ——
因为它会往 append-only 表写 54 行，**写进去删不掉**。

R1.1 带来的 `tools/maintenance/phase4_first_catchup.py` 把这件事做成了可审计的顺序：
零行证明 → 记录源码与公式指纹 → `VACUUM INTO` 备份 → 单独 sync subject →
对账 exclusion code → 断网跑 Outcome/Review → 出 JSON + Markdown 报告。

### 预检（不写库）

```
"status": "PREFLIGHT_OK",
"preflight_counts": { "outcome_sets": 0, "review_records": 0, "measurement_runs": 0, ... },
"metric_algorithm": { "implementation_sha256": "634b3185ae24221b..." },
"source_tree_sha256": "e4baa7141490c9e6..."
```

### 正式执行

```
subject_sync:  scanned 54 / created 54 / eligible 9 / ineligible 45
reasons:       ELIGIBLE 9 · LEGACY_LINEAGE_MISSING 44 · REPLAY_EXCLUDED 1
outcomes_created: 0    not_ready: 9    quarantined: 0
status: COMPLETED_NOT_READY     exit 2
```

与演练副本的数字**逐项一致**。

`outcomes_created: 0` 不是故障 —— `cn.index.eod_bars` 在生产库里有 **0 个快照**
（那条 EOD 定时器还没到第一次触发时间）。Outcome 读的正是它，所以 9 张可评价卡
全部停在 `no_outcome`。

⇒ 这次 catch-up **没有产生任何 OutcomeSet**，所以公式冻结点**还没落下**。
公式仍然可以改。

### 三条事后核对

```bash
# ① 备份能独立打开、且是 catch-up 之前的状态
integrity: ok / user_version: 31 / review_subjects: 0 / decision_records: 54

# ② 幂等：再跑一次
subject_sync: {'scanned': 0, 'created': 0, ...}     ← 没有新增

# ③ 审计产物不进仓库
git check-ignore data/audit/...  →  .gitignore:2:/data/*
```

### 9 张可评价卡是什么样

```
2026-09-24  BIGA-20260924-007  WAIT
2026-09-24  BIGA-20260924-009  WAIT
2026-09-25  BIGA-20260925-001  WAIT
2026-09-25  BIGA-20260925-002  WAIT
2026-09-26  BIGA-20260926-001  WAIT
…（共 9 张，全部 WAIT）
```

三个自然日、单一标签、零方差。44 张被排除是因为缺 `run_id` / `evidence_set_id` ——
那是 Run Provenance 落地之前的历史卡，按设计**不回填伪 ID**。

> 这一步把「Evidence Ready 卡在采样」从一句判断变成了**库里的一个数**：
> `distinct_outcome_anchor_days: 0`。

---

## 验证

```bash
cd ~/.openclaw-biga/workspace

# ① 指纹覆盖辅助函数
python3 -c "
import sys; sys.path.insert(0,'src')
from easyup_biga.review import metric_algorithms as ma
print(sorted(ma._same_module_callees(ma._market_index_outcome_synchronized_close_v1)))"
# 期望含 _daily_returns

# ② 闸门报出公式指纹
python3 tools/verify/phase4_acceptance.py --code-only | grep formula

# ③ catch-up 幂等
./bin/biga-review catchup ; echo "exit=$?"     # 期望 2，且 scanned=0

# ④ 备份可独立打开
python3 -c "
import sqlite3
b=sqlite3.connect('file:data/audit/phase4-first-catchup/pre-catchup-v31.sqlite3?mode=ro',uri=True)
print(b.execute('PRAGMA integrity_check').fetchone()[0],
      b.execute('SELECT COUNT(*) FROM review_subjects').fetchone()[0])"
# 期望 ok 0
```

---

## 本章要点

| # | 要点 |
|---|---|
| 1 | 「下一次就不行了」是一句话；**做成写边界的一次 SQL 比对**才是机制 |
| 2 | 指纹的冻结点应当是**第一条持久化记录**，且是全局的（跨 Subject） |
| 3 | 🔴 **只哈希函数自己 ≠ 哈希公式** —— 它调用的同模块辅助函数决定了一半的指标 |
| 4 | 闭包要**派生**（AST 找同模块可调用对象），不要手写名单 |
| 5 | 边界（只跟同模块、只跟可调用对象）是**选择不是疏漏** ⇒ 写进 docstring **并写成判据** |
| 6 | 模块级常量不在指纹内 ⇒ 顺手断言「calculator 没用模块常量」，把「今天不留洞」守住 |
| 7 | 🔴 探针没红的第三种原因：**判据查了「它用到的东西」而不是「它自己的行为」**（前两种见第 74、75 章） |
| 8 | 行为判据要造两个**只差一处**的真实样本，并先断言「其余部分确实相同」 |
| 9 | 第一次写 append-only 表要有可审计顺序：零行证明 → 指纹留档 → 备份 → 执行 → 报告 |
| 10 | `exit 2` 是业务 NOT_READY，不是失败；**它不允许被包装成 Evidence Ready** |
| 11 | 没有 OutcomeSet ⇒ 公式冻结点还没落下 ⇒ **公式此刻仍然可以改** |
