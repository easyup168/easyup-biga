"""OpenClawRuntimeAdapter —— Specialist 生命周期的唯一入口（设计文档 §6 批 C）。

它把「Python 编排器怎么起一个 Specialist、怎么等、怎么取消、怎么看状态」收成
四个方法，**上层（C-II 的 Orchestrator）只跟这四个方法打交道**，不碰运行时的
MCP 工具名、不碰 JSON-RPC、不碰运行时的原始状态措辞。

🔴 状态归一化是这一层的核心职责
--------------------------------
`sessions_spawn` / `agents_wait` / `subagents` 返回的是运行时**自己的**措辞
（`accepted` / `queued` / `running` / `done` / `killed` / `forbidden`…），
会随运行时版本变。Adapter 对外只暴露 `SpawnStatus` 这一小组自定义状态，
内部做翻译 —— 运行时改一个词，只改这里的映射表，不波及整条编排链。

🔴 R-3：认不出来的原始状态映射到 `UNKNOWN`，**绝不当成 `SUCCEEDED`**。
   静默把未知当成功，正是本项目最优先防范的 fail-open。

grant 生命周期 / groupId / token 用量三条硬约束见 `mcp.py` 与设计文档 §7。
token 用量随 `SpawnResult.usage` 带出来，**不在这里写库** —— C-I 不接任何 run，
没有 run_events 可写；写库是 C-II 的事（它有 run 上下文）。在没有消费方之前
建写入路径就是 L-1。
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from typing import Any, Iterator

from .mcp import DEFAULT_BIGA, Grant, MCPClient, MCPError

__all__ = [
    "SpawnStatus",
    "SPAWN_STATUSES",
    "TERMINAL_SPAWN_STATUSES",
    "normalize_status",
    "SpawnHandle",
    "SpawnResult",
    "OpenClawRuntimeAdapter",
    "SpawnStartError",
]


class SpawnStatus:
    """Adapter 对外暴露的**归一化**状态 —— 与运行时原始措辞解耦。"""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    #: 🔴 认不出来的原始状态落这里，**不落 SUCCEEDED**（R-3）。
    UNKNOWN = "unknown"


SPAWN_STATUSES: frozenset[str] = frozenset({
    SpawnStatus.RUNNING, SpawnStatus.SUCCEEDED, SpawnStatus.FAILED,
    SpawnStatus.TIMEOUT, SpawnStatus.CANCELLED, SpawnStatus.UNKNOWN,
})

TERMINAL_SPAWN_STATUSES: frozenset[str] = frozenset({
    SpawnStatus.SUCCEEDED, SpawnStatus.FAILED, SpawnStatus.TIMEOUT,
    SpawnStatus.CANCELLED,
})

#: 运行时原始措辞 → 归一化状态。实测见过的都在这里；没见过的走 UNKNOWN。
#: 🔴 加新映射前先确认那个词真的从运行时来过，别凭想象加 —— 想当然把某个
#:    未知词当成 SUCCEEDED 就是 R-3 要防的洞。
_RAW_TO_STATUS: dict[str, str] = {
    # 起步 / 在途
    "accepted": SpawnStatus.RUNNING,
    "queued": SpawnStatus.RUNNING,
    "running": SpawnStatus.RUNNING,
    "in_progress": SpawnStatus.RUNNING,
    "active": SpawnStatus.RUNNING,
    "pending": SpawnStatus.RUNNING,
    # 成功
    "done": SpawnStatus.SUCCEEDED,
    "completed": SpawnStatus.SUCCEEDED,
    "succeeded": SpawnStatus.SUCCEEDED,
    "success": SpawnStatus.SUCCEEDED,
    "ok": SpawnStatus.SUCCEEDED,
    # 失败
    "failed": SpawnStatus.FAILED,
    "error": SpawnStatus.FAILED,
    "errored": SpawnStatus.FAILED,
    "forbidden": SpawnStatus.FAILED,
    "not_found": SpawnStatus.FAILED,
    "not_owner": SpawnStatus.FAILED,
    "rejected": SpawnStatus.FAILED,
    # 超时
    "timeout": SpawnStatus.TIMEOUT,
    "timed_out": SpawnStatus.TIMEOUT,
    # 取消（实测：cancel 之后 recent[] 里是 "killed"）
    "killed": SpawnStatus.CANCELLED,
    "cancelled": SpawnStatus.CANCELLED,
    "canceled": SpawnStatus.CANCELLED,
    "aborted": SpawnStatus.CANCELLED,
}


def normalize_status(raw: Any) -> str:
    """把运行时原始状态词翻译成 `SpawnStatus`。认不出来 ⇒ `UNKNOWN`（R-3）。"""
    if raw is None:
        return SpawnStatus.UNKNOWN
    return _RAW_TO_STATUS.get(str(raw).strip().lower(), SpawnStatus.UNKNOWN)


class SpawnStartError(RuntimeError):
    """`start()` 没能把一个 spawn 起起来（运行时结构化拒绝 / 响应里没有 runId）。

    🔴 让它当场抛，而不是返回一个没有 runId 的坏 handle —— 一个起不来的 spawn
    被后续 `wait()` 拿去等，只会以一种更难排查的方式失败。
    """


@dataclass(frozen=True)
class SpawnHandle:
    """一次 spawn 的引用。`run_id` 是 `agents_wait` 用的 id；`session_key` 是
    运行时给的 `childSessionKey`（排查 / 取消关联用）。"""

    run_id: str
    agent: str
    task_id: str
    group_id: str
    session_key: str


@dataclass(frozen=True)
class SpawnResult:
    """一次 spawn 的归一化结果。

    Attributes:
        status: `SpawnStatus` 之一（归一化，**不是**运行时原始词）。
        result: Specialist 返回的结构化/文本结果（成功时）。
        usage: `{"input": n, "output": n}`，来自 `agents_wait` 的 usage。
            🔴 只带出来，**不在 Adapter 里写库**（C-II 写进 run_events.detail）。
        error: 失败时的原因（结构化文本），成功时为 None。
        raw_status: 运行时原始状态词，仅供排查；判断一律用 `status`。
    """

    handle: SpawnHandle
    status: str
    result: Any | None = None
    usage: dict[str, int] | None = None
    error: str | None = None
    raw_status: str | None = None


class OpenClawRuntimeAdapter:
    """Specialist 生命周期的唯一入口：`start` / `wait` / `cancel` / `status`。

    通常这样拿到一个已握手、grant 会自动清理的实例::

        with OpenClawRuntimeAdapter.attach(
                "agent:main:orchestrator-<run_id>", ttl_ms=800_000) as adapter:
            h = adapter.start("market", task_id, task_text, group_id=gid)
            [res] = adapter.wait([h], timeout_sec=300)
    """

    def __init__(self, client: MCPClient):
        self._c = client

    # ── grant + 握手 + 清理，一把梭 ──────────────────────────────────────
    @classmethod
    @contextlib.contextmanager
    def attach(
        cls,
        session_key: str,
        *,
        ttl_ms: int,
        biga: str = DEFAULT_BIGA,
        timeout: float = 60.0,
    ) -> Iterator["OpenClawRuntimeAdapter"]:
        """铸 grant → 建 client → 握手 → 用完删 `.mcp.json`。

        🔴 `ttl_ms` 必须 ≥ 本次运行的总预算，否则长跑到一半 grant 过期（§7-2）。
        """
        with Grant.mint(session_key, ttl_ms=ttl_ms, biga=biga) as grant:
            client = MCPClient(grant.url, grant.token, timeout=timeout)
            client.initialize()
            yield cls(client)

    # ── start ────────────────────────────────────────────────────────────
    def start(
        self,
        agent: str,
        task_id: str,
        task: str,
        *,
        group_id: str,
        run_timeout_sec: int | None = None,
        task_name: str | None = None,
        output_schema: dict | None = None,
    ) -> SpawnHandle:
        """起一个 Specialist（collect 模式），返回它的 handle。

        🔴 `collect=true` + `group_id`：没有 requesting run 时 API 硬约束要求带
        `groupId`（§7-1）。同一批 fan-out 的多个 spawn **共用一个 group_id**。

        `task` 是给 Specialist 的完整指令文本（调用方负责把 `task_id` 写进去，
        那是 ORCHESTRATION.md 的口径，不是 Adapter 的职责）。
        """
        args: dict[str, Any] = {
            "agentId": agent,
            "context": "isolated",
            "collect": True,
            "groupId": group_id,
            "task": task,
        }
        if run_timeout_sec is not None:
            args["runTimeoutSeconds"] = run_timeout_sec
        if task_name is not None:
            args["taskName"] = task_name
        if output_schema is not None:
            args["outputSchema"] = output_schema
        try:
            res = self._c.call_tool("sessions_spawn", args)
        except MCPError as e:
            raise SpawnStartError(f"[{agent}] spawn 被运行时拒绝：{e}") from e
        run_id = res.get("runId")
        if not run_id:
            raise SpawnStartError(
                f"[{agent}] spawn 响应里没有 runId —— 起没起来判不了。响应：{res}")
        return SpawnHandle(
            run_id=run_id, agent=agent, task_id=task_id, group_id=group_id,
            session_key=res.get("childSessionKey") or res.get("sessionKey") or "")

    # ── wait ───────────────────────────────────────────────────────────────
    def wait(self, handles: list[SpawnHandle], timeout_sec: float) -> list[SpawnResult]:
        """等一批 handle 全部到终态或整体超时。返回顺序与 `handles` 一致。

        `agents_wait` 一次「任一完成就返回」，所以这里循环收集，直到全部到齐或
        整体预算耗尽。到期仍没回来的，status 记 `TIMEOUT`（不是 UNKNOWN —— 我们
        知道它是「等超时了」，这是有信息的）。
        """
        by_id = {h.run_id: h for h in handles}
        results: dict[str, SpawnResult] = {}
        deadline = time.monotonic() + timeout_sec
        while by_id and time.monotonic() < deadline:
            slice_sec = max(1, int(min(30, deadline - time.monotonic())))
            try:
                resp = self._c.call_tool(
                    "agents_wait", {"ids": list(by_id), "timeoutSeconds": slice_sec})
            except MCPError as e:
                # 整批 wait 被拒（比如 id 全非法）—— 把还没收到的都记成 FAILED，
                # 不静默丢。
                for rid, h in list(by_id.items()):
                    results[rid] = SpawnResult(
                        handle=h, status=SpawnStatus.FAILED,
                        error=f"agents_wait 失败：{e}", raw_status=None)
                    del by_id[rid]
                break
            for entry in resp.get("completed", []) or []:
                rid = entry.get("runId")
                h = by_id.pop(rid, None)
                if h is None:
                    continue
                raw = entry.get("status")
                results[rid] = SpawnResult(
                    handle=h, status=normalize_status(raw),
                    result=entry.get("result"),
                    usage=_usage_of(entry.get("usage")),
                    error=entry.get("error") if normalize_status(raw) != SpawnStatus.SUCCEEDED else None,
                    raw_status=raw)
        for rid, h in by_id.items():  # 到期还没回来的
            results[rid] = SpawnResult(
                handle=h, status=SpawnStatus.TIMEOUT,
                error=f"等待超过 {int(timeout_sec)}s 仍未返回", raw_status=None)
        return [results[h.run_id] for h in handles]

    # ── status ───────────────────────────────────────────────────────────
    def status(self, handle: SpawnHandle) -> str:
        """查一个 handle 现在的**归一化**状态（不是运行时原始词）。

        🔴 对外永远返回 `SpawnStatus` 之一 —— 上层不该看到运行时的原始措辞。
        """
        listing = self._c.call_tool("subagents", {"action": "list", "recentMinutes": 60})
        raw = _raw_status_for(listing, handle.run_id)
        return normalize_status(raw)

    # ── cancel ─────────────────────────────────────────────────────────────
    def cancel(self, handle: SpawnHandle) -> None:
        """取消一个 handle 对应的 spawn。

        🔴 运行时的 cancel 只认 `subagents.tasks[].taskId`（实测：传 runId /
        taskName 都被 `Task outside session tree` 拒），而那个 taskId 与 spawn
        返回的 runId **没有直接关联字段**。用 `active[]`（带 runId）与 `tasks[]`
        （带 taskId）的**同序对应**把 runId 映射到 taskId。

        ⚠️ `active[]` 的顺序**不是** spawn 顺序（实测两个 spawn 在 active[] 里是
        反的）—— 所以必须按 `active[i].runId == run_id` 定位 i，再取 `tasks[i]`，
        绝不能按「我第几个 spawn 的」去 index。这条对应由
        `tools/verify/adapter_spike.py cancel` 实证过：取消 h0，死的正是 h0、
        h1 仍在跑。找不到就当它已经不在跑了，静默返回（取消一个已结束的东西不是错误）。
        """
        listing = self._c.call_tool("subagents", {"action": "list", "recentMinutes": 60})
        task_id = _task_id_for(listing, handle.run_id)
        if task_id is None:
            return  # 已经不在活跃列表里 —— 无可取消
        with contextlib.suppress(MCPError):
            self._c.call_tool("subagents", {"action": "cancel", "taskId": task_id})


def _usage_of(usage: Any) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    return {"input": int(usage.get("inputTokens", 0)),
            "output": int(usage.get("outputTokens", 0))}


def _active_and_tasks(listing: dict) -> tuple[list[dict], list[dict]]:
    return listing.get("active", []) or [], listing.get("tasks", []) or []


def _raw_status_for(listing: dict, run_id: str) -> Any:
    """从 subagents list 里取某个 runId 的原始状态：先看 active[]，再看 recent[]。"""
    for a in listing.get("active", []) or []:
        if a.get("runId") == run_id:
            return a.get("status") or (a.get("execution") or {}).get("state")
    for r in listing.get("recent", []) or []:
        if r.get("runId") == run_id:
            return r.get("status") or (r.get("execution") or {}).get("state")
    return None


def _task_id_for(listing: dict, run_id: str) -> str | None:
    """把 runId 映射到 cancel 要的 tasks[].taskId，靠 active[]/tasks[] 同序对应。

    实证依据：subagents list 的 `active[]`（keyed by runId）与 `tasks[]`
    （keyed by taskId）逐位对应同一个 subagent（P1 探针验证）。
    """
    active, tasks = _active_and_tasks(listing)
    for i, a in enumerate(active):
        if a.get("runId") == run_id and i < len(tasks):
            return tasks[i].get("taskId")
    return None
