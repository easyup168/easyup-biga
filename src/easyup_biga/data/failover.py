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


class ProviderChainExhausted(RuntimeError):
    def __init__(self, dataset_id: str, attempts: tuple[ProviderExecutionAttempt, ...]):
        self.dataset_id = dataset_id
        self.attempts = attempts
        detail = "；".join(f"{x.provider_id}={x.error}" for x in attempts) or "（一个都没登记）"
        super().__init__(f"{dataset_id} 的 PRIMARY/FALLBACK 全部失败：{detail}")


def provider_chain(dataset_id: str) -> tuple[tuple[str, ProviderRole], ...]:
    """确定性的 PRIMARY → FALLBACK 顺序。"""
    items = [b for b in bindings_for_dataset(dataset_id) if b.role in _SUBSTITUTABLE_ORDER]
    items.sort(key=lambda b: (_SUBSTITUTABLE_ORDER[b.role], b.provider_id))
    return tuple((b.provider_id, b.role) for b in items)


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
            _record(ProviderExecutionAttempt(provider_id, role, False, "没有配置取数函数"))
            continue
        try:
            value = fetcher()
        except Exception as exc:
            _record(ProviderExecutionAttempt(
                provider_id, role, False, f"{type(exc).__name__}: {exc}"))
            continue
        _record(ProviderExecutionAttempt(provider_id, role, True, None))
        return ProviderExecutionResult(provider_id, role, value, tuple(attempts))
    raise ProviderChainExhausted(dataset_id, tuple(attempts))
