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
        dataset_id="cn.security_master",
        title="沪深北全市场 A 股名单（point-in-time universe）",
        schema_version=1,
        primary_provider="eastmoney_security_master",
        # 🔴 备胎是 2026-09-26 **被一次真故障逼出来的**，不是预防性设计。
        #    那天 `push2*.eastmoney.com` 整组 502（同一时刻 `push2ex` /
        #    `push2his` / `datacenter-web` 全 200 ⇒ 不是限流、不是请求头），
        #    而这个数据集是全市场 EOD 的第一步 —— 它取不到，当天整条链就停。
        #
        #    原来这里写着「故意留空：登记一个没验过的备胎等于承诺一条
        #    可能也是断的降级路径」。那句话**仍然成立**，所以这次登记的这个
        #    是**真跑通过**的：5568 只、三个交易所齐全、耗时 111s。
        #
        #    ⚠️ 口径与主源**不等价**（没有上市日），差异写在适配器模块头里。
        fallback_providers=("sina_security_master",),
        validation_providers=(),
        # 一次同步一个分区：这份名单描述的是**取回那一刻**的在册状态。
        partition_keys=("as_of_date",),
        storage_policy="sqlite_fact_security_master",
        quality_policy="cn-security-master-v1",
        raw_table="raw_market_snapshot",
        fact_table="fact_security_master",
        consumers=("easyup_biga.persistence.data:security_universe_at",),
    ),
    DatasetDefinition(
        dataset_id="cn.trading_calendar",
        title="A 股交易日历（含交易所已公布的未来排期）",
        schema_version=1,
        # 实测主源是新浪不是深交所 —— 见模块头。szse 作 fallback 保留：
        # 它是**官方**源，在连得通的环境里它才是更权威的那个。
        primary_provider="sina_calendar",
        fallback_providers=("szse",),
        validation_providers=(),
        # 🔴 切片键是 `as_of`（这份日历是**什么时候取的**），不是月份。
        #    外部草案写 ("month",) 是按深交所 monthList 端点的形状写的，换主源
        #    之后不成立 —— 新浪那个端点一次返回全量（往回 730 天 + 未来）。
        #    但「没有切片」也是错的：交易所**逐步公布**未来排期，11 月取到的
        #    日历含次年、9 月取到的不含 ⇒ 两次取回是同一数据集的两个版本，
        #    区分它们的正是取回时刻。
        partition_keys=("as_of",),
        storage_policy="sqlite_fact",
        quality_policy="cn-trading-calendar-v1",
        raw_table="raw_market_snapshot",
        fact_table="fact_trading_calendar",
        consumers=(
            "easyup_biga.providers.tradetime:market_is_open",
            "easyup_biga.persistence.db:is_trading_day",
        ),
    ),
    DatasetDefinition(
        dataset_id="cn.equity.daily_bars",
        title="全市场 A 股 EOD 日线（不复权原始 OHLCV）",
        schema_version=1,
        # 🔴 **主备在 2026-09-26 对调了。**
        #    依据不是「东财那天挂了」，是两条长期证据：
        #    ① 同机另一套长期运行的实例，每天的全市场日线走的就是新浪
        #       这个端点 —— 不是「听说能用」，是每天在跑；
        #    ② 公开的 A 股数据源目录把东财标为「接口共用同一套风控，
        #       IP 被封会成片失联」，并建议优先用不封 IP 的源。
        #    我们这次撞上的正是成片失联。
        #
        #    ⚠️ 两者口径**不等价**（自报总数 / 成交量单位 / 停牌股是否返回），
        #      差异写在 `providers/sina_eod.py` 的模块头里，不当等价替换。
        primary_provider="sina_eod",
        # 🔴 顺序有讲究：
        #    1. 新浪 —— 收盘即可取，最快（12 页 / 28 秒）
        #    2. 通达信盘后包 —— **第三个风控面**，且是唯一能按指定交易日
        #       取的源（⇒ 补历史）。代价是它「收盘后数小时」才发布，
        #       17:30 的定时器可能赶不上 ⇒ 排在新浪之后
        #    3. 东财 —— 与 push2 同组，2026-09-26 起整组故障
        #
        #    ⚠️ 2026-09-26 用 09-24 的数据逐只对拍过新浪与盘后包：
        #      5556 只的收盘价与开盘价**零处不一致**。
        fallback_providers=("tdx_daily_package", "eastmoney_eod"),
        validation_providers=(),
        # 一天一个分区。修订（盘后更正）走同分区的新 data_version，
        # 不改写已经落盘的那份 —— 物理路径里带 data_version=N。
        partition_keys=("trade_date",),
        storage_policy="parquet_lake",
        quality_policy="cn-equity-daily-bars-v1",
        # 🔴 raw 落的是**文件面**，控制面里只留一行元数据 ⇒ raw_table 指
        #    `raw_artifacts`（工件登记表），不是 `raw_market_snapshot`。
        #    这两张表不是一回事：后者存 body，前者只存 uri + 哈希。
        raw_table="raw_artifacts",
        fact_table=None,
        # 真读它的是 DuckDB 那两条查询 —— 它们直接扫 lake/ 下的 Parquet。
        consumers=(
            "easyup_biga.data.analytics:query_eod_as_of",
            "easyup_biga.data.analytics:query_eod_between",
        ),
    ),
    DatasetDefinition(
        dataset_id="cn.index.daily_bars",
        title="指数日线（market/sector/technical 共用的脊梁）",
        schema_version=1,
        primary_provider="sina",
        fallback_providers=(),
        # 🔴 **故意留空。** tencent 今天确实被 market-calc 用来取成交额并与日线
        #    互相印证，但那是 skill 内部的事，不走 Registry 的 validator 流程。
        #    写上等于点名一个今天没人执行的角色 —— 等 P3-2 真的接上再填。
        validation_providers=(),
        # 🔴 切片键是 `evidence_set_id`，一次冻结**一个分区**。
        #    这个数据集走的是**决策快照道**：`SnapshotCoordinator` 一次把
        #    sh/sz 一起冻成一个 bundle，下游读的也是整份。
        #    ⚠️ 我一度写成 `(symbol, as_of)`（抄自外部 P3-1），那是错的 ——
        #    bundle 里有多个 symbol，按 symbol 切等于声称有多个分区而实际只发布
        #    一个。外部 P3-2 自己把这个值改了，方向与本仓库的 `_check_partition_keys()`
        #    抓到的是同一件事。
        partition_keys=("evidence_set_id",),
        storage_policy="sqlite_evidence_bundle",
        quality_policy="cn-index-daily-bridge-v1",
        raw_table="raw_market_snapshot",
        consumers=("easyup_biga.application.coordinator:SnapshotCoordinator",),
    ),
    DatasetDefinition(
        dataset_id="cn.security.tradability",
        title="A 股逐日可交易状态",
        schema_version=1,
        primary_provider="derived_biga",
        fallback_providers=(), validation_providers=(),
        partition_keys=("trade_date",), storage_policy="parquet",
        quality_policy="cn-tradability-v1", raw_table="raw_artifacts",
        consumers=("easyup_biga.data.analytics:query_tradability",),
    ),
    DatasetDefinition(
        dataset_id="cn.equity.adjustment_factors",
        title="A 股逐日复权因子（与原始 OHLC 独立）",
        schema_version=1,
        primary_provider="csv_adjustment",
        fallback_providers=(), validation_providers=(),
        partition_keys=("trade_date",), storage_policy="parquet",
        quality_policy="cn-adjustment-factor-v1", raw_table="raw_artifacts",
        consumers=("easyup_biga.data.analytics:query_adjustment_factors",),
    ),
    DatasetDefinition(
        dataset_id="cn.market.emotion_close",
        title="收盘情绪派生快照",
        schema_version=1,
        primary_provider="derived_biga",
        fallback_providers=(), validation_providers=(),
        partition_keys=("trade_date",), storage_policy="parquet",
        quality_policy="cn-emotion-close-v1", raw_table="raw_artifacts",
        consumers=("easyup_biga.data.analytics:query_emotion_close",),
    ),
    DatasetDefinition(
        dataset_id="cn.index.realtime_quote",
        title="决策时点指数实时行情冻结",
        schema_version=1,
        primary_provider="tencent",
        fallback_providers=(), validation_providers=(),
        partition_keys=("evidence_set_id",), storage_policy="parquet_evidence_bundle",
        quality_policy="cn-realtime-quote-v1", raw_table="raw_artifacts",
        consumers=("easyup_biga.data.decision_client:DecisionDataClient",),
    ),
    DatasetDefinition(
        dataset_id="cn.market.breadth",
        title="决策时点全市场涨跌家数冻结",
        schema_version=1,
        primary_provider="eastmoney",
        # 🔴 备用源**自算**家数（主源是源自己报的）。差异见适配器模块头：
        #    口径多了北交所、「平盘」的定义是我们定的、代价是要拉整个市场。
        #    ⚠️ 登记它的前提同前：**真跑通过**（2026-09-26 实测
        #      涨 1120 / 跌 4305 / 平 137，8~12 秒）。
        fallback_providers=("sina_breadth",), validation_providers=(),
        partition_keys=("evidence_set_id",), storage_policy="parquet_evidence_bundle",
        quality_policy="cn-market-breadth-v1", raw_table="raw_artifacts",
        consumers=("easyup_biga.data.decision_client:DecisionDataClient",),
    ),
    DatasetDefinition(
        dataset_id="cn.sector.board_snapshot",
        title="决策时点行业/概念板块快照",
        schema_version=1,
        primary_provider="eastmoney",
        fallback_providers=(), validation_providers=(),
        partition_keys=("evidence_set_id",), storage_policy="parquet_evidence_bundle",
        quality_policy="cn-sector-board-v1", raw_table="raw_artifacts",
        consumers=("easyup_biga.data.decision_client:DecisionDataClient",),
    ),
    DatasetDefinition(
        dataset_id="cn.market.limit_pool",
        title="决策交易日涨停/炸板/跌停池冻结",
        schema_version=1,
        primary_provider="eastmoney",
        fallback_providers=(), validation_providers=(),
        partition_keys=("evidence_set_id",), storage_policy="parquet_evidence_bundle",
        quality_policy="cn-limit-pool-v1", raw_table="raw_artifacts",
        consumers=("easyup_biga.data.decision_client:DecisionDataClient",),
    ),
    DatasetDefinition(
        dataset_id="cn.news.flash",
        title="决策时点新浪 7x24 快讯冻结",
        schema_version=1,
        primary_provider="sina_news",
        fallback_providers=(), validation_providers=(),
        partition_keys=("evidence_set_id",), storage_policy="parquet_evidence_bundle",
        quality_policy="cn-news-flash-v1", raw_table="raw_artifacts",
        consumers=("easyup_biga.data.decision_client:DecisionDataClient",),
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
