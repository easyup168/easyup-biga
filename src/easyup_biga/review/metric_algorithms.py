"""Versioned Outcome metric algorithms and machine-verifiable formula fingerprints.

The policy id answers *which business rule* owns an OutcomeSet.  The algorithm id and
fingerprints answer *which exact formula implementation* produced the numbers.  Every
OutcomeSet persists these values and the storage boundary freezes the first fingerprint seen
for one OutcomePolicy id.  Therefore changing a formula requires a new OutcomePolicy id and a
new algorithm id; old calculators remain available for offline replay.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import io
import json
import math
import statistics
import textwrap
import tokenize
from dataclasses import dataclass
from typing import Any, Callable, Mapping, TYPE_CHECKING

from .contracts import OutcomeMetric, horizon_trading_days

if TYPE_CHECKING:  # pragma: no cover - type-only dependency avoids registry/targets cycle.
    from .targets import TargetBar

__all__ = [
    "METRIC_ALGORITHM_REGISTRY",
    "MetricAlgorithmDefinition",
    "calculate_target_metrics",
    "get_metric_algorithm",
]


@dataclass(frozen=True, slots=True)
class MetricAlgorithmDefinition:
    algorithm_id: str
    formula_spec: Mapping[str, Any]
    formula_sha256: str
    implementation_sha256: str
    fingerprint_scheme_id: str
    calculator: Callable[..., tuple[OutcomeMetric, ...]]

    def manifest_ref(self) -> dict[str, str]:
        return {
            "algorithm_id": self.algorithm_id,
            "formula_sha256": self.formula_sha256,
            "implementation_sha256": self.implementation_sha256,
            "fingerprint_scheme_id": self.fingerprint_scheme_id,
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _formula_sha256(spec: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(dict(spec)).encode("utf-8")).hexdigest()


def _token_stream(func: Callable[..., Any]) -> list[tuple[int, str]]:
    """One function's semantic token stream, comments and formatting stripped."""
    source = textwrap.dedent(inspect.getsource(func))
    ignored = {
        tokenize.ENCODING,
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.ENDMARKER,
    }
    return [
        (token.type, token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type not in ignored
    ]


def _same_module_callees(func: Callable[..., Any]) -> dict[str, Callable[..., Any]]:
    """`func` 及它**在本模块内**能走到的全部辅助函数（传递闭包）。

    🔴 **只哈希 `func` 自己是不够的。** 外部 P4-R1.1 的第一版就是那样，
    而它声称守的是「公式没变」。实测：把 `_daily_returns()` 里的
    `bars[idx-1].close` 改成 `.open`，`positive_day_ratio` 从 0.5 变成 1.0，
    而指纹**一个字符都没变** —— 守卫确实在查一件事，只是不是它声称在查的那件事。

    ⚠️ 边界说清楚：只跟进**同模块**的可调用对象。
    跨模块的依赖（`horizon_trading_days` 来自 `.contracts`、`OutcomeMetric`
    是契约类）**不在指纹内** —— 它们由各自的契约测试守，且把整个 import 图
    拉进来会让任何无关改动都逼出一次 policy 升版。
    这条边界是**选择**，不是疏漏；改它要连同本 docstring 一起改。
    """
    module = getattr(func, "__module__", None)
    found: dict[str, Callable[..., Any]] = {}

    def walk(fn: Callable[..., Any]) -> None:
        name = getattr(fn, "__name__", None)
        if not name or name in found:
            return
        found[name] = fn
        globals_ = getattr(fn, "__globals__", {})
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Name):
                continue
            target = globals_.get(node.id)
            if callable(target) and getattr(target, "__module__", None) == module:
                walk(target)

    walk(func)
    return found


def _implementation_sha256(func: Callable[..., Any]) -> str:
    """Hash semantic source tokens of the calculator **and its same-module helpers**.

    This is deliberately conservative: renaming a variable changes the fingerprint and therefore
    requires a new policy version once data exists.  That cost is preferable to silently mixing
    historical formulas under one Policy id.
    """
    parts = [
        [name, _token_stream(fn)]
        for name, fn in sorted(_same_module_callees(func).items())
    ]
    return hashlib.sha256(_canonical_json(parts).encode("utf-8")).hexdigest()


