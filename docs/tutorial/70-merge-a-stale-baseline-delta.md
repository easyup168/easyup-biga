# 第 70 章 · 合一份基线已经过期的交付（Phase 4 · P4-0/P4-1/P4-2）

> 📄 **过程 · 写完即冻结**（2026-09-26）
> **覆盖**：第五次评审外部交付的过程 —— 基线漂移怎么识别、哪些改动只能取一半、改掉的四处各自的判据 ｜ **不覆盖**：Phase 4 的设计裁定（见 [`../design/phase-4-review-measurement.md`](../design/phase-4-review-measurement.md)）、后续批次

---

## 目标 / 产出

把 `easyup-biga-phase4-p4-0-batch1-rebased-20260926` 这份外部交付评审后合进 `phase4` 分支：
Phase 4 的契约、六份 Policy Registry、schema v28–v31、ReviewSubject 捕获，
以及新数据集 `cn.index.eod_bars`。

产出：`src/easyup_biga/review/` 四个模块 + `persistence/review.py` +
`data/datasets/index_eod_bars.py` + schema v28–v31 + 两个测试文件。

---

## 为什么这么做

### 前四次的判据这次给出了「不适用」

第 62、63、66、68 章各评审过一份外部交付，攒出两条判据：

1. **数它包不包含你已知的修正**（第 66 章）—— 不含 ⇒ 基线不同 ⇒ 只能按层合
2. **基线就是我们的 HEAD ⇒ 用三方合并**（第 68 章）—— 前者冲突时会问你，手工缝只会悄悄选边

这次两条都不直接适用，因为包自己把基线写在了 `README_FIRST.md` 里：

```
source: easyup-biga-phase3——2026092602(1).zip
project version in baseline: 0.9.2.dev0
baseline SHA-256: 4bd964cd80b5...
```

而仓库当时是 **`0.9.23.dev0`**。这不是「不知道基线」，是**明确知道基线，而它过期了 21 个版本**。

> 🔴 **交付方自报的基线是最先该读的一行，不是最后。**
> 它比任何 diff 都快地告诉你「整份覆盖会退掉什么」。

### 第一个 diff 就证实了代价

`data/registry.py` 一份的整份覆盖会退掉：

| 退掉的 | 它是怎么来的 |
|---|---|
| `cn.security_master` 的 `sina_security_master` 备胎 | 2026-09-26 被一次真故障逼出来，且真跑通过（5568 只、111s） |
| `cn.equity.daily_bars` 主备对调（`sina_eod` 主） | 两条长期证据 + 那天东财整组 502 |
| `cn.market.breadth` / `cn.sector.board_snapshot` 的备胎 | 各自实测通过 |
| `cn.market.limit_pool` 的 `schema_version=2` | 行上多了 `row_fields_json` |
| `cn.news.flash` 的 `cls_news` 主源 + `schema_version=2` | 行上多了 `level` |

`producers.py` 会把 provider 退回 `eastmoney_eod` / `sina_news`；
`persistence/__init__.py` 会**删掉 `latest_trading_day`** 的导出。

这些回退**一条都不会报错**。它们只是让某天的降级路径悄悄消失。

⇒ 裁定：被修改的 8 个文件一律**只取新增的那一段**，不整份覆盖。

---

## 执行

### 只取新增段的四种形状

| 文件 | 取什么 | 怎么取 |
|---|---|---|
| `data/registry.py` | 一条 `DatasetDefinition` | 手写插入（顺带补齐本仓库口径的注释） |
| `data/producers.py` | 一行 `ProducerDefinition` | 锚点插入 |
| `data/quality.py` | 一条 `QualityPolicy` | 从包里按边界切片后插入 |
| `persistence/schema.py` | `_V28`…`_V31` 四个常量 + 四条迁移项 | 从包里切片后插入 |

`data/analytics.py` 是第五种形状：它的改动是**泛化**（把写死的
`dataset_id='cn.equity.daily_bars'` 变成参数），方向对，
但外部实现把现有的中文 docstring 全删了 —— 那里记着 P3-13 与 P3-R2 的理由。
⇒ 手工把泛化搬过来，docstring 原样留下。

### 🔴 切片时踩到的坑：`index()` 取到了同名注释的第一处

第一次切 schema 的 v28–v31 块用的是：

