"""PROVIDER_REGISTRY —— 数据源适配器名册的**唯一源**。

照 `AGENT_REGISTRY` 的形状：一处手写、关系派生、反向测试（ADR-002）。

🔴 `provider_id` 是**适配器级**，不是「这家公司」
-------------------------------------------------
第一版取 `raw_market_snapshot.source` 的前缀，于是新浪的日线、日历、快讯
合成了一个 `sina`。理由当时看着很充分：别让同一个源有第二套名字。

**但那个模型答错了它自己要答的问题。** `datasets_of("sina")` 会说
「sina 挂了会影响 3 个数据集」—— 而那是三个**可以各自独立挂**的端点
（kline / calendar / 7×24 是三套完全不同的接口）。它系统性地**高估影响面**，
而「它挂了会影响什么」正是这份名册存在的主要理由之一。

⇒ 改成与 `providers/` 下的模块同名，与外部设计 v1 的 Provider Inventory 一致。

第一版真正想守的那件事没有丢，它挪到了 `source_prefix`
------------------------------------------------------
「别让同一个数据源在源码、库、注册表里有三套名字」仍然成立，只是它约束的
是**前缀**而不是 id：`sina` 与 `sina_calendar` 的 `source_prefix` 都是 `sina`，
和库里的 `sina:kline/…` / `sina:calendar/…` 对得上。
由测试单向钉住：每个注册的 `source_prefix` 必须在源码里作为 `"<prefix>:` 出现过。

关系派生，不手写
----------------
外部骨架的 `ProviderDefinition` 有手写的 `datasets` 字段，而 dataset 侧已经有
`primary/fallback/validation` —— 一对**孪生清单**（开发流程第五问）。
ADR-002 自己说的是「one handwritten source, derived views」⇒ 这里取派生，
手写只留 `DATASETS` 一处。

⚠️ 只注册**已激活 dataset 用得到**的适配器。`tencent` / `eastmoney` / `sina_news`
   今天都还是 agent 直连的遗留路径（外部设计标为 MIGRATE），它们在 P3-6
   各自的迁移 PR 里进册 —— 现在进来就是零消费方。
"""

from __future__ import annotations

from .contracts import DatasetDefinition, ProviderDefinition
from .registry import DATASET_REGISTRY

__all__ = ["PROVIDERS", "PROVIDER_REGISTRY", "PROVIDER_IDS", "ProviderNotRegistered",
           "get_provider", "datasets_of", "uses_provider", "provider_for_source",
           "role_of"]


class ProviderNotRegistered(KeyError):
    """这个 provider 不在册 —— **一个专有异常，不是裸 KeyError**。

    🔴 为什么值得单独一个类型：调用方需要**精确**捕获它。
    外部 P3-2 实现在 `SnapshotCoordinator` 里写的是 `except KeyError:`，
    而那段 `publish()` 有上百行、其中 `entry["snapshot_id"]`、manifest 字段缺失
    等等**都会抛 KeyError**。用裸 KeyError 兜底，等于把「这个源没登记」
    和「我的数据结构坏了」当成同一件事处理 —— 而后者会被静默咽掉。

    这正是本项目最优先防范的形状：**静默 fail-open**（R-3）。
    """


