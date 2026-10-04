# 第 72 章 · Review 引擎，与一个安静的兜底分支（Phase 4 · P4-5）

> 📄 **过程 · 写完即冻结**（2026-09-26）
> **覆盖**：合 Batch 3（确定性 ReviewRecord 引擎）的过程、值得原样保留的写边界、以及补上的那条判据 ｜ **不覆盖**：Phase 4 的设计裁定（见 [`../design/phase-4-review-measurement.md`](../design/phase-4-review-measurement.md)）、后续批次

---

## 目标 / 产出

合入 `easyup-biga-phase4-batch3-review-engine-20260926`：P4-5，
把冻结的 `ReviewSubject` + `OutcomeSet` 投影成一条**可读的、确定性的**单卡复盘。

产出：`review/reviews.py`、`persistence/review.py` 的 v30 落库、
契约里的 `review_id_for` / `review_content_payload`。

---

## 为什么这么做

### 这批是三批里最干净的一份

前两批各改掉三四处。这一份只补了一条判据，原因值得记下来：

1. 它的 `README_FIRST.md` 把基线写全了（「Phase 3 + P4-0/Batch 1 + Batch 2」），
   而且明说「**不要直接应用到更老的 Phase 3 ZIP 上**」
2. 它**独立修好了**我在第 70 章报告的那个 `ABSTAIN_SYSTEM` 漏项 ——
   它的 `registry.py` 现在手写的是完整的六个值

⚠️ 但第 2 点仍然是**手写的第二份**。本仓库保留从 `contracts.REVIEW_LABELS` 派生的写法：
值对了不等于形状对了，下一次改动仍然可能只改一边。

### 解压踩到的一个小坑：这份 zip 没有顶层目录

前两份解出来都是 `easyup-biga-phase4-batchN-.../`。
这一份直接把 `src/`、`tests/` 和四个 `.md` 摊在了 `docs/external/code/` 下。

`extractall(".")` 不会拒绝这种布局，也不会提示。
好在那一层原本只有目录和 zip，没有同名文件被覆盖 —— **这是运气，不是设计**。

> 通用原则：解压第三方归档前先看一眼 `namelist()` 的**第一段路径**是不是都一样。
> 「解到哪里」这件事，归档自己说了算。

---

## 值得原样保留的：写边界不信任调用方

`save_review_record()` 在同一个写事务里核对了八件事：

| 核对 | 挡住的是 |
|---|---|
| `content_sha256` 与 canonical payload 重算相等 | 手拼一条 Review 塞进来 |
| `primary_horizon` 与 policy 一致 | 用 H5 的 policy 存一条 H1 的结论 |
| subject 存在且 `ELIGIBLE` | 给一张被排除的卡产出复盘 |
| outcome 存在且属于同一个 subject | 跨 subject 的血缘 |
| QUARANTINED outcome ⇒ 只能 `UNSCORABLE` 且无 utility | 把一份被隔离的数据算出分数 |
| `COMPLETE` ⇒ 主 H5 return 必须真的在库里 | 「完成」了却没有依据 |
| 方向类标签必须有 utility、弃权类必须没有 | 标签与分数各说各话 |
| 新版本的 `data_version` 必须更大，且 `supersedes` 指向当前最新 | 把旧版本回插到新版本后面 |

这八条里最值得学的是最后一条：**它不是「按顺序写」的约定，是写入时的不变量。**
回插一条旧版本在任何一处日志上都看不出异常 —— 只有在这里被拒才是响的。

顺带一个小设计：`review_id` 由 `(subject, outcome_set, policy)` 派生 ⇒
同一个 outcome 版本重跑必然拿到同一个 id ⇒ 幂等靠身份，不靠「查一下有没有」。
而如果打分逻辑变了、id 相同但 hash 不同，写边界会**拒绝**——
这正确地把「改打分规则」逼成「发新的 ReviewPolicy id」，而不是悄悄改写历史。

---

## 补上的那条判据：兜底分支对「未知」是对的，对「新增」是静默降级

方向映射是逐个分支匹配的：

