"""OpenClaw 运行时数据的**只读**读取器 —— 延迟与成本核算的唯一数据源。

为什么它在 `_store/` 里
----------------------
全仓规矩是「一切 DB 访问走 `_store`」。这个模块遵守它。

为什么它和 `db.py` 分开
----------------------
它读的是**别人的库**：OpenClaw 运行时自己的状态与会话库。两点根本不同：

1. **schema 不归我们管。** 升级 OpenClaw 就可能变，且不会通知我们。
2. **它不是事实层。** BigA 的结论不建立在它之上；它只用于**核算**
   （这次跑了多久、花了多少 token）。

混进 `db.py` 会让人误以为它和 `decision_records` 是同一类东西。

🔴 降级原则
-----------
schema 对不上时**返回空 + 明确原因**，绝不：

* 崩溃 —— 核算工具挂掉会让人干脆不看核算
* 返回部分数据却装作完整 —— 那正是本项目最怕的静默失真

调用方必须检查 `RuntimeProbe.unavailable`，非空就说明这份数据不可信。
"""

from __future__ import annotations

import json
import pathlib
import sqlite3  # store-exempt: 读 OpenClaw 运行时的外部库；本模块就是那道唯一入口
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from _contract import CN_TZ

__all__ = ["RuntimeProbe", "AgentTurn", "ToolCall", "read_turns", "read_tool_calls",
           "read_task_runs", "list_agents", "OPENCLAW_HOME"]

OPENCLAW_HOME = pathlib.Path.home() / ".openclaw-biga"


@dataclass(frozen=True)
class AgentTurn:
    """一次 LLM 轮次 —— 从 `prompt.submitted` 到 `model.completed`。

    🔴 注意这与 `agent_runs.elapsed_ms` 测的**不是同一件事**：
    那个是 skill 的耗时（几秒），这个是 agent 的 LLM 轮次（几十秒）。
    延迟分析只有用后者才有意义。
    """

    agent_id: str
    session_key: str
    run_id: str
    model: str
    started_at: datetime
    ended_at: datetime
    tokens_in: int
    tokens_out: int
    cache_read: int
    cache_write: int
    cost_usd: float
    stop_reason: str | None = None

    @property
    def duration_ms(self) -> int:
        return int((self.ended_at - self.started_at).total_seconds() * 1000)

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out + self.cache_read + self.cache_write


@dataclass(frozen=True)
class ToolCall:
    """一次工具调用。诊断「它到底在干什么」用的最小单位。

    🔴 为什么值得单独读出来：本项目两次最有价值的诊断都来自拆开调用序列 ——
    Supervisor 那 159 秒里有 47 秒零工具调用（在重打 JSON）；
    Specialist 加 stance 后慢一倍，是因为 62 秒都在 grep 源码找词表。
    两次的第一反应都是「提示词写得不好」，两次都错。
    """

    agent_id: str
    session_key: str
    at: datetime          # 北京时间（在 `_parse_ts` 统一转好）
    name: str
    args_len: int
    command: str          # exec 类调用的命令原文，其余为空


@dataclass
class RuntimeProbe:
    """一次读取的结果 + 它有多可信。"""

    turns: list[AgentTurn] = field(default_factory=list)
    #: 🔴 非空就代表这份数据**不完整**。调用方必须显示它，不得忽略。
    unavailable: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unavailable


def _agent_dbs() -> list[tuple[str, pathlib.Path]]:
    out: list[tuple[str, pathlib.Path]] = []
    root = OPENCLAW_HOME / "agents"
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        db = d / "agent" / "openclaw-agent.sqlite"
        if db.is_file():
            out.append((d.name, db))
    return out


def _parse_ts(v: Any) -> datetime | None:
    """把运行时的时间戳解析成**北京时间**。

    🔴 OpenClaw 的 trajectory 里 `ts` 是 UTC（带 `Z`），而本项目对外一律北京时间。
    转换放在**读取边界这一处**，不让每个消费方各自记得 `.astimezone(CN_TZ)` ——
    那种「人人都要记得」的义务迟早会漏一个，而漏掉的表现是
    **时间差了 8 小时却仍然是个合法时刻**，不报错。

    实测踩过：手写的临时脚本直接打 `ts`，于是同一件事在延迟报告里是 08:00、
    在脚本里是 00:00，白白花时间对不上号。
    """
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(CN_TZ)
    except ValueError:
        return None


