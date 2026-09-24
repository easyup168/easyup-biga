#!/usr/bin/env python3
"""stale_run_reaper.py —— 扫描卡在非终态太久的 run，收成 TIMEOUT（外部评审 §14）。

🔴 它解决的问题
----------------
`orchestrator.py` 被外部信号打断（`bin/biga-card` 的外层 `timeout` 发 SIGTERM、
`kill`、机器重启）时来不及走自己的 except 链——`run_events` 永远停在最后一次成功
转移到的那个非终态（比如 `STAGE1_RUNNING`）。这与 D-1（`OrchestratorTimeout`）是
两个互补的修复：D-1 处理"应用层能预见的超时"（`_stage_timeout` 主动判断预算耗
尽），这里处理"进程根本没机会自己处理"的那一半。

设计文档 `docs/design/deterministic-orchestration.md` §24-25 复核这条评审断言
的结论是"✅ 成立"；`bin/biga-card` 的 `trap '_reap_orch' EXIT` 只保证子进程被
`kill -TERM`，不保证 `run_events` 写终态。

🔴 本批只做纯函数工具，不接调度（`docs/guide/orchestration-kickoff-prompt.md`
点名的范围：「可以写一个纯函数……但不要接 cron 或 systemd timer——那是批 G 的
地盘」）。调用者手工跑，或者未来某一批接进 systemd timer，这里不管。

判据：`run_events` 最新一条 `to_state` 不在 `TERMINAL_STATES` 里（还在跑）
+ `decision_runs.created_at` 距今已经超过 `threshold_sec`。两张表都没有
`deadline_at` 这类字段，用起点时间近似——阈值需要明显宽松于正常出卡耗时
（170~200s）与硬超时兜底（`CARD_DEADLINE_SEC+60`=840s），默认 20 分钟。

转移时用 `find_stale_runs` 扫描到的**那一刻**的状态做 `expected`（防
TOCTOU——`transition()` 内部的 `UNIQUE(run_id, seq)` CAS 会核对 `expected` 是否
仍等于当前真实状态；扫描到真正转移之间如果这个 run 自己推进了（无论是走到了
终态，还是变成了别的非终态——说明其实还在正常运行），`expected` 就对不上，CAS
拒绝，不覆盖）。⚠️ 这里**不能**重新实时读一次当前状态再传给 `transition()`——
那样 `expected` 永远等于"当前真实状态"，CAS 形同虚设。
`enqueue_run_failed` 本身是幂等的（`aggregate=run_id`），文档字符串早就写明
"reaper/重跑可安全补"——这里是那句话第一次真正有了调用方。
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import datetime

_REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills"))

from _contract import RunState, TERMINAL_STATES, now_cn  # noqa: E402
from _store import (  # noqa: E402
    IllegalTransition,
    UnknownRun,
    connect,
    enqueue_run_failed,
    transition,
)

__all__ = ["DEFAULT_THRESHOLD_SEC", "find_stale_runs", "reap", "main"]

#: 默认阈值：明显大于硬超时兜底（`CARD_DEADLINE_SEC+60`=840s），给真正在跑的 run
#: 留足余量，避免误杀。20 分钟。
DEFAULT_THRESHOLD_SEC = 1200


def find_stale_runs(
    threshold_sec: float, *, path: pathlib.Path | str | None = None
) -> list[dict]:
    """扫描非终态、且已经超过 `threshold_sec` 未推进的 run（只读，不改任何状态）。

    「非终态」由每个 run 最新一条 `run_events.to_state` 决定；「太久」用
    `decision_runs.created_at`（run 开始的时刻）与当前时刻的差近似——没有更精确
    的 deadline 字段可用。
    """
    now = now_cn()
    with connect(path, readonly=True) as conn:
        rows = conn.execute(
            """SELECT dr.run_id, dr.decision_id, dr.created_at, re.to_state
                 FROM decision_runs dr
                 JOIN run_events re ON re.run_id = dr.run_id
                WHERE re.seq = (SELECT MAX(seq) FROM run_events
                                 WHERE run_id = dr.run_id)"""
        ).fetchall()
    stale = []
    for r in rows:
        if r["to_state"] in TERMINAL_STATES:
            continue
        age = (now - datetime.fromisoformat(r["created_at"])).total_seconds()
        if age >= threshold_sec:
            stale.append({"run_id": r["run_id"], "decision_id": r["decision_id"],
                         "state": r["to_state"], "age_sec": age})
    return stale


def reap(
    threshold_sec: float = DEFAULT_THRESHOLD_SEC, *, dry_run: bool = True,
    path: pathlib.Path | str | None = None,
) -> list[dict]:
    """把超过 `threshold_sec` 还没推进的 run 收成 `TIMEOUT`。

    `dry_run=True`（默认）只打印候选、不真的转移——运维工具的默认安全姿态，
    需要显式 `--apply`/`dry_run=False` 才会真的写库。

    返回每条候选的处理结果（`action` 字段）：
      * `would-reap`：dry-run，没有真的做任何事。
      * `reaped`：真的转移到了 TIMEOUT，并入队了一条 `run_failed` 通知。
      * `skipped-cas-conflict`：扫描到真正转移之间，这个 run 的状态变了——可能
        自己走到了终态（正常收尾，或被另一次 reap 抢先收了），也可能推进到了
        别的非终态（其实还在正常运行）——`transition()` 的 CAS 据此拒绝，不覆盖。

    🔴 `expected` 必须用 `find_stale_runs` 扫描时读到的**旧状态**（`c["state"]`），
    不能重新 `current_state()` 实时读取——用实时读取的话，只要 run 还没到终态、
    随便推进到哪个非终态，`expected` 永远等于"当前真实状态"，CAS 形同虚设，
    等于把一个正常运行中的 run 直接转去 TIMEOUT。用扫描时的旧值做 expected，
    两次读取之间只要状态真的变了（无论是变成终态还是变成别的非终态），CAS 都会
    如实拒绝——这才是 TOCTOU 保护该有的样子。
    """
    candidates = find_stale_runs(threshold_sec, path=path)
    result = []
    for c in candidates:
        run_id = c["run_id"]
        if dry_run:
            result.append({**c, "action": "would-reap"})
            continue
        try:
            transition(run_id, c["state"], RunState.TIMEOUT,
                      detail={"reason": "stale-run-reaper"}, path=path)
        except (IllegalTransition, UnknownRun):
            result.append({**c, "action": "skipped-cas-conflict"})
            continue
        enqueue_run_failed(run_id, reason="stale-run-reaper", path=path)
        result.append({**c, "action": "reaped"})
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="扫描卡在非终态太久的 run，收成 TIMEOUT（外部评审 §14）")
    ap.add_argument("--threshold-sec", type=float, default=DEFAULT_THRESHOLD_SEC,
                    help=f"多久没推进算 stale（默认 {DEFAULT_THRESHOLD_SEC}s）")
    ap.add_argument("--apply", action="store_true",
                    help="真的转移（默认 dry-run，只打印候选、不写库）")
    a = ap.parse_args(argv)
    results = reap(a.threshold_sec, dry_run=not a.apply)
    if not results:
        print("没有发现 stale run。")
        return 0
    for r in results:
        print(f"{r['run_id']}  decision={r['decision_id']}  "
              f"state={r['state']}  age={r['age_sec']:.0f}s  action={r['action']}")
    if not a.apply:
        print("\n（dry-run，未真的转移。真收：--apply）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
