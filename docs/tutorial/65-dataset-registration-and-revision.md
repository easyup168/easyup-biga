# 65 · 注册一个数据集，以及「修订」原来一次都没成功过（Phase 3 · P3-4 收口）

> 📄 **过程** · 写完即冻结
> **覆盖**：把 `cn.equity.daily_bars` 真正接进注册表的全过程；四个候选里
> 为什么只有一个能进；以及注册之后第一条端到端测试挖出的那个 bug
> **不覆盖**：P3-11…P3-17 的回放/验收层（第 66 章），P3-6 的 skill 迁移

---

## 目标 / 产出

上一轮把外部实现包的 P3-4/P3-5 四个 dataset 模块合进了
`src/easyup_biga/data/datasets/`，测试全绿。这一章做完会发现：

- 那四个模块当时**一个都跑不了**
- 四个里最终只有一个够格进注册表
- 进册之后写的第一条端到端测试，立刻挖出一个让**同一个交易日出现两套价格**的 bug

产出是 `cn.equity.daily_bars` 进册 + `tests/test_eod_dataset_live.py` 十条行为判据。

---

## 为什么这么做

### 一、「有测试」和「有能跑到底的路径」是两件事

上一轮合完，四个模块的测试全绿，我据此写了「P3-4/P3-5 部分落地」。

第二轮加完发布闸门，`--code-only` 第一次运行就打脸：

```text
❌ P3-4 尚未落地 ⇒ 注册表里没有：['cn.equity.daily_bars', 'cn.security.tradability']
❌ P3-5 尚未落地 ⇒ 注册表里没有：['cn.equity.adjustment_factors', 'cn.market.emotion_close']
```

四个模块引用的 dataset id **从没进过注册表**。而每条发布路径的第一行都是
`get_dataset(dataset_id)` —— 一调就抛。它们是死代码。

为什么测试全绿？因为那些测试测的是 `normalize()` / `derive()` /
`write_parquet_rows()` —— 全是**不经过注册表**的下层函数。

> 🔴 **通用原则：一个模块的单元测试全绿，只说明它的零件对。
> 「这些零件连起来能不能从头跑到尾」是另一个判据，要另外写。**
> 判断标准很朴素：**有没有一条测试是从真正的入口函数调进去的。**

### 二、四个里为什么只有一个能进

`DatasetDefinition` 的契约里有一句写死的话：

```python
if not self.consumers:
    raise ValueError(f"{self.dataset_id}: consumers 不能为空 —— "
                     f"答不出谁读它，这条就还不该进册（零消费方 = L-1 死配置）。")
```

于是「能不能注册」这个问题有一个**机械的**判据：答得出谁读它吗。逐个问：

| dataset | 谁读它 | 结论 |
|---|---|---|
| `cn.equity.daily_bars` | `analytics.query_eod_as_of` / `query_eod_between` —— 真的扫 `lake/` 下的 Parquet | ✅ |
| `cn.security.tradability` | **没有**。它被算出来、当场用掉（当日线覆盖率的分母），从没被读回过 | ❌ |
| `cn.equity.adjustment_factors` | **没有**，连写入方都没人调 | ❌ |
| `cn.market.emotion_close` | **没有**，同上 | ❌ |

这不是「漏了三行注册」。三个模块**真的还没有读取方**，而注册表里多一条
没人读的记录，就是它专门要防的那种死配置。

> 通用原则：注册表的价值全在「它描述的是事实」。一条「以后会有人读」的
> 记录会让读者以为这条链已经有人管了 —— 而那恰恰是没人管的那条。

顺带查出 `tradability` 原本那条发布路径还有个更具体的问题：

```python
raw = json.dumps([item.to_dict() for item in records], ...)   # ← 派生结果
publisher.publish(..., raw_text=raw, rows=[item.to_dict() for item in records])
```

它把**自己的输出重新序列化**当原始响应存。回放去校验这份 raw，校验的是
「我刚写的文件还是我刚写的样子」—— 证明不了任何关于出处的事，而 Parquet
分区里存的又是同一批行，等于同一份数据存了两遍。

> 裁定 16 那句正好适用：**凑出来的溯源比没有溯源更糟，它会让人以为查得到。**

⇒ `run_eod_bundle` 改成只发布日线，可交易性留在结果里作覆盖率的分母。
等它真有读取方（筛选/复盘要区分「停牌」与「缺数据」）再进册，那时它的
raw 应当指向**同一份 provider 响应**，而不是自己的输出。

### 三、被自己的守卫拦一次，是好事

注册完跑全量，红了一条：

```text
test_只激活已有生产持久链的数据集
  Extra items in the left set: {'cn.equity.daily_bars'}
```

它的 docstring 早就写着为什么：

> 🔴 这条会随 P3-6 的每次迁移 PR 而改 —— 那是**有意的**：改这条测试就是在
> 声明「又有一个数据集归平台管了」，逼人做一次明确的动作，而不是往注册表里
> 悄悄加一行。

于是改它的时候，顺手把「另外三个为什么没进」写进了注释。**这条守卫真正的
产物不是那个断言，是它逼出来的那段说明。**

