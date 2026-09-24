#!/usr/bin/env python3
"""technical-calc —— 指数技术指标，输出合法 FactBundle。

🔴 批 E-II：从产合体 `AgentVerdict` 迁到产 `FactBundle`（只事实、无 stance），形状与
E-I 迁 emotion 完全一致。stance 由 Technical Agent 事后 `amend_verdict.py --stance` 追加一个
`AgentAssessment`（不重打事实）。消费方零改动（`load_verdict` 多态）。

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
    evidence_origins,
    resolve_provenance,
    ADHOC_TASK_SEQ,
    Evidence,
    FactBundle,
    MissingItem,
    new_task_id,
    now_cn,
)
from _sources import (  # noqa: E402
    implausible_bars,
    SourceError,
    as_of_for_trade_date,
    fetch_index_daily,
)
from _store import (  # noqa: E402
    init_schema,
    raw_text_sha256,
    save_fact_bundle,
    save_raw_snapshot,
)
from _snapshot import SnapshotCoordinator  # noqa: E402

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


def build_fact_bundle(*, break_source: set[str], store: bool, task_id: str,
                      evidence_set_id: str | None = None) -> FactBundle:
    t_start = time.monotonic()
    missing: list[MissingItem] = []
    warnings: list[str] = []
    result: dict[str, Any] = {}
    evidence: list[Evidence] = []
    retrieved = now_cn()
    as_of: datetime | None = None
    raw_hash: str | None = None
    es_id: str | None = None  # 读冻结时填冻结集 id（批 E-I），供 risk CROSS_CHECK 结构核对

    # 🔴 给了 evidence_set_id 就读本次决策的冻结快照（不联网、不重复落盘），
    #    没给就跟今天一样自己抓（手工调试单跑不被连坐拦掉，批 D-II 做什么第 2 条）。
    coord = SnapshotCoordinator() if evidence_set_id is not None else None

    daily = None
    if "daily" in break_source:
        missing.append(MissingItem(
            f"{SYMBOL_LABEL}日线 —— 数据源被人为中断（--break-source daily）",
            "technical.daily.source_broken"))
    elif coord is not None:
        # 🔴 fail-closed：读冻结失败（号不存在 / 没冻这个 symbol / 冻的根数不够）
        #    直接上抛 SnapshotReadError，**绝不静默退回 fetch_index_daily**——
        #    那是探针 P5 要防的（悄悄退回独立抓取 = 假装什么都对）。
        daily = coord.read_index_daily(evidence_set_id, SYMBOL, bars=BAR_COUNT)
    else:
        try:
            daily = fetch_index_daily(SYMBOL, bars=BAR_COUNT)
        except (SourceError, ValueError) as e:
            missing.append(MissingItem(f"{SYMBOL_LABEL}日线 —— 数据源不可用: {e}",
                                       "technical.daily.unavailable"))

    # 本 skill 只读一个来源，但溯源归属走**全仓唯一那份实现**（批 2）——
    # 自己写一个 `None if derived else raw_hash` 就又成了第六份口径。
    # 下面两张表各只有一条目，在 src/raw_hash/es_id 落定之后填。
    _hash_tbl: dict[str, str] = {}
    _es_tbl: dict[str, str] = {}

    def add(field: str, value: Any, label: str, source: str, *,
            kind: str | None, inputs: tuple[str, ...] = ()) -> None:
        """`kind` 无默认值 —— 强制每个调用点自己说清楚（裁定 16）。
