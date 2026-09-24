# 第 51 章 · 让证据说清自己是什么（裁定 16 · 批 3）

> 📄 **过程** · 写完即冻结
> **覆盖**：契约铁律「派生值必须说得出出处」、`input_ids_for` 血缘解析、
> 五个 skill 的 `kind` 显式声明与多输入接线 ｜
> **不覆盖**：risk 的 444 条跨 verdict 引用（要「引用别的 verdict 里的证据」，
> 机制不同）、跨源聚合（`volume_total` / `board_counts` / `market_open`）——
> 它们**故意留 `kind=None`**，原因见「留白不是漏做」

## 目标 / 产出

| 产出 | 内容 |
|---|---|
| 契约铁律 | `kind="derived"` ⇒ 必须有 `raw_hash` **或** `input_evidence_ids` |
| `input_ids_for` | 写字段名，解析成 `evidence_id`；找不到就抛 |
| 五个 skill | `add()` 系列的 `kind` **无默认值**，41 个调用点逐个声明 |
| 解耦修复 | emotion 的指纹不再受 `store` 开关门控 |
| 探针 | `test_evidence_kind_declared.py` 21 条，4 处 sabotage |

## 为什么这么做

### 1. 铁律不是「派生一律要 inputs」

最初的想法是「`derived` ⇒ 必须声明 `input_evidence_ids`」。写到一半发现它**对一大类是错的**：

```python
add(f"ma{n}", v, f"MA{n}", f"derived:{src}")   # technical 的 MA5
```

`ma5` 是从**整份 K 线**算出来的。批 2 之后它的 `raw_hash` 已经指回那一份 raw —
出处完整，再要一串 evidence id 只能硬编一个，那正是裁定 16 禁止的「硬凑」。

⇒ 规则改成**两条出路，必须占其一**：

| 情形 | 怎么说清出处 |
|---|---|
| 从**一份**原始响应算出来（MA、涨跌幅）| `raw_hash` 指回那一份 |
| 从**别的值**算出来（炸板率 = 炸板/(涨停+炸板)）| `input_evidence_ids` |

> 通用原则：铁律要按**证据实际长什么样**来定，不是按「哪条规则听起来更严」。
> 一条覆盖不到真实形态的严规则，逼出来的只会是假数据。

### 2. `kind` 不给默认值

```python
def add(field, value, label, source, *, kind: str | None, inputs=()) -> None:
```

给 `kind` 一个默认值，「忘了想这条是什么」和「想过了，就是这个」**写出来一模一样**。
而裁定 16 要的恰恰是让人想一遍。41 个调用点逐个声明，是这条裁定的全部成本。

> 通用原则：要人做判断的参数不能有默认值。
> 默认值把「未决定」伪装成「已决定」，而两者的区别正是你想记录的东西。

### 3. 留白不是漏做

有 6 个字段**故意留 `kind=None`**（合法的「尚未归类」），并在调用点写明原因：

```python
# 🔴 下面三个的 kind 留空（未声明），**不是漏写**：
#    它们是两份日线快照的跨源聚合 —— 既不出自单一响应（raw_hash 填不了），
#    也不是从某几条已有证据算出来的（今天没有 per-symbol 的成交量证据）。
#    硬编一组 inputs 就是裁定 16 明令禁止的"硬凑"。
add("volume_total", ..., "sina:kline", kind=None)
```

`volume_total` 是沪深两市之和、`volume_ma20` 还用了 20 天历史序列、
`board_counts` 是行业榜+概念榜的合计、`market_open` 出自交易日历+时钟。
四类的共同点：**Evidence 今天只能记一个 `raw_hash`**，而它们有多个来源。

> 通用原则：遇到表达不了的情况，**留白并写明原因**，不要编一个能过检的值。
> 一个诚实的 `None` 加三行理由，比一个凑出来的 id 列表有用得多 ——
> 后者会让下一个人以为这条链是通的。

### 4. 铁律一上线就抓到一个真问题

接完 emotion 立刻红了四条测试：

```
ValueError: Evidence(field='max_streak', kind='derived') 既没有 raw_hash
也没有 input_evidence_ids
```

查下去不是夹具问题：

```python
self._keep(pool, r)
if self.store:                       # ← 指纹写在这里面
    self.hashes[f"em:push2ex/{pool}"] = raw_text_sha256(r.raw_text)
```

**指纹的计算被 `store` 开关门控了。** 于是 `--no-store` 跑出来的证据全都没有
`raw_hash` —— 同一段代码、同一份数据，**可追溯性却取决于一个与追溯无关的开关**。

market / sector / news 三个 skill 本来就是先算哈希再判 store，emotion 是唯一的例外。

> 通用原则：一个属性属于**数据**还是属于**这次运行的选项**，要分清。
> 哈希是那份响应的属性，与「我们这次存不存盘」无关。
> 把前者写进后者的分支里，就会得到「同样的输入、不同的可追溯性」。

## 执行

```bash
domain/evidence.py       铁律：derived ⇒ raw_hash 或 input_evidence_ids
domain/provenance.py     input_ids_for(evidence, fields, of=...)
skills/*/scripts/*.py    add() 系列加 kind（无默认值）+ inputs；41 个调用点
emotion_calc.py          指纹计算移出 `if self.store`
```

接完之后 emotion 的血缘链是真的两跳：

```
emotion_score ← limit_up_count, max_streak, broken_rate
                                            └← limit_up_count, broken_board_count
```

## 坑

### 坑 1 · 用正则改多行调用，只吃到了单行的那些

