"""DATASET_REGISTRY —— 数据集名册的**唯一源**。

沿用外部 Phase 3 设计包 `reference/registry_example.py` 的形状
（`dict[dataset_id, DatasetDefinition]`），内容全部换成**探活查到的真实情况**。

🔴 为什么不能照抄参考骨架里的条目
---------------------------------
参考骨架给 `cn.trading_calendar` 写的是 `primary_provider="szse_calendar"`、
`partition_keys=("month",)`。实测：

```text
fact_trading_calendar 全部 1326 行的 source = sina:calendar/klc_td_sh
providers/szse.py 有测试、有导出，但没有任何生产调用方
```

深交所那条路**在本项目的部署环境里连不通**（`bin/biga-calendar` 与
`providers/tradetime.py` 的模块头都写着这件事），所以批 L 之后真正在跑的是
`sina_calendar`。照抄参考骨架 = 注册一个在这台机器上**一行都没产出过**的主源，
并且 `("month",)` 那个切片键也只对深交所的 monthList 端点成立。

⇒ 开发流程 §1「设计先探活」在 P3-0 的对应动作，就是先查库再写注册表。

名册的范围：**今天真的在跑的全部数据集**
-----------------------------------------
不是「Phase 3 要做的那几个」。这两份清单不一样，混起来就是本注册表最容易
出的错：注册表只收一部分，于是「系统里有哪些数据集」立刻有了第二份答案。

⇒ 判据取 `raw_market_snapshot.source` 里**真实出现过**的前缀与端点（实测 7 组），
  一个不漏地收进来。将来 P3-3/P3-4 的新数据集是往这里加，不是另起一份。

⚠️ 还没有 `cn.equity.daily_bars` / `cn.security_master` / `cn.security.tradability`
/ `cn.equity.adjustment_factors` —— 它们的 Provider **一次都还没探活过**。
参考骨架给的是 `candidate_primary` / `candidate_fallback` 这种占位符，
那是诚实的写法，但占位符不该进真注册表：注册一个不存在的 provider，
读的人会以为这条链已经有人管了。它们在 P3-3 / P3-4 探活之后再进来。
"""

from __future__ import annotations

from .contracts import DatasetDefinition

__all__ = ["DATASETS", "DATASET_REGISTRY", "DATASET_IDS", "get_dataset"]


#: 数据集名册的**唯一手写处**。加/删/改数据集只动这里。
#:
#: 🔴 为什么手写的是元组、dict 另外派生：dict 推导会把重复的 id **静默吃掉**
#:    （后一条覆盖前一条），于是 `DATASET_REGISTRY.values()` 里永远不可能有
#:    重复——拿它查唯一性等于什么都没查。留下元组，`len(DATASETS)` 与
#:    `len(DATASET_REGISTRY)` 的**差**才是重复留下的唯一痕迹。
#:    这是探针抓出来的：第一版把唯一性断言打在 dict 上，弄坏了也不红（L-13）。
DATASETS: tuple[DatasetDefinition, ...] = (
    DatasetDefinition(
        dataset_id="cn.trading_calendar",
        title="A 股交易日历（含交易所已公布的未来排期）",
        # 🔴 主源是 sina 不是深交所 —— 见模块头。szse 作为 fallback 保留：
        #    它是**官方**源，在连得通的环境里它才是更权威的那个。
        primary_provider="sina",
        fallback_providers=("szse",),
        validation_providers=(),
        # sina 那个端点一次返回全量（往回 730 天 + 未来），没有自然切片。
        # 参考骨架写 ("month",) 是按深交所 monthList 的形状写的，换主源后不成立。
        partition_keys=(),
        storage_policy="sqlite_fact",
        raw_table="raw_market_snapshot",
        fact_table="fact_trading_calendar",
    ),
    DatasetDefinition(
        dataset_id="cn.index.daily_bars",
        title="指数日线（market/sector/technical 共用的脊梁）",
        primary_provider="sina",
        fallback_providers=(),
        # tencent 是唯一自带完整时间戳的源，risk 拿它做交叉校验（架构 §6.2）。
        validation_providers=("tencent",),
        partition_keys=("trade_date",),
        storage_policy="sqlite_raw_snapshot",
        raw_table="raw_market_snapshot",
    ),
    DatasetDefinition(
        dataset_id="cn.index.quote",
        title="指数实时行情（成交额 + 唯一自带时间戳的源）",
        primary_provider="tencent",
        fallback_providers=(),
        validation_providers=(),
        # 实时快照按时刻取，不按交易日切。
        partition_keys=(),
        storage_policy="sqlite_raw_snapshot",
        raw_table="raw_market_snapshot",
    ),
    DatasetDefinition(
        dataset_id="cn.market.breadth",
        title="涨跌家数与上涨占比（市场宽度）",
        primary_provider="em",
        fallback_providers=(),
        validation_providers=(),
        # ⚠️ 这个端点**不带任何日期**（架构 §6.2）⇒ trade_date 是推断出来的。
        #    切片键仍然是它 —— 但用的人要知道这个日期的来路不同于日线。
        partition_keys=("trade_date",),
        storage_policy="sqlite_raw_snapshot",
        raw_table="raw_market_snapshot",
    ),
    DatasetDefinition(
        dataset_id="cn.market.emotion_close",
        title="涨停 / 跌停 / 炸板 / 连板（情绪股池）",
        primary_provider="em",
        fallback_providers=(),
        validation_providers=(),
        partition_keys=("trade_date",),
        storage_policy="sqlite_raw_snapshot",
        raw_table="raw_market_snapshot",
    ),
    DatasetDefinition(
        dataset_id="cn.sector.rankings",
        title="板块榜（概念 / 行业）",
        primary_provider="em",
        fallback_providers=(),
        validation_providers=(),
        # 与家数同款：榜单不带日期，trade_date 取自新浪日线。
        partition_keys=("trade_date",),
        storage_policy="sqlite_raw_snapshot",
        raw_table="raw_market_snapshot",
    ),
    DatasetDefinition(
        dataset_id="cn.news.flash",
        title="7×24 财经快讯",
        primary_provider="sina",
        fallback_providers=(),
        validation_providers=(),
        # 连续事件流，**没有「收盘」概念**（架构 §6.2 / FIX-03）⇒ 无自然切片。
        partition_keys=(),
        storage_policy="sqlite_raw_snapshot",
        raw_table="raw_market_snapshot",
    ),
)

#: 按 id 索引 —— 从 `DATASETS` 派生，不手写。
DATASET_REGISTRY: dict[str, DatasetDefinition] = {d.dataset_id: d for d in DATASETS}

#: 定义顺序即展示顺序 —— `bin/biga-data list` 不再自己排一遍。
DATASET_IDS: tuple[str, ...] = tuple(DATASET_REGISTRY)


def get_dataset(dataset_id: str) -> DatasetDefinition:
    """按 id 取数据集定义。未知 id **fail closed**，不返回 None。

    🔴 返回 None 会让调用方写出 `if ds:` 然后静默跳过 —— 而「这个数据集不存在」
    和「这个数据集今天没数据」是完全不同的两件事，前者是拼错了名字。
    """
    try:
        return DATASET_REGISTRY[dataset_id]
    except KeyError:
        raise KeyError(
            f"未注册的数据集 {dataset_id!r}。\n"
            f"  已注册：{list(DATASET_REGISTRY)}\n"
            f"  新数据集要先在 data/registry.py 里注册 —— 注册表是名册的唯一源，"
            f"不从数据库动态创建。"
        ) from None
