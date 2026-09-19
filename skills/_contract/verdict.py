"""AgentVerdict —— 一个 Specialist Agent 的结构化回答。

🔴 本模块落地三条契约铁律，全部在 `__post_init__` 里**拒绝构造**而不是记日志：

  铁律 1  `UNKNOWN` ≠ `PASS`。算不出来必须说算不出来。
          ⇒ `missing` 非空时不允许 `verdict='PASS'`，也不允许 `status='completed'`。
  铁律 2  `missing` 非空 ⇒ 不得给买入结论（在 DecisionCard 层再挡一道）。
  铁律 3  每个 `result` 字段必须能追到至少一条 `Evidence`。
          ⇒ `result` 的键集合必须被 `evidence` 的 `field` 集合覆盖。

为什么是「拒绝构造」而不是「打个 warning」：
本项目最优先防范的失败模式是**静默 fail-open** —— 检查在数据缺失时悄悄放行，
而放行与通过的日志长得一模一样。让非法状态**根本无法被表示**，是唯一可靠的办法。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Literal, get_args

from .evidence import Evidence

__all__ = [
    "AgentVerdict",
    "VerdictStatus",
    "VerdictLevel",
    "TASK_ID_RE",
    "new_task_id",
]

VerdictStatus = Literal["completed", "partial", "failed"]
VerdictLevel = Literal["PASS", "WARNING", "BLOCK", "UNKNOWN"]

_STATUSES: frozenset[str] = frozenset(get_args(VerdictStatus))
_LEVELS: frozenset[str] = frozenset(get_args(VerdictLevel))

#: 任务号格式 BIGA-YYYYMMDD-NNN
TASK_ID_RE = re.compile(r"^BIGA-\d{8}-\d{3}$")


def new_task_id(seq: int, *, day: str | None = None) -> str:
    """生成 ``BIGA-YYYYMMDD-NNN`` 形式的任务号。"""
    from .evidence import now_cn

    day = day or now_cn().strftime("%Y%m%d")
    if not re.fullmatch(r"\d{8}", day):
        raise ValueError(f"day 必须是 YYYYMMDD，收到 {day!r}")
    if not 0 <= seq <= 999:
        raise ValueError(f"seq 必须在 0..999，收到 {seq}")
    return f"BIGA-{day}-{seq:03d}"


@dataclass
class AgentVerdict:
    """Specialist → Supervisor 的唯一返回结构。

    Attributes:
        task_id: ``BIGA-YYYYMMDD-NNN``。
        agent: agentId，例如 ``emotion``。
        status: 这次执行本身是否完成。``partial`` = 跑完了但有字段没算出来。
        verdict: 业务判断。``UNKNOWN`` 表示**判断不出来**，与 ``PASS`` 严格区分。
        result: 结构化结论。每个键必须有对应的 Evidence（铁律 3）。
        confidence: 0..1。
        evidence: 支撑 `result` 的证据。
        warnings: 不阻断但需要人看到的问题。
        missing: 🔴 **必填项里没算出来的那些**。非空即代表结论不完整。
        elapsed_ms: 本次耗时，用于延迟预算核算。
    """

    task_id: str
    agent: str
    status: VerdictStatus
    verdict: VerdictLevel
    result: dict[str, Any] = dc_field(default_factory=dict)
    confidence: float = 0.0
    evidence: list[Evidence] = dc_field(default_factory=list)
    warnings: list[str] = dc_field(default_factory=list)
    missing: list[str] = dc_field(default_factory=list)
    elapsed_ms: int = 0

    def __post_init__(self) -> None:
        if not TASK_ID_RE.match(self.task_id):
            raise ValueError(
                f"task_id 必须形如 BIGA-YYYYMMDD-NNN，收到 {self.task_id!r}"
            )
        if not isinstance(self.agent, str) or not self.agent.strip():
            raise ValueError(f"agent 必须是非空字符串，收到 {self.agent!r}")
        if self.status not in _STATUSES:
            raise ValueError(f"status 必须是 {sorted(_STATUSES)} 之一，收到 {self.status!r}")
        if self.verdict not in _LEVELS:
            raise ValueError(f"verdict 必须是 {sorted(_LEVELS)} 之一，收到 {self.verdict!r}")
        if not isinstance(self.confidence, (int, float)) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence 必须在 0..1，收到 {self.confidence!r}")
        if self.elapsed_ms < 0:
            raise ValueError(f"elapsed_ms 不能为负，收到 {self.elapsed_ms}")
        for e in self.evidence:
            if not isinstance(e, Evidence):
                raise TypeError(
                    f"evidence 必须全部是 _contract.Evidence，收到 {type(e).__name__} —— "
                    "不许自建第二套证据结构（铁律 4）"
                )

        # --- 铁律 3：result 的每个键都要有证据 ---
        covered = {e.field for e in self.evidence}
        orphan = sorted(set(self.result) - covered)
        if orphan:
            raise ValueError(
                f"[{self.agent}] result 字段无证据支撑: {orphan} —— "
                f"已有证据覆盖 {sorted(covered)}。无据之言不入 Card（铁律 3）"
            )

        # --- 铁律 1：算不出来不许说 PASS ---
        if self.missing:
            if self.verdict == "PASS":
                raise ValueError(
                    f"[{self.agent}] missing={self.missing} 非空却给出 verdict='PASS' —— "
                    "UNKNOWN ≠ PASS，算不出来必须说算不出来（铁律 1）"
                )
            if self.status == "completed":
                raise ValueError(
                    f"[{self.agent}] missing={self.missing} 非空却声称 status='completed' —— "
                    "应为 'partial' 或 'failed'"
                )

        # --- 反向：声称 UNKNOWN 就必须说清楚缺什么 ---
        if self.verdict == "UNKNOWN" and not self.missing:
            raise ValueError(
                f"[{self.agent}] verdict='UNKNOWN' 但 missing 为空 —— "
                "判断不出来必须列出缺了什么，否则 Card 上的「缺失项」是空的，"
                "读者看到的就是一个没有理由的 UNKNOWN"
            )

        # --- failed 不该带结论 ---
        if self.status == "failed" and self.verdict != "UNKNOWN":
            raise ValueError(
                f"[{self.agent}] status='failed' 时 verdict 只能是 'UNKNOWN'，"
                f"收到 {self.verdict!r}"
            )

    # --- 便捷查询 ---

    def evidence_for(self, field: str) -> list[Evidence]:
        """取支撑某个 result 字段的全部证据。"""
        return [e for e in self.evidence if e.field == field]

    @property
    def max_staleness_sec(self) -> int:
        """最旧的一条证据有多旧。全部证据缺失时返回 -1。"""
        return max((e.staleness_sec for e in self.evidence), default=-1)

    # --- 序列化 ---

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent": self.agent,
            "status": self.status,
            "verdict": self.verdict,
            "result": self.result,
            "confidence": self.confidence,
            "evidence": [e.to_dict() for e in self.evidence],
            "warnings": list(self.warnings),
            "missing": list(self.missing),
            "elapsed_ms": self.elapsed_ms,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentVerdict":
        return cls(
            task_id=d["task_id"],
            agent=d["agent"],
            status=d["status"],
            verdict=d["verdict"],
            result=d.get("result", {}),
            confidence=d.get("confidence", 0.0),
            evidence=[Evidence.from_dict(x) for x in d.get("evidence", [])],
            warnings=list(d.get("warnings", [])),
            missing=list(d.get("missing", [])),
            elapsed_ms=d.get("elapsed_ms", 0),
        )
