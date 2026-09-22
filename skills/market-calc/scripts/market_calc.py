#!/usr/bin/env python3
"""market-calc —— A 股市场状态事实计算，输出一份合法的 AgentVerdict。

分工（architecture.md §6 硬约束 S-1 / S-2）
--------------------------------------------
本脚本**只产出事实**，不做任何判断性归类。

  归这里（Python）              归 Market Agent（LLM）
  ─────────────────────────    ────────────────────────────
  指数点位、涨跌幅               这算不算「放量上涨」
  两市成交额                    2.07 万亿在当前位置是高是低
  量能比（今日量 / 20 日均量）     缩量反弹要不要提示风险
  涨跌家数、上涨占比              宽度与指数背离说明什么

🔴 事实的归属（裁定 15）
------------------------
**涨跌家数归 market，不归 emotion。** 上游文档把它同时派给了两个 agent，
但它的端点是**不带日期的实时快照** —— 两个 agent 并行各调一次，
同一张 Card 上就会出现同一个字段两个值，而两个都带着推断出来的 `as_of`。

    同一个事实只能有一个生产方。

🔴 三个源，各自只干它能自证的那件事
------------------------------------
============  ===========================  ==========================
源            提供                          它自己声明的时间
============  ===========================  ==========================
新浪日线       交易日 / 点位 / 涨跌幅 / 量能    每行自带 ``day``  ← **权威**
腾讯行情       成交额                        14 位时间戳
东财 ulist    涨跌家数                      **没有**（只能推断）
============  ===========================  ==========================

五条守卫，每条都对应一次**真见过**的失败形状（见各 `_sources` 模块的实测记录）：

1. 点位 ≤ 0 或 |涨跌幅| > 20% ⇒ 进 `missing`，**不填 0**
   （东财延迟源实测返回过 `最新=0.0`、`涨跌幅=-29971017728.0`，
    `rc=0`、字段齐全、类型正确 —— 量级校验是唯一能拦住它的东西）
2. 腾讯时间戳的日期 ≠ 新浪日线末行 ⇒ 进 `missing`，不挑一个用
3. 日线不足 21 根 ⇒ 量能进 `missing`，**不拿 15 天凑一个均值**
4. 腾讯量 ×100 与新浪量偏离 > 1% ⇒ 进 `missing`（单位口径变了）
5. 涨跌家数端点无日期 ⇒ `warning`（as_of 是推断的）

用法::

    python3 market_calc.py                      # 最近一个交易日
    python3 market_calc.py --date 20260918      # 严格模式：对不上进 missing[]
    python3 market_calc.py --break-source sina_sh   # 演练 UNKNOWN ≠ PASS
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import pathlib
import sys
import threading
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
    INDEX_PCT_LIMIT,
    BreadthResult,
    IndexDaily,
    IndexQuote,
    SourceError,
    as_of_for_trade_date,
    fetch_breadth,
    fetch_index_daily,
    fetch_index_quote,
)
from _store import (  # noqa: E402
    init_schema,
    payload_sha256,
    save_raw_snapshot,
    save_verdict,
)

AGENT = "market"
CALC_VERSION = "market-calc/1"

#: 两个市场：上证综指 + 深证**综指**（不是成指 —— 综指覆盖整个深市，
#: 与涨跌家数、成交额的口径一致）。
MARKETS = {"sh": ("sh000001", "上证指数"), "sz": ("sz399106", "深证综指")}

#: 取多少根日线。20 日均量需要 21 根（20 根基线 + 今日），
#: 多取几根抵消节假日边界 —— 但不足时不凑（守卫 3）。
BAR_COUNT = 25
MA_WINDOW = 20

#: 守卫：涨跌幅围栏。**判据在 `_sources/sanity.py`，这里只取别名。**
#: 原来两个 skill 各写一个 `PCT_ABS_LIMIT`，值还不一样（20 / 15）——
#: 看起来像抄漏了。搬到一处并分开命名，让「不同」变成明示的决定。
PCT_ABS_LIMIT = INDEX_PCT_LIMIT

#: 守卫 4：两源成交量的允许偏离。
#: 🔴 不用「完全相等」：盘中两个源的刷新时刻不同，必然有微小差异
#:    （实测收盘后 sz 两源差 45 股 / 7.5e-10）。
#:    要求逐位相等会造出一个盘中永远报红的检查，而永远报警的检查会被忽略。
VOLUME_XCHECK_TOL = 0.01

_YI = 1e8          # 亿
_WAN_TO_YI = 1e4   # 万元 → 亿元

#: 数据齐备时应当产出的字段数，用于 `confidence`。
#: 🔴 **故意不做 min(…, 1.0) 截断**：截断会把「加了字段忘了改这个数」
#:    变成一个悄悄偏小的置信度；不截断则契约层当场拒绝构造（confidence > 1）。
#:    宁可当场炸，也不要一个悄悄不准的数 —— 由 test_market_calc 钉死。
_EXPECTED_FIELDS = 15


class Collector:
    """采集 + 记账。把「取到了什么 / 哪里失败了」分别攒起来。"""

    def __init__(self, date: str | None, break_source: set[str], store: bool):
        self._lock = threading.Lock()
        self.date = date
        self.break_source = break_source
        self.store = store
        self.daily: dict[str, IndexDaily] = {}
        self.quotes: dict[str, IndexQuote] = {}
        self.breadth: BreadthResult | None = None
        self.missing: list[str] = []
        self.warnings: list[str] = []
        self.raw: list[tuple[str, Any]] = []
        #: source → 原始响应的哈希。Evidence.raw_hash 用它指回 raw 层。
        self.hashes: dict[str, str] = {}

    def _note(self, *, missing: MissingItem | None = None,
              warning: str | None = None) -> None:
        with self._lock:
            if missing:
                self.missing.append(missing)
            if warning:
                self.warnings.append(warning)

    def _keep_raw(self, source: str, payload: Any, as_of: datetime) -> None:
        """记一份原始响应。**`as_of` 必须是这个源自己的时刻。**

        🔴 外部评审 F4：原来这里不收 `as_of`，落库时统一用一个全局变量
        （日线的交易日）。于是同一天跑两次，两份**内容不同**的实时快照
        共用同一个 `(source, as_of)` 键 —— 而这个组合正是
        `ix_raw_source_asof` 索引存在的理由。

        更糟的是 `tencent:quote`：它 payload 里嵌着自己的时间戳
        （`20260921144345`），而 raw 层写的是上一个交易日 15:00。
        那个时间戳在 Evidence 层已经算出来、也已经拿去做交叉校验了，
        到落 raw 这一步被弃之不用。

        ⚠️ 修 `af91a0d` 时只改了 Evidence 构造那一层，
        没有触及几十行外这条并行路径 —— 两边的测试各自全绿，
        **bug 正好落在两者之间从未被同时检查过的缝隙里**。
        """
        with self._lock:
            self.raw.append((source, payload, as_of))
            self.hashes[source] = payload_sha256(payload)

    # -- 采集 --------------------------------------------------------------

    def collect_daily(self, key: str) -> None:
        symbol, label = MARKETS[key]
        if f"sina_{key}" in self.break_source:
            self._note(missing=MissingItem(
                f"{label}日线 —— 数据源被人为中断（--break-source sina_{key}）",
                "market.index_daily.source_broken"))
            return
        try:
            d = fetch_index_daily(symbol, bars=BAR_COUNT)
        except (SourceError, ValueError) as e:
            self._note(missing=MissingItem(f"{label}日线 —— 数据源不可用: {e}",
                                          "market.index_daily.unavailable"))
            return

        if self.date is not None and d.trade_date != self.date:
            # 显式指定了日期 = 一个契约。对不上就是没拿到要的东西。
            self._note(missing=MissingItem(
                f"{label}日线 —— 请求 {self.date} 但数据源最新一根是 {d.trade_date}",
                "market.index_daily.date_mismatch"))
            return

        with self._lock:
            self.daily[key] = d
        self._keep_raw(f"sina:kline/{symbol}", d.raw, d.server_as_of or now_cn())

    def collect_quotes(self) -> None:
        if "tencent" in self.break_source:
            self._note(missing=MissingItem("成交额 —— 数据源被人为中断（--break-source tencent）",
                                          "market.turnover.source_broken"))
            return
        try:
            q = fetch_index_quote([sym for sym, _ in MARKETS.values()])
        except (SourceError, ValueError) as e:
            self._note(missing=MissingItem(f"成交额 —— 数据源不可用: {e}",
                                          "market.turnover.unavailable"))
            return
        with self._lock:
            self.quotes = q
        # 腾讯行情**自己带时间戳** —— 这是本源存在的主要理由，别再丢掉它
        self._keep_raw("tencent:quote", {k: v.raw for k, v in q.items()},
                       max(v.server_as_of or now_cn() for v in q.values()))

    def collect_breadth(self) -> None:
        if "breadth" in self.break_source:
            self._note(missing=MissingItem("涨跌家数 —— 数据源被人为中断（--break-source breadth）",
                                          "market.breadth.source_broken"))
            return
        try:
            b = fetch_breadth()
        except SourceError as e:
            self._note(missing=MissingItem(f"涨跌家数 —— 数据源不可用: {e}",
                                          "market.breadth.unavailable"))
            return
        with self._lock:
            self.breadth = b
        # `server_as_of is None` ⇒ 这个端点不带日期，它说的就是「此刻」。
        # 套上日线的交易日，就是把实时数写成上一个交易日的事实。
        self._keep_raw("em:push2delay/ulist.np", b.raw, b.server_as_of or now_cn())
        # ⚠️ 不在这里发 as_of 警告：它依赖交易日，而交易日要等日线回来才知道。
        #    并行采集下在这里读是竞态，统一放到全部完成后做。


def _pct(daily: IndexDaily) -> float | None:
    """涨跌幅（%）。同源相除，不跨源 —— 需要至少两根。"""
    if len(daily.bars) < 2:
        return None
    prev = daily.bars[-2].close
    if prev <= 0:
        return None
    return round((daily.last.close / prev - 1) * 100, 4)


def _ma_volume(daily: IndexDaily) -> float | None:
    """前 `MA_WINDOW` 根（**不含今日**）的平均成交量，单位股。

    不含今日是有意的：把今天算进自己的基线，会让放量被自己稀释。
    根数不够返回 None —— 由上层记 missing，**不用更短的窗口凑**。
    """
    if len(daily.bars) < MA_WINDOW + 1:
        return None
    window = daily.bars[-(MA_WINDOW + 1):-1]
    return sum(b.volume for b in window) / len(window)


def build_verdict(
    *,
    date: str | None,
    break_source: set[str],
    store: bool,
    task_id: str,
) -> AgentVerdict:
    t_start = time.monotonic()
    c = Collector(date, break_source, store)

    jobs = [lambda: c.collect_daily("sh"), lambda: c.collect_daily("sz"),
            c.collect_quotes, c.collect_breadth]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        for fut in [pool.submit(j) for j in jobs]:
            fut.result()

    # ── 交易日：只认日线。两市报的日期不一致就不能当同一天的事实汇总 ──
    reported = {k: d.trade_date for k, d in c.daily.items()}
    trade_date: str | None = None
    if len(set(reported.values())) > 1:
        c.missing.append(MissingItem(
            f"全部市场指标 —— 两市日线报告的交易日不一致 {sorted(set(reported.values()))}，"
            "不能当作同一天的事实汇总", "market.index_daily.date_inconsistent"))
        c.daily.clear()
    elif reported:
        trade_date = next(iter(reported.values()))
    else:
        c.missing.append(MissingItem("全部市场指标 —— 没有任何日线可用，交易日无从确定",
                                     "market.trade_date.undetermined"))

    result: dict[str, Any] = {}
    evidence: list[Evidence] = []
    retrieved = now_cn()
    as_of: datetime | None = None

    def _raw_hash_for(source: str) -> str | None:
        """这条证据出自哪份原始响应。

        派生字段（`derived:` 开头）没有单一来源，返回 None ——
        **不硬凑一个哈希**：凑出来的溯源比没有溯源更糟，它会让人以为查得到。
        """
        if source.startswith("derived:"):
            return None
        if source in c.hashes:
            return c.hashes[source]
        cand = [k for k in c.hashes if source.startswith(k)]
        return c.hashes[max(cand, key=len)] if cand else None

    def add(field: str, value: Any, label: str, source: str) -> None:
        result[field] = value
        evidence.append(Evidence(
            field=field, source=source, value=value,
            as_of=as_of, retrieved_at=retrieved,
            calc_version=CALC_VERSION, label=label,
            raw_hash=_raw_hash_for(source),
        ))


    # 🔴 无日期端点的 as_of 不能沿用日线的收盘时刻。
    #
    #    实测（BIGA-20260921-017，周一 12:41 午休）：涨跌家数取回的是
    #    **今天此刻**的 4385/1100/145，而卡面上写着 `as_of 09-18 15:00`
    #    —— 周五收盘。一个今天的数，挂着上周五的时间戳。
    #
    #    代码其实知道（它发了一条 warning），但 warning 当时不上卡，
    #    于是卡面看起来板上钉钉。
    #
    #    根子是 as_of 只算了一次就被所有证据共用。而这个端点连日期字段都没有
    #    ⇒ 我们能诚实声明的只有「取回它的时刻」。
    #
    #    ⚠️ 注意这里的不对称：带日期的源（腾讯行情）会被核对、不一致就报
    #    date_mismatch；**唯独没有日期的那个源反而被默认对齐** ——
    #    而它恰恰是最可能对不上的。
    def add_live(field: str, value: Any, label: str, source: str) -> None:
        """实时快照类证据：as_of = 取回时刻。"""
        result[field] = value
        evidence.append(Evidence(
            field=field, source=source, value=value,
            as_of=retrieved, retrieved_at=retrieved,
            calc_version=CALC_VERSION, label=label,
            raw_hash=_raw_hash_for(source)))

    if trade_date:
        as_of, as_of_warning = as_of_for_trade_date(trade_date, retrieved_at=retrieved)
        if as_of_warning:
            c.warnings.append(as_of_warning)
        add("trade_date", trade_date, "交易日", "sina:kline")

        # ── 守卫 2：腾讯与新浪必须说的是同一天 ──────────────────────
        quotes_usable = bool(c.quotes)
        if c.quotes:
            bad = {sym: q.trade_date for sym, q in c.quotes.items()
                   if q.trade_date != trade_date}
            if bad:
                c.missing.append(MissingItem(
                    f"成交额 —— 腾讯行情的日期 {sorted(set(bad.values()))} "
                    f"与日线的 {trade_date} 不一致，两个独立源说的不是同一天",
                    "market.turnover.date_mismatch"))
                quotes_usable = False

        turnover_total = 0.0
        for key, (symbol, label) in MARKETS.items():
            d = c.daily.get(key)
            q = c.quotes.get(symbol) if quotes_usable else None

            # ── 守卫 1：点位与涨跌幅的量级校验（日线派生）─────────────
            if d is not None:
                pct = _pct(d)
                if d.last.close <= 0:
                    c.missing.append(MissingItem(
                        f"{label}点位 —— 数据源给出 {d.last.close}，不是有效点位",
                        "market.index_quote.invalid_value"))
                elif pct is None:
                    c.missing.append(MissingItem(
                        f"{label}涨跌幅 —— 日线不足两根，无法与前收比较",
                        "market.index_pct.insufficient_bars"))
                elif abs(pct) > PCT_ABS_LIMIT:
                    c.missing.append(MissingItem(
                        f"{label}点位与涨跌幅 —— 算出的涨跌幅 {pct}% 超出 "
                        f"±{PCT_ABS_LIMIT}%，是数据源给了垃圾值而不是行情",
                        "market.index_quote.out_of_range"))
                else:
                    add(f"{key}_close", round(d.last.close, 2), f"{label}点位",
                        f"sina:kline/{symbol}")
                    add(f"{key}_pct", pct, f"{label}涨跌幅(%)",
                        f"derived:sina:kline/{symbol}")

            # ── 守卫 4：两源成交量的单位/口径校验 ─────────────────────
            # 🔴 成交额来自腾讯，**不因新浪日线缺失而连坐**。
            #    早先的写法把整个循环体挡在 `if d is None: continue` 后面，
            #    结果日线一挂，turnover 既不产出也不进 missing —— **字段凭空消失**。
            #    一个悄悄消失的字段正是 R-3 要防的形状：它在 Card 上什么也不留下。
            if q is not None and d is not None and d.last.volume > 0:
                rel = abs(q.volume_hand * 100 - d.last.volume) / d.last.volume
                if rel > VOLUME_XCHECK_TOL:
                    c.missing.append(MissingItem(
                        f"{label}成交额 —— 腾讯成交量×100 与新浪日线偏离 {rel:.2%}"
                        f"（>{VOLUME_XCHECK_TOL:.0%}），两源口径或单位已不一致",
                        "market.turnover.unit_mismatch"))
                    q = None
            elif q is not None and d is None:
                c.warnings.append(
                    f"{label}成交额 —— 日线缺失，无法做双源交叉校验，该值只有单源支撑")

            if q is not None:
                yi = round(q.amount_wan / _WAN_TO_YI, 2)
                add(f"turnover_{key}", yi, f"{label}成交额(亿元)", f"tencent:quote/{symbol}")
                turnover_total += yi

        if {f"turnover_{k}" for k in MARKETS} <= set(result):
            add("turnover_total", round(turnover_total, 2), "两市成交额(亿元)",
                "derived:tencent:quote")
        else:
            c.missing.append(MissingItem(
                "两市成交额 —— 需要两个市场的成交额同时可用，缺一不能合计",
                "market.turnover_total.incomplete"))

        # ── 守卫 3：量能需要 21 根，不足不凑 ─────────────────────────
        mas = {k: _ma_volume(d) for k, d in c.daily.items()}
        if c.daily and all(v is not None for v in mas.values()):
            today_vol = sum(d.last.volume for d in c.daily.values())
            base_vol = sum(mas.values())
            add("volume_total", round(today_vol / _YI, 2), "两市成交量(亿股)", "sina:kline")
            add("volume_ma20", round(base_vol / _YI, 2),
                f"两市{MA_WINDOW}日均量(亿股，不含今日)", "derived:sina:kline")
            add("volume_ratio", round(today_vol / base_vol, 4) if base_vol else None,
                f"量能比(今日/{MA_WINDOW}日均)", "derived:sina:kline")
        else:
            short = [MARKETS[k][1] for k, v in mas.items() if v is None]
            c.missing.append(MissingItem(
                f"量能 —— {'、'.join(short) or '两市'}日线不足 {MA_WINDOW + 1} 根，"
                f"不用更短的窗口凑一个均值", "market.volume.insufficient_bars"))

        # ── 守卫 5：涨跌家数没有自己的日期 ───────────────────────────
        if c.breadth and (c.breadth.advance + c.breadth.decline
                          + c.breadth.flat) == 0:
            # 🔴 涨跌平三项全为 0 在任何真实交易时段都不可能 ——
            #    这是新一天开盘前数据源被清零。更糟的是它会被贴上**日线的交易日**，
            #    于是 Card 上出现「9-18 上涨家数 0」，而那天真实是 4277。
            c.missing.append(MissingItem(
                f"涨跌家数 —— 三项合计为 0，任何真实交易时段都不会这样："
                f"这是新一天尚未开始、数据源已清零，"
                f"而它会被误贴到 {trade_date} 上",
                "market.breadth.not_yet_formed"))
            c.breadth = None

        if c.breadth:
            b = c.breadth
            c.warnings.append(
                "涨跌家数接口不返回交易日字段，其 as_of 是按日线的交易日推断的")
            add_live("advance_count", b.advance, "上涨家数", "em:push2delay/ulist.np")
            add_live("decline_count", b.decline, "下跌家数", "em:push2delay/ulist.np")
            add_live("flat_count", b.flat, "平盘家数", "em:push2delay/ulist.np")
            # 分母不可能为 0 —— 上面的 not_yet_formed 守卫已经把那种情况挡掉了。
            # 🔴 原来这里有个 `else: 分母为零` 分支，加了守卫之后它**永远走不到** ——
            #    恒假分支就是 L-7，留着只会让人以为还有一条路。
            total = b.advance + b.decline + b.flat
            add_live("advance_ratio", round(b.advance / total, 4), "上涨家数占比",
                "derived:em:push2delay/ulist.np")

    if store:
        for source, payload, src_as_of in c.raw:
            save_raw_snapshot(source=source, as_of=src_as_of.isoformat(),
                              retrieved_at=retrieved.isoformat(), payload=payload)

    # verdict 表达的是**数据完整度**，不是市场判断。
    # 「这算不算放量上涨」由 Market Agent 依据 AGENTS.md 里的口径来说。
    core = {"trade_date", "sh_close", "turnover_total"}
    if not c.missing:
        status, level = "completed", "PASS"
    elif core <= set(result):
        status, level = "partial", "WARNING"
    else:
        status, level = "partial", "UNKNOWN"

    return AgentVerdict(
        task_id=task_id,
        agent=AGENT,
        status=status,
        verdict=level,
        result=result,
        data_completeness=round(len(result) / _EXPECTED_FIELDS, 2) if result else 0.0,
        evidence=evidence,
        warnings=c.warnings,
        missing=c.missing,
        elapsed_ms=int((time.monotonic() - t_start) * 1000),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A 股市场状态事实计算 → AgentVerdict JSON")
    ap.add_argument("--date", help="交易日 YYYYMMDD。给了就是严格模式")
    ap.add_argument("--task-id", help="BIGA-YYYYMMDD-NNN，缺省自动生成")
    ap.add_argument("--break-source", action="append", default=[], metavar="NAME",
                    help="演练用：人为中断某个数据源 (sina_sh|sina_sz|tencent|breadth)")
    ap.add_argument("--no-store", action="store_true", help="不写 raw_market_snapshot")
    ap.add_argument("--render", action="store_true", help="附带人类可读摘要")
    args = ap.parse_args(argv)

    store = not args.no_store
    if store:
        init_schema()

    v = build_verdict(date=args.date, break_source=set(args.break_source),
                      store=store, task_id=args.task_id or new_task_id(ADHOC_TASK_SEQ))
    # 🔴 判定原件直接落库，返回一个 id 供 agent 引用。
    #    在此之前契约要求 agent「把这份 JSON 原样带上」—— 实测它做不到原样：
    #    15 条 evidence 的 retrieved_at 转述后一条不剩。
    #    让数据不经过 LLM，是唯一可靠的修法。
    ref = save_verdict(v) if store else None

    print(json.dumps(v.to_dict(), ensure_ascii=False, indent=2))
    if ref is not None:
        print(f"verdict_ref={ref}", file=sys.stderr)

    if args.render:
        print("\n" + "─" * 60, file=sys.stderr)
        print(f"{AGENT}  {v.status}/{v.verdict}  耗时 {v.elapsed_ms}ms"
              + (f"  verdict_ref={ref}" if ref is not None else "  (未落库)"),
              file=sys.stderr)
        for e in v.evidence:
            print(f"  {e.display_label:<22} = {e.value}"
                  f"   as_of {e.as_of:%Y-%m-%d %H:%M}", file=sys.stderr)
        if v.warnings:
            print("  警告:", file=sys.stderr)
            for w in v.warnings:
                print(f"    · {w}", file=sys.stderr)
        if v.missing:
            print(f"  ⚠ 缺失项（{len(v.missing)}）:", file=sys.stderr)
            for m in v.missing:
                print(f"    · {m}", file=sys.stderr)

    # 退出码：0=完整，2=有缺失但核心可用，3=核心缺失
    return {"PASS": 0, "WARNING": 2}.get(v.verdict, 3)


if __name__ == "__main__":
    raise SystemExit(main())