def read_turns(*, since: datetime | None = None) -> RuntimeProbe:
    """读出所有 agent 的 LLM 轮次。

    `prompt.submitted` 与 `model.completed` 按 `runId` 配对。
    只配上对的才计入 —— **半截的轮次宁可不要，也不要算出一个偏小的耗时**。
    """
    probe = RuntimeProbe()
    dbs = _agent_dbs()
    if not dbs:
        probe.unavailable.append(f"没有找到任何 agent 库（{OPENCLAW_HOME / 'agents'}）")
        return probe

    for agent_id, path in dbs:
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as e:
            probe.unavailable.append(f"{agent_id}: 打不开 {path.name} —— {e}")
            continue
        try:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "trajectory_runtime_events" not in names:
                probe.unavailable.append(
                    f"{agent_id}: 没有 trajectory_runtime_events 表 —— "
                    "OpenClaw 的 schema 可能变了，本次核算不完整")
                continue

            starts: dict[str, datetime] = {}
            pending: list[dict[str, Any]] = []
            for row in conn.execute(
                "SELECT event_json FROM trajectory_runtime_events ORDER BY seq"
            ):
                try:
                    ev = json.loads(row["event_json"])
                except (json.JSONDecodeError, TypeError):
                    continue
                typ, run_id = ev.get("type"), ev.get("runId")
                if not run_id:
                    continue
                if typ == "prompt.submitted":
                    if (t := _parse_ts(ev.get("ts"))):
                        starts[run_id] = t
                elif typ == "model.completed":
                    pending.append(ev)

            for ev in pending:
                run_id = ev["runId"]
                t0, t1 = starts.get(run_id), _parse_ts(ev.get("ts"))
                if t0 is None or t1 is None:
                    # 配不上对就跳过，不猜一个起点
                    continue
                if since and t1 < since:
                    continue
                u = ((ev.get("data") or {}).get("usage")) or {}
                cost = (u.get("cost") or {}).get("total")
                probe.turns.append(AgentTurn(
                    agent_id=agent_id,
                    session_key=ev.get("sessionKey") or "",
                    run_id=run_id,
                    model=ev.get("modelId") or "",
                    started_at=t0, ended_at=t1,
                    tokens_in=int(u.get("input") or 0),
                    tokens_out=int(u.get("output") or 0),
                    cache_read=int(u.get("cacheRead") or 0),
                    cache_write=int(u.get("cacheWrite") or 0),
                    cost_usd=float(cost) if cost is not None else 0.0,
                    stop_reason=(ev.get("data") or {}).get("stopReason"),
                ))
        except sqlite3.Error as e:
            probe.unavailable.append(f"{agent_id}: 读取失败 —— {e}")
        finally:
            conn.close()

    probe.turns.sort(key=lambda t: t.started_at)
    return probe


@dataclass(frozen=True)
class TaskRun:
    """OpenClaw 调度器记的一次执行 —— 与 `AgentTurn` 是**两份独立记录**。

    存在的意义就是能和 trajectory 对账：
    调度器说跑过、而 trajectory 里没有对应轮次，说明其中一份漏了。
    这种不一致必须报出来，不能让使用者看到一份残缺却完整模样的分解。
    """

    runtime: str
    agent_id: str
    status: str
    started_at: datetime | None
    ended_at: datetime | None


def list_agents() -> list[str]:
    """有轨迹库的 agent 名单。"""
    return [name for name, _ in _agent_dbs()]


def read_tool_calls(agent: str) -> tuple[list[ToolCall], list[str]]:
    """读出某个 agent 的全部工具调用。返回 (调用, 不可用原因)。"""
    db = OPENCLAW_HOME / "agents" / agent / "agent" / "openclaw-agent.sqlite"
    if not db.is_file():
        return [], [f"{agent}: 找不到 {db.name}"]
    out: list[ToolCall] = []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as e:
        return [], [f"{agent}: 打不开 {db.name} —— {e}"]
    try:
        for row in conn.execute(
                "SELECT event_json FROM trajectory_runtime_events ORDER BY seq"):
            try:
                ev = json.loads(row["event_json"])
            except json.JSONDecodeError:
                continue
            if ev.get("type") != "tool.call":
                continue
            at = _parse_ts(ev.get("ts"))
            if at is None:
                continue
            d = ev.get("data") or {}
            raw = d.get("args") or d.get("input") or {}
            out.append(ToolCall(
                agent_id=agent,
                session_key=ev.get("sessionKey") or "?",
                at=at,
                name=d.get("name") or d.get("toolName") or "?",
                args_len=len(json.dumps(raw, ensure_ascii=False)),
                command=str(raw.get("command", "")) if isinstance(raw, dict) else "",
            ))
    except sqlite3.Error as e:
        return out, [f"{agent}: 读 trajectory 出错 —— {e}"]
    finally:
        conn.close()
    return out, []


def read_task_runs(*, since: datetime | None = None) -> tuple[list[TaskRun], list[str]]:
    """读运行时调度器的执行记录。返回 (记录, 不可用原因)。"""
    db = OPENCLAW_HOME / "state" / "openclaw.sqlite"
    if not db.is_file():
        return [], [f"运行时状态库不存在：{db}"]
    out: list[TaskRun] = []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as e:
        return [], [f"打不开状态库：{e}"]
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "task_runs" not in names:
            return [], ["状态库里没有 task_runs 表 —— OpenClaw schema 可能变了"]

        def ms(v: Any) -> datetime | None:
            try:
                # 显式 CN_TZ，不用系统本地时区 —— 换台机器就会变
                return datetime.fromtimestamp(int(v) / 1000, tz=CN_TZ)
            except (TypeError, ValueError):
                return None

        for r in conn.execute(
            "SELECT runtime, agent_id, status, started_at, ended_at FROM task_runs"
        ):
            t0 = ms(r["started_at"])
            if since and t0 and t0 < since:
                continue
            out.append(TaskRun(runtime=r["runtime"] or "", agent_id=r["agent_id"] or "",
                               status=r["status"] or "", started_at=t0,
                               ended_at=ms(r["ended_at"])))
    except sqlite3.Error as e:
        return out, [f"读 task_runs 失败：{e}"]
    finally:
        conn.close()
    return out, []