---

## 坑

### 坑 1 · 修订功能原来一次都没成功过

注册完，写端到端测试。第七条是「修订走新版本且不改写旧分区」：

```text
assert snap["data_version"] == 2
E       assert 1 == 2
```

`new_revision=True` 发布出来的还是 v1。查到 `DatasetSnapshotService.publish()`：

```python
existing = find_dataset_snapshot(dataset_id, partition_key, status=COMPLETE)
if existing is not None:
    return ...reuse...          # ← 完全没看 request.data_version
```

**只要该分区已经有一份 COMPLETE，请求 v2 也原样退回 v1。**

后果不止「修订失败」。调用方是**先写 Parquet、再进账本**的，所以 v2 的文件
已经落盘：

```text
盘上的 Parquet：
    trade_date=20260925/data_version=000001/part-00000.parquet
    trade_date=20260925/data_version=000002/part-00000.parquet
控制面认得的分区：
    v1 只有一个

query_eod_between（扫盘取最高版本）→ [99.0, 100.0, 101.0, 102.0, 103.0]
query_eod_as_of（走控制面）        → [10.0,  11.0,  12.0,  13.0,  14.0]
```

**同一个交易日，两条查询给出两套完全不同的价格，两边都不报错。**
这正是裁定 15 要防的「某天悄悄给出两个数」。

修法两条：

1. 幂等判据改成 `existing["data_version"] >= request.data_version`
2. `data_version > 1` 时**当场**要求它指向当前有效快照，不指就抛

> 🔴 第 2 条为什么不能只靠事后的 `audit_revision_chain`：等审计发现断链时，
> 两份快照都已经落库了，而「哪份是当前有效的」已经答不出来。
> **不变量要在写入时守，审计是第二道防线不是第一道。**

### 坑 2 · 探针瞄错了地方（第二次）

给「零消费方」这条写探针：把契约里那个 `raise ValueError` 短路掉，
期待 `test_没有零消费方的已激活dataset` 变红。

**它没红。** 因为契约在**构造时**就挡住了空 consumers，注册表里永远不可能
出现那种 dataset，测试的那半分支根本到不了。

两层守卫各守各的：契约守「构造」，测试守「consumers 指的符号真的存在」。
重瞄到后者（把一个 consumer 改成不存在的符号）立刻红。

> 通用原则：**探针要打在被测判据实际生效的那条路径上。**
> 两层守卫叠在一起时，破坏下层证明不了上层有用 —— 只证明下层有用。

---

## 执行

```bash
# 1. provider 进册（与 security_master 同站点 ⇒ 同一个 source 前缀 em）
# 2. dataset 进册
# 3. quality policy 进册（必须写清「不检查什么」）
# 4. eod_pipeline 停止发布 tradability

python3 -m pytest tests/test_eod_dataset_live.py -q
# ..........                                                               [100%]

python3 tools/verify/phase3_acceptance.py --code-only
#   ❌ P3-4 尚未落地 ⇒ 注册表里没有：['cn.security.tradability']     ← 从 2 个缩到 1 个
```

---

## 验证

```bash
# 数据集名册多了一条，且能跑
./bin/biga-data list --json | python3 -c "import json,sys; print(sorted(d['dataset_id'] for d in json.load(sys.stdin)))"
# ['cn.equity.daily_bars', 'cn.index.daily_bars', 'cn.security_master', 'cn.trading_calendar']

# 端到端十条 + 探针
python3 -m pytest tests/test_eod_dataset_live.py tests/test_data_registry.py -q
```

预期：全绿；`bin/biga-data list` 显示 4 个数据集。

---

## 本章要点

| # | 要点 |
|---|---|
| 1 | **「有测试」和「有能跑到底的路径」是两件事。** 四个模块单元测试全绿，而它们引用的 dataset id 从没进过注册表 —— 入口函数一调就抛 |
| 2 | 判断标准很朴素：**有没有一条测试是从真正的入口函数调进去的** |
| 3 | 「能不能注册」有机械判据：**答得出谁读它吗。** 四个里只有一个答得出 |
| 4 | 剩下三个不是漏注册，是**真的还没有读取方** —— 注册它们等于替一件没人做的事背书 |
| 5 | `tradability` 把**自己的输出重新序列化**当 raw 存 ⇒ 回放校验的是「我刚写的文件还是我刚写的样子」。**凑出来的溯源比没有溯源更糟** |
| 6 | 🔴 修订功能**原来一次都没成功过**：幂等判据完全不看 `data_version` |
| 7 | 后果不是「修订失败」：调用方先写文件后进账本 ⇒ 盘上有 v2、控制面只认 v1，**两条查询给出两套价格，都不报错** |
| 8 | 不变量要在**写入时**守。等审计发现断链，两份快照都落库了，「哪份有效」已经答不出来 |
| 9 | 被自己的守卫拦一次是好事 —— 它逼出来的那段说明，比那个断言值钱 |
| 10 | 探针要打在**被测判据实际生效的那条路径**上。破坏下层证明不了上层有用 |
