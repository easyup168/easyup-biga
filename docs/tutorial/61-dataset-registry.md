# 61 · 给数据集上户口（Phase 3 · P3-0）

> 📘 **过程** · 写完即冻结
> **覆盖**：P3-0 —— Dataset / Provider 两张名册、`bin/biga-data`、十条守卫及其探针 ｜
> **不覆盖**：Store / Job Runner / Quality（P3-1 之后）、14 条适配裁定的来由（见
> [`../design/phase-3-data-platform.md`](../design/phase-3-data-platform.md)）

---

## 目标 / 产出

一份能回答「**这个系统里有哪些数据集、各自谁在产、它挂了会影响什么**」的名册，
并且这份名册有**行为上的**消费方，不是一份没人读的配置。

产出：

```text
src/easyup_biga/data/contracts.py           领域契约 + 状态→退出码
src/easyup_biga/data/registry.py            7 个数据集（唯一手写处）
src/easyup_biga/data/provider_registry.py   4 个数据源，关系**派生**
bin/biga-data                               list / providers，带 --json
tests/test_data_registry.py                 10 条守卫，每条都见过红
```

---

## 为什么这么做

### 外部设计包给了参考骨架，而它的第一条就错了

Phase 3 的外部设计包带了 `reference/registry_example.py`，里面 `cn.trading_calendar`
这条是：

```python
primary_provider="szse_calendar",
partition_keys=("month",),
```

看起来完全合理 —— 深交所是**官方**源，monthList 端点确实按月取。
仓库里也到处这么写：`CLAUDE.md` 说批 L 落地的是「深交所官方日历」，
`providers/szse.py` 的模块头第一行就是「`cn.trading_calendar` 的 Provider 适配器」。

开发流程 §1 说**设计先探活**。查一下库：

```text
sqlite> SELECT source, COUNT(*) FROM fact_trading_calendar GROUP BY 1;
sina:calendar/klc_td_sh    1326
```

**1326 行，全部来自新浪，深交所一行都没有。**

再查调用方：`providers/szse.py` 有测试、被 `providers/__init__.py` 导出，
但**没有任何生产调用方** —— `bin/biga-calendar` 用的是 `sina_calendar`。
原因写在那个脚本的头里：本项目的部署环境连不通深交所，所以批 L 之后
真正在跑的是新浪那条路。

> 🔴 **照抄参考骨架 = 注册一个在这台机器上一行都没产出过的主源。**
> 而且 `("month",)` 那个切片键也只对深交所的端点成立。
> 这不是参考骨架写错了 —— 它按文档写，文档也没错，**错的是从没人回头核对过现状**。

⇒ 最终写成：primary 是 `sina`，`szse` 作 fallback 保留（它在连得通的环境里
才是更权威的那个），`partition_keys` 是空元组。

> 通用原则：**注册表描述的是「现在真的是什么」，不是「设计上应该是什么」。**
> 这两者一旦分叉，读的人会按前者的语气相信后者。

### 名册的范围：不是「Phase 3 要做的那几个」

第一版想只注册 Phase 3 目标清单里的数据集。写到一半发现不对：

查 `raw_market_snapshot.source` 的实际取值，**今天已经在跑 7 组**：

```text
sina:kline/…      指数日线          em:push2ex/…     情绪股池
sina:calendar/…   交易日历          em:push2delay/…  涨跌家数
sina:7x24/…       7×24 快讯         em:clist/…       板块榜
tencent:quote     指数实时行情
```

只注册目标清单里那几个，等于宣布「系统里有哪些数据集」有**两份答案**：
注册表一份、`raw_market_snapshot` 里的事实一份。
而注册表是后来的那份 —— 它会输。

⇒ 7 个全收。将来的 `cn.equity.daily_bars` 是往这里加，不是另起一份。

⚠️ 反过来也成立：**还没探活过的数据集不进注册表。** 参考骨架给的是
`candidate_primary` / `candidate_fallback` 这种占位符 —— 那是诚实的写法，
但占位符不该进真注册表：注册一个不存在的 provider，读的人会以为这条链
已经有人管了。

### `provider_id` 写 `eastmoney` 就是第三套口径

库里的 `source` 前缀是 `em`，不是 `eastmoney`。模块叫 `eastmoney.py`。
如果注册表写 `provider_id="eastmoney"`，这个系统里「东方财富」就有了三个名字：
源码字面量一套、库里一套、注册表又一套。

