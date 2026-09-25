"""Data Run 状态机 —— 一次数据任务从收到到终态经过哪些格子。

取自外部 P3-0/P3-1 实现包并加上理由。**照 `domain/run.py::RunState` 的形状**：
一份权威（`StrEnum`）+ 内省派生（`frozenset`/合法转移表），改状态只改一处。

🔴 Data Run 与 Decision Run 是两条独立生命周期
-----------------------------------------------
它们看起来很像（都有 append-only 事件流、CAS、`UNIQUE(run_id, seq)`），
但生命周期完全不同：一次**采集**的失败不该影响一次**决策**的状态机，
反之亦然。共用一套状态会让「这次决策失败了」和「昨晚的日线没取到」
长成同一件事。

⚠️ 终态与 `DataRunStatus`（`contracts.py`）**值域一致但不是同一个东西**：
这里是状态机的格子，那里是对外汇报的终态 + 退出码映射。
`DataRunStatus` 的 `SKIPPED_UP_TO_DATE` 在这里叫 `SKIPPED` ——
状态机只关心「跳过了」，为什么跳过是汇报层的事。
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "DataRunState",
    "DATA_RUN_STATES",
    "DATA_RUN_INITIAL_STATE",
    "DATA_RUN_TERMINAL_STATES",
    "DATA_RUN_LEGAL_TRANSITIONS",
]


class DataRunState(StrEnum):
    """状态的**唯一手写处**。下面所有集合从它派生。"""

    RECEIVED = "RECEIVED"
    FETCHING = "FETCHING"
    RAW_STORED = "RAW_STORED"
    NORMALIZING = "NORMALIZING"
    VALIDATING = "VALIDATING"
    PUBLISHING = "PUBLISHING"
    SNAPSHOT_CREATED = "SNAPSHOT_CREATED"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


DATA_RUN_STATES: frozenset[str] = frozenset(item.value for item in DataRunState)
DATA_RUN_INITIAL_STATE: str = DataRunState.RECEIVED.value

DATA_RUN_TERMINAL_STATES: frozenset[str] = frozenset({
    DataRunState.COMPLETED.value,
    DataRunState.PARTIAL.value,
    DataRunState.QUARANTINED.value,
    DataRunState.FAILED.value,
    DataRunState.SKIPPED.value,
    DataRunState.CANCELLED.value,
    DataRunState.TIMEOUT.value,
})

#: 主干：一次顺利的采集依次经过的格子。
_MAIN = (
    ("RECEIVED", "FETCHING"),
    ("FETCHING", "RAW_STORED"),
    ("RAW_STORED", "NORMALIZING"),
    ("NORMALIZING", "VALIDATING"),
    ("VALIDATING", "PUBLISHING"),
    ("PUBLISHING", "SNAPSHOT_CREATED"),
    ("SNAPSHOT_CREATED", "COMPLETED"),
)

#: 🔴 失败/取消/超时可以从**任何在途格子**发生 —— 派生而不是手写，
#:    否则加一个新的中间格子时必然漏掉它的三条出口。
_IN_FLIGHT = tuple(s for s in DATA_RUN_STATES if s not in DATA_RUN_TERMINAL_STATES)
_FAILURE = tuple((s, t) for s in _IN_FLIGHT for t in ("FAILED", "CANCELLED", "TIMEOUT"))

#: 质量裁定的两个非 COMPLETE 出口 —— 只能从 VALIDATING 出来。
#: 「数据不全」「源冲突」都是**校验的结论**，不是发布阶段的意外。
_QUALITY = (("VALIDATING", "PARTIAL"), ("VALIDATING", "QUARANTINED"))

#: 幂等跳过只可能在一开始就判定（已有 COMPLETE 的同版本）。
_SKIP = (("RECEIVED", "SKIPPED"),)

DATA_RUN_LEGAL_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    _MAIN + _FAILURE + _QUALITY + _SKIP)