#: 数据源名册的**唯一手写处**。关系（谁支持哪些 dataset）不在这里写 —— 见 `datasets_of`。
#: 手写元组 + 派生 dict 的理由同 `registry.DATASETS`：dict 会静默吃掉重复的 id。
PROVIDERS: tuple[ProviderDefinition, ...] = (
    ProviderDefinition(
        provider_id="sina_calendar",
        title="新浪交易日历 —— 含交易所已公布的未来排期，当前 primary",
        source_prefix="sina",
        modules=("easyup_biga.providers.sina_calendar",),
    ),
    ProviderDefinition(
        provider_id="sina_breadth",
        title="新浪涨跌家数 —— cn.market.breadth 的 FALLBACK；从全市场快照自算，含北交所",
        source_prefix="sina",
        modules=("easyup_biga.providers.sina_breadth",),
    ),
    ProviderDefinition(
        provider_id="tdx_daily_package",
        title="通达信官网盘后包 —— cn.equity.daily_bars 的 FALLBACK；唯一能按指定交易日补历史",
        source_prefix="tdx",
        modules=("easyup_biga.providers.tdx_daily_package",),
    ),
    ProviderDefinition(
        provider_id="sina_eod",
        title="新浪全市场日线 —— cn.equity.daily_bars 的 PRIMARY（2026-09-26 与东财对调）",
        source_prefix="sina",
        modules=("easyup_biga.providers.sina_eod",),
    ),
    ProviderDefinition(
        provider_id="sina_security_master",
        title="新浪全市场 A 股名单 —— cn.security_master 的 FALLBACK（不含上市日）",
        source_prefix="sina",
        modules=("easyup_biga.providers.sina_security_master",),
    ),
    ProviderDefinition(
        provider_id="eastmoney_security_master",
        title="东财全市场 A 股名单 —— cn.security_master 的 PRIMARY；2026-09-26 起有 FALLBACK",
        source_prefix="em",
        modules=("easyup_biga.providers.eastmoney_security_master",),
    ),
    ProviderDefinition(
        provider_id="eastmoney_eod",
        title="东财全市场收盘行情 —— 一次请求拿回全市场，EOD 日线的唯一来源",
        # 与 eastmoney_security_master 同一个站点 ⇒ 同一个 source 前缀。
        # 🔴 前缀是**站点级**的、provider_id 是**适配器级**的，两者不是一对一
        #    （sina 与 sina_calendar 也都写 "sina:"）。这正是 P13 要按
        #    「本 dataset 登记了谁」求交集、而不能直接拿前缀查的原因。
        source_prefix="em",
        modules=("easyup_biga.providers.eastmoney_eod",),
    ),
    ProviderDefinition(
        provider_id="szse",
        title="深交所官方日历 —— 本项目部署环境连不通，作 fallback 保留",
        source_prefix="szse",
        modules=("easyup_biga.providers.szse",),
    ),
    ProviderDefinition(
        provider_id="sina",
        title="新浪日线 —— 指数日线的唯一来源",
        source_prefix="sina",
        modules=("easyup_biga.providers.sina",),
    ),
    ProviderDefinition(
        provider_id="derived_biga",
        # ⚠️ **不是网络数据源** —— 它是「这份数据由 BigA 自己算出来」的身份。
        #    没有它，派生数据集的 `provider_id` 只能借一个真数据源的名字，
        #    而那会让溯源**假装**这行来自某个行情商（裁定 16 的「不硬凑」）。
        #    `source_prefix="derived"` 让 raw 层的 source 字符串自己说清这件事。
        title="BigA 本地确定性派生数据（不是网络源）",
        source_prefix="derived",
        modules=("easyup_biga.providers.derived",),
    ),
    ProviderDefinition(
        provider_id="csv_adjustment",
        title="本地 CSV 复权因子适配器",
        source_prefix="csv-adjustment",
        modules=("easyup_biga.providers.csv_adjustment",),
    ),
    ProviderDefinition(
        provider_id="tencent",
        title="腾讯指数实时行情",
        source_prefix="tencent",
        modules=("easyup_biga.providers.tencent",),
    ),
    ProviderDefinition(
        provider_id="eastmoney",
        title="东财 breadth / board / limit-pool 适配器",
        source_prefix="em",
        modules=("easyup_biga.providers.eastmoney",),
    ),
    ProviderDefinition(
        provider_id="sina_news",
        title="新浪 7x24 快讯适配器",
        source_prefix="sina",
        modules=("easyup_biga.providers.sina_news",),
    ),
)

#: 按 id 索引 —— 从 `PROVIDERS` 派生，不手写。
PROVIDER_REGISTRY: dict[str, ProviderDefinition] = {p.provider_id: p for p in PROVIDERS}

#: 定义顺序即展示顺序。
PROVIDER_IDS: tuple[str, ...] = tuple(PROVIDER_REGISTRY)


