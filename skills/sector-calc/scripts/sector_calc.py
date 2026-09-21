#!/usr/bin/env python3
"""sector-calc —— 板块强度与资金方向的事实，输出合法 AgentVerdict。

分工（architecture.md §6）
--------------------------
本脚本只产出**事实**：哪些板块涨在前、资金流向哪里、上涨板块占多少。
「这算不算一条主线」「能不能追」由 Sector Agent 判断（铁律 4）。

🔴 全为 0 不是「所有板块都平盘」
--------------------------------
板块榜按涨跌幅排序，而**盘前它会被清零**（实测 2026-09-21 周一 08:48：
496 行全部 `pct=0.0`）。此时「第一名」是任意的一行 ——
照着它说「今日领涨板块是 X」完全是编造。

⚠️ 同一时刻，腾讯行情与新浪日线**仍然保留上一交易日的数据**。
   不同端点对「新一天还没开始」的表现是**相反**的：一个保留旧值，一个清零。
   ⇒ 不能靠「有没有数据」判断，必须逐个源自己判断。

🔴 交易日从哪来
---------------
板块榜**不带任何日期字段**（与涨跌家数同款）。交易日取自新浪日线 ——
与 market 同一个权威来源，因此两者的 `trade_date` 天然可以对上。
（risk 会核对这一点；对不上就说明有人拿的不是同一天。）

🔴 不做的事：板块持续性
------------------------
「这个板块连涨几天了」需要历史序列，Phase 2 不做历史回补。
这是**范围外**，不是数据缺失 —— 所以它**不进 `missing`**，
而是写进 Sector Agent 的契约：不许谈持续性。

    `missing[]` 是给「本该有却这次没有」的。范围外每次都在，
    混进去就把真正的缺失淹没了。

用法::

    python3 sector_calc.py
    python3 sector_calc.py --render
    python3 sector_calc.py --break-source industry
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
    AgentVerdict,
    Evidence,
    MissingItem,
    new_task_id,
    now_cn,
)
from _sources import (  # noqa: E402
    BoardResult,
    IndexDaily,
    SourceError,
    as_of_for_trade_date,
    fetch_boards,
    fetch_index_daily,
)
from _store import (  # noqa: E402
    init_schema,
    payload_sha256,
    save_raw_snapshot,
    save_verdict,
)

AGENT = "sector"
CALC_VERSION = "sector-calc/1"

#: 交易日的权威来源 —— 与 market 同一个，因此两者的 trade_date 可以对上。
_DATE_SYMBOL = "sh000001"

#: 榜单各取前几名。取太多上不了卡，也帮不了判断。
TOP_N = 5
BOTTOM_N = 3

#: 板块涨跌幅的量级上限。板块是一篮子股票的加权，不可能接近个股涨停幅度。
PCT_ABS_LIMIT = 15.0

_YI = 1e8
_EXPECTED_FIELDS = 9


def _brief(b) -> dict[str, Any]:
    return {"name": b.name, "pct": round(b.pct, 2),
            "inflow_yi": round(b.main_inflow / _YI, 2), "leader": b.leader}


class Collector:
    def __init__(self, break_source: set[str], store: bool):
        self._lock = threading.Lock()
        self.break_source = break_source
        self.store = store
        self.boards: dict[str, BoardResult] = {}
        self.daily: IndexDaily | None = None
        self.missing: list[MissingItem] = []
        self.warnings: list[str] = []
        self.raw: list[tuple[str, Any]] = []
        self.hashes: dict[str, str] = {}

    def _note(self, *, missing: MissingItem | None = None,
              warning: str | None = None) -> None:
        with self._lock:
            if missing:
                self.missing.append(missing)
            if warning:
                self.warnings.append(warning)

    def _keep_raw(self, source: str, payload: Any) -> None:
        with self._lock:
            self.raw.append((source, payload))
            self.hashes[source] = payload_sha256(payload)

    def collect_board(self, kind: str, label: str) -> None:
        if kind in self.break_source:
            self._note(missing=MissingItem(
                f"{label} —— 数据源被人为中断（--break-source {kind}）",
                "sector.board.source_broken"))
            return
        try:
            r = fetch_boards(kind)
        except (SourceError, ValueError) as e:
            self._note(missing=MissingItem(f"{label} —— 数据源不可用: {e}",
                                           "sector.board.unavailable"))
            return

        # 🔴 全为 0 = 这一天还没开始，不是「所有板块都平盘」
        if r.nonzero_count == 0:
            self._note(missing=MissingItem(
                f"{label} —— 全部 {len(r.boards)} 个板块涨跌幅均为 0，"
                f"这是盘前/数据源未刷新，**不是「所有板块都平盘」**；"
                f"此时榜单排序无意义，不能据此说谁领涨",
                "sector.board.pre_session"))
            return

        bad = [b.name for b in r.boards if abs(b.pct) > PCT_ABS_LIMIT]
        if bad:
            self._note(missing=MissingItem(
                f"{label} —— {len(bad)} 个板块涨跌幅超出 ±{PCT_ABS_LIMIT}%"
                f"（例如 {bad[:3]}），是数据源给了垃圾值而不是行情",
                "sector.board.out_of_range"))
            return

        with self._lock:
            self.boards[kind] = r
        self._keep_raw(f"em:clist/{kind}", r.raw)

    def collect_date(self) -> None:
        if "date" in self.break_source:
            self._note(missing=MissingItem(
                "交易日 —— 数据源被人为中断（--break-source date）",
                "sector.trade_date.source_broken"))
            return
        try:
            self.daily = fetch_index_daily(_DATE_SYMBOL, bars=2)
        except (SourceError, ValueError) as e:
            self._note(missing=MissingItem(
                f"交易日 —— 无法从日线确定（{e}），板块数据将无从定位到哪一天",
                "sector.trade_date.unavailable"))
            return
        self._keep_raw(f"sina:kline/{_DATE_SYMBOL}", self.daily.raw)


def build_verdict(*, break_source: set[str], store: bool, task_id: str) -> AgentVerdict:
    t_start = time.monotonic()
    c = Collector(break_source, store)

    jobs = [lambda: c.collect_board("industry", "行业板块榜"),
            lambda: c.collect_board("concept", "概念板块榜"),
            c.collect_date]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        for fut in [pool.submit(j) for j in jobs]:
            fut.result()

    result: dict[str, Any] = {}
    evidence: list[Evidence] = []
    retrieved = now_cn()
    as_of: datetime | None = None

    def raw_hash_for(source: str) -> str | None:
        return None if source.startswith("derived:") else c.hashes.get(source)

    def add(field: str, value: Any, label: str, source: str) -> None:
        result[field] = value
        evidence.append(Evidence(
            field=field, source=source, value=value,
            as_of=as_of, retrieved_at=retrieved,
            calc_version=CALC_VERSION, label=label,
            raw_hash=raw_hash_for(source)))

    if c.daily is None:
        c.missing.append(MissingItem(
            "全部板块指标 —— 没有可信的交易日，无法确定这批数据描述的是哪一天",
            "sector.trade_date.undetermined"))
    else:
        trade_date = c.daily.trade_date
        as_of, as_of_warning = as_of_for_trade_date(trade_date, retrieved_at=retrieved)
        if as_of_warning:
            c.warnings.append(as_of_warning)
        add("trade_date", trade_date, "交易日", f"sina:kline/{_DATE_SYMBOL}")

        if c.boards:
            c.warnings.append(
                "板块榜不返回交易日字段，其 as_of 是按指数日线的交易日推断的")

        counts: dict[str, int] = {}
        for kind, label, tag in (("industry", "行业", "industry"),
                                 ("concept", "概念", "concept")):
            r = c.boards.get(kind)
            if r is None:
                continue
            src = f"em:clist/{kind}"
            ranked = sorted(r.boards, key=lambda b: b.pct, reverse=True)
            counts[tag] = len(ranked)
            add(f"{tag}_top", [_brief(b) for b in ranked[:TOP_N]],
                f"{label}涨幅前 {TOP_N}", src)
            up = sum(1 for b in ranked if b.pct > 0)
            add(f"{tag}_advance_ratio", round(up / len(ranked), 4),
                f"上涨{label}板块占比", f"derived:{src}")
            if tag == "industry":
                add("industry_bottom", [_brief(b) for b in ranked[-BOTTOM_N:]],
                    f"{label}跌幅前 {BOTTOM_N}", src)
                by_money = sorted(r.boards, key=lambda b: b.main_inflow, reverse=True)
                add("main_inflow_top", [_brief(b) for b in by_money[:TOP_N]],
                    "主力净流入前 5（行业）", src)
                add("main_inflow_total_yi",
                    round(sum(b.main_inflow for b in r.boards) / _YI, 2),
                    "行业主力净流入合计(亿元)", f"derived:{src}")
            if any(b.leader is None for b in ranked[:TOP_N]):
                c.warnings.append(f"{label}榜前 {TOP_N} 中有板块未返回领涨股")

        if counts:
            add("board_counts", counts, "各榜板块数", "em:clist")
        else:
            c.missing.append(MissingItem(
                "板块强度 —— 行业榜与概念榜都不可用", "sector.board.none"))

    if store and as_of is not None:
        for source, payload in c.raw:
            save_raw_snapshot(source=source, as_of=as_of.isoformat(),
                              retrieved_at=retrieved.isoformat(), payload=payload)

    core = {"trade_date", "industry_top", "industry_advance_ratio"}
    if not c.missing:
        status, level = "completed", "PASS"
    elif core <= set(result):
        status, level = "partial", "WARNING"
    else:
        status, level = "partial", "UNKNOWN"

    return AgentVerdict(
        task_id=task_id, agent=AGENT, status=status, verdict=level,
        result=result,
        confidence=round(len(result) / _EXPECTED_FIELDS, 2) if result else 0.0,
        evidence=evidence, warnings=c.warnings, missing=c.missing,
        elapsed_ms=int((time.monotonic() - t_start) * 1000))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="板块强度与资金方向 → AgentVerdict JSON")
    ap.add_argument("--break-source", action="append", default=[], metavar="NAME",
                    help="演练：人为中断 (industry|concept|date)")
    ap.add_argument("--task-id")
    ap.add_argument("--no-store", action="store_true")
    ap.add_argument("--render", action="store_true")
    args = ap.parse_args(argv)

    store = not args.no_store
    if store:
        init_schema()
    v = build_verdict(break_source=set(args.break_source), store=store,
                      task_id=args.task_id or new_task_id(1))
    ref = save_verdict(v) if store else None

    print(json.dumps(v.to_dict(), ensure_ascii=False, indent=2))
    if ref is not None:
        print(f"verdict_ref={ref}", file=sys.stderr)
    if args.render:
        print("\n" + "─" * 60, file=sys.stderr)
        print(f"{AGENT}  {v.status}/{v.verdict}  耗时 {v.elapsed_ms}ms"
              + (f"  verdict_ref={ref}" if ref else "  (未落库)"), file=sys.stderr)
        for e in v.evidence:
            val = e.value
            if isinstance(val, list):
                val = "; ".join(f"{x['name']}({x['pct']}%)" for x in val[:3]) + " …"
            print(f"  {e.display_label:<22} = {val}", file=sys.stderr)
        for w in v.warnings:
            print(f"  ⚠ {w}", file=sys.stderr)
        for m in v.missing:
            print(f"  ⚠ 缺失 [{m.code}] {m}", file=sys.stderr)
    return {"PASS": 0, "WARNING": 2}.get(v.verdict, 3)


if __name__ == "__main__":
    raise SystemExit(main())