⇒ `provider_id` **取 `raw_market_snapshot.source` 的前缀**，由守卫钉住。

### 孪生清单要派生，不要对账

参考骨架的 `ProviderDefinition` 有 `supported_datasets`，手写。
而 `DatasetDefinition` 里已经有 `primary_provider` / `fallback_providers` /
`validation_providers` —— 同一件事的两种写法。

这正是开发流程第五问：「有的话**谁派生谁**」。

⇒ 手写只留 `DATASET_REGISTRY` 一处，`datasets_of(provider_id)` 从它派生。
结构上不可能对不上，**不需要一条测试去追它**。

顺带地，派生出来的东西自己就是个有用的答案：

```text
sina      它挂了会影响：cn.trading_calendar、cn.index.daily_bars、cn.news.flash
```

这个问题今天没有别的地方答得出来。

### 名册必须有行为上的消费方

`domain/registry.py` 的 docstring 里写着为什么当初**不**建 Dataset Registry：
「装一个没有消费方的字段就是一条 L-1 死配置」。Phase 3 解除了那个理由
（那时只有一个 dataset 走完全链），但**新名册自己也得有消费方**。

⇒ 把 `bin/biga-data list` 从 P3-1 拉到 P3-0。它小（只读静态定义），
而且让这个里程碑可以被演示，不是只能被阅读。

⚠️ 它**不能**叫 `biga data list`：`bin/biga` 是 openclaw CLI 的纯 passthrough，
那条命令会被原样转给 openclaw，当场报未知子命令（裁定 2）。

---

## 坑

### 坑 1 · 探针抓到两条**结构上不可能红**的守卫

按开发流程 §3 逐条弄坏守卫，十条里有两条没红：

```text
P1  dataset_id 重复      🔴 没红
P2  provider_id 重复     🔴 没红
```

守卫是这么写的：

```python
ids = [d.dataset_id for d in DATASET_REGISTRY.values()]
assert len(ids) == len(set(ids))
```

而注册表是这么建的：

```python
DATASET_REGISTRY = {d.dataset_id: d for d in (...)}
```

**dict 推导已经把重复的 id 静默吃掉了** —— 后一条覆盖前一条，
`values()` 里永远不可能有重复。这条断言结构上不可能失败。

最难受的地方：我在同一个函数的 docstring 里写着

> 「重复的 id 会**静默互相覆盖** —— 长度对不上是唯一能看见的痕迹」

道理写对了，断言打在了**看不见那个痕迹的地方**。这是 L-13 的又一个样本。

⇒ 修法是把手写的元组留成权威：

```python
DATASETS: tuple[DatasetDefinition, ...] = (...)          # 唯一手写处
DATASET_REGISTRY = {d.dataset_id: d for d in DATASETS}   # 派生
```

守卫改成比 `len(DATASETS)` 与 `len(DATASET_REGISTRY)` —— 那个差值就是痕迹。

> 通用原则：**当一个结构会「静默吸收」某类错误，守卫必须打在被吸收之前那一步。**
> 打在之后，它查的是一个已经不可能出错的地方。

### 坑 2 · 还原了源码，`__pycache__` 里还是坏的

探针补完重跑，P1/P2 红了，但 P8（退出码）**在随后的全量里红了**，
而源码里明明白白写着正确的值。

原因：探针的 P8 把 `DataStatus.TIMEOUT: 2,` 改成 `DataStatus.TIMEOUT: 1,` ——
**等长替换**，还原又发生在同一秒内。Python 的 `.pyc` 失效判据是
**mtime + size**，两者都没变 ⇒ 缓存被判定为仍然有效，
下一次 import 拿到的是**被改坏的那一份**。

于是：源码 sha256 逐字节一致、探针如实报告「已还原」，
而随后的测试红在一个**源码里根本不存在的值**上。

开发流程 §3 原本就写着「探针跑完必须确认工作区已还原」——
这次把它补完整了：**`__pycache__` 也是工作区状态**。

⇒ 探针脚本加 `_purge_pyc()`，在备份之后、每次还原之后各清一次。

### 坑 3 · 守卫的判据方向选反了就既漏又噪