def _daily_returns(bars: tuple["TargetBar", ...]) -> list[float]:
    out: list[float] = []
    for idx, bar in enumerate(bars):
        base = bar.open if idx == 0 else bars[idx - 1].close
        out.append(bar.close / base - 1.0)
    return out


def _market_index_outcome_synchronized_close_v1(
    target_id: str,
    horizon: str,
    bars: tuple["TargetBar", ...],
    *,
    expected_days: int,
) -> tuple[OutcomeMetric, ...]:
    if expected_days != horizon_trading_days(horizon):
        raise ValueError("expected_days disagrees with horizon")
    if not bars:
        raise ValueError("bars must not be empty")
    ordered = tuple(sorted(bars, key=lambda item: item.trade_date))
    if len(ordered) != expected_days:
        raise ValueError("target metric calculation requires complete horizon coverage")
    base = ordered[0].open
    if base <= 0:
        raise ValueError("anchor open must be positive")
    returns = _daily_returns(ordered)
    source_ids = tuple(
        dict.fromkeys(
            snapshot_id
            for bar in ordered
            for snapshot_id in bar.source_snapshot_ids
        )
    )
    observed = ordered[-1].trade_date
    coverage = len(ordered) / expected_days
    close_path = [0.0] + [item.close / base - 1.0 for item in ordered]
    if target_id == "market.index_blend.v1":
        mfe = max(close_path)
        mae = min(close_path)
    else:
        mfe = max(item.high for item in ordered) / base - 1.0
        mae = min(item.low for item in ordered) / base - 1.0
    values = {
        "return": ordered[-1].close / base - 1.0,
        "mfe": mfe,
        "mae": mae,
        "positive_day_ratio": sum(value > 0 for value in returns) / len(returns),
        "realized_vol": statistics.pstdev(returns) * math.sqrt(252.0),
        "coverage": coverage,
    }
    return tuple(
        OutcomeMetric(
            target_id=target_id,
            horizon=horizon,
            observed_trade_date=observed,
            metric_name=name,
            value=float(value),
            unit="ratio",
            source_snapshot_ids=source_ids,
            coverage_ratio=coverage,
        )
        for name, value in values.items()
    )


_SYNC_CLOSE_V1_SPEC: dict[str, Any] = {
    "algorithm_id": "market-index-outcome-synchronized-close-v1",
    "anchor": "first-bar-open",
    "return": "last-close/anchor-open-1",
    "direct-index-mfe": "max(daily-intraday-high)/anchor-open-1",
    "direct-index-mae": "min(daily-intraday-low)/anchor-open-1",
    "blend-mfe": "max([0]+synchronized-daily-close/anchor-open-1)",
    "blend-mae": "min([0]+synchronized-daily-close/anchor-open-1)",
    "coverage": "observed-trading-days/expected-trading-days",
    "daily-return": "day-close/(anchor-open-if-first-else-prior-close)-1",
}

_SYNC_CLOSE_V1 = MetricAlgorithmDefinition(
    algorithm_id="market-index-outcome-synchronized-close-v1",
    formula_spec=_SYNC_CLOSE_V1_SPEC,
    formula_sha256=_formula_sha256(_SYNC_CLOSE_V1_SPEC),
    implementation_sha256=_implementation_sha256(_market_index_outcome_synchronized_close_v1),
    fingerprint_scheme_id="python-token-stream-v1",
    calculator=_market_index_outcome_synchronized_close_v1,
)

METRIC_ALGORITHM_REGISTRY: Mapping[str, MetricAlgorithmDefinition] = {
    _SYNC_CLOSE_V1.algorithm_id: _SYNC_CLOSE_V1,
}


def get_metric_algorithm(algorithm_id: str) -> MetricAlgorithmDefinition:
    try:
        return METRIC_ALGORITHM_REGISTRY[str(algorithm_id)]
    except KeyError as exc:
        raise KeyError(f"unknown Outcome metric algorithm: {algorithm_id}") from exc


def calculate_target_metrics(
    target_id: str,
    horizon: str,
    bars: tuple["TargetBar", ...],
    *,
    expected_days: int,
    metric_algorithm_id: str,
) -> tuple[OutcomeMetric, ...]:
    definition = get_metric_algorithm(metric_algorithm_id)
    return definition.calculator(
        target_id,
        horizon,
        bars,
        expected_days=expected_days,
    )
