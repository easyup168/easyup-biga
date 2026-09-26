#!/usr/bin/env python3
"""emotion-calc —— A 股情绪事实计算，输出一份合法的 FactBundle。

🔴 批 E-I 试点：这是第一个迁到「事实与判断拆开」的 skill。它现在产 `FactBundle`
（只事实、无 stance），stance 由 emotion Agent 事后用 `amend_verdict.py --stance`
追加一个 `AgentAssessment` —— **不重打这份事实**（补丁路径当年就是为了防这个：
`BIGA-20260920-002` 那次重打丢了 15 条 evidence 的 retrieved_at）。其余五个 skill
仍产 `AgentVerdict`（合体），E-II 起再迁。

分工（architecture.md §6 硬约束 S-1 / S-2）
--------------------------------------------
本脚本**只产出事实与分数**，不做任何判断性归类。

  归这里（Python）          归 Emotion Agent（LLM）
  ─────────────────────    ────────────────────────────
  涨停/跌停/炸板家数         这算不算「亢奋期」
  炸板率、最高板、梯队分布    炸板率 24% 在当前位置是否算健康
  赚钱效应中位数             要不要提示追高风险
  情绪分（确定性公式）        这个分数此刻该不该信

为什么划在这里：判断逻辑一旦散进 skill，就会产生第二套口径 ——
同一个「强不强」在 skill 里算一遍、在 agent 的 prompt 里又算一遍，
两边慢慢漂开，而且漂开时不会报错。

🔴 涨跌家数不在这里（裁定 15）
------------------------------
上游文档把「上涨/下跌家数」同时派给了 market 与 emotion，本项目**只让 market 算**。

它的端点是**不带日期字段的实时快照**：两个 agent 并行各调一次，
同一张 Card 上就会出现**同一个字段两个值**，而两个都带着推断出来的 `as_of`。
不会报错，只会某天悄悄给出两个数。

    同一个事实只能有一个生产方。

情绪分公式只用 涨停家数 / 最高板 / 炸板率，因此这次移出**不影响任何派生值**。

🔴 缺失即缺失
-------------
任何一项算不出来，都进 `missing[]` 并如实出现在 Decision Card 上。
**绝不填 0、绝不跳过、绝不用昨天的值顶替。**
这是本项目最优先防范的失败模式：静默 fail-open —— 放行与通过的日志长得一模一样。

用法::

    # 最近一个交易日（宽松：接受数据源给出的最新交易日）
    python3 emotion_calc.py

    # 指定交易日（严格：数据源给的日期对不上就进 missing[]）
    python3 emotion_calc.py --date 20260918

    # 演练数据源中断，验证 UNKNOWN ≠ PASS
    python3 emotion_calc.py --break-source limit_up
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import pathlib
import sys
import time
import threading
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
    CN_TZ,
    Evidence,
    FactBundle,
    MissingItem,
    new_task_id,
    now_cn,
)
from _data import (  # noqa: E402
    DecisionDataClient,
    PoolResult,
    SourceError,
    as_of_for_trade_date,
    fetch_pool,
    session_in_progress,
)
from _store import (  # noqa: E402
    init_schema,
    raw_text_sha256,
    save_fact_bundle,
    save_raw_snapshot,
)

AGENT = "emotion"
CALC_VERSION = "emotion-calc/1"

#: 情绪分参考公式的权重。业内常见口径，**未经本项目验证** ——
#: 它的区分力要到测量阶段做安慰剂检验后才算数，因此只作为一个内部参考数，
#: 不参与任何归类，也不单独上 Card。
#: 情绪分的三个输入。`if` 判齐备与 `inputs=` 声明血缘**共用这一份** ——
#: 分成两处写死同一组名字，就是第二套口径（L-3），改一处忘另一处不报错。
_SCORE_INPUTS: tuple[str, ...] = ("limit_up_count", "max_streak", "broken_rate")

_SCORE_W = {"limit_up_div": 150.0, "limit_up_w": 40.0,
            "streak_div": 10.0, "streak_w": 30.0,
            "seal_w": 30.0}

#: 数据齐备时应当产出的字段数，用于 `confidence`。
#: ⚠️ 移出涨跌家数前这里写的是 15，而实际只有 13 个字段 ——
#:    意味着**数据完整时 confidence 也只有 0.87，「完整」永远读不出来**。
#:    现在钉死为真实字段数，并由测试守住（同 market-calc）。
_EXPECTED_FIELDS = 10


class Collector:
    """采集 + 记账。把「取到了什么 / 哪里失败了」分别攒起来。"""

    def __init__(self, date: str | None, break_source: set[str], store: bool,
                 evidence_set_id: str | None = None):
        self._lock = threading.Lock()
        self.date = date
        self.break_source = break_source
        self.store = store
        self.evidence_set_id = evidence_set_id
        self._data = DecisionDataClient() if evidence_set_id is not None else None
        self.pools: dict[str, PoolResult] = {}
        self.missing: list[str] = []
        self.warnings: list[str] = []
        self.raw_ids: list[int] = []
        #: source → 原始响应的哈希。Evidence.raw_hash 用它指回 raw 层。
        self.hashes: dict[str, str] = {}
        #: source → 本次决策冻结的 EvidenceSet id。
        self.es_ids: dict[str, str] = {}

    def _note(self, *, missing: MissingItem | None = None,
              warning: str | None = None) -> None:
        with self._lock:
            if missing:
                self.missing.append(missing)
            if warning:
                self.warnings.append(warning)

    def _keep(self, pool: str, r: PoolResult) -> None:
        with self._lock:
            self.pools[pool] = r

    def _keep_raw(self, snapshot_id: int) -> None:
        with self._lock:
            self.raw_ids.append(snapshot_id)

    # -- 采集 --------------------------------------------------------------

    def _target_date(self) -> str:
        return self.date or now_cn().strftime("%Y%m%d")

    def collect_pool(self, pool: str, label: str) -> None:
        if pool in self.break_source:
            self._note(missing=MissingItem(f"{label} —— 数据源被人为中断（--break-source {pool}）",
                                          "emotion.pool.source_broken"))
            return
        try:
            if self._data is not None:
                r, frozen_hash = self._data.read_pool(self.evidence_set_id, pool)
            else:
                r = fetch_pool(pool, self._target_date())
                frozen_hash = None
        except (SourceError, ValueError) as e:
            self._note(missing=MissingItem(f"{label} —— 数据源不可用: {e}",
                                          "emotion.pool.unavailable"))
            return

        if r.qdate is None:
            self._note(missing=MissingItem(
                f"{label} —— 返回中没有 qdate，无法确认这是哪一天的数据",
                "emotion.pool.no_qdate"))
            return

        if self.date is not None and not r.date_matches:
            # 显式指定了日期 = 一个契约。对不上就是没拿到要的东西。
            self._note(missing=MissingItem(
                f"{label} —— 请求 {self.date} 但数据源返回的是 {r.qdate} 的数据",
                "emotion.pool.date_mismatch"))
            return
        if self.date is None and r.qdate != self._target_date():
            self._note(warning=f"{label} 取到的是最近交易日 {r.qdate} 的数据，不是今天")

        self._keep(pool, r)
        # 🔴 指纹**不受 `store` 门控**（批 3）：哈希是这份响应本身的属性，
        #    与「我们这次存不存盘」无关。原先它写在 `if self.store:` 里面，于是
        #    `--no-store` 跑出来的证据**全都没有 raw_hash** —— 同一段代码、同一份
        #    数据，可追溯性却取决于一个与追溯无关的开关。
        #    市场/板块/快讯三个 skill 本来就是先算哈希再判 store，这里是唯一的例外。
        source = f"em:push2ex/{pool}"
        if self._data is not None:
            with self._lock:
                if frozen_hash:
                    self.hashes[source] = frozen_hash
                self.es_ids[source] = self.evidence_set_id
        else:
            with self._lock:
                # 批 I：hash 基于原始响应文本，与 save_raw_snapshot 的 content_sha256 同口径。
                self.hashes[source] = raw_text_sha256(r.raw_text)
            if self.store:
                got = now_cn()
                snap_as_of, _ = as_of_for_trade_date(r.qdate, retrieved_at=got)
                self._keep_raw(save_raw_snapshot(
                    source=source,
                    as_of=snap_as_of.isoformat(),
                    retrieved_at=got.isoformat(),
                    payload=r.raw,
                    raw_text=r.raw_text,
                ))

    # -- 派生 --------------------------------------------------------------

    @property
    def qdate(self) -> str | None:
        """三个池报的交易日。不一致时返回 None 并记入 missing。"""
        dates = {r.qdate for r in self.pools.values() if r.qdate}
        if not dates:
            return None
        if len(dates) > 1:
            return None
        return dates.pop()


def _ladder(rows: list[dict[str, Any]]) -> dict[int, int]:
    """连板梯队分布 {板数: 家数}。`lbc` = 连板次数。"""
    out: dict[int, int] = {}
    for r in rows:
        n = int(r.get("lbc") or 1)
        out[n] = out.get(n, 0) + 1
    return dict(sorted(out.items()))


def build_fact_bundle(
    *,
    date: str | None,
    break_source: set[str],
    store: bool,
    task_id: str,
    evidence_set_id: str | None = None,
) -> FactBundle:
    t_start = time.monotonic()
    c = Collector(date, break_source, store, evidence_set_id)

    # 三个请求互不依赖，并行拿。串行约 20s，并行约 8s ——
    # 端到端预算卡在 90s，这一步不是调优，是能不能用的问题。
    jobs = [
        lambda: c.collect_pool("limit_up", "涨停家数"),
        lambda: c.collect_pool("broken_board", "炸板家数"),
        lambda: c.collect_pool("limit_down", "跌停家数"),
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        for fut in [pool.submit(j) for j in jobs]:
            fut.result()

    # 三个池报了不同的交易日 —— 不能把它们当成同一天的事实汇总。
    reported = {r.qdate for r in c.pools.values() if r.qdate}
    if len(reported) > 1:
        c.missing.append(MissingItem(
            f"全部情绪指标 —— 各股池报告的交易日不一致 {sorted(reported)}，"
            "不能当作同一天的事实汇总", "emotion.pool.date_inconsistent"))
        c.pools.clear()

    qdate = c.qdate
    result: dict[str, Any] = {}
    evidence: list[Evidence] = []
    retrieved = now_cn()
    #: 🔴 as_of 取自数据自己声明的 qdate，绝不取自我请求的日期
    #:    （理由见 `_sources/eastmoney.py` 的实测表）；
    #:    而「交易日 → 时刻」的换算走共享实现，它认得「今天尚未收盘」那个分支
    #:    —— 少了那个分支，盘中跑会 as_of > retrieved_at，契约层直接拒绝构造。
    as_of: datetime | None = None

    def _raw_hash_for(source: str) -> str | None:
        return resolve_provenance(source, c.hashes)

    def add(field: str, value: Any, label: str, source: str, *,
            kind: str | None, inputs: tuple[str, ...] = (),
            origins: tuple = ()) -> None:
        """产出一条证据。

        `kind` **没有默认值**，强制每个调用点自己说清楚（裁定 16）——
        给它一个默认值，等于让「忘了想」和「想过了」写出来一模一样。

        `inputs` 只对「从别的值算出来」的派生值有意义，写**字段名**，
        由 `input_ids_for` 翻译成 evidence_id。
        """
        result[field] = value
        evidence.append(Evidence(
            field=field, source=source, value=value,
            as_of=as_of, retrieved_at=retrieved,
            calc_version=CALC_VERSION, label=label,
            raw_hash=_raw_hash_for(source),
            evidence_set_id=resolve_provenance(source, c.es_ids),
            kind=kind,
            derived_from=evidence_origins(evidence, inputs, of=field) + tuple(origins),
        ))

    if qdate and c.pools and all(r.total == 0 for r in c.pools.values()) \
            and session_in_progress(qdate, retrieved_at=retrieved):
        # 🔴 盘中/盘前三个池全为 0 —— 这是**还没形成**，不是「今天一个涨停都没有」。
        #    当成事实上卡，读者看到的是「冰点」这种极端读数。
        c.missing.append(MissingItem(
            f"全部情绪指标 —— {qdate} 的股池此刻全部为 0，"
            f"而这一天尚未收盘：数据**还没形成**，不是「涨停 0 家」",
            "emotion.pool.not_yet_formed"))
        c.pools.clear()
        qdate = None

    if qdate:
        as_of, as_of_warning = as_of_for_trade_date(qdate, retrieved_at=retrieved)
        if as_of_warning:
            c.warnings.append(as_of_warning)
        zt = c.pools.get("limit_up")
        zb = c.pools.get("broken_board")
        dt = c.pools.get("limit_down")

        if zt:
            add("limit_up_count", zt.total, "涨停家数", "em:push2ex/limit_up",
                kind="observed")
            ladder = _ladder(zt.rows)
            add("max_streak", max(ladder) if ladder else 0, "最高板",
                "em:push2ex/limit_up", kind="derived")
            add("streak_2plus_count", sum(v for k, v in ladder.items() if k >= 2),
                "连板家数(≥2)", "em:push2ex/limit_up", kind="derived")
            add("streak_ladder", {str(k): v for k, v in ladder.items()},
                "连板梯队分布", "em:push2ex/limit_up", kind="derived")
            never_broken = sum(1 for r in zt.rows if int(r.get("zbc") or 0) == 0)
            add("seal_never_broken_rate",
                round(never_broken / zt.total, 4) if zt.total else None,
                "全天未炸板占比", "em:push2ex/limit_up", kind="derived")

        if zb:
            add("broken_board_count", zb.total, "炸板家数", "em:push2ex/broken_board",
                kind="observed")
        if dt:
            add("limit_down_count", dt.total, "跌停家数", "em:push2ex/limit_down",
                kind="observed")

        # 炸板率需要两个池同时在场 —— 缺一个就不是「算出来是 0」，是「算不出来」。
        if zt and zb:
            denom = zt.total + zb.total
            if denom:
                add("broken_rate", round(zb.total / denom, 4), "炸板率",
                    "derived:limit_up+broken_board", kind="derived",
                    inputs=("limit_up_count", "broken_board_count"))
            else:
                c.missing.append(MissingItem("炸板率 —— 涨停与炸板家数均为 0，分母为零",
                                             "emotion.broken_rate.zero_denominator"))
        else:
            c.missing.append(MissingItem("炸板率 —— 需要涨停池与炸板池同时可用",
                                         "emotion.broken_rate.incomplete"))

        # 情绪分：确定性公式，三个输入缺一不可。
        if set(_SCORE_INPUTS) <= set(result):
            w = _SCORE_W
            score = (
                min(result["limit_up_count"] / w["limit_up_div"], 1.0) * w["limit_up_w"]
                + min(result["max_streak"] / w["streak_div"], 1.0) * w["streak_w"]
                + (1.0 - result["broken_rate"]) * w["seal_w"]
            )
            add("emotion_score", round(score, 2), "情绪分(参考)",
                "derived:emotion-calc", kind="derived",
                # 🔴 与上面那个 `if` 检查的三项**同一份名单** —— 两处写死同一组
                #    字段名就是第二套口径，改了一处忘另一处不会报错。
                inputs=tuple(_SCORE_INPUTS))
        else:
            c.missing.append(MissingItem("情绪分 —— 需要涨停家数 / 最高板 / 炸板率三项齐备",
                                         "emotion.score.incomplete"))

        # 🔴 三个股池各自报 qdate，取**共识**（不一致就是 None）⇒ 它出自那几份响应，
        # 不是任何单一一份。批 4 之前这里没有 raw_hash 也说不清为什么，
        # 曾被我错判成「表键对不上的 bug」—— 实际是跨源，用 raw_origins 指回全部。
        add("trade_date", qdate, "交易日", "em:push2ex/qdate", kind="observed",
            origins=raw_origins(c.hashes.get(f"em:push2ex/{name}")
                                for name in c.pools))
    else:
        c.missing.append(MissingItem("全部情绪指标 —— 没有任何股池返回可用的交易日",
                                     "emotion.trade_date.undetermined"))

    # verdict 表达的是**数据完整度**，不是市场判断。
    # 「这算不算亢奋期」由 Emotion Agent 依据 AGENTS.md 里的口径来说。
    core = {"limit_up_count", "broken_rate", "max_streak"}
    if not c.missing:
        status, level = "completed", "PASS"
    elif core <= set(result):
        status, level = "partial", "WARNING"
    else:
        status, level = "partial", "UNKNOWN"

    # 🔴 批 E-I：emotion 是试点 —— 产 FactBundle（只事实、无 stance），不再产
    #    合体 AgentVerdict。stance 由 emotion Agent 事后用 amend_verdict.py 追加一个
    #    AgentAssessment（不重打这份事实）。其余五个 skill 仍产 AgentVerdict（E-II 再迁）。
    return FactBundle(
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
    ap = argparse.ArgumentParser(description="A 股情绪事实计算 → FactBundle JSON（批 E-I）")
    ap.add_argument("--date", help="交易日 YYYYMMDD。给了就是严格模式："
                                   "数据源返回的日期对不上即进 missing[]")
    ap.add_argument("--task-id", help="BIGA-YYYYMMDD-NNN，缺省自动生成")
    ap.add_argument("--run-id", default=None,
                    help="本次编排执行尝试的 run_id（RunContext.run_id），由 Supervisor "
                         "传下来落进 agent_verdicts.run_id。只 capture 不校验，缺省 None")
    ap.add_argument("--evidence-set-id", default=None,
                    help="编排器冻结的数据集 id；给了就只读冻结 DatasetSnapshot，不直连 Provider")
    ap.add_argument("--break-source", action="append", default=[],
                    metavar="NAME",
                    help="演练用：人为中断某个数据源 "
                         "(limit_up|broken_board|limit_down)")
    ap.add_argument("--no-store", action="store_true",
                    help="不写 raw_market_snapshot（默认写）")
    ap.add_argument("--render", action="store_true", help="附带人类可读摘要")
    args = ap.parse_args(argv)

    store = not args.no_store
    if store:
        init_schema()

    fb = build_fact_bundle(
        date=args.date,
        break_source=set(args.break_source),
        store=store,
        task_id=args.task_id or new_task_id(ADHOC_TASK_SEQ),
        evidence_set_id=args.evidence_set_id,
    )
    # 🔴 事实原件直接落库（批 E-I：FactBundle，不含 stance），返回一个 id 供 agent 引用。
    #    在此之前契约要求 agent「把这份 JSON 原样带上」—— 实测它做不到原样：
    #    15 条 evidence 的 retrieved_at 转述后一条不剩。让数据不经过 LLM，是唯一可靠的修法。
    #    stance 由 emotion Agent 事后 `amend_verdict.py --ref <这个号> --stance <词>` 追加，
    #    落成一条 AgentAssessment，**不重打这份事实**。
    ref = save_fact_bundle(fb, run_id=args.run_id) if store else None

    print(json.dumps(fb.to_dict(), ensure_ascii=False, indent=2))
    if ref is not None:
        print(f"verdict_ref={ref}", file=sys.stderr)

    if args.render:
        print("\n" + "─" * 60, file=sys.stderr)
        print(f"{AGENT}  {fb.status}/{fb.verdict}  耗时 {fb.elapsed_ms}ms"
              + (f"  verdict_ref={ref}" if ref is not None else "  (未落库)"),
              file=sys.stderr)
        for e in fb.evidence:
            print(f"  {e.display_label:<16} = {e.value}"
                  f"   as_of {e.as_of:%Y-%m-%d %H:%M}", file=sys.stderr)
        if fb.warnings:
            print("  警告:", file=sys.stderr)
            for w in fb.warnings:
                print(f"    · {w}", file=sys.stderr)
        if fb.missing:
            print(f"  ⚠ 缺失项（{len(fb.missing)}）:", file=sys.stderr)
            for m in fb.missing:
                print(f"    · {m}", file=sys.stderr)

    # 退出码：0=完整，2=有缺失但核心可用，3=核心缺失
    return {"PASS": 0, "WARNING": 2}.get(fb.verdict, 3)


if __name__ == "__main__":
    raise SystemExit(main())
