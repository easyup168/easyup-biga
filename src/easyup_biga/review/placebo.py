"""Deterministic Phase-4 resampling and placebo helpers.

All randomness is local to an explicit seed.  The inference unit is one anchor day;
functions operate on day-level values and never expand multiple Cards from the same day.
"""
from __future__ import annotations

import itertools
import math
import random
import statistics
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

__all__ = [
    "PermutationResult",
    "BootstrapResult",
    "bh_fdr_adjust",
    "bootstrap_effect_ci",
    "holm_adjust",
    "lagged_labels",
    "permutation_test",
]


@dataclass(frozen=True, slots=True)
class PermutationResult:
    observed_effect: float
    p_value: float
    iterations: int
    exact: bool
    null_mean: float
    null_ci_low: float
    null_ci_high: float


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    effect: float
    ci_low: float
    ci_high: float
    iterations: int
    confidence_level: float


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("values must not be empty")
    return statistics.fmean(float(v) for v in values)


def _effect(a: Sequence[float], b: Sequence[float]) -> float:
    return _mean(a) - _mean(b)


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("cannot take quantile of empty values")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be in [0,1]")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = (len(sorted_values) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(sorted_values[lo])
    frac = pos - lo
    return float(sorted_values[lo]) * (1.0 - frac) + float(sorted_values[hi]) * frac


def permutation_test(
    group_a: Sequence[float],
    group_b: Sequence[float],
    *,
    iterations: int,
    seed: int,
    alternative: str = "greater",
) -> PermutationResult:
    """Day-level label permutation preserving group sizes and label frequency."""
    a = tuple(float(v) for v in group_a)
    b = tuple(float(v) for v in group_b)
    if not a or not b:
        raise ValueError("both permutation groups must be non-empty")
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    if alternative not in {"greater", "less", "two-sided"}:
        raise ValueError("alternative must be greater/less/two-sided")
    observed = _effect(a, b)
    pooled = a + b
    n_a = len(a)
    total_combinations = math.comb(len(pooled), n_a)
    null: list[float] = []
    exact = total_combinations <= iterations
    if exact:
        indices = range(len(pooled))
        for chosen in itertools.combinations(indices, n_a):
            chosen_set = set(chosen)
            pa = [pooled[i] for i in indices if i in chosen_set]
            pb = [pooled[i] for i in indices if i not in chosen_set]
            null.append(_effect(pa, pb))
    else:
        rng = random.Random(int(seed))
        indices = list(range(len(pooled)))
        for _ in range(iterations):
            shuffled = indices.copy()
            rng.shuffle(shuffled)
            chosen = set(shuffled[:n_a])
            pa = [pooled[i] for i in indices if i in chosen]
            pb = [pooled[i] for i in indices if i not in chosen]
            null.append(_effect(pa, pb))

    if alternative == "greater":
        extreme = sum(v >= observed for v in null)
    elif alternative == "less":
        extreme = sum(v <= observed for v in null)
    else:
        extreme = sum(abs(v) >= abs(observed) for v in null)
    p = extreme / len(null) if exact else (extreme + 1) / (len(null) + 1)
    ordered = sorted(null)
    return PermutationResult(
        observed_effect=observed,
        p_value=float(p),
        iterations=len(null),
        exact=exact,
        null_mean=_mean(null),
        null_ci_low=_quantile(ordered, 0.025),
        null_ci_high=_quantile(ordered, 0.975),
    )


def bootstrap_effect_ci(
    group_a: Sequence[float],
    group_b: Sequence[float],
    *,
    iterations: int,
    seed: int,
    confidence_level: float,
) -> BootstrapResult:
    """Percentile CI from independent day-level resampling within each group."""
    a = tuple(float(v) for v in group_a)
    b = tuple(float(v) for v in group_b)
    if not a or not b:
        raise ValueError("both bootstrap groups must be non-empty")
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be in (0,1)")
    rng = random.Random(int(seed))
    effects: list[float] = []
    for _ in range(iterations):
        sa = [a[rng.randrange(len(a))] for _ in a]
        sb = [b[rng.randrange(len(b))] for _ in b]
        effects.append(_effect(sa, sb))
    effects.sort()
    alpha = 1.0 - confidence_level
    return BootstrapResult(
        effect=_effect(a, b),
        ci_low=_quantile(effects, alpha / 2.0),
        ci_high=_quantile(effects, 1.0 - alpha / 2.0),
        iterations=iterations,
        confidence_level=confidence_level,
    )


def holm_adjust(pvalues: Mapping[str, float]) -> dict[str, float]:
    """Holm step-down family-wise adjusted p-values."""
    items = sorted((str(k), float(v)) for k, v in pvalues.items())
    for _key, value in items:
        if not 0.0 <= value <= 1.0:
            raise ValueError("p-values must be in [0,1]")
    ranked = sorted(items, key=lambda item: (item[1], item[0]))
    m = len(ranked)
    out: dict[str, float] = {}
    running = 0.0
    for idx, (key, p) in enumerate(ranked):
        adjusted = min(1.0, (m - idx) * p)
        running = max(running, adjusted)
        out[key] = min(1.0, running)
    return out


def bh_fdr_adjust(pvalues: Mapping[str, float]) -> dict[str, float]:
    """Benjamini-Hochberg FDR adjusted p-values."""
    items = sorted((str(k), float(v)) for k, v in pvalues.items())
    for _key, value in items:
        if not 0.0 <= value <= 1.0:
            raise ValueError("p-values must be in [0,1]")
    ranked = sorted(items, key=lambda item: (item[1], item[0]))
    m = len(ranked)
    out: dict[str, float] = {}
    running = 1.0
    for reverse_idx in range(m - 1, -1, -1):
        key, p = ranked[reverse_idx]
        rank = reverse_idx + 1
        running = min(running, p * m / rank)
        out[key] = min(1.0, running)
    return out


def lagged_labels(rows: Iterable[tuple[str, str | None]]) -> dict[str, str | None]:
    """Map each anchor day to the previous distinct anchor day's label."""
    ordered = sorted((str(day), label) for day, label in rows)
    out: dict[str, str | None] = {}
    previous: str | None = None
    for day, label in ordered:
        out[day] = previous
        previous = label
    return out
