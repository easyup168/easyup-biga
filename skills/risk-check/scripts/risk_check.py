#!/usr/bin/env python3
"""risk-check —— 从 Stage 1 的**冻结证据**里算出风险事实，输出 AgentVerdict。

🔴 它不采数据
--------------
本脚本**没有任何数据源依赖**（不 import `_sources`，由测试钉死）。
输入只有 Stage 1 各 Specialist 的 `verdict_id`。

理由是制衡层的意义所在：risk 要审的是**别人看到的那份证据**。
它自己重采一遍，就会出现「risk 看到的市场」与「market 看到的市场」不是同一个 ——
那时候它审的是自己的幻觉，不是这次决策的依据。

    Stage 1 证据冻结 ⇒ Stage 2 只读不采 ⇒ 回放时两边看到的完全一样

🔴 它不给结论
--------------
本脚本只回答「哪些**写死的阈值**被触发了」「上游证据有多完整/多新鲜/是否自相矛盾」。

「这算不算该否决」是 Risk Agent 的判断（铁律 4）。
阈值本身写在 `THRESHOLDS` 里并带版本号 —— 改了阈值要升 `CALC_VERSION`，
否则历史结论无法清点。

用法::

    python3 risk_check.py --verdict-ids 21,22
    python3 risk_check.py --verdict-ids 21,22 --render
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from typing import Any

_HERE = pathlib.Path(__file__).resolve()
_REPO = _HERE.parent.parent.parent.parent
sys.path.insert(0, str(_REPO / "skills"))

from _contract import (  # noqa: E402
    STAGE1_AGENTS,
    AgentVerdict,
    Evidence,
    MissingItem,
    new_task_id,
    now_cn,
)
from _store import init_schema, load_verdict, save_verdict  # noqa: E402

AGENT = "risk"
CALC_VERSION = "risk-check/1"

#: 写死的风险阈值。`(字段, 比较, 阈值, 代码, 人话)`
#:
#: 🔴 这些数字**未经本项目验证**，与情绪分同样属于「业内常见口径」。
#:    它们的区分力要到测量阶段做过检验才算数。
#:    现在的作用只是把「哪些线被碰到了」变成可复查的事实，不是风险评分。
THRESHOLDS: tuple[tuple[str, str, float, str, str], ...] = (
    ("broken_rate",    ">", 0.50, "risk.emotion.broken_rate_high", "炸板率超过 50%，买盘承接明显不足"),
    ("volume_ratio",   ">", 2.00, "risk.market.volume_spike",      "量能是 20 日均量的 2 倍以上，异常放量"),
    ("volume_ratio",   "<", 0.50, "risk.market.volume_dry",        "量能不足 20 日均量的一半，极度缩量"),
    ("advance_ratio",  "<", 0.20, "risk.market.breadth_weak",      "上涨家数占比不足 20%，普跌"),
    ("max_streak",     ">", 6.99, "risk.emotion.streak_extreme",   "最高板 7 板以上，情绪处在高位"),
    ("limit_down_count", ">", 29.9, "risk.emotion.limit_down_many", "跌停家数超过 30，恐慌迹象"),
)

#: 已知互斥的 stance 组合 —— 上游互相打架时，下游不该假装看不见。
CONFLICT_PAIRS: tuple[tuple[str, str, str, str], ...] = (
    ("market", "放量上涨", "emotion", "恐慌"),
    ("market", "放量上涨", "emotion", "冰点"),
    ("market", "缩量上涨", "emotion", "亢奋"),
    ("market", "放量下跌", "emotion", "亢奋"),
)

#: A 股收盘时刻。用来判断「这批证据描述的那个交易时段还开着吗」。
#:
#: 🔴 为什么不直接数「超过 N 秒的证据有几条」
#:    第一版是那么写的，结果非交易日跑出来「25 条证据全部陈旧」——
#:    因为 as_of 停在上一个交易日收盘，距今几十小时。
#:    **一个在周末必然全红的指标，等于没有指标**（与 R-3 同源）。
#:    改成只报两个事实：最旧证据的年龄 + 那个交易时段是否还开着。
#:    「几十小时算不算陈旧」由 Risk Agent 结合 `session_live` 判断。
_CLOSE_HHMM = (15, 0)


def _cmp(value: Any, op: str, bound: float) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return v > bound if op == ">" else v < bound


def build_verdict(*, verdict_ids: list[int], store: bool, task_id: str) -> AgentVerdict:
    t_start = time.monotonic()
    retrieved = now_cn()
    missing: list[MissingItem] = []
    warnings: list[str] = []

    upstream: list[AgentVerdict] = []
    for vid in verdict_ids:
        v = load_verdict(vid)
        if v is None:
            missing.append(MissingItem(
                f"上游判定 #{vid} —— 在 agent_verdicts 里不存在，无法审阅",
                "risk.upstream.verdict_not_found"))
            continue
        upstream.append(v)

    result: dict[str, Any] = {}
    evidence: list[Evidence] = []

    if not upstream:
        missing.append(MissingItem(
            "全部风险判据 —— 没有拿到任何上游判定，无从审起",
            "risk.upstream.none"))
        return AgentVerdict(
            task_id=task_id, agent=AGENT, status="failed", verdict="UNKNOWN",
            result={}, confidence=0.0, evidence=[], warnings=warnings,
            missing=missing, elapsed_ms=int((time.monotonic() - t_start) * 1000))

    # 🔴 risk 的结论不可能比它最旧的输入更新鲜。
    as_of = min(e.as_of for v in upstream for e in v.evidence) \
        if any(v.evidence for v in upstream) else retrieved

    def add(field: str, value: Any, label: str) -> None:
        result[field] = value
        evidence.append(Evidence(
            field=field, source="derived:risk-check", value=value,
            as_of=as_of, retrieved_at=retrieved,
            calc_version=CALC_VERSION, label=label))

    present = sorted(v.agent for v in upstream)
    add("upstream_agents", present, "到场的上游 Agent")
    add("coverage_ratio",
        round(len([a for a in present if a in STAGE1_AGENTS]) / len(STAGE1_AGENTS), 4),
        f"Stage 1 覆盖率(应到 {len(STAGE1_AGENTS)})")

    absent = [a for a in STAGE1_AGENTS if a not in present]
    if absent:
        missing.append(MissingItem(
            f"风险面不完整 —— Stage 1 缺席：{'、'.join(absent)}，"
            f"这些领域的风险本次没有被看过",
            "risk.upstream.coverage_incomplete"))

    add("upstream_missing_count", sum(len(v.missing) for v in upstream),
        "上游缺失项总数")

    # --- 上游自报的交易日是否一致 ---
    dates = {v.agent: v.result.get("trade_date") for v in upstream
             if v.result.get("trade_date")}
    consistent = len(set(dates.values())) <= 1
    add("trade_date_consistent", consistent, "上游交易日是否一致")
    if dates:
        add("upstream_trade_date",
            next(iter(set(dates.values()))) if consistent else sorted(set(dates.values())),
            "上游自报的交易日")
    if not consistent:
        missing.append(MissingItem(
            f"风险判断的时间基准 —— 上游报告了不同的交易日 {dates}，"
            f"不能当作同一天的事实一起审",
            "risk.upstream.trade_date_inconsistent"))

    # --- 证据新鲜度 ---
    ages = [e.staleness_sec for v in upstream for e in v.evidence]
    if ages:
        add("max_staleness_sec", max(ages), "最旧证据的年龄(秒)")
    today = retrieved.strftime("%Y%m%d")
    live = (consistent and dates and next(iter(set(dates.values()))) == today
            and (retrieved.hour, retrieved.minute) < _CLOSE_HHMM)
    add("session_live", bool(live), "证据所属交易时段是否仍在进行")

    # --- 写死阈值 ---
    tripped: list[str] = []
    for field, op, bound, code, why in THRESHOLDS:
        for v in upstream:
            if field in v.result and _cmp(v.result[field], op, bound):
                tripped.append(code)
                warnings.append(f"[{code}] {why}（{v.agent}.{field}={v.result[field]}）")
                break
    add("tripped_thresholds", tripped, "被触发的风险阈值")

    # --- 上游 stance 互斥 ---
    stances = {v.agent: v.stance for v in upstream if v.stance}
    conflicts = [f"{a}={x} 与 {b}={y}"
                 for a, x, b, y in CONFLICT_PAIRS
                 if stances.get(a) == x and stances.get(b) == y]
    add("stance_conflict", conflicts, "上游判断互相矛盾之处")
    if conflicts:
        warnings.append("上游判断互斥：" + "；".join(conflicts)
                        + " —— 至少有一方是错的，不要各取所需")

    # 🔴 没有 stance 的上游 = 它没给判断。risk 审的是判断，不是只审数字。
    silent = [v.agent for v in upstream if not v.stance and v.verdict != "UNKNOWN"]
    if silent:
        missing.append(MissingItem(
            f"上游判断 —— {'、'.join(silent)} 没有给出方向判断，无法审阅其结论",
            "risk.upstream.stance_absent"))

    core = {"coverage_ratio", "tripped_thresholds", "trade_date_consistent"}
    if not missing:
        status, level = "completed", "PASS"
    elif core <= set(result):
        status, level = "partial", "WARNING"
    else:
        status, level = "partial", "UNKNOWN"

    v = AgentVerdict(
        task_id=task_id, agent=AGENT, status=status, verdict=level,
        result=result, confidence=round(len(result) / _EXPECTED_FIELDS, 2) if result else 0.0,
        evidence=evidence, warnings=warnings, missing=missing,
        elapsed_ms=int((time.monotonic() - t_start) * 1000))
    return v


#: 数据齐备时应当产出的字段数（同 market/emotion，故意不截断，由测试钉死）。
_EXPECTED_FIELDS = 9


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="从 Stage 1 冻结证据算风险事实 → AgentVerdict JSON")
    ap.add_argument("--verdict-ids", required=True,
                    help="Stage 1 各 Specialist 的 verdict_id，逗号或空格分隔")
    ap.add_argument("--task-id", help="BIGA-YYYYMMDD-NNN，缺省自动生成")
    ap.add_argument("--no-store", action="store_true", help="不落判定原件")
    ap.add_argument("--render", action="store_true", help="附带人类可读摘要")
    args = ap.parse_args(argv)

    ids = []
    for raw in args.verdict_ids.replace(",", " ").split():
        if not raw.isdigit():
            print(f"--verdict-ids 只接受数字 id，收到 {raw!r}。"
                  f"这个 id 由各 Specialist 的 skill 在 stderr 上打印：verdict_ref=NN",
                  file=sys.stderr)
            return 2
        ids.append(int(raw))

    store = not args.no_store
    if store:
        init_schema()

    v = build_verdict(verdict_ids=ids, store=store,
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
            print(f"  {e.display_label:<26} = {e.value}", file=sys.stderr)
        for w in v.warnings:
            print(f"  ⚠ {w}", file=sys.stderr)
        for m in v.missing:
            print(f"  ⚠ 缺失 [{m.code}] {m}", file=sys.stderr)

    return {"PASS": 0, "WARNING": 2}.get(v.verdict, 3)


if __name__ == "__main__":
    raise SystemExit(main())
