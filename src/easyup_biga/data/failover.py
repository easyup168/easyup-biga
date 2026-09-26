"""Provider 的选择与降级 —— 属于数据层边界，**不进 agent**。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：给定 dataset，按注册表顺序依次试 PRIMARY → FALLBACK，并把**每一次
  尝试**（含失败）交还给调用方去落账
- **不覆盖**：落库、冲突裁决、质量判定。本模块只回答「最后是谁取到的」

🔴 VALIDATOR 不是替补
---------------------
校验源存在的意义是「和主源对不上时把快照判 QUARANTINED」（裁定 15）。
把它当 fallback 顶上去，等于在主源挂掉时**静默换了一套口径** ——
那正是裁定 15 要防的「某天悄悄给出两个数」。所以 `provider_chain()` 只取
PRIMARY 与 FALLBACK。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from easyup_biga.providers.http import SourceError

from .contracts import ProviderRole
from .provider_registry import bindings_for_dataset

#: 可以顶替主源的角色，以及它们的先后。VALIDATOR 不在其中 —— 见模块头。
_SUBSTITUTABLE_ORDER: dict[ProviderRole, int] = {
    ProviderRole.PRIMARY: 0,
    ProviderRole.FALLBACK: 1,
}


@dataclass(frozen=True, slots=True)
class ProviderExecutionAttempt:
    provider_id: str
    role: ProviderRole
    succeeded: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderExecutionResult:
    provider_id: str
    role: ProviderRole
    value: Any
    attempts: tuple[ProviderExecutionAttempt, ...]


class ProviderChainExhausted(SourceError):
    """整条 PRIMARY/FALLBACK 链都失败了。

    🔴 **基类是 `SourceError` 而不是 `RuntimeError`**（2026-09-26 改）。

    「每一个 provider 都失败了」本来就是一种**取数失败** ——
    它该和单源失败走同一条降级路径（上浮到 `missing[]`），
    而不是把整次决策炸掉。

    改这个基类之前它是 `RuntimeError`，而 `decision_client.freeze_required`
    只接 `(SourceError, ValueError)` 当作可降级失败。于是给 breadth 配上
    备用源的那一刻，「两个源都挂」从「这条数据缺失」变成了
    **「整张卡出不来」** —— 加备胎反而让系统更脆。

    ⚠️ 测试当场抓到了它。写在这里，因为下一个给别的 dataset 配备胎的人
    会走到同一个路口：**降级链的终点必须仍然是一次可降级的失败。**
    """


    def __init__(self, dataset_id: str, attempts: tuple[ProviderExecutionAttempt, ...]):
        self.dataset_id = dataset_id
        self.attempts = attempts
        detail = "；".join(f"{x.provider_id}={x.error}" for x in attempts) or "（一个都没登记）"
        super().__init__(f"{dataset_id} 的 PRIMARY/FALLBACK 全部失败：{detail}")


def provider_chain(dataset_id: str) -> tuple[tuple[str, ProviderRole], ...]:
    """PRIMARY → FALLBACK，只做**过滤**。

    🔴 这里不再排序。`bindings_for_dataset()` 返回的就已经是
    「PRIMARY → 按 id 排的 FALLBACK → 按 id 排的 VALIDATOR」，再排一次是
    **恒等操作** —— 探针实测：把那行 sort 删掉，没有任何测试会红。

    > 一个永远观察不到生效的排序，和一个永远不会红的守卫是同一种东西：
    > 它让读的人以为顺序在这里被保证，于是不再去看真正保证它的地方。

    ⇒ 顺序的契约只有一处（`bindings_for_dataset`），并由
      `test_data_registry.py::test_provider绑定的顺序是确定的` 钉住。
    """
    return tuple(
        (b.provider_id, b.role)
        for b in bindings_for_dataset(dataset_id)
        if b.role in _SUBSTITUTABLE_ORDER
    )


def execute_with_fallback(
    dataset_id: str,
    fetchers: Mapping[str, Callable[[], Any]],
    *,
    on_attempt: Callable[[ProviderExecutionAttempt], None] | None = None,
) -> ProviderExecutionResult:
    """按注册表顺序执行取数，**每一次尝试都回调一次**（成功与失败同等对待）。

    🔴 返回真正取到数的那个 provider，而不是 dataset 的 primary ——
    发布侧要拿它写 `source`。写成 primary 会让降级过的那天在卡上**看起来
    像正常的一天**：数据来自备用源，溯源却指着主源。
    """
    attempts: list[ProviderExecutionAttempt] = []

    def _record(attempt: ProviderExecutionAttempt) -> None:
        attempts.append(attempt)
        if on_attempt:
            on_attempt(attempt)

    chain = provider_chain(dataset_id)
    if not chain:
        raise ProviderChainExhausted(dataset_id, ())
    for provider_id, role in chain:
        fetcher = fetchers.get(provider_id)
        if fetcher is None:
            raise RuntimeError(
                f"{dataset_id} 的 provider {provider_id!r} 已注册但没有配置取数函数")
        try:
            value = fetcher()
        except SourceError as exc:
            # Only an explicitly classified provider/source failure may trigger
            # fallback.  Programming errors (TypeError/KeyError/AssertionError/...)
            # must escape immediately instead of being hidden by a healthy backup.
            _record(ProviderExecutionAttempt(
                provider_id, role, False, f"{type(exc).__name__}: {exc}"))
            continue
        _record(ProviderExecutionAttempt(provider_id, role, True, None))
        return ProviderExecutionResult(provider_id, role, value, tuple(attempts))
    raise ProviderChainExhausted(dataset_id, tuple(attempts))