```python
start = src.index("# Phase 4 Review & Measurement Foundation")
end   = src.index("#: (版本号, SQL)。只许在末尾追加，不许改动已发布的条目。")
block = src[start:end]
```

那条注释在文件里**出现两次**（P3 当初也是这么插进去的，留下了一条孤儿副本）。
`index()` 取到第一处，于是 `end < start`，`block` 成了**空字符串** ——
而 `str.replace(anchor, "" + anchor)` 是恒等操作，**不报错**。

结果是 `MIGRATIONS` 里多了 `(28, _V28)` 而 `_V28` 从未定义，
`NameError` 在 import 时才炸。

> 通用原则：**切片的两个锚点必须来自同一次扫描方向。**
> 混用 `index` 与 `rindex` 之前，先确认锚点在文件里是不是唯一的。

改成 `rindex` 之后一次通过，7530 字符。

---

## 改掉的四处

### ① `review_subjects` 的唯一键：一列 → 三元组

外部**实现**写的是 `UNIQUE(decision_record_id)`，
外部**设计文档 §9.1** 写的是 `UNIQUE(decision_record_id, target_policy_id, anchor_policy_id)`。

今天两者等价（v1 只有一对 policy）。代价在将来：

- `review_subjects` 是 append-only（触发器强制）
- 设计文档自己写着 AnchorPolicy 的 `09:20` cutoff「不是永恒真理，未来发新 policy id」
- 一列唯一键 ⇒ 发新 policy 的那天，同一张卡**没有第二个 id 可用**，只能重建表，
  而那时 `outcome_sets` 已经有外键指着它

**v28 还没发布 ⇒ 这是免费修它的最后一刻。**

连带三处：身份函数带上 policy 对（后缀取 8 位哈希，不把 policy id 原样拼进去 ——
policy id 里有点号，拼进去会让 id 的分段取决于它有没有分隔符，那是 L-13）；
`list_uncaptured_decision_records()` 按三元组 join；
`save_review_subject()` 的存在性查询同改。

🔴 **最后那条才是真正会咬人的地方**：只按 `record_id` join 的话，
新 policy 下补采会**一条都不返回**，看起来像「没有欠账」。

### ② ReviewLabel 词表的第三份副本漏了一个值

三处手写：`contracts._REVIEW_LABEL`（6 个）、schema v30 的 `CHECK`（6 个）、
`ReviewPolicyDefinition.__post_init__` 里的 `required`（**5 个，漏 `ABSTAIN_SYSTEM`**）。

后果很具体：设计文档 §10.3 要求「系统/数据原因的 BLOCK 归 `ABSTAIN_SYSTEM`、
不进方向比较」，而那个值在契约层允许、在存储层允许，
**却会被 policy 构造函数当场拒掉**。

⇒ `contracts.REVIEW_LABELS` 成为唯一手写处，registry 从它派生。
schema 的 `CHECK` 是 SQL 文本派生不了，由一条测试对账。

这是开发流程第五问的第 N 个实例：**一份清单在别处已经有权威答案，这里又手抄了一份。**

### ③ `query_index_eod_between` 的 `data_root` 是个没有作用的形参

外部实现给新函数留了它，只为与 `query_eod_between` 长得一样。
而 P3-R2 之后 Parquet 路径来自控制面里的 `storage_uri`，`_sql_path()` 只做引号转义 ——
这个形参一点作用都没有。

讽刺的是，**包自带的测试自己证明了这一点**：它传 `data_root=tmp_path`（一个空临时目录）
并断言返回 `[]`，而那个 `[]` 来自被 monkeypatch 掉的选择器，与 `data_root` 无关。

> 一个「看起来能把查询指向另一个 lake、其实不能」的参数，比没有这个参数更糟。

⚠️ `query_eod_between` 那个**保留** —— 它有既有调用方（`data/cli.py` 与 8 处测试），
属于 API 兼容而不是新增表面。两者的区别不是「一致性」，是**有没有人已经在传它**。

### ④ 两条「钉死 v27」的断言

`assert init_schema(db) == SCHEMA_VERSION == 27` 在 v28 之后必然红。
外部实现放宽成 `>= 27`，方向对，但留下一个叫
`test_schema到v27且八张地基表都在` 而实际断言 `>= 27` 的测试名。

⇒ 顺手改名成 `test_schema至少到v27且八张地基表都在`，并把「这条真正守的是什么」
写进注释：**地基表在 v27 建齐、且后续版本没把它们弄丢。**

---

