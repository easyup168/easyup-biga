"""DecisionCard —— 交给人的最终产物。

渲染原则（上游文档 §11）：
> 优先展示**证据项、风险项、缺失项、状态**，而不是一个看似精确的模型分数。

内部可以保留评分用于实验与回测，**但不上 Card**。

🔴 本模块落地铁律 2：`missing` 非空 ⇒ 不得给 `BUY`。
另外加一道：任一 Verdict 为 `BLOCK` ⇒ 不得给 `BUY`（Risk Agent 的否决权）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Literal, get_args

from .verdict import AgentVerdict

__all__ = ["DecisionCard", "CardStatus", "DECISION_ID_RE"]

CardStatus = Literal["BUY", "WAIT", "AVOID", "BLOCK"]
_CARD_STATUSES: frozenset[str] = frozenset(get_args(CardStatus))

DECISION_ID_RE = re.compile(r"^BIGA-\d{8}-\d{3}$")


@dataclass
class DecisionCard:
    """Supervisor 合成的决策卡。

    Attributes:
        decision_id: ``BIGA-YYYYMMDD-NNN``。
        status: 卡片状态。**不是交易指令** —— 最终动作由人决定。
        headline: 核心矛盾，一句话。
        verdicts: 全部 Specialist 的回答（回放的原料）。
        missing: 缺失项汇总。必须 ⊇ 各 Verdict 的 missing 之并集。
        synthesis: 合成说明。
        model_ref: 做这次合成的模型标识，回放对比时要用。
        generated_at: 生成时刻（ISO8601，带时区）。
        elapsed_ms: 端到端耗时。
    """

    decision_id: str
    status: CardStatus
    headline: str
    verdicts: list[AgentVerdict]
    synthesis: str
    model_ref: str
    missing: list[str] = dc_field(default_factory=list)
    generated_at: str = ""
    elapsed_ms: int = 0

    def __post_init__(self) -> None:
        if not DECISION_ID_RE.match(self.decision_id):
            raise ValueError(
                f"decision_id 必须形如 BIGA-YYYYMMDD-NNN，收到 {self.decision_id!r}"
            )
        if self.status not in _CARD_STATUSES:
            raise ValueError(
                f"status 必须是 {sorted(_CARD_STATUSES)} 之一，收到 {self.status!r}"
            )
        if not self.verdicts:
            raise ValueError("verdicts 不能为空 —— 没有任何 Specialist 回答就不该出卡")
        for v in self.verdicts:
            if not isinstance(v, AgentVerdict):
                raise TypeError(
                    f"verdicts 必须全部是 _contract.AgentVerdict，收到 {type(v).__name__} —— "
                    "不许自建第二套 Verdict 结构（铁律 4）"
                )
        if not self.headline.strip():
            raise ValueError("headline 不能为空 —— Card 必须给出一句话的核心矛盾")
        if not self.model_ref.strip():
            raise ValueError("model_ref 不能为空 —— 回放对比要靠它标定这次是谁合成的")

        if not self.generated_at:
            from .evidence import now_cn

            self.generated_at = now_cn().isoformat()

        # --- 缺失项必须完整上浮：漏报一条，Card 就在掩盖它自己不知道的事 ---
        upstream = {m for v in self.verdicts for m in v.missing}
        dropped = sorted(upstream - set(self.missing))
        if dropped:
            raise ValueError(
                f"Verdict 报告的缺失项没有上浮到 Card: {dropped} —— "
                "缺失项必须逐条显示，汇总时丢弃等于静默 fail-open"
            )

        # --- 铁律 2 ---
        if self.missing and self.status == "BUY":
            raise ValueError(
                f"missing={self.missing} 非空却给出 status='BUY' —— "
                "证据不完整时不得给买入结论（铁律 2）"
            )

        # --- Risk Agent 的否决权 ---
        blockers = [v.agent for v in self.verdicts if v.verdict == "BLOCK"]
        if blockers and self.status == "BUY":
            raise ValueError(
                f"{blockers} 给出 BLOCK 却仍然 status='BUY' —— 制衡层的否决权不可被合成阶段绕过"
            )

    # --- 便捷查询 ---

    def verdict_of(self, agent: str) -> AgentVerdict | None:
        for v in self.verdicts:
            if v.agent == agent:
                return v
        return None

    @property
    def is_complete(self) -> bool:
        """所有 Specialist 都完整作答、且无缺失项。"""
        return not self.missing and all(v.status == "completed" for v in self.verdicts)

    # --- 渲染 ---

    def render(self, width: int = 62) -> str:
        """渲染成上游文档 §11 那张纯文本卡片。"""
        rule = "─" * width
        head = (
            f"BIGA DECISION CARD   {self.decision_id}   "
            f"{self.generated_at[11:19]}   耗时 {self.elapsed_ms / 1000:.0f}s"
        )
        lines = [head, rule]

        # 各 Agent 一行摘要。
        # 🔴 按键名排序取前 3 个，而不是按插入顺序 ——
        # Card 落库时 JSON 是排过序的，回放取回来的 result 顺序与在线不同。
        # 用插入顺序会让「在线 vs 回放」的渲染结果无谓地不一致，
        # 而回放 diff 里的每一处差异都应该是真实差异。
        for v in self.verdicts:
            summary = "; ".join(
                f"{k}={v.result[k]}" for k in sorted(v.result)[:3]
            ) or "—"
            lines.append(f"{v.agent:<12} {v.verdict:<8} {summary}")
        lines.append("")
        lines.append(f"状态：{self.status}")
        lines.append("")
        lines.append(f"核心矛盾：{self.headline}")

        # 🔴 缺失项永远显示，哪怕是 0 条 —— 「没有缺失项」本身是一条信息
        lines.append("")
        if self.missing:
            lines.append(f"⚠ 缺失项（{len(self.missing)}）")
            for m in self.missing:
                lines.append(f"  · {m}")
        else:
            lines.append("缺失项：无")

        lines.append("")
        lines.append("证据")
        any_ev = False
        for v in self.verdicts:
            for e in v.evidence:
                any_ev = True
                lines.append(
                    f"  [{v.agent}] {e.display_label} = {e.value}"
                    f"   as_of {e.as_of.strftime('%m-%d %H:%M')}   src {e.source}"
                )
        if not any_ev:
            lines.append("  （无）")

        if self.synthesis.strip():
            lines.append("")
            lines.append(self.synthesis.strip())

        lines.append(rule)
        lines.append(f"model_ref: {self.model_ref}   ·   本卡为决策辅助，不构成投资建议")
        return "\n".join(lines)

    # --- 序列化 ---

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "status": self.status,
            "headline": self.headline,
            "verdicts": [v.to_dict() for v in self.verdicts],
            "missing": list(self.missing),
            "synthesis": self.synthesis,
            "model_ref": self.model_ref,
            "generated_at": self.generated_at,
            "elapsed_ms": self.elapsed_ms,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DecisionCard":
        return cls(
            decision_id=d["decision_id"],
            status=d["status"],
            headline=d["headline"],
            verdicts=[AgentVerdict.from_dict(x) for x in d["verdicts"]],
            synthesis=d.get("synthesis", ""),
            model_ref=d["model_ref"],
            missing=list(d.get("missing", [])),
            generated_at=d.get("generated_at", ""),
            elapsed_ms=d.get("elapsed_ms", 0),
        )
