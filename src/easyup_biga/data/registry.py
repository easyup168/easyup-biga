"""DATASET_REGISTRY —— 数据集名册的**唯一源**。

照 `domain/registry.py` 的 `AGENT_REGISTRY` 形状：一处手写、其余派生、反向测试
（外部设计 `BigA Data Architecture v1` 的 ADR-002 逐字确认了这个模式）。

🔴 名册答的是「**Data Platform 管着哪些数据集**」，不是「系统里有哪些数据集」
------------------------------------------------------------------------
这两个问题的答案今天**不一样**，混起来是本名册最容易犯的错 —— 我第一版就犯了。

第一版按「`raw_market_snapshot.source` 里真实出现过的 7 组」全收，理由是
「只收一半就会有两份答案」。那个理由把两个问题混成了一个：

  · 「系统里有哪些数据集」—— 今天的答案在**各 skill 的代码里**，不在这里。
    Registry 收不收它们，都改变不了这一点。
  · 「Data Platform 管着哪些」—— 这才是本名册该答的。

而那 5 个（realtime_quote / breadth / board_snapshot / limit_pool / news.flash）
今天是 **agent 直接访问 provider** 的遗留路径，Data Platform 对它们一无所知。
把它们收进来，等于让名册**声称管着 5 个它完全没接手的东西** ——
正是本仓库那条「点名了一个不存在的东西，读者会认为这条已经有人管了」。

⇒ 条目**按 Milestone 激活**：有真实生产者或消费者才进来（ADR-002）。
  待迁的 5 个记在 `docs/design/phase-3-data-platform.md` 的迁移清单里，
  各自在 P3-6 的迁移 PR 中进册。

🔴 探活过、但仍然不进册的例子
-----------------------------
外部草案给 `cn.trading_calendar` 写的主源是深交所官方端点。实测
`fact_trading_calendar` 全部 1326 行的 source 是 `sina:calendar/klc_td_sh`，
而 `providers/szse.py` 有测试、有导出、**零生产调用方**（部署环境连不通）。
⇒ primary 是 `sina_calendar`，`szse` 作 fallback 保留。

这条说明「探活」与「该不该进册」是两个独立判据：探活回答「它是真的吗」，
Milestone 激活回答「今天有人用它吗」。两个都过才进来。
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
        # 实测主源是新浪不是深交所 —— 见模块头。szse 作 fallback 保留：
        # 它是**官方**源，在连得通的环境里它才是更权威的那个。
        primary_provider="sina_calendar",
        fallback_providers=("szse",),
        validation_providers=(),
        # 新浪那个端点一次返回全量（往回 730 天 + 未来），没有自然切片。
        # 外部草案写 ("month",) 是按深交所 monthList 的形状写的，换主源后不成立。
        partition_keys=(),
        storage_policy="sqlite_fact",
        raw_table="raw_market_snapshot",
        fact_table="fact_trading_calendar",
        consumers=(
            "easyup_biga.providers.tradetime:market_is_open",
            "easyup_biga.persistence.db:is_trading_day",
        ),
    ),
    DatasetDefinition(
        dataset_id="cn.index.daily_bars",
        title="指数日线（market/sector/technical 共用的脊梁）",
        primary_provider="sina",
        fallback_providers=(),
        # 🔴 **故意留空。** tencent 今天确实被 market-calc 用来取成交额并与日线
        #    互相印证，但那是 skill 内部的事，不走 Registry 的 validator 流程。
        #    写上等于点名一个今天没人执行的角色 —— 等 P3-2 真的接上再填。
        validation_providers=(),
        partition_keys=("trade_date",),
        storage_policy="sqlite_raw_snapshot",
        raw_table="raw_market_snapshot",
        consumers=("easyup_biga.application.coordinator:SnapshotCoordinator",),
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