给 technical 的 11 个调用点加 `kind=` 时用了一条正则，它只匹配单行 `add(...)`。
四个多行调用（`price_vs_ma20_pct` / `ma_order` / 两个 `dist_to_*`）没被改到，
测试报 `missing 1 required keyword-only argument: 'kind'`。

改法是换成**按括号配平取整块**再补在末尾。

> 通用原则：对**调用**做批量改写，正则的单位是「行」，而调用的单位是「配平的括号块」。
> 两者不一致时，正则会安静地漏掉多行的那些。

### 坑 2 · 补在第一个参数后面 ⇒ 位置参数跟在关键字参数后

第二次尝试把 `kind="derived",` 插在调用的**第一行末尾**，于是：

```python
add_live("industry_bottom", [...], kind="derived",
         "行业榜末尾", "em:clist/industry")      # ← SyntaxError
```

关键字参数后面不能再有位置参数。同样得按块取、补在**收尾的 `)` 之前**。

### 坑 3 · 写了个"自动补全"脚本，它把类别标错了

漏网的调用点用脚本统一补 `kind="derived"`，结果把 market 的
`advance_count` / `decline_count` / `flat_count` 也标成了 derived ——
它们是涨跌家数接口**直接返回的计数**，是 `observed`。

> 通用原则：`kind` 是**语义判断**，不能批量填。
> 脚本可以帮你找出漏了哪些，但每一个的答案得自己看一眼 ——
> 这恰恰是「不给默认值」想逼出来的那个动作，我差点用脚本把它绕过去。

### 坑 4 · 一条测试钉了整行源码，加个参数就红

`test_news自己那个同名字段不受影响`（E-19 留下的）断言的是一整行源码：

```python
assert 'add("staleness_sec", stale, "距最新一条(秒)", src)' in src
```

它的**意图**是「news 的同名字段没被顺手统一掉」，钉的却是整行 ——
批 3 给 `add()` 加参数时它就红了。判据放宽到「字段名 + 标签」这对组合。

> 通用原则：源码级判据要钉**意图所在的那几个 token**，不是整行。
> 钉整行等于给所有无关改动埋一颗雷。

## 验证

```bash
cd ~/.openclaw-biga/workspace
python3 -m pytest tests/test_evidence_kind_declared.py -q     # 期望 21 passed

# 血缘链真的连上了（离线）
python3 - <<'PY'
import sys, json, importlib.util, dataclasses
sys.path.insert(0, "skills")
spec = importlib.util.spec_from_file_location(
    "emotion_calc", "skills/emotion-calc/scripts/emotion_calc.py")
ec = importlib.util.module_from_spec(spec); sys.modules["emotion_calc"] = ec
spec.loader.exec_module(ec)
import _sources as s
Q = "20260918"
def pool(n, t, rows=None):
    raw = {"rc": 0, "data": {"tc": t, "qdate": int(Q)}}
    return s.PoolResult(pool=n, requested_date=Q, qdate=Q, total=t,
                        rows=rows if rows is not None else [{"lbc":1,"zbc":0}]*t,
                        raw=raw, raw_text=json.dumps({"pool":n, **raw}))
plan = {"limit_up": pool("limit_up", 78, [{"lbc":n,"zbc":z} for n,z in
                         [(1,1)]*66+[(2,0)]*8+[(3,0)]*2+[(4,0)]*2]),
        "broken_board": pool("broken_board", 25), "limit_down": pool("limit_down", 0, [])}
ec.fetch_pool = lambda n, date, **kw: dataclasses.replace(plan[n], requested_date=date)
v = ec.build_fact_bundle(date=Q, store=False, task_id="BIGA-20260918-001",
                         break_source=set())
by = {e.evidence_id: e.field for e in v.evidence}
for e in v.evidence:
    if e.input_evidence_ids:
        print(f"{e.field} ← {', '.join(by[i] for i in e.input_evidence_ids)}")
PY
# 期望：
#   broken_rate ← limit_up_count, broken_board_count
#   emotion_score ← limit_up_count, max_streak, broken_rate
```

## 本章要点

| # | 要点 |
|---|---|
| 1 | 🔴 铁律按**证据实际长什么样**定，不按「哪条听起来更严」—— 覆盖不到真实形态的严规则只会逼出假数据 |
| 2 | 派生值两条出路：单一来源走 `raw_hash`，多输入走 `input_evidence_ids`，必须占其一 |
| 3 | 🔴 要人做判断的参数**不能有默认值** —— 默认值把「未决定」伪装成「已决定」 |
| 4 | 🔴 表达不了就**留白并写明原因**，别编一个能过检的值 —— 凑出来的链让下一个人以为它是通的 |
| 5 | 6 个跨源聚合字段故意留 `kind=None`：Evidence 今天只能记一个 `raw_hash`，而它们有多个来源 |
| 6 | 🔴 分清属性属于**数据**还是属于**这次运行的选项** —— emotion 把哈希写进了 `if store` 分支 |
| 7 | 同样的输入、不同的可追溯性，是那个耦合的直接后果（`--no-store` 跑出来的证据全无 raw_hash）|
| 8 | 正则改**调用**会漏掉多行的：正则的单位是行，调用的单位是配平的括号块 |
| 9 | 关键字参数后不能跟位置参数 ⇒ 补参数要补在收尾的 `)` 之前 |
| 10 | 🔴 `kind` 是**语义判断，不能批量填** —— 我写的自动补全脚本把三个 observed 标成了 derived |
| 11 | 源码级判据要钉**意图所在的 token**，不是整行 —— 钉整行是给所有无关改动埋雷 |
