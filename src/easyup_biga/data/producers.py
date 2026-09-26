"""Phase 3 active-dataset producer registry.

Dataset Registry says what the platform owns; this registry says what production
entrypoint can actually produce/freeze each active Phase-3 dataset.  Keeping this
explicit closes the former "registered + bound + no producer" false-green gate.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable, Any

from .registry import get_dataset

__all__ = ["ProducerDefinition", "PRODUCERS", "PRODUCER_REGISTRY", "get_producer", "resolve_producer"]


@dataclass(frozen=True, slots=True)
class ProducerDefinition:
    dataset_id: str
    entrypoint: str
    provider_id: str | None = None


PRODUCERS: tuple[ProducerDefinition, ...] = (
    ProducerDefinition("cn.security_master", "easyup_biga.data.datasets.security_master:SecurityMasterService.sync", "eastmoney_security_master"),
    ProducerDefinition("cn.trading_calendar", "easyup_biga.data.client:refresh_trading_calendar", None),
    ProducerDefinition("cn.index.daily_bars", "easyup_biga.data.datasets.index_daily:IndexDailyDatasetBridge.publish", None),
    ProducerDefinition("cn.equity.daily_bars", "easyup_biga.data.eod_pipeline:run_eod_bundle", "eastmoney_eod"),
    ProducerDefinition("cn.security.tradability", "easyup_biga.data.eod_pipeline:run_eod_bundle", "derived_biga"),
    ProducerDefinition("cn.equity.adjustment_factors", "easyup_biga.data.datasets.adjustment_factors:run", "csv_adjustment"),
    ProducerDefinition("cn.market.emotion_close", "easyup_biga.data.datasets.emotion_close:run", "derived_biga"),
    ProducerDefinition("cn.index.realtime_quote", "easyup_biga.data.decision_client:DecisionDataClient.freeze_required", "tencent"),
    ProducerDefinition("cn.market.breadth", "easyup_biga.data.decision_client:DecisionDataClient.freeze_required", "eastmoney"),
    ProducerDefinition("cn.sector.board_snapshot", "easyup_biga.data.decision_client:DecisionDataClient.freeze_required", "eastmoney"),
    ProducerDefinition("cn.market.limit_pool", "easyup_biga.data.decision_client:DecisionDataClient.freeze_required", "eastmoney"),
    ProducerDefinition("cn.news.flash", "easyup_biga.data.decision_client:DecisionDataClient.freeze_required", "sina_news"),
)

PRODUCER_REGISTRY: dict[str, ProducerDefinition] = {p.dataset_id: p for p in PRODUCERS}


def get_producer(dataset_id: str) -> ProducerDefinition:
    try:
        return PRODUCER_REGISTRY[dataset_id]
    except KeyError:
        raise KeyError(f"active dataset {dataset_id!r} has no registered producer") from None


def resolve_producer(dataset_id: str) -> Callable[..., Any]:
    spec = get_producer(dataset_id)
    module_name, attr_path = spec.entrypoint.split(":", 1)
    # 🔴 为什么是字符串入口而不是直接持有 callable：**避开 import 环**。
    #    producers → datasets/* → publication → snapshots → registry，
    #    而注册表侧要反查 producer。惰性解析把这个环打开。
    #
    #    这条路解析的是**生产者入口**（`module:attr`），拿不到数据库连接 ——
    #    守卫防的是「绕开 AST 检查拿到一条真实可读写的 sqlite3 连接」。
    obj: Any = importlib.import_module(module_name)  # store-exempt: 解析生产者入口，非 DB 连接
    for name in attr_path.split("."):
        obj = getattr(obj, name)
    if not callable(obj):
        raise TypeError(f"producer entrypoint is not callable: {spec.entrypoint}")
    return obj


@dataclass(frozen=True, slots=True)
class _ModuleProvider:
    dataset_id: str
    provider_id: str


def _dataset_module_providers() -> dict[str, _ModuleProvider]:
    """扫 `data/datasets/` 下每个模块自报的 `(DATASET_ID, PROVIDER_ID)`。

    🔴 为什么扫模块、不从 producer 的 `entrypoint` 推：**推不出来**。
    `cn.security.tradability` 的生产入口是 `eod_pipeline:run_eod_bundle`，
    而它的 `PROVIDER_ID` 常量在 `datasets/tradability.py` 里 ——
    入口模块与常量模块不是同一个。

    反过来，每个 dataset 模块自己同时带着这两个常量，配对是**自描述的**，
    不需要第四份手写清单。
    """
    import pkgutil

    from . import datasets as _pkg

    out: dict[str, _ModuleProvider] = {}
    for item in pkgutil.iter_modules(_pkg.__path__):
        name = f"{_pkg.__name__}.{item.name}"
        # store-exempt: 读 dataset 模块的两个字符串常量，非 DB 连接
        mod = importlib.import_module(name)
        ds_id = getattr(mod, "DATASET_ID", None)
        pv_id = getattr(mod, "PROVIDER_ID", None)
        if ds_id and pv_id:
            out[name] = _ModuleProvider(str(ds_id), str(pv_id))
    return out


def validate_producer_binding(dataset_id: str) -> None:
    """三处 provider id 必须一致 —— 注册表 / 本册声明 / **模块里那个常量**。

    🔴 第三处是关键，而第一版漏了它。
    provider id 今天有三份手写副本：

    1. `registry.py` 的 `DatasetDefinition.primary_provider`
    2. `producers.py` 这份声明（`ProducerDefinition.provider_id`）
    3. dataset 模块里的 `PROVIDER_ID` 常量 —— **运行时真正传给 `publish()` 的那个**

    第一版只比 1 与 2。于是把 3 改坏（`derived_biga` → `derived-biga`），
    本闸门照样报绿，而生产路径上 `role_of()` 会当场 fail-closed。

    > **守卫查的是它自己那份抄件，不是运行时真正用的那个值。**

    ⚠️ 这个闸门恰恰是为了消灭一处 false-green 才写的 —— 它自己带了一处。
    实测踩过：2026-09-26 我把注册表里的 id 从连字符改成下划线，漏改了
    dataset 模块的常量，`emotion_close.run()` 从此必抛；而那条路在生产上
    被 eastmoney 故障挡着，没炸出来。
    """
    spec = get_producer(dataset_id)
    ds = get_dataset(dataset_id)
    if spec.provider_id is not None and spec.provider_id != ds.primary_provider:
        raise ValueError(
            f"producer/provider drift for {dataset_id}: implementation={spec.provider_id!r} "
            f"registry primary={ds.primary_provider!r}"
        )
    for module_name, declared in _dataset_module_providers().items():
        if declared.dataset_id != dataset_id:
            continue
        if declared.provider_id != ds.primary_provider:
            raise ValueError(
                f"producer/provider drift for {dataset_id}: "
                f"{module_name}.PROVIDER_ID={declared.provider_id!r} 与注册表 primary="
                f"{ds.primary_provider!r} 不一致 —— "
                f"运行时传给 publish() 的是前者，而注册表按后者查绑定，"
                f"这条路会在 role_of() 上 fail-closed。")