## 坑

### 探针 2 在 collect 阶段就炸了，而那是**对的**

给「三处词表同源」配探针时，把 registry 的派生改回手抄的 5 个值，
预期是那条测试 FAILED。实际拿到的是：

```
ERROR collecting tests/test_p4_batch1.py
E   ValueError: review labels 必须与 contracts.REVIEW_LABELS 一致；少了 ['ABSTAIN_SYSTEM']
```

`import` 期就炸了 —— 因为 registry 的 v1 实例是模块级常量，
构造时就会跑 `__post_init__`。

第一反应是「探针没打中」。**不是。** 收集期报错同样是「它会红」的证明，
而且是更强的那种：这个错误在**任何**导入 `review` 包的地方都会炸，
而不是只在那一条测试里。

> 🔴 **判据是「破坏后红、还原后绿」，不是「红在哪一行」。**
> 第 66 章那条「缺后半句会把一直红读成红了」的教训，反过来也成立：
> 红的形式不对，先问它是不是**更早**地红了。

### 还原之后必须清 `__pycache__`（第 61 章的老账）

三次探针都是等长或近等长替换，且还原发生在同一秒内 ——
`mtime + size` 两个判据都没变，`.pyc` 会被认定仍然有效。
每次还原后 `find . -name __pycache__ -exec rm -rf {} +`，不是可选项。

---

## 验证

```bash
cd ~/.openclaw-biga/workspace

# ① v1–v27 的迁移体与合并前逐字相同
python3 - <<'PY'
import subprocess, sys; sys.path.insert(0, 'src')
old = subprocess.run(["git","show","HEAD:src/easyup_biga/persistence/schema.py"],
                     capture_output=True, text=True).stdout
ns = {}; exec(compile(old, "old", "exec"), ns)
from easyup_biga.persistence.schema import MIGRATIONS as NEW
OLD = ns["MIGRATIONS"]
assert [v for v,_ in OLD] == list(range(1, 28))
assert all(a == b for a, b in zip(OLD, NEW[:27])), "v1-v27 被改动了"
print("v1-v27 逐字相同 ✅ 新增:", [v for v,_ in NEW[27:]])
PY

# ② Phase 3 的成果一条都没回退
python3 tools/verify/phase3_acceptance.py --code-only   # 期望 exit 0
python3 -c "
import sys; sys.path.insert(0,'src')
from easyup_biga.data.registry import get_dataset
assert get_dataset('cn.security_master').fallback_providers == ('sina_security_master',)
assert get_dataset('cn.equity.daily_bars').primary_provider == 'sina_eod'
assert get_dataset('cn.news.flash').primary_provider == 'cls_news'
import easyup_biga.persistence as P; assert 'latest_trading_day' in P.__all__
print('Phase 3 成果未回退 ✅')"

# ③ Phase 4 的两个测试文件
python3 -m pytest -q tests/test_p4_0.py tests/test_p4_batch1.py
```

预期：① 打印 `新增: [28, 29, 30, 31]`；② 两行 ✅ 且闸门 exit 0；③ 全绿（1 条 `installed` 跳过）。

---

## 本章要点

| # | 要点 |
|---|---|
| 1 | **交付方自报的基线是最先该读的一行** —— 它比任何 diff 都快地告诉你整份覆盖会退掉什么 |
| 2 | 基线差 21 个版本时，前几章的「三方合并」判据不适用；只能**按层只取新增段** |
| 3 | 回退型破坏**一条都不会报错**，它只是让某天的降级路径悄悄消失 |
| 4 | 切片的两个锚点必须来自同一次扫描方向；`end < start` 时 `replace` 是恒等操作、不报错 |
| 5 | 一个未发布的 schema 版本是**免费修唯一键的最后一刻**；append-only 表的约束改不动 |
| 6 | 「未捕获」这类概念一旦**相对于某个版本**，join 条件漏掉那个版本就会报「没有欠账」 |
| 7 | 孪生清单的第 N 份副本漏一个值时，症状是「契约允许、存储允许、构造函数拒绝」 |
| 8 | 一个没有作用的形参比没有这个形参更糟；**包自带的测试有时会自己证明这一点** |
| 9 | 探针在 collect 阶段炸同样是「它会红」，而且是更强的那种 |
| 10 | 放宽一条断言之后要顺手改名 —— 名字与断言不符的测试，读的人会以为它还在守原来那件事 |
