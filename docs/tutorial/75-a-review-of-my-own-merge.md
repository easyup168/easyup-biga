# 第 75 章 · 一份针对我自己那次合并的评审（Phase 4 · P4-R1）

> 📄 **过程 · 写完即冻结**（2026-09-27）
> **覆盖**：合 P4-R1（14 条评审发现的修复）的过程、评审替我抓到的两个 bug、以及我在它的修复里又改掉的两处 ｜ **不覆盖**：Phase 4 的设计裁定（见 [`../design/phase-4-review-measurement.md`](../design/phase-4-review-measurement.md)）

---

## 这一份和前五份都不一样

前五份交付的基线都不是我们这棵树（`0.9.2.dev0`、上一批的原版、冻结 zip……），
所以每一次都要先分辨「哪些是新增、哪些是把我的修改推平」。

这一份的 `PHASE4_DEEP_REVIEW_REPORT.md` 第一行写着：

```
Baseline reviewed: easyup-biga-phase4.zip
Baseline project version: 0.9.28.dev0
Baseline schema: v31
```

`0.9.28.dev0` **就是我们的 HEAD**。用第 66 章那条判据反过来数一遍 ——
我那 16 处修正在它的包里**一处不少**：

```
✅ REVIEW_LABELS 唯一手写      ✅ horizon 从名字派生
✅ policy 三元组身份            ✅ uncaptured 按三元组 join
✅ stance 导入期对账            ✅ MeasurementRun 身份校验
✅ schema 前置检查              ✅ query_index_eod_between 仍无 data_root
…
```

⇒ 第一次可以**整份落盘再用 `git diff` 复核**，而不是逐块切片。

> 通用原则：**「基线是不是你这棵树」决定了合并方法，而它写在交付的第一页。**

---

## 评审替我抓到的两个真 bug

### ① F13：我在 Batch 4 制造了一处重复定义

`review/contracts.py` 里 `review_id_for` 与 `review_content_payload` 各有**两份**，
内容逐字相同。

怎么来的：Batch 3 把这两个函数插在 `class ReviewSubject` 之前；
Batch 4 我从包里切 `measurement_*` 四个函数时用的区间是

```python
start = src.index("def measurement_cohort_payload(")
end   = src.index("@dataclass(frozen=True, slots=True)\nclass ReviewSubject:")
```

而在**批 4 那个包里**，`review_id_for` / `review_content_payload` 恰好落在这两者之间 ——
于是它们被连带切了进来，插到了已经存在的那一份后面。

> 🔴 **两份内容完全相同 ⇒ 后者覆盖前者 ⇒ 行为零差异 ⇒ 没有任何测试会红。**
> 这正是「切片式合并」独有的失败模式：切片的边界由**别人那棵树的布局**决定。

第 70 章记过切片的另一个坑（锚点扫描方向），这是同一家族的第二个。
判据应当是：切完之后 grep 一遍新块里的**顶层 `def`**，看有没有已经存在的。

### ② F01：`max(retrieved_values)` 是字符串 max

`data/datasets/index_daily.py` 里：

```python
knowledge_cutoff = max(retrieved_values)       # ← 字符串比较
```

`"2026-01-01T02:00:00+01:00"`（01:00 UTC）与 `"2026-01-01T08:00:00+08:00"`（00:00 UTC）：
**前者是更晚的时刻，却排在后面。**

这条我在 Batch 1–5 五轮里一次都没看出来。它今天不咬人只因为所有写入方都用
`now_cn()`（生产库实测 1326 行全是 `+08:00`），而那是**约定不是强制**。

---

## 我在 R1 的修复里又改掉的两处

### ③ `finalizer.py` 把判据挂回了错误文案上

```python
static_code_ready = not [
    e for e in code_errors
    if e != "missing source-bound FULL_REGRESSION PASS for current tree/environment"
]
```

这个形状本仓库 Phase 3 已经点名过一次，`data/finalizer.py` 的模块头原话：

> 外部实现把两类错误堆进同一个 list，再用 `e.startswith(...)` 之类的前缀把它们捞回来。
> 那是 L-13 的形状：**判据挂在文案上**。今天它碰巧对，因为文案恰好互不重叠；
> 把某条错误改个措辞，`code_ready` 就会悄悄翻面，而没有任何测试会红。

而**R1 这次改动本身就是把那句文案改了**（`missing verified ...` → `missing source-bound ...`），
两处同时改对了才没出事。

探针实测：保留文案判据、只把消息里的 `for current` 改成 `for the current` ——
`static_code_ready` 当场翻面。

⇒ 从头分成 `static_errors` 与 `regression_errors` 两个 list；
`code_errors` 只在**给人看**的时候合起来。

⚠️ 第一次给这条修复配探针时，它**没红** —— 因为我根本没写测试。
第 74 章那条「探针没红时先问是守卫没用还是场景触发不到」，这次的答案是第三种：
**守卫不存在。** 补了一条不看任何文案、只看两个布尔组合的测试。

### ④ F11 的对账少了一层

R1 给 `MeasurementPolicy` 加了 `required_analysis_keys`，并在**导入期**断言
它与模块常量 `_CONFIRMATORY_KEYS ∪ …` 相等。方向对，但只对了两层中的一层：

```
policy  ←→  模块常量      R1 在导入期查了 ✅
模块常量 ←→ 真正产出的 key  没人查 ❌
```

