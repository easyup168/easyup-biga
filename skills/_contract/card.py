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

from .missing import MissingItem
from .verdict import VETO_STANCE, AgentVerdict

__all__ = ["DecisionCard", "CardStatus", "DECISION_ID_RE"]

CardStatus = Literal["BUY", "WAIT", "AVOID", "BLOCK"]
_CARD_STATUSES: frozenset[str] = frozenset(get_args(CardStatus))

DECISION_ID_RE = re.compile(r"^BIGA-\d{8}-\d{3}$")

#: 卡面上单个值的最大宽度。
#:
#: 🔴 这不是排版偏好，是**定位**：Card 是给人看的一页纸
#: （上游文档 §11：优先展示证据项、风险项、缺失项、状态）。
#:
#: 实测踩到：`news` 的 `items` 字段装着 67 条快讯原文，
#: 渲染出来的卡片 **34.5 KB** —— 它在技术上完整，在用途上作废了。
#: 而且没有任何东西报错，因为「把 result 渲染出来」这件事本身是对的。
#:
#: 完整值永远在 `card_json` 与 `agent_verdicts` 里，截断只发生在**显示层**。
_MAX_VALUE_WIDTH = 72


def _brief(value: Any) -> str:
    """把一个值压成一行。列表只报条数与首项，长文本截断。"""
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        head = _brief(value[0])
        return f"[{len(value)} 项] {head}" if len(value) > 1 else f"[1 项] {head}"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}={value[k]}" for k in list(value)[:3]) + (
            ", …}" if len(value) > 3 else "}")
    t = str(value)
    return t if len(t) <= _MAX_VALUE_WIDTH else t[:_MAX_VALUE_WIDTH - 1] + "…"


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
    missing: list[MissingItem] = dc_field(default_factory=list)
    generated_at: str = ""
    elapsed_ms: int = 0

    def __post_init__(self) -> None:
        self.missing = [MissingItem.coerce(m) for m in self.missing]

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

        # --- 制衡层的否决权 ---
        # 🔴 判据是 stance 而不是 verdict：verdict 只说数据全不全。
        #    一个字段装不下「数据完整」和「我要否决」两件事。
        blockers = [v.agent for v in self.verdicts if v.stance == VETO_STANCE]
        if blockers and self.status == "BUY":
            raise ValueError(
                f"{blockers} 给出 stance={VETO_STANCE!r} 却仍然 status='BUY' —— "
                "制衡层的否决权不可被合成阶段绕过"
            )
        # 🔴 反向：有人否决，Card 的状态就必须体现出来。
        #    允许 AVOID / BLOCK，不允许 WAIT —— WAIT 的意思是「再看看」，
        #    而否决的意思是「不要做」，把后者显示成前者就是软化了制衡层。
        if blockers and self.status not in ("AVOID", "BLOCK"):
            raise ValueError(
                f"{blockers} 给出 stance={VETO_STANCE!r}，Card 状态却是 "
                f"{self.status!r} —— 否决必须体现为 AVOID 或 BLOCK"
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
                f"{k}={_brief(v.result[k])}" for k in sorted(v.result)[:3]
            ) or "—"
            # 🔴 verdict 与 stance 并列显示，因为它们回答的是两个不同的问题：
            #    verdict=数据全不全，stance=市场偏哪边。
            #    只显示前者，读者会把「PASS」误读成「看好」。
            lines.append(
                f"{v.agent:<12} {v.verdict:<8} {(v.stance or '—'):<6} {summary}")
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
                lines.append(f"      [{m.code}]")
        else:
            lines.append("缺失项：无")

        # 🔴 warning 必须上卡。
        #
        #    实测（BIGA-20260921-017）：四个 Agent 各发了一条 warning，
        #    **一条都没显示**。其中一条是
        #    「涨跌家数接口不返回交易日字段，其 as_of 是按日线的交易日推断的」——
        #    而卡面上那行证据写着 `as_of 09-18 15:00`，看起来像板上钉钉。
        #
        #    warning 与 missing 的分工是「这个数能用但要注意」vs「这个数没有」。
        #    把前者藏起来，等于只保留了它的名字。
        warns = [(v.agent, w) for v in self.verdicts for w in v.warnings]
        if warns:
            lines.append("")
            lines.append(f"⚠ 提请注意（{len(warns)}）")
            for agent, w in warns:
                lines.append(f"  · [{agent}] {w}")

        lines.append("")
        lines.append("证据")
        any_ev = False
        for v in self.verdicts:
            for e in v.evidence:
                any_ev = True
                lines.append(
                    f"  [{v.agent}] {e.display_label} = {_brief(e.value)}"
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
            "missing": [m.to_dict() for m in self.missing],
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
            missing=[MissingItem.coerce(m) for m in d.get("missing", [])],
            generated_at=d.get("generated_at", ""),
            elapsed_ms=d.get("elapsed_ms", 0),
        )
