"""PROVIDER_REGISTRY —— 数据源名册的**唯一源**。

沿用外部 Phase 3 设计包 `reference/registry_example.py` 的形状，但有**一处
结构性改动**：参考骨架里 `ProviderDefinition.supported_datasets` 是**手写**的，
这里改成从 `DATASET_REGISTRY` **派生**。

🔴 为什么必须派生
-----------------
「provider 支持哪些 dataset」与「dataset 的 primary/fallback/validation 是谁」
是同一件事的两种写法 —— 一对**孪生清单**。开发流程第五问问的就是这个：
「有的话谁派生谁；都没有的话，将来谁会成为第三份？」

两处各写一份，必然有一天只改了一处。而它的失败是**静默**的：注册表看起来
完整，只是某个 provider 少列了一个 dataset，于是「sina 挂了会影响什么」
少答一项 —— 而那正是这份名册存在的理由。

⇒ 手写只有 `DATASET_REGISTRY` 一处，这里只手写 provider 自己的元数据
  （叫什么、代码在哪），关系全部派生。结构上就不可能对不上，不需要一条
  测试去追它。

provider_id 的口径
------------------
🔴 **必须等于 `raw_market_snapshot.source` 的前缀**，不是模块名。
实测库里在用的：

```text
sina:kline/sh000001      sina:calendar/klc_td_sh      sina:7x24/zhibo152
em:push2ex/limit_up      em:push2delay/ulist.np       em:clist/industry
tencent:quote
```

所以东方财富的 id 是 **`em`** 而不是 `eastmoney` —— 后者会成为第三套口径
（源码字面量一套、库里一套、注册表又一套）。由 `tests/test_data_registry.py`
单向钉住：每个注册的 provider_id 都必须在源码里作为 `"<id>:` 出现过。

⚠️ 一个 provider 可以对应**多个适配器模块**：sina 一家供了日线、日历、快讯
三个端点，分别在三个模块里。所以 `modules` 是元组不是单值。
"""

from __future__ import annotations

from .contracts import ProviderDefinition
from .registry import DATASET_REGISTRY

__all__ = ["PROVIDERS", "PROVIDER_REGISTRY", "PROVIDER_IDS", "get_provider", "datasets_of"]


#: 数据源名册的**唯一手写处**。关系（谁支持哪些 dataset）不在这里写 —— 见 `datasets_of`。
#: 手写元组 + 派生 dict 的理由同 `registry.DATASETS`：dict 会静默吃掉重复的 id。
PROVIDERS: tuple[ProviderDefinition, ...] = (
    ProviderDefinition(
        provider_id="sina",
        title="新浪财经（日线 / 交易日历 / 7×24 快讯）",
        modules=(
            "easyup_biga.providers.sina",
            "easyup_biga.providers.sina_calendar",
            "easyup_biga.providers.sina_news",
        ),
    ),
    ProviderDefinition(
        provider_id="em",
        title="东方财富（涨跌家数 / 板块榜 / 情绪股池）",
        modules=("easyup_biga.providers.eastmoney",),
    ),
    ProviderDefinition(
        provider_id="tencent",
        title="腾讯行情（成交额 + 自带时间戳的交叉校验源）",
        modules=("easyup_biga.providers.tencent",),
    ),
    ProviderDefinition(
        provider_id="szse",
        title="深交所官方日历（本项目部署环境连不通，作 fallback 保留）",
        modules=("easyup_biga.providers.szse",),
    ),
)

#: 按 id 索引 —— 从 `PROVIDERS` 派生，不手写。
PROVIDER_REGISTRY: dict[str, ProviderDefinition] = {p.provider_id: p for p in PROVIDERS}

#: 定义顺序即展示顺序。
PROVIDER_IDS: tuple[str, ...] = tuple(PROVIDER_REGISTRY)


def datasets_of(provider_id: str) -> tuple[str, ...]:
    """这个 provider 被哪些数据集用到 —— **从 `DATASET_REGISTRY` 派生**。

    返回按数据集注册顺序排列的 id 元组。三种角色（primary / fallback /
    validation）都算「用到」：问这个问题的场景是「它挂了会影响什么」，
    而 validation 源挂掉会让交叉校验判不了 —— 那也是影响。
    """
    get_provider(provider_id)          # 未知 id 先 fail closed，别返回空元组
    return tuple(
        ds.dataset_id
        for ds in DATASET_REGISTRY.values()
        if provider_id == ds.primary_provider
        or provider_id in ds.fallback_providers
        or provider_id in ds.validation_providers
    )


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
            f"  ⚠️ id 取的是 raw_market_snapshot.source 的前缀（东方财富是 'em' "
            f"不是 'eastmoney'）—— 写模块名会造出第三套口径。"
        ) from None
