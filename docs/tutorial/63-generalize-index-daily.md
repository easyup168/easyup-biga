# 63 · 把已在生产的冻结接进通用血缘（Phase 3 · P3-2）

> 📘 **过程** · 写完即冻结
> **覆盖**：P3-2 —— 通用 DatasetSnapshotService、index_daily 桥接、EvidenceSet v2、验收外部实现改掉的五处 ｜
> **不覆盖**：P3-1 的元数据表（见 [62 章](62-metadata-foundation.md)）、Parquet（P3-4）

---

## 目标 / 产出

P3-2 的红线是**什么都不变**：Agent 输出、Run、Card、raw hash 语义、手工单跑的
fallback 全部照旧，只是在**同一批不可变字节**上再发布一份标准血缘。

```text
src/easyup_biga/data/snapshots.py            通用发布服务
src/easyup_biga/data/datasets/index_daily.py 桥接器（不抓数据）
src/easyup_biga/data/quality.py              质量策略真名册
EvidenceSet manifest v2                      旧键逐字保留 + 两个新键
```

---

## 为什么这么做

### 这一章是第二次验收外部实现，重点在「哪些该改、哪些不该」

外部 P3-2 实现的**核心设计是对的**，而且有两处值得学：

**一 · `raw_artifact_id` 只在恰好一个 artifact 时才填。**
一个冻结 bundle 有 sh + sz 两份 raw，此时 `dataset_partitions.raw_artifact_id`
留空，由 `provider_attempts` 承担一对多的血缘边。它的注释原话是
「do not lie with one arbitrary FK」——**与仓库「不硬凑：凑出来的溯源比没有
溯源更糟」是同一条，独立到达。**

**二 · 发布前真的核对哈希。** 逐个 symbol 把冻结 raw 的 `content_sha256`
与 manifest 里记的比对，对不上就抛。一份「登记了但对不上原始字节」的血缘，
比没有血缘更糟。

改掉的五处见设计文档。这里只记最有教训价值的两条。

### 改掉的：`except KeyError:` 把两件事当成一件

它给「注入 fetcher + provider 未注册」留了一条退路，判据是：

```python
except KeyError:
    if not self._bridge_allows_unknown_provider:
        raise
```

而 `publish()` 有上百行，里面 `entry["snapshot_id"]`、manifest 字段缺失
**都会抛 KeyError**。于是一个真正的结构性 bug，在那条退路上会被**静默咽掉**：
冻结照常完成，manifest 悄悄退回 v1，没有任何地方报错。

⇒ 给「这个源没登记」一个**专有异常** `ProviderNotRegistered`，只咽它。

> 通用原则：**用异常类型表达「哪一件事」，不要用它的基类兜底。**
> 裸 `KeyError` 的意思是「某个 dict 查不到」，那几乎总是比你想说的宽。

### 改掉的：`source.split(":")[0]` 当 provider_id

那是**站点前缀**，不是适配器 id。对指数日线恰好相等，对交易日历就错——
`sina:calendar/...` 会被解析成 `sina`，而真实适配器是 `sina_calendar`。

⇒ `provider_for_source(dataset_id, source)`：按「**这个 dataset 登记了谁**」
与「前缀对得上的是谁」求交集，不唯一就 fail closed。

> 通用原则：**当一个值恰好能用，先问它是不是恰好。**
> 前缀等于 id 在这个数据集上成立，在下一个数据集上就不成立了。

---

## 坑

### 🔴 我删掉了一条分叉，然后被一条早就存在的测试按回去

外部实现的 seam 让「测试走的路」和「生产走的路」不是同一条，我据此把它
**整个删掉**，并且在注释里写了理由。快照/编排测试全绿。

跑全量才红：批 E-20.2 的 `test_换个provider落库的source跟着变`。
那条测试验的是**出处不会说谎**——fetcher 可注入，换一个 provider，落库的
`source` 必须跟着变。它**必须**注入一个未注册的 provider 才能测到这件事。