def uses_provider(ds: DatasetDefinition, provider_id: str) -> bool:
    """`ds` 用到 `provider_id` 吗 —— 三种角色任一算数。

    🔴 单独抽成纯函数，是为了让「三种角色一个都没漏」这条**可以脱离当前
    注册表内容被测**。实测踩过：P3-0 收窄到两个 dataset 之后
    `validation_providers` 全为空，于是把这个分支删掉，守卫照样全绿 ——
    守卫没写错，是**数据不再走到那条分支**。

    仓库里已有同款解法：`test_条数只数被跟踪的文档` 喂的是一份**合成的**
    tracked 集合而不是真实返回值，理由一样 —— 纯函数逻辑不该由「此刻注册了
    什么」决定能不能被验到。

    ⚠️ validation 角色也算「用到」：问这个问题的场景是「它挂了会影响什么」，
    而校验源挂掉会让交叉校验判不了 —— 那也是影响。
    """
    return (provider_id == ds.primary_provider
            or provider_id in ds.fallback_providers
            or provider_id in ds.validation_providers)


def datasets_of(provider_id: str) -> tuple[str, ...]:
    """这个 provider 被哪些数据集用到 —— **从 `DATASET_REGISTRY` 派生**。

    返回按数据集注册顺序排列的 id 元组。
    """
    get_provider(provider_id)          # 未知 id 先 fail closed，别返回空元组
    return tuple(ds.dataset_id for ds in DATASET_REGISTRY.values()
                 if uses_provider(ds, provider_id))


def get_provider(provider_id: str) -> ProviderDefinition:
    """按 id 取数据源定义。未知 id **fail closed**。

    🔴 与 `get_dataset` 同样的理由：返回 None 会让「拼错了名字」和
    「这个源今天没数据」长成同一个分支。
    """
    try:
        return PROVIDER_REGISTRY[provider_id]
    except KeyError:
        raise ProviderNotRegistered(
            f"未注册的数据源 {provider_id!r}。\n"
            f"  已注册：{list(PROVIDER_REGISTRY)}\n"
            f"  ⚠️ id 与 providers/ 下的模块同名（适配器级，不是「这家公司」）。\n"
            f"  未激活的适配器不在册：它们在各自的 P3-6 迁移 PR 里进来，"
            f"现在进册就是零消费方。"
        ) from None


def provider_for_source(dataset_id: str, source: str) -> str:
    """从 raw 层的 `source`（如 `sina:kline/sh000001`）反查**适配器级** provider_id。

    🔴 不能直接取前缀当 provider_id。本仓库的 `provider_id` 是适配器级的
    （`sina` / `sina_calendar` / `szse`），而 `source` 的前缀是**站点级**的
    —— `sina` 与 `sina_calendar` 都写 `sina:`。外部实现直接
    `source.split(":")[0]` 当 provider_id，对指数日线恰好相等，对交易日历就错。

    ⇒ 判据取交集：**这个 dataset 登记的 provider** ∩ **前缀对得上的 provider**。
    交集不唯一就 fail closed —— 猜一个出来正是「悄悄选边」。
    """
    prefix = source.split(":", 1)[0]
    registered = set(DATASET_REGISTRY[dataset_id].fallback_providers) | {
        DATASET_REGISTRY[dataset_id].primary_provider,
        *DATASET_REGISTRY[dataset_id].validation_providers,
    }
    hits = sorted(pid for pid in registered
                  if PROVIDER_REGISTRY[pid].source_prefix == prefix)
    if len(hits) != 1:
        raise ProviderNotRegistered(
            f"{dataset_id} 的 source 前缀 {prefix!r} 对应到 {hits or '（无）'} —— "
            f"必须恰好一个。\n"
            f"  该 dataset 登记的 provider：{sorted(registered)}\n"
            f"  前缀不是 provider_id：sina 与 sina_calendar 都写 'sina:'，"
            f"所以要按「本 dataset 登记了谁」求交集，不能直接取前缀。")
    return hits[0]