```python
def _direction(card_status, risk_veto):
    if card_status == "BUY":   return 1, None
    if card_status == "AVOID": return -1, None
    if card_status == "BLOCK" and risk_veto: return -1, None
    if card_status == "WAIT":  return None, "ABSTAIN"
    if card_status == "BLOCK": return None, "ABSTAIN_SYSTEM"
    return None, "UNSCORABLE"          # ← 兜底
```

兜底分支是**设计要求的**（「Unsupported status → UNSCORABLE」），对未知值完全正确。

问题在另一个方向：`CardStatus` 哪天多出第五个值，
那种卡的 Review 会**全部**变成 `UNSCORABLE`，而没有任何一条报错。
症状读起来像「这些卡复盘不了」，不像「有人忘了加分支」。

⇒ 加一条测试，遍历**权威源** `domain.card._CARD_STATUSES`
（它由 `CardStatus` Literal 用 `get_args` 派生，不是手抄的），
断言没有任何合法状态落进兜底。

探针：往 `CardStatus` 里加一个 `"REDUCE"`，测试立刻红：

```
AssertionError: 这些合法的 Card 状态在 `_direction()` 里没有显式分支，
                会被兜底成 UNSCORABLE：['REDUCE']。
```

错误消息里写了下一步（补分支 + 同步设计文档 + **发新 policy id 而不是改 v1**）——
开发流程第 8 条：报错要指路。

> 🔴 **一个「对未知值正确」的兜底，同时也是一个「对新增值静默」的洞。**
> 判据不是「有没有兜底」，是「权威源里的每一项，有没有走到过兜底之外」。

---

## 顺带：第三次贴回同样的两处裁定

`persistence/review.py` 又一次被整份带着走（这批新增 ~140 行 v30 落库）。
处理与第 71 章相同：取整份，把三元组唯一键那两处重新贴回，每处配
`assert s.count(old) == 1`。

模块头那段「本文件有哪几处本仓库裁定」也顺势更新到 P4-5。
**它的价值在第三次合并时才显现出来** —— 第一次写它的时候像是多余的注释。

---

## 验证

```bash
cd ~/.openclaw-biga/workspace

# ① Batch 1 的裁定第三次仍在
python3 -c "
import sys, inspect; sys.path.insert(0,'src')
from easyup_biga.review.contracts import review_subject_id_for, REVIEW_LABELS, HORIZONS
from easyup_biga.persistence.review import list_uncaptured_decision_records as f
assert {'target_policy_id','anchor_policy_id'} <= set(inspect.signature(f).parameters)
assert 'ABSTAIN_SYSTEM' in REVIEW_LABELS and HORIZONS == ('H1','H3','H5','H20')
print('三次合并之后裁定仍在 ✅')"

# ② 方向映射覆盖所有合法状态
python3 -m pytest -q "tests/test_p4_batch3.py::test_每一个合法的Card状态在方向映射里都有显式分支"

# ③ Phase 4 四个测试文件
python3 -m pytest -q tests/test_p4_0.py tests/test_p4_batch1.py \
                     tests/test_p4_batch2.py tests/test_p4_batch3.py
```

---

## 本章要点

| # | 要点 |
|---|---|
| 1 | 交付方**把基线写全并明说不要用在更老的树上**，能省掉整轮的推平风险 |
| 2 | 外部独立修好了上一轮报告的 bug，但**值对了不等于形状对了** —— 仍然保留派生写法 |
| 3 | 解压第三方归档前先看 `namelist()` 的第一段路径 —— 没有顶层目录的 zip 会摊一地 |
| 4 | 写边界的八条核对里，最值钱的是「旧版本不许回插」：那是写入时的**不变量**，不是顺序约定 |
| 5 | 身份由内容派生 ⇒ 幂等靠身份、不靠查询；打分规则改了会撞 hash 冲突，逼你发新 policy |
| 6 | 🔴 **一个「对未知值正确」的兜底，同时是一个「对新增值静默」的洞** |
| 7 | 判据不是「有没有兜底」，是「权威源里的每一项有没有走到过兜底之外」 |
| 8 | 在被反复覆盖的文件头写「本文件有哪几处裁定」—— 它的价值在第三次合并时才显现 |