「`provider_id` 与既有 `source` 前缀同口径」这条，第一版想扫
`providers/` 下的 `"<x>:` 字面量，再和注册表对齐。**那是错的**：

- `"em:` 根本不在 `providers/eastmoney.py` 里 —— 它在各个 skill 里拼
- `providers/` 下另有 `"https:` / `"m:` / `"fbt:` 这类噪音前缀

反向扫会既漏（`em`）又噪（`https`）。

⇒ 改成**单向**：每个**注册了的** `provider_id` 必须在源码里作为 `"<id>:`
出现过。它精确命中要防的那件事 —— 有人写 `provider_id="eastmoney"`。

### 坑 4 · 顺手写下的一句空断言

P1 里曾有这么一段：

```python
keys = [d.storage_key if hasattr(d, "storage_key") else d.dataset_id ...]
assert len(keys) == len(set(keys))
```

`storage_key` 这个字段最终没装（它描述的是 P3-1 才存在的 Partition Store）。
于是 `hasattr` 恒假，这一段退化成**重复了上一条断言**。
不是错的，是**什么都没查** —— 而它看起来像查了两件事。

---

## 执行

```bash
bin/biga-data list          # 7 个数据集
bin/biga-data providers     # 4 个数据源 + 各自被谁用到
bin/biga-data list --json   # 机器可读
```

实际输出（节选）：

```text
已注册 7 个数据集：

  cn.trading_calendar       sina     · fallback szse
                            A 股交易日历（含交易所已公布的未来排期）
                            → sqlite_fact（raw_market_snapshot + fact_trading_calendar）

  cn.index.daily_bars       sina     · 校验 tencent
                            指数日线（market/sector/technical 共用的脊梁）
                            → sqlite_raw_snapshot（raw_market_snapshot）
```

---

## 验证

```bash
python3 -m pytest tests/test_data_registry.py -q     # 13 条全绿
bin/biga-data list --json | python3 -m json.tool | head -3
bin/biga-data bogus; echo "exit=$?"                  # 预期 exit=1
```

十条守卫的探针结论（每条都真的弄坏过）：

| 探针 | 弄坏什么 | 结果 |
|---|---|---|
| P1 | 两条定义用同一个 `dataset_id` | ✅ 红 |
| P2 | 两条定义用同一个 `provider_id` | ✅ 红 |
| P3 | `validation_providers` 指向未注册的 id | ✅ 红 |
| P4 | `get_dataset` 未知 id 返回 `None` | ✅ 红 |
| P5 | `provider_id` 写成 `eastmoney` | ✅ 红 |
| P6 | `modules` 指向不存在的模块 | ✅ 红 |
| P7 | `fact_table` 点名一张不存在的表 | ✅ 红 |
| P8 | `TIMEOUT` 退 1（参考骨架的原值） | ✅ 红 |
| P9 | 派生漏掉 validation 角色 | ✅ 红 |
| P10 | CLI 退非零 | ✅ 红 |

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 外部参考骨架的 `primary_provider` 是按文档写的，实测那个源在本项目**一行都没产出过** —— 先探活再写注册表 |
| 2 | 注册表的范围是「今天真的在跑的全部」，不是「这一期要做的那几个」；收一半等于立刻有了第二份答案 |
| 3 | 没探活过的数据集**不进**注册表 —— 占位符会让人以为这条链已经有人管了 |
| 4 | `provider_id` 取 `raw_market_snapshot.source` 的前缀（东财是 `em`），写模块名就是第三套口径 |
| 5 | 孪生清单要**派生**不要对账：`supported_datasets` 从 `DATASET_REGISTRY` 算出来，结构上不可能漂 |
| 6 | 🔴 dict 推导会**静默吃掉**重复 key ⇒ 唯一性守卫打在 `.values()` 上结构性不可能红（L-13） |
| 7 | 🔴 还原源码不够，**`__pycache__` 也是工作区状态** —— 等长替换 + 同秒还原能骗过 mtime+size 缓存判据 |
| 8 | 守卫的判据方向选反会既漏又噪；「注册表 → 源码」的单向存在性正好命中要防的那件事 |
| 9 | 名册必须有**行为上**的消费方（退出码 + 可解析输出），文件存在性证明不了 L-1 |
| 10 | CLI 是 `bin/biga-data` 不是 `biga data` —— `bin/biga` 是 openclaw 的纯 passthrough |