def role_of(dataset_id: str, provider_id: str) -> "ProviderRole":
    """这个 provider 在这个 dataset 上扮演什么角色。未绑定就 fail closed。

    🔴 角色属于「dataset × provider」这条边，而本仓库把那条边**内联**在
    `DatasetDefinition` 的三个字段里（只有几个 dataset 时，独立的 binding 表
    是纯开销）。外部实现用的是一张平行的 `PROVIDER_BINDINGS` 元组 ——
    那与 dataset 侧的三个字段是一对**孪生清单**，两处各写一份必然漂。
    这里从唯一手写处派生，语义等价。
    """
    from .contracts import ProviderRole
    ds = DATASET_REGISTRY.get(dataset_id)
    if ds is None:
        raise KeyError(f"未注册的数据集 {dataset_id!r}")
    if provider_id == ds.primary_provider:
        return ProviderRole.PRIMARY
    if provider_id in ds.fallback_providers:
        return ProviderRole.FALLBACK
    if provider_id in ds.validation_providers:
        return ProviderRole.VALIDATOR
    raise ValueError(
        f"{provider_id!r} 没有绑定到 {dataset_id!r}。\n"
        f"  该 dataset 登记的：primary={ds.primary_provider!r} "
        f"fallback={list(ds.fallback_providers)} validation={list(ds.validation_providers)}\n"
        f"  要新增角色就改 data/registry.py 里那个 dataset 的字段。")


def bindings_for_dataset(dataset_id: str) -> tuple["DatasetProviderBinding", ...]:
    """这个 dataset 的全部 dataset × provider × role 边，**从注册表派生**。

    🔴 为什么不是一张手写的 `PROVIDER_BINDINGS` 元组（外部实现的做法）：
    那与 `DatasetDefinition` 的 primary/fallback/validation 三个字段是一对
    **孪生清单** —— 同一件事写两处。漂了不会报错，只会让 failover 按一份
    清单走、而 `role_of()` 按另一份答题，两者各自自洽。
    ⇒ 这里只从唯一手写处（`registry.DATASETS`）算出来。

    顺序：PRIMARY → FALLBACK（**按声明顺序**）→ VALIDATOR（按 id 排）。

    🔴 **FALLBACK 从「按 id 排」改成「按声明顺序」（2026-09-26）。**

    原来两者都按 id 排，理由是「确定性」。但元组本身已经是确定的 ——
    排序买不到额外的确定性，却**吃掉了声明里的信息**：
    `fallback_providers` 是个**有序元组**，写的人自然会按优先级排，
    而它被静默重排了。那是「看起来能控制某件事、实际不能」的配置。

    逼出这次改动的是一个真实场景：`cn.equity.daily_bars` 有两个备用源，
    一个快（盘后包，且是唯一能补历史的），一个慢且当时正在故障（东财）。
    按 id 排 ⇒ 故障那个排在前面，每次都要先白等它三次重试。

    ⚠️ **VALIDATOR 仍然按 id 排**，因为那里顺序**没有含义** ——
    校验源是全部都要问的，不存在「先问谁」。在没有含义的地方保留排序，
    是为了让输出稳定；在有含义的地方保留排序，是把含义丢掉。
    """
    from .contracts import DatasetProviderBinding, ProviderRole
    ds = DATASET_REGISTRY.get(dataset_id)
    if ds is None:
        raise KeyError(f"未注册的数据集 {dataset_id!r}")
    out = [DatasetProviderBinding(dataset_id, ds.primary_provider, ProviderRole.PRIMARY)]
    out += [DatasetProviderBinding(dataset_id, pid, ProviderRole.FALLBACK)
            for pid in ds.fallback_providers]
    out += [DatasetProviderBinding(dataset_id, pid, ProviderRole.VALIDATOR)
            for pid in sorted(ds.validation_providers)]
    return tuple(out)


def all_bindings() -> tuple["DatasetProviderBinding", ...]:
    """全注册表的边，按 dataset 定义顺序展开。同样是派生值，不手写。"""
    return tuple(b for ds_id in DATASET_REGISTRY for b in bindings_for_dataset(ds_id))
