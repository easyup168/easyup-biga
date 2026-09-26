#!/usr/bin/env python3
"""sector-calc —— 板块强度与资金方向的事实，输出合法 FactBundle。

🔴 批 E-II：从产合体 `AgentVerdict` 迁到产 `FactBundle`（只事实、无 stance），形状与
E-I 迁 emotion 完全一致。stance 由 Sector Agent 事后 `amend_verdict.py --stance` 追加一个
`AgentAssessment`（不重打事实）。消费方零改动（`load_verdict` 多态）。

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
    evidence_origins,
    raw_origins,
    resolve_provenance,
    ADHOC_TASK_SEQ,
    Evidence,
    FactBundle,
    MissingItem,
    new_task_id,
    now_cn,
)
from _data import (  # noqa: E402
    DecisionDataClient,
    BOARD_PCT_LIMIT,
    BoardResult,
    IndexDaily,
    SourceError,
    as_of_for_trade_date,
    fetch_boards,
    fetch_index_daily,
)
from _store import (  # noqa: E402
    init_schema,
    raw_text_sha256,
    save_fact_bundle,
    save_raw_snapshot,
)
from _snapshot import SnapshotCoordinator  # noqa: E402

AGENT = "sector"
CALC_VERSION = "sector-calc/1"

#: 交易日的权威来源 —— 与 market 同一个，因此两者的 trade_date 可以对上。
_DATE_SYMBOL = "sh000001"

#: 榜单各取前几名。取太多上不了卡，也帮不了判断。
TOP_N = 5
BOTTOM_N = 3

#: 板块涨跌幅的量级上限。板块是一篮子股票的加权，不可能接近个股涨停幅度。
#: 守卫：涨跌幅围栏。**判据在 `_sources/sanity.py`，这里只取别名。**
#: 原来两个 skill 各写一个 `PCT_ABS_LIMIT`，值还不一样（20 / 15）——
#: 看起来像抄漏了。搬到一处并分开命名，让「不同」变成明示的决定。
PCT_ABS_LIMIT = BOARD_PCT_LIMIT

_YI = 1e8
_EXPECTED_FIELDS = 9


def _brief(b) -> dict[str, Any]:
    return {"name": b.name, "pct": round(b.pct, 2),
            "inflow_yi": (round(b.main_inflow / _YI, 2)
                          if b.main_inflow is not None else None),
            "leader": b.leader}


class Collector:
    def __init__(self, break_source: set[str], store: bool,
                 evidence_set_id: str | None = None):
        self._lock = threading.Lock()
        self.break_source = break_source
        self.store = store
        # 给了就读冻结快照的日线（交易日），没给自己抓（手工调试路径）。
        self.evidence_set_id = evidence_set_id
        self._coord = SnapshotCoordinator() if evidence_set_id is not None else None
        self._data = DecisionDataClient() if evidence_set_id is not None else None
        self.boards: dict[str, BoardResult] = {}
        self.daily: IndexDaily | None = None
        self.missing: list[MissingItem] = []
        self.warnings: list[str] = []
        #: (source, 解析后 payload, 该源自己的 as_of, 原始响应文本)。批 I：原文一并累积。
        self.raw: list[tuple[str, Any, datetime, str]] = []
        self.hashes: dict[str, str] = {}
        #: source → 冻结集 id（只有读冻结的 source 有）。Evidence.evidence_set_id 用它（批 E-I）。
        self.es_ids: dict[str, str] = {}

    def _note(self, *, missing: MissingItem | None = None,
              warning: str | None = None) -> None:
        with self._lock:
            if missing:
                self.missing.append(missing)
            if warning:
                self.warnings.append(warning)

    def _keep_raw(self, source: str, payload: Any, as_of: datetime,
                  raw_text: str) -> None:
        """记一份原始响应。**`as_of` 必须是这个源自己的时刻**（F4，同 market）。"""
        with self._lock:
            self.raw.append((source, payload, as_of, raw_text))
            # 批 I：hash 基于原始响应文本，与 save_raw_snapshot 的 content_sha256 同口径。
            self.hashes[source] = raw_text_sha256(raw_text)

    def collect_board(self, kind: str, label: str) -> None:
        if kind in self.break_source:
            self._note(missing=MissingItem(
                f"{label} —— 数据源被人为中断（--break-source {kind}）",
                "sector.board.source_broken"))
            return
        try:
            if self._data is not None:
                r, frozen_hash = self._data.read_boards(self.evidence_set_id, kind)
            else:
                r = fetch_boards(kind)
                frozen_hash = None
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
        source = f"em:clist/{kind}"
        if self._data is not None:
            with self._lock:
                if frozen_hash:
                    self.hashes[source] = frozen_hash
                self.es_ids[source] = self.evidence_set_id
        else:
            # `server_as_of is None` ⇒ 板块榜不带日期，它说的就是「此刻」
            self._keep_raw(source, r.raw, r.server_as_of or now_cn(), r.raw_text)

    def collect_date(self) -> None:
        if "date" in self.break_source:
            self._note(missing=MissingItem(
                "交易日 —— 数据源被人为中断（--break-source date）",
                "sector.trade_date.source_broken"))
            return
        if self._coord is not None:
            # 🔴 fail-closed：读冻结失败直接上抛 SnapshotReadError，不静默退回
            #    自己抓（P5）。交易日与 market/technical 取自**同一份**冻结日线，
            #    三者的 trade_date 因此必然一致（这正是 risk CROSS_CHECK 要的共享）。
            self.daily = self._coord.read_index_daily(self.evidence_set_id, _DATE_SYMBOL, bars=2)
            with self._lock:
                # raw 已由 freeze 落库，不重复落盘；hash 用冻结集登记的那份（整份 raw）。
                self.hashes[f"sina:kline/{_DATE_SYMBOL}"] = \
                    self._coord.frozen_content_sha256(self.evidence_set_id, _DATE_SYMBOL)
                # 批 E-I：Evidence 直接声明冻结集 id。
                self.es_ids[f"sina:kline/{_DATE_SYMBOL}"] = self.evidence_set_id
            return
        try:
            self.daily = fetch_index_daily(_DATE_SYMBOL, bars=2)
        except (SourceError, ValueError) as e:
            self._note(missing=MissingItem(
                f"交易日 —— 无法从日线确定（{e}），板块数据将无从定位到哪一天",
                "sector.trade_date.unavailable"))
            return
        self._keep_raw(f"sina:kline/{_DATE_SYMBOL}", self.daily.raw,
                       self.daily.server_as_of or now_cn(), self.daily.raw_text)


def build_fact_bundle(*, break_source: set[str], store: bool, task_id: str,
                      evidence_set_id: str | None = None) -> FactBundle:
    t_start = time.monotonic()
    c = Collector(break_source, store, evidence_set_id)

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
        return resolve_provenance(source, c.hashes)

    def es_id_for(source: str) -> str | None:
        return resolve_provenance(source, c.es_ids)

    def add(field: str, value: Any, label: str, source: str, *,
            kind: str | None, inputs: tuple[str, ...] = (),
            origins: tuple = ()) -> None:
        result[field] = value
        evidence.append(Evidence(
            field=field, source=source, value=value,
            as_of=as_of, retrieved_at=retrieved,
            calc_version=CALC_VERSION, label=label,
            raw_hash=raw_hash_for(source),
            evidence_set_id=es_id_for(source),
            kind=kind,
            derived_from=evidence_origins(evidence, inputs, of=field) + tuple(origins)))


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
    def add_live(field: str, value: Any, label: str, source: str, *,
            kind: str | None, inputs: tuple[str, ...] = (),
            origins: tuple = ()) -> None:
        """实时快照类证据：as_of = 取回时刻。"""
        result[field] = value
        evidence.append(Evidence(
            field=field, source=source, value=value,
            as_of=retrieved, retrieved_at=retrieved,
            calc_version=CALC_VERSION, label=label,
            raw_hash=raw_hash_for(source),
            evidence_set_id=es_id_for(source),
            kind=kind,
            derived_from=evidence_origins(evidence, inputs, of=field) + tuple(origins)))

    if c.daily is None:
        c.missing.append(MissingItem(
            "全部板块指标 —— 没有可信的交易日，无法确定这批数据描述的是哪一天",
            "sector.trade_date.undetermined"))
    else:
        trade_date = c.daily.trade_date
        as_of, as_of_warning = as_of_for_trade_date(trade_date, retrieved_at=retrieved)
        if as_of_warning:
            c.warnings.append(as_of_warning)
        add("trade_date", trade_date, "交易日", f"sina:kline/{_DATE_SYMBOL}",
            kind="observed")

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
            add_live(f"{tag}_top", [_brief(b) for b in ranked[:TOP_N]],
                f"{label}涨幅前 {TOP_N}", src, kind="derived")
            up = sum(1 for b in ranked if b.pct > 0)
            add_live(f"{tag}_advance_ratio", round(up / len(ranked), 4),
                f"上涨{label}板块占比", f"derived:{src}", kind="derived")
            if tag == "industry":
                add_live("industry_bottom", [_brief(b) for b in ranked[-BOTTOM_N:]],
                    f"{label}跌幅前 {BOTTOM_N}", src, kind="derived")
                # 🔴 外部评审 F6：资金字段可以**独立于 pct** 失效。
                #    上面那条 pre_session 守卫只看 pct，放行之后资金侧
                #    若是全缺或全零，排序结果是任意的 —— 而「第一名」
                #    读起来毫无破绽，带着「0.0 亿」直接上卡。
                known = [b for b in r.boards if b.main_inflow is not None]
                if len(known) < len(r.boards):
                    c.missing.append(MissingItem(
                        f"主力净流入 —— {len(r.boards) - len(known)}/{len(r.boards)} "
                        f"个板块接口未给该字段，排名不成立",
                        "sector.inflow.field_absent"))
                elif not r.inflow_nonzero_count:
                    c.missing.append(MissingItem(
                        f"主力净流入 —— 全部 {len(r.boards)} 个板块均为 0，"
                        f"这是盘前/资金流统计未开始，**不是「没有资金进出」**；"
                        f"此时「前 5」取到谁纯属排序偶然",
                        "sector.inflow.all_zero"))
                else:
                    by_money = sorted(known, key=lambda b: b.main_inflow, reverse=True)
                    add_live("main_inflow_top", [_brief(b) for b in by_money[:TOP_N]],
                        "主力净流入前 5（行业）", src, kind="derived")
                    add_live("main_inflow_total_yi",
                        round(sum(b.main_inflow for b in known) / _YI, 2),
                        "行业主力净流入合计(亿元)", f"derived:{src}", kind="derived")
            if any(b.leader is None for b in ranked[:TOP_N]):
                c.warnings.append(f"{label}榜前 {TOP_N} 中有板块未返回领涨股")

        if counts:
            # 行业榜 + 概念榜两份响应的合计 —— 跨源聚合，用 raw_origins 指回那两份。
            add_live("board_counts", counts, "各榜板块数", "em:clist", kind="derived",
                     origins=raw_origins(c.hashes.get(f"em:clist/{k}")
                                         for k in ("industry", "concept")))
        else:
            c.missing.append(MissingItem(
                "板块强度 —— 行业榜与概念榜都不可用", "sector.board.none"))

    if store:
        for source, payload, src_as_of, raw_text in c.raw:
            save_raw_snapshot(source=source, as_of=src_as_of.isoformat(),
                              retrieved_at=retrieved.isoformat(),
                              payload=payload, raw_text=raw_text)

    core = {"trade_date", "industry_top", "industry_advance_ratio"}
    if not c.missing:
        status, level = "completed", "PASS"
    elif core <= set(result):
        status, level = "partial", "WARNING"
    else:
        status, level = "partial", "UNKNOWN"

    # 🔴 批 E-II：产 FactBundle（只事实、无 stance）；stance 由 Sector Agent 事后追加。
    return FactBundle(
        task_id=task_id, agent=AGENT, status=status, verdict=level,
        result=result,
        data_completeness=round(len(result) / _EXPECTED_FIELDS, 2) if result else 0.0,
        evidence=evidence, warnings=c.warnings, missing=c.missing,
        elapsed_ms=int((time.monotonic() - t_start) * 1000))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="板块强度与资金方向 → FactBundle JSON（批 E-II）")
    ap.add_argument("--break-source", action="append", default=[], metavar="NAME",
                    help="演练：人为中断 (industry|concept|date)")
    ap.add_argument("--task-id")
    ap.add_argument("--run-id", default=None,
                    help="本次编排执行尝试的 run_id（RunContext.run_id），由 Supervisor "
                         "传下来落进 agent_verdicts.run_id。只 capture 不校验，缺省 None")
    ap.add_argument("--evidence-set-id", default=None,
                    help="给了就读这份冻结快照的日线（编排出卡时传）；缺省自己联网抓")
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
            val = e.value
            if isinstance(val, list):
                val = "; ".join(f"{x['name']}({x['pct']}%)" for x in val[:3]) + " …"
            print(f"  {e.display_label:<22} = {val}", file=sys.stderr)
        for w in fb.warnings:
            print(f"  ⚠ {w}", file=sys.stderr)
        for m in fb.missing:
            print(f"  ⚠ 缺失 [{m.code}] {m}", file=sys.stderr)
    return {"PASS": 0, "WARNING": 2}.get(fb.verdict, 3)


if __name__ == "__main__":
    raise SystemExit(main())