删掉 seam 等于要求那条测试改用注册过的源，而那样它就测不到它要测的东西了。

⇒ 恢复 seam，只改捕获范围。

> 🔴 教训：**「测试路径 ≠ 生产路径」不等于「那条分叉是错的」。**
> 先问那条分叉在为哪条测试服务。我跳过了这一步，理由听起来还很正当。

⚠️ 顺带：**局部绿不能代替全量。** 我改完跑了快照 + 编排三个文件、全绿，
差一点就那样收工了。那条红的测试在第四个文件里。

### 探针落错地方，弄坏了也不红

P15 想守的是「seam 只咽未注册，不咽结构性 KeyError」。第一版探针把
`except ProviderNotRegistered` 改成 `except KeyError`，然后跑
「严格路径会不会抛」那条测试 —— **没红**。

因为放宽捕获是个**超集**：严格路径走的是 re-raise 分支，两种写法都会抛。
那条测试根本区分不了。

⇒ 判据改成：**在调试缝上**让 `publish` 抛一个结构性 `KeyError`，断言它传出来。
这才是要守的那句话。

> 这是第 61 章那条教训的另一个形状：判据要打在**被守的那件事可达的地方**。
> 上次是「结构上不可能重复」，这次是「两种写法在这条路径上行为相同」。

### `partition_keys` 的第一个真实收益

`cn.index.daily_bars` 的切片键我抄自外部 P3-1 的 `(symbol, as_of)`。
P3-2 的桥按 `{"evidence_set_id": ...}` 发布 —— **P3-1 加的
`_check_partition_keys()` 当场抓到**。

正确答案是 `("evidence_set_id",)`：一次冻结发布**一个**分区，bundle 里有多个
symbol，按 symbol 切等于声称有多个分区而实际只发布一个。外部 P3-2 自己也改了
这个值。

> 上一章把那个字段从装饰品变成承重件，这一章它第一次承重。

---

## 验证

```bash
python3 -m pytest tests/test_index_daily_dataset_snapshot.py -q   # P3-2
python3 -m pytest tests/test_snapshot_provenance.py -q            # 批 E-20.2 不许红
python3 -m pytest -q                                              # 全量
```

六条新守卫的探针结论（每条都真的弄坏过）：

| 探针 | 弄坏什么 | 结果 |
|---|---|---|
| P12 | `quality_policy` 指向未注册的策略 | ✅ 红 |
| P12b | 策略不写「不检查什么」 | ✅ 红 |
| P13 | provider 解析退回取前缀 | ✅ 红 |
| P14 | manifest 自报出处不再与 raw 核对 | ✅ 红 |
| P15 | 捕获退回裸 `KeyError`（吞结构性 bug） | ✅ 红（判据改到可达处之后）|
| P16 | 血缘不再与主行同事务 | ✅ 红 |

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 外部实现两处值得学：**不拿随便选的外键撒谎**、**发布前真的核对哈希** |
| 2 | 🔴 用异常**类型**表达「哪一件事」——裸 `KeyError` 几乎总是比你想说的宽 |
| 3 | 一个值「恰好能用」时先问它是不是恰好：站点前缀等于适配器 id，只在这个数据集上成立 |
| 4 | 🔴 **「测试路径 ≠ 生产路径」不等于那条分叉是错的**——先问它在为哪条测试服务 |
| 5 | 局部绿不能代替全量：我跑了三个相关文件全绿，红的那条在第四个文件里 |
| 6 | 探针也会落错地方：放宽捕获是**超集**，原测试区分不了，判据得挪到行为真的分叉的那一处 |
| 7 | `quality_policy` 这类字段必须有真名册，且每条策略要写清**不检查什么**——只写「检查什么」会让读者默认其余都查了 |
| 8 | 上一章把 `partition_keys` 变成承重件，这一章它第一次抓到真东西 |
