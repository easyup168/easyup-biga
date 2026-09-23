"""运行身份 + 状态机的存储层 —— `decision_runs` / `run_events` 的读写。

设计文档 §5。契约层（`_contract.run`）定义**哪些状态、哪条转移合法**；
这里只做**落库**：开一个 run、compare-and-set 地推进状态、复述走过的路。

🔴 状态不在 `decision_runs` 上原地 UPDATE
------------------------------------------
`decision_runs` 是只追加的（触发器强制）。当前状态由 `run_events` 的**最新一行**
给出。`transition()` 靠 `UNIQUE(run_id, seq)` 做 CAS：

    读到最新 seq=N、状态=X
    断言 X == expected（回退 / expected 传错在这里被挡）
    INSERT seq=N+1  ←── 并发两次同转移都要写 seq=N+1，唯一约束只让一个落地

这与 `decision_ids` 用主键冲突仲裁占号是同一招：**唯一约束是唯一可靠的并发
仲裁，「先查再写」永远有竞态窗口。**
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
from typing import Any

from _contract import (
    INITIAL_STATE,
    LEGAL_TRANSITIONS,
    RunContext,
    RunState,
    now_cn,
)

from .db import connect

__all__ = [
    "IllegalTransition",
    "UnknownRun",
    "open_run",
    "transition",
    "current_state",
    "run_header",
    "run_events",
    "run_journey",
    "find_run_by_trigger",
]


class IllegalTransition(RuntimeError):
    """CAS 拒绝了一次转移：跳步 / 回退 / expected 传错 / 并发被抢先。

    单独一个类型，是为了让调用方能把「这一步本来就不该走」与「库真的坏了」
    分开处理 —— 前者是编排逻辑的 bug，后者是基础设施故障。
    """


class UnknownRun(RuntimeError):
    """对一个没 `open_run` 过的 run_id 做转移 / 查状态。"""


def open_run(ctx: RunContext, *, path: pathlib.Path | str | None = None) -> str:
    """开一个新 run：写 `decision_runs` 头 + 初始事件（进入 `RECEIVED`）。返回 run_id。

    🔴 只能开一次：`decision_runs.run_id` 是主键，同一个 run_id 再 open 会撞主键。
    初始事件的 `from_state` 是 NULL —— 进入 RECEIVED 之前没有状态，它不是一次
    「转移」，所以不过 `LEGAL_TRANSITIONS` 那道校验（open_run 是唯一入口）。
    """
    if not isinstance(ctx, RunContext):
        raise TypeError(f"open_run 只接受 _contract.RunContext，收到 {type(ctx).__name__}")
    now = now_cn().isoformat()
    with connect(path) as conn:
        try:
            conn.execute(
                "INSERT INTO decision_runs "
                "(run_id, decision_id, trigger_id, evidence_set_id, origin, "
                " non_interactive, created_at) VALUES (?,?,?,?,?,?,?)",
                (ctx.run_id, ctx.decision_id, ctx.trigger_id, ctx.evidence_set_id,
                 ctx.origin, int(ctx.non_interactive), ctx.created_at),
            )
        except sqlite3.IntegrityError as e:
            if "decision_runs" in str(e) or "run_id" in str(e):
                raise IllegalTransition(
                    f"run_id={ctx.run_id} 已经开过了 —— 一次执行尝试只能 open 一次。"
                    f"重试 / 回放请用 new_run_id() 生成新的 run_id。") from e
            raise
        conn.execute(
            "INSERT INTO run_events (run_id, seq, from_state, to_state, at, detail) "
            "VALUES (?,?,?,?,?,?)",
            (ctx.run_id, 1, None, RunState.RECEIVED, now, None),
        )
    return ctx.run_id


def _latest(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT seq, to_state FROM run_events WHERE run_id=? ORDER BY seq DESC LIMIT 1",
        (run_id,),
    ).fetchone()


def transition(
    run_id: str,
    expected_state: str,
    next_state: str,
    *,
    detail: dict[str, Any] | None = None,
    path: pathlib.Path | str | None = None,
) -> str:
    """compare-and-set 地把 run 从 `expected_state` 推进到 `next_state`。

    非法转移一律**抛错**，不静默：

      · `(expected, next)` 不在 `LEGAL_TRANSITIONS` 里（跳步 / 回退 / 乱跳）
      · run 的当前状态不是 `expected`（回退，或 expected 传错，或已被别人推进）
      · 并发有人抢先写了同一个 seq（`UNIQUE(run_id, seq)` 拦下）

    🔴 **不允许任何代码直接 UPDATE state** —— 状态只经这里推进（设计文档 §5）。

    Args:
        detail: 这次转移的附加信息，序列化进 `run_events.detail`。
            legacy 路径发现 decision_id 后用它带上；失败态带上原因。
    """
    # ① 合法边校验（静态：这条转移在设计里存不存在）
    if (expected_state, next_state) not in LEGAL_TRANSITIONS:
        legal_next = sorted(t for f, t in LEGAL_TRANSITIONS if f == expected_state)
        raise IllegalTransition(
            f"{expected_state} → {next_state} 不是合法转移。\n"
            f"  从 {expected_state} 出发合法的下一步：{legal_next or '（无——它是终态）'}\n"
            f"  跳步 / 回退 / 乱跳都在这里被挡（设计文档 §5 的状态机）。")
    blob = json.dumps(detail, ensure_ascii=False, sort_keys=True) if detail is not None else None
    now = now_cn().isoformat()
    with connect(path) as conn:
        # 🔴 让并发转移排队而不是立刻「database is locked」——
        #    CAS 的仲裁应该发生在 UNIQUE 约束上（一个赢一个撞），
        #    而不是在拿锁那一步就随机失败。设计文档 §2 记的「默认 5000ms」
        #    过去是隐式的，这里对写路径显式设一次。
        conn.execute("PRAGMA busy_timeout=5000")
        row = _latest(conn, run_id)
        if row is None:
            raise UnknownRun(
                f"run {run_id} 不存在 —— 先 open_run()。\n"
                f"  （查已有的 run：biga-card --status <run_id>）")
        cur_seq, cur_state = int(row["seq"]), row["to_state"]
        # ② compare：当前状态必须正好是 expected
        if cur_state != expected_state:
            raise IllegalTransition(
                f"CAS 失败：run {run_id} 现在在 {cur_state}，不是期望的 {expected_state}。\n"
                f"  可能已被另一次转移推进（回退 / 并发双写），或 expected 传错了。")
        # ③ set：追加 seq=N+1。UNIQUE(run_id, seq) 是真正的并发仲裁。
        try:
            conn.execute(
                "INSERT INTO run_events (run_id, seq, from_state, to_state, at, detail) "
                "VALUES (?,?,?,?,?,?)",
                (run_id, cur_seq + 1, expected_state, next_state, now, blob),
            )
        except sqlite3.IntegrityError as e:
            # UNIQUE(run_id, seq) 撞了：另一个并发转移抢先写了 seq=N+1。
            if "run_events" in str(e) or "seq" in str(e) or "UNIQUE" in str(e).upper():
                raise IllegalTransition(
                    f"CAS 失败：run {run_id} 的 seq={cur_seq + 1} 已被另一个并发转移抢先 —— "
                    f"同一个 run 不允许并发双写。") from e
            raise
    return next_state


def current_state(
    run_id: str, *, path: pathlib.Path | str | None = None
) -> str | None:
    """run 现在在哪个状态。没有这个 run 返回 None（由调用方决定这算不算错）。"""
    with connect(path, readonly=True) as conn:
        row = _latest(conn, run_id)
    return row["to_state"] if row else None


def run_header(
    run_id: str, *, path: pathlib.Path | str | None = None
) -> dict[str, Any] | None:
    """取 `decision_runs` 那一行（运行身份头）。找不到返回 None。"""
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT run_id, decision_id, trigger_id, evidence_set_id, origin, "
            "non_interactive, created_at FROM decision_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["non_interactive"] = bool(d["non_interactive"])
    return d


def find_run_by_trigger(
    trigger_id: str, *, path: pathlib.Path | str | None = None
) -> dict[str, Any] | None:
    """这个 `trigger_id` 起过的（最早那次）运行头 —— 入站幂等的**查询侧**（批 G-II）。

    占号的原子仲裁在 `decision_ids.trigger_id`（`reserve_decision_for_trigger`）；
    这里是给「已在处理」那句 ACK 补一个当前状态用的：一次外部请求重投时，回一句
    「决策 X 已在处理（跑到 STAGE1_RUNNING）」比只回「重复了」有用。走 v7 的
    `ix_runs_trigger` 索引。找不到（占了号但 run 还没 open）返回 None ——
    调用方仍可凭 `reserve_decision_for_trigger` 的 `created=False` 判定这是重投。
    """
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT run_id, decision_id, trigger_id, evidence_set_id, origin, "
            "non_interactive, created_at FROM decision_runs WHERE trigger_id=? "
            "ORDER BY created_at ASC, rowid ASC LIMIT 1",
            (trigger_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["non_interactive"] = bool(d["non_interactive"])
    return d


def run_events(
    run_id: str, *, path: pathlib.Path | str | None = None
) -> list[dict[str, Any]]:
    """取一个 run 的全部转移事件，按 seq 升序 —— 这次运行走过的路。"""
    with connect(path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT seq, from_state, to_state, at, detail FROM run_events "
            "WHERE run_id=? ORDER BY seq ASC",
            (run_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d["detail"]) if d["detail"] else None
        out.append(d)
    return out


def run_journey(
    run_id: str, *, path: pathlib.Path | str | None = None
) -> dict[str, Any] | None:
    """身份头 + 事件序列 + 当前状态，一次取齐 —— `biga-card --status` 的数据源。

    找不到这个 run 返回 None。
    """
    header = run_header(run_id, path=path)
    if header is None:
        return None
    events = run_events(run_id, path=path)
    return {
        "header": header,
        "events": events,
        "current_state": events[-1]["to_state"] if events else None,
    }
