#!/usr/bin/env python3
"""technical-calc —— 指数技术指标，输出合法 AgentVerdict。

🔴 做**指数**，不做个股
------------------------
architecture §3.1 给 technical 的职责写的是「个股技术结构」。
但 Phase 2 **不选股**，没有个股输入 —— 硬做就会变成第二个 `discipline`
（没有输入源却硬建，只能编）。

⇒ Phase 2 的 technical 分析**上证指数**的技术面。它有真实输入（日线序列），
   产出可核对。个股技术面等有了选股输入再说。

⚠️ 只做上证一个指数也是有意的：两市指数高度相关，做两份只会让 Agent
   在两个几乎相同的信号之间纠结，而不会多出信息。
   这是**范围外**，不是数据缺失 —— 不进 `missing`（理由见 `phase-2-specialists.md` §3.8）。

分工（铁律 4）
--------------
本脚本只给**数字**：均线值、MACD 三线、RSI、距 60 日高低点的百分比。
「算不算多头排列」「是不是突破」由 Technical Agent 判断 ——
它拿到 `ma_order` 这种**确定性观察**，不需要自己比大小。

口径（改了要升 `CALC_VERSION`，否则历史结论无法清点）
-----------------------------------------------------
- EMA 用前 n 根的简单平均做种子
- ``DIF = EMA12 − EMA26``，``DEA = EMA9(DIF)``，``HIST = 2 × (DIF − DEA)``（A 股惯例）
- RSI14 用 Wilder 平滑

用法::

    python3 technical_calc.py
    python3 technical_calc.py --render
    python3 technical_calc.py --break-source daily
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from datetime import datetime
from typing import Any

_HERE = pathlib.Path(__file__).resolve()
_REPO = _HERE.parent.parent.parent.parent
sys.path.insert(0, str(_REPO / "skills"))

from _contract import (  # noqa: E402
    ADHOC_TASK_SEQ,
    AgentVerdict,
    Evidence,
    MissingItem,
    new_task_id,
    now_cn,
)
from _sources import (  # noqa: E402
    SourceError,
    as_of_for_trade_date,
    fetch_index_daily,
)
from _store import (  # noqa: E402
    init_schema,
    payload_sha256,
    save_raw_snapshot,
    save_verdict,
)

AGENT = "technical"
CALC_VERSION = "technical-calc/1"

SYMBOL = "sh000001"
SYMBOL_LABEL = "上证指数"

#: 取多少根。MA60 要 60 根，MACD 的 EMA26+DEA9 要 ~35 根预热，
#: 120 根足够且一次请求拿得下（实测 `datalen=120` 真生效）。
BAR_COUNT = 120
MA_WINDOWS = (5, 10, 20, 60)
HL_WINDOW = 60
RSI_WINDOW = 14
#: MACD 的最短预热根数。不足就不给 —— **不用更短的窗口凑一个看起来像的数**。
MACD_MIN_BARS = 35

_EXPECTED_FIELDS = 14


def _sma(xs: list[float], n: int) -> float | None:
    return round(sum(xs[-n:]) / n, 3) if len(xs) >= n else None


def _ema_series(xs: list[float], n: int) -> list[float]:
    """EMA 序列，用前 n 根的简单平均做种子。"""
    if len(xs) < n:
        return []
    k = 2.0 / (n + 1)
    out = [sum(xs[:n]) / n]
    for x in xs[n:]:
        out.append(out[-1] + k * (x - out[-1]))
    return out


def _macd(closes: list[float]) -> tuple[float, float, float] | None:
    if len(closes) < MACD_MIN_BARS:
        return None
    e12, e26 = _ema_series(closes, 12), _ema_series(closes, 26)
    if not e12 or not e26:
        return None
    # 两条 EMA 起点不同，右对齐后再相减
    n = min(len(e12), len(e26))
    dif = [a - b for a, b in zip(e12[-n:], e26[-n:])]
    dea = _ema_series(dif, 9)
    if not dea:
        return None
    return round(dif[-1], 4), round(dea[-1], 4), round(2 * (dif[-1] - dea[-1]), 4)


def _rsi(closes: list[float], n: int = RSI_WINDOW) -> float | None:
    """Wilder 平滑的 RSI。"""
    if len(closes) < n + 1:
        return None
    diffs = [b - a for a, b in zip(closes[:-1], closes[1:])]
    gains = [d if d > 0 else 0.0 for d in diffs]
    losses = [-d if d < 0 else 0.0 for d in diffs]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        ag = (ag * (n - 1) + g) / n
        al = (al * (n - 1) + l) / n
    if al == 0:
        # 区间内一天都没跌 —— 这是真的 100，不是除零错误
        return 100.0 if ag > 0 else 50.0
    return round(100 - 100 / (1 + ag / al), 2)


def build_verdict(*, break_source: set[str], store: bool, task_id: str) -> AgentVerdict:
    t_start = time.monotonic()
    missing: list[MissingItem] = []
    warnings: list[str] = []
    result: dict[str, Any] = {}
    evidence: list[Evidence] = []
    retrieved = now_cn()
    as_of: datetime | None = None
    raw_hash: str | None = None

    daily = None
    if "daily" in break_source:
        missing.append(MissingItem(
            f"{SYMBOL_LABEL}日线 —— 数据源被人为中断（--break-source daily）",
            "technical.daily.source_broken"))
    else:
        try:
            daily = fetch_index_daily(SYMBOL, bars=BAR_COUNT)
        except (SourceError, ValueError) as e:
            missing.append(MissingItem(f"{SYMBOL_LABEL}日线 —— 数据源不可用: {e}",
                                       "technical.daily.unavailable"))

    def add(field: str, value: Any, label: str, source: str) -> None:
        result[field] = value
        evidence.append(Evidence(
            field=field, source=source, value=value,
            as_of=as_of, retrieved_at=retrieved,
            calc_version=CALC_VERSION, label=label,
            raw_hash=None if source.startswith("derived:") else raw_hash))

    if daily is not None:
        src = f"sina:kline/{SYMBOL}"
        raw_hash = payload_sha256(daily.raw)
        trade_date = daily.trade_date
        as_of, as_of_warning = as_of_for_trade_date(trade_date, retrieved_at=retrieved)
        if as_of_warning:
            warnings.append(as_of_warning)
        if store:
            save_raw_snapshot(source=src, as_of=as_of.isoformat(),
                              retrieved_at=retrieved.isoformat(), payload=daily.raw)

        closes = [b.close for b in daily.bars]
        add("trade_date", trade_date, "交易日", src)

        if daily.last.close <= 0:
            missing.append(MissingItem(
                f"{SYMBOL_LABEL}收盘价 —— 数据源给出 {daily.last.close}，不是有效点位",
                "technical.close.invalid_value"))
        else:
            add("close", round(daily.last.close, 2), f"{SYMBOL_LABEL}收盘", src)

            short_ma = []
            for n in MA_WINDOWS:
                v = _sma(closes, n)
                if v is None:
                    short_ma.append(n)
                else:
                    add(f"ma{n}", v, f"MA{n}", f"derived:{src}")
            if short_ma:
                missing.append(MissingItem(
                    f"MA{'/'.join(map(str, short_ma))} —— 日线只有 {len(closes)} 根，"
                    f"不用更短的窗口凑一个看起来像的数",
                    "technical.ma.insufficient_bars"))

            ma20 = result.get("ma20")
            if ma20:
                add("price_vs_ma20_pct",
                    round((daily.last.close / ma20 - 1) * 100, 2),
                    "收盘相对 MA20(%)", f"derived:{src}")

            have = [n for n in MA_WINDOWS if f"ma{n}" in result]
            if len(have) >= 2:
                # 确定性观察，不是判断：把大小关系摆出来，Agent 不用自己比
                order = sorted(have, key=lambda n: result[f"ma{n}"], reverse=True)
                add("ma_order", ">".join(f"ma{n}" for n in order),
                    "均线大小关系", f"derived:{src}")

            m = _macd(closes)
            if m is None:
                missing.append(MissingItem(
                    f"MACD —— 日线不足 {MACD_MIN_BARS} 根，EMA 预热不够，"
                    f"算出来的值不可用",
                    "technical.macd.insufficient_bars"))
            else:
                add("macd_dif", m[0], "MACD DIF", f"derived:{src}")
                add("macd_dea", m[1], "MACD DEA", f"derived:{src}")
                add("macd_hist", m[2], "MACD 柱", f"derived:{src}")

            r = _rsi(closes)
            if r is None:
                missing.append(MissingItem(
                    f"RSI{RSI_WINDOW} —— 日线不足 {RSI_WINDOW + 1} 根",
                    "technical.rsi.insufficient_bars"))
            else:
                add("rsi14", r, f"RSI{RSI_WINDOW}", f"derived:{src}")

            if len(closes) >= HL_WINDOW:
                win = [b for b in daily.bars[-HL_WINDOW:]]
                hi, lo = max(b.high for b in win), min(b.low for b in win)
                add("dist_to_high60_pct",
                    round((daily.last.close / hi - 1) * 100, 2),
                    f"距 {HL_WINDOW} 日高点(%)", f"derived:{src}")
                add("dist_to_low60_pct",
                    round((daily.last.close / lo - 1) * 100, 2),
                    f"距 {HL_WINDOW} 日低点(%)", f"derived:{src}")
            else:
                missing.append(MissingItem(
                    f"距 {HL_WINDOW} 日高低点 —— 日线只有 {len(closes)} 根",
                    "technical.range.insufficient_bars"))
    else:
        missing.append(MissingItem(
            "全部技术指标 —— 没有日线，无从算起",
            "technical.daily.undetermined"))

    core = {"trade_date", "close", "ma20"}
    if not missing:
        status, level = "completed", "PASS"
    elif core <= set(result):
        status, level = "partial", "WARNING"
    else:
        status, level = "partial", "UNKNOWN"

    return AgentVerdict(
        task_id=task_id, agent=AGENT, status=status, verdict=level,
        result=result,
        confidence=round(len(result) / _EXPECTED_FIELDS, 2) if result else 0.0,
        evidence=evidence, warnings=warnings, missing=missing,
        elapsed_ms=int((time.monotonic() - t_start) * 1000))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="指数技术指标 → AgentVerdict JSON")
    ap.add_argument("--break-source", action="append", default=[], metavar="NAME",
                    help="演练：人为中断 (daily)")
    ap.add_argument("--task-id")
    ap.add_argument("--no-store", action="store_true")
    ap.add_argument("--render", action="store_true")
    args = ap.parse_args(argv)

    store = not args.no_store
    if store:
        init_schema()
    v = build_verdict(break_source=set(args.break_source), store=store,
                      task_id=args.task_id or new_task_id(ADHOC_TASK_SEQ))
    ref = save_verdict(v) if store else None

    print(json.dumps(v.to_dict(), ensure_ascii=False, indent=2))
    if ref is not None:
        print(f"verdict_ref={ref}", file=sys.stderr)
    if args.render:
        print("\n" + "─" * 60, file=sys.stderr)
        print(f"{AGENT}  {v.status}/{v.verdict}  耗时 {v.elapsed_ms}ms"
              + (f"  verdict_ref={ref}" if ref else "  (未落库)"), file=sys.stderr)
        for e in v.evidence:
            print(f"  {e.display_label:<20} = {e.value}", file=sys.stderr)
        for w in v.warnings:
            print(f"  ⚠ {w}", file=sys.stderr)
        for m in v.missing:
            print(f"  ⚠ 缺失 [{m.code}] {m}", file=sys.stderr)
    return {"PASS": 0, "WARNING": 2}.get(v.verdict, 3)


if __name__ == "__main__":
    raise SystemExit(main())