给 `_confirmatory()` 加一条 spec 而两张清单都不动时，导入期检查照样通过，
那条分析只是**悄悄不进** `required_analysis_keys` —— 于是 Evidence Ready 不要求它。

⇒ 判据落到**真正产出的东西**上：`_confirmatory()` 跑完时对账一次。
探针加一条 `P4-H05:extra`，当场 `RuntimeError`。

---

## 🔴 夹具不现实，不是行为错了

整份落盘后全量跑出 **10 条红**，全在 Phase 1–3 的老测试里：

```
ValueError: retrieved_at must be valid ISO-8601   (Invalid isoformat string: 'b')
ValueError: retrieved_at must include timezone    ('2026-01-01T08:00')
```

R1 给 `save_trading_calendar()` 等写路径加了 `cn_iso()` 规范化（严格要求带时区），
而老夹具传的是 `retrieved_at="b"` / `"t"` 这类占位串。

按第 57 章那条先分清：

| 查什么 | 结果 |
|---|---|
| 生产库 `fact_trading_calendar.retrieved_at` | 1326 行**全部** `+08:00` |
| 生产写入方传的是什么 | `retrieved.isoformat()`（aware） |
| 同一个测试文件别处怎么写 | `"2026-09-19T15:00:03+08:00"` —— realistic |

⇒ **红的是夹具。** 占位串是偷懒，不是设计。改成真实时间戳。

### 顺带发现：那条测试原本证明不了它声称的事

`test_取最新一条_按retrieved_at` 我第一版改成了跨偏移（`+08:00` vs `+01:00`），
想顺手证明 F01。**但写路径现在会把两者都规范化成 `+08:00`** ——
字面序于是也对，这条用例证明不了读路径的排序修复。

⇒ 拆成两条：
- 原测试保持简单（同偏移，测「取更晚那条」）
- 新增一条**直接 INSERT** 两行不同偏移的数据，绕开写路径的规范化 ——
  它测的是「写路径规范化之前落下的行」这个真正的风险对象

探针把 `is_trading_day` 的排序改回字面序，新测试当场红；旧测试不红。

> 通用原则：**写路径的规范化会掩盖读路径的缺陷。**
> 想测读路径，就得绕开写路径 —— 而绕开的那条路必须对应一个真实存在过的场景。

---

## 一处需要注意的时机

F07 把 `market.index_blend.v1` 的 MFE/MAE 从「平均两个指数各自的日内高低」
改成「同步的日收盘路径」。按设计文档 §7.3，**改变历史值的规则应当发新的
`OutcomePolicy` v2**。

它能原地改 v1，**只因为生产库里一条 `OutcomeSet` 都还没有** ——
schema 昨天才迁到 v31，`catchup` 还没跑。

> 换个顺序（先 catchup 再合 R1），这条修复就得是 `market-h1-h3-h5-h20-v2`，
> 而 v1 会永远留着一批用旧公式算的行。

---

## 验证

```bash
cd ~/.openclaw-biga/workspace

# ① 我的 16 处修正一处没丢（含 F13 的重复已消）
python3 -c "
import sys, re, pathlib; sys.path.insert(0,'src')
s = pathlib.Path('src/easyup_biga/review/contracts.py').read_text(encoding='utf-8')
assert len(re.findall(r'^def review_id_for', s, re.M)) == 1
assert 'REVIEW_LABELS: tuple' in s and 'def horizon_trading_days' in s
print('契约层 ✅')"

# ② F08：没有库也能跑代码面校验
./bin/biga-review --db /nonexistent/x.db verify --code-only ; echo "exit=$?"   # 期望 0

# ③ 跨偏移排序
python3 -m pytest -q "tests/test_szse.py::TestFailDirectionUnchanged::test_跨时区偏移的旧行也按绝对时刻排"

# ④ 三个闸门
python3 tools/verify/phase3_acceptance.py --code-only ; echo "exit=$?"   # 0
python3 tools/verify/phase4_acceptance.py --code-only ; echo "exit=$?"   # 0
```

---

## 本章要点

| # | 要点 |
|---|---|
| 1 | **「基线是不是你这棵树」决定合并方法，而它写在交付的第一页** |
| 2 | 基线相同 ⇒ 整份落盘再用 `git diff` 复核，比逐块切片更安全 |
| 3 | 🔴 **切片式合并的独有失败模式：切片边界由别人那棵树的布局决定** —— 我因此复制了一对已存在的函数 |
| 4 | 两份内容完全相同的定义 ⇒ 行为零差异 ⇒ 没有任何测试会红；判据是切完 grep 一遍顶层 `def` |
| 5 | `max()` 作用在 ISO 字符串上，跨 UTC 偏移会选错 —— 五轮评审我一次都没看出来 |
| 6 | 「所有写入方都用 `now_cn()`」是**约定不是强制**，直到写路径真的开始拒绝 |
| 7 | 判据挂在错误文案上时，**改文案的那次改动自己就是触发条件** |
| 8 | 探针没红的第三种原因：**守卫根本不存在**（前两种见第 74 章） |
| 9 | 对账要查到**真正产出的东西**，只查「常量 ↔ 常量」会漏掉「常量 ↔ 行为」 |
| 10 | 🔴 **写路径的规范化会掩盖读路径的缺陷** —— 想测读路径就得绕开写路径 |
| 11 | 改公式本该发新 policy 版本；能原地改只因为**还没有一行历史数据** |
