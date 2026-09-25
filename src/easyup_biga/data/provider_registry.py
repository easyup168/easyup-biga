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

__all__ = ["PROVIDERS", "PROVIDER_REGISTRY", "PROVIDER_IDS",
           "get_provider", "datasets_of", "uses_provider"]


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
        raise KeyError(
            f"未注册的数据源 {provider_id!r}。\n"
            f"  已注册：{list(PROVIDER_REGISTRY)}\n"
            f"  ⚠️ id 与 providers/ 下的模块同名（适配器级，不是「这家公司」）。\n"
            f"  未激活的适配器不在册：它们在各自的 P3-6 迁移 PR 里进来，"
            f"现在进册就是零消费方。"
        ) from None