本 skill 的证据全部出自同一份 K 线 ⇒ `raw_hash` 即完整出处。"""
        result[field] = value
        evidence.append(Evidence(
            field=field, source=source, value=value,
            as_of=as_of, retrieved_at=retrieved,
            calc_version=CALC_VERSION, label=label,
            raw_hash=resolve_provenance(source, _hash_tbl),
            evidence_set_id=resolve_provenance(source, _es_tbl),
            kind=kind,
            derived_from=evidence_origins(evidence, inputs, of=field)))

    if daily is not None:
        src = f"sina:kline/{SYMBOL}"
        if coord is not None:
            # 🔴 raw_hash 指向冻结集登记的 content_sha256（整份 raw 的指纹），
            #    不对自己读到的这一截重算 —— 三个消费者读不同根数才能得到同一个
            #    raw_hash，risk 的 CROSS_CHECK 靠它判断是否真的共享同一份（P4）。
            #    raw 已由 freeze 落库，这里不重复落盘。
            raw_hash = coord.frozen_content_sha256(evidence_set_id, SYMBOL)
            # 批 E-I：Evidence 直接声明「我出自哪个冻结集」，比 raw_hash 更硬。
            es_id = evidence_set_id
        else:
            # 批 I：hash 基于原始响应文本，与 save_raw_snapshot 的 content_sha256 同口径。
            raw_hash = raw_text_sha256(daily.raw_text)
        # 填表 —— 此后 add() 无论拿到 `sina:kline/…` 还是 `derived:sina:kline/…`
        # 都解析得到同一份溯源（派生值确实出自这一份 raw）。
        _hash_tbl[src] = raw_hash
        if es_id is not None:
            _es_tbl[src] = es_id
        trade_date = daily.trade_date
        as_of, as_of_warning = as_of_for_trade_date(trade_date, retrieved_at=retrieved)
        if as_of_warning:
            warnings.append(as_of_warning)
        if store and coord is None:
            save_raw_snapshot(source=src, as_of=as_of.isoformat(),
                              retrieved_at=retrieved.isoformat(),
                              payload=daily.raw, raw_text=daily.raw_text)

        closes = [b.close for b in daily.bars]
        add("trade_date", trade_date, "交易日", src, kind="observed")

        if daily.last.close <= 0:
            missing.append(MissingItem(
                f"{SYMBOL_LABEL}收盘价 —— 数据源给出 {daily.last.close}，不是有效点位",
                "technical.close.invalid_value"))
        else:
            add("close", round(daily.last.close, 2), f"{SYMBOL_LABEL}收盘", src,
                kind="observed")

            short_ma = []
            for n in MA_WINDOWS:
                v = _sma(closes, n)
                if v is None:
                    short_ma.append(n)
                else:
                    add(f"ma{n}", v, f"MA{n}", f"derived:{src}", kind="derived")
            if short_ma:
                missing.append(MissingItem(
                    f"MA{'/'.join(map(str, short_ma))} —— 日线只有 {len(closes)} 根，"
                    f"不用更短的窗口凑一个看起来像的数",
                    "technical.ma.insufficient_bars"))

            ma20 = result.get("ma20")
            if ma20:
                add("price_vs_ma20_pct",
                    round((daily.last.close / ma20 - 1) * 100, 2),
                    "收盘相对 MA20(%)", f"derived:{src}", kind="derived")

            have = [n for n in MA_WINDOWS if f"ma{n}" in result]
            if len(have) >= 2:
                # 确定性观察，不是判断：把大小关系摆出来，Agent 不用自己比
                order = sorted(have, key=lambda n: result[f"ma{n}"], reverse=True)
                add("ma_order", ">".join(f"ma{n}" for n in order),
                    "均线大小关系", f"derived:{src}", kind="derived")

            m = _macd(closes)
            if m is None:
                missing.append(MissingItem(
                    f"MACD —— 日线不足 {MACD_MIN_BARS} 根，EMA 预热不够，"
                    f"算出来的值不可用",
                    "technical.macd.insufficient_bars"))
            else:
                add("macd_dif", m[0], "MACD DIF", f"derived:{src}", kind="derived")
                add("macd_dea", m[1], "MACD DEA", f"derived:{src}", kind="derived")
                add("macd_hist", m[2], "MACD 柱", f"derived:{src}", kind="derived")

            r = _rsi(closes)
            if r is None:
                missing.append(MissingItem(
                    f"RSI{RSI_WINDOW} —— 日线不足 {RSI_WINDOW + 1} 根",
                    "technical.rsi.insufficient_bars"))
            else:
                add("rsi14", r, f"RSI{RSI_WINDOW}", f"derived:{src}", kind="derived")

            if len(closes) >= HL_WINDOW:
                win = [b for b in daily.bars[-HL_WINDOW:]]
                # 🔴 外部评审 F5：窗口里**任意一根**坏 tick 都会污染这两个数，
                #    而唯一的守卫只查最新一根的收盘价。实测把第 91 根的 low
                #    改成 0.01（正数，不触发任何「≤0」检查），算出
                #    dist_to_low60_pct = 3118.99 万 %，verdict 仍然 PASS。
                sick = implausible_bars(win)
                if sick:
                    missing.append(MissingItem(
                        f"距 {HL_WINDOW} 日高低点 —— 窗口里有 {len(sick)} 处坏值："
                        + "；".join(sick[:3])
                        + "。这是数据源给了垃圾值，不是行情",
                        "technical.range.bad_bars"))
                    hi = lo = None
                else:
                    hi, lo = max(b.high for b in win), min(b.low for b in win)
            else:
                hi = lo = None

            if hi is not None and lo is not None:
                add("dist_to_high60_pct",
                    round((daily.last.close / hi - 1) * 100, 2),
                    f"距 {HL_WINDOW} 日高点(%)", f"derived:{src}", kind="derived")
                add("dist_to_low60_pct",
                    round((daily.last.close / lo - 1) * 100, 2),
                    f"距 {HL_WINDOW} 日低点(%)", f"derived:{src}", kind="derived")
            elif len(closes) < HL_WINDOW:
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

    # 🔴 批 E-II：产 FactBundle（只事实、无 stance）；stance 由 Technical Agent 事后追加。
    return FactBundle(
        task_id=task_id, agent=AGENT, status=status, verdict=level,
        result=result,
        data_completeness=round(len(result) / _EXPECTED_FIELDS, 2) if result else 0.0,
        evidence=evidence, warnings=warnings, missing=missing,
        elapsed_ms=int((time.monotonic() - t_start) * 1000))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="指数技术指标 → FactBundle JSON（批 E-II）")
    ap.add_argument("--break-source", action="append", default=[], metavar="NAME",
                    help="演练：人为中断 (daily)")
    ap.add_argument("--task-id")
    ap.add_argument("--run-id", default=None,
                    help="本次编排执行尝试的 run_id（RunContext.run_id），由 Supervisor "
                         "传下来落进 agent_verdicts.run_id。只 capture 不校验，缺省 None")
    ap.add_argument("--evidence-set-id", default=None,
                    help="给了就读这份冻结快照（编排出卡时传）；缺省自己联网抓（手工调试）")
    ap.add_argument("--no-store", action="store_true")
    ap.add_argument("--render", action="store_true")
    args = ap.parse_args(argv)

    store = not args.no_store
    if store:
        init_schema()
    fb = build_fact_bundle(break_source=set(args.break_source), store=store,
                           task_id=args.task_id or new_task_id(ADHOC_TASK_SEQ),
                           evidence_set_id=args.evidence_set_id)
    # 🔴 批 E-II：事实原件（FactBundle，不含 stance）直接落库；stance 由 agent 事后追加。
    ref = save_fact_bundle(fb, run_id=args.run_id) if store else None

    print(json.dumps(fb.to_dict(), ensure_ascii=False, indent=2))
    if ref is not None:
        print(f"verdict_ref={ref}", file=sys.stderr)
    if args.render:
        print("\n" + "─" * 60, file=sys.stderr)
        print(f"{AGENT}  {fb.status}/{fb.verdict}  耗时 {fb.elapsed_ms}ms"
              + (f"  verdict_ref={ref}" if ref else "  (未落库)"), file=sys.stderr)
        for e in fb.evidence:
            print(f"  {e.display_label:<20} = {e.value}", file=sys.stderr)
        for w in fb.warnings:
            print(f"  ⚠ {w}", file=sys.stderr)
        for m in fb.missing:
            print(f"  ⚠ 缺失 [{m.code}] {m}", file=sys.stderr)
    return {"PASS": 0, "WARNING": 2}.get(fb.verdict, 3)


if __name__ == "__main__":
    raise SystemExit(main())
