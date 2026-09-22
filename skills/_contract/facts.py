"""FactBundle / AgentAssessment / AgentOutcome —— 把「事实」与「判断」拆开（批 E-I）。

要解决的断层（已有真实事故）
----------------------------
`AgentVerdict` 把两件产生方不同、时机不同的东西焊在一个 frozen dataclass 里：

  · **事实**（skill 算出来的：`result`/`evidence`/`data_completeness`/`missing`/
    `status`/`verdict`/`elapsed_ms`）—— skill 跑完那一刻就有，是机械记账；
  · **判断**（`stance`，Agent 给的）—— skill 跑完之后 Agent 才补得上。

焊在一起的代价，`amend_verdict.py` 的存在本身就是证据：没有它，Agent 想只加一个
判断，就只能把整份事实重打一遍 —— `BIGA-20260920-002` 那次，15 条 Evidence 的
`retrieved_at` 全部转述丢失（L-10）；F9 那次加 stance 让 Stage 1 延迟翻倍。

三个新类型
----------
    FactBundle       只装事实（AgentVerdict 减去 stance）。skill 产出它。
    AgentAssessment  只装判断（stance + 指回哪一份 FactBundle）。Agent 产出它，
                     **不抄事实**（只带 task_id/agent 标识 + fact_ref 行号，anti-L-10）。
    AgentOutcome     FactBundle + AgentAssessment 的组合视图。跨两型的铁律
                     （UNKNOWN 的事实上不许挂方向判断）在拼起来那一刻校验；
                     `to_agent_verdict()` 把它压回旧消费者认识的 AgentVerdict。

🔴 铁律不因为拆了就松：事实层的铁律走 `verdict.check_fact_invariants` **唯一实现**
   （FactBundle 与 AgentVerdict 共用），stance 走 `check_stance_*`。不各写一遍（L-3）。
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from types import MappingProxyType
from typing import Any, Mapping

from .evidence import Evidence
from .missing import MissingItem
from .verdict import (
    TASK_ID_RE,
    AgentVerdict,
    VerdictLevel,
    VerdictStatus,
    check_fact_invariants,
    check_stance_vocab,
    check_stance_vs_verdict,
)

__all__ = ["FactBundle", "AgentAssessment", "AgentOutcome", "LegacyAdapter"]


@dataclass(frozen=True)
class FactBundle:
    """skill 能独立算出来的一切 —— `AgentVerdict` 减去 `stance`。

    字段名与 `AgentVerdict` 对齐（迁移时能机械对应），校验走同一份
    `check_fact_invariants`。**没有 stance** —— 那是 `AgentAssessment` 的事。
    """

    task_id: str
    agent: str
    status: VerdictStatus
    verdict: VerdictLevel
    result: Mapping[str, Any] = dc_field(default_factory=dict)
    data_completeness: float = 0.0
    evidence: tuple[Evidence, ...] = dc_field(default_factory=tuple)
    warnings: tuple[str, ...] = dc_field(default_factory=tuple)
    missing: tuple[MissingItem, ...] = dc_field(default_factory=tuple)
    elapsed_ms: int = 0

    def __post_init__(self) -> None:
        # 归一化：与 AgentVerdict 同款，光 frozen 挡不住对可变对象原地 mutate。
        object.__setattr__(
            self, "missing", tuple(MissingItem.coerce(m) for m in self.missing))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "result", MappingProxyType(dict(self.result)))
        check_fact_invariants(
            agent=self.agent, task_id=self.task_id, status=self.status,
            verdict=self.verdict, result=self.result,
            data_completeness=self.data_completeness, evidence=self.evidence,
            missing=self.missing, elapsed_ms=self.elapsed_ms)

    def evidence_for(self, field: str) -> list[Evidence]:
        return [e for e in self.evidence if e.field == field]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent": self.agent,
            "status": self.status,
            "verdict": self.verdict,
            "result": dict(self.result),
            "data_completeness": self.data_completeness,
            "evidence": [e.to_dict() for e in self.evidence],
            "warnings": list(self.warnings),
            "missing": [m.to_dict() for m in self.missing],
            "elapsed_ms": self.elapsed_ms,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FactBundle":
        return cls(
            task_id=d["task_id"],
            agent=d["agent"],
            status=d["status"],
            verdict=d["verdict"],
            result=d.get("result", {}),
            # 与 AgentVerdict 同款双键兼容（历史只有 confidence）。
            data_completeness=d.get("data_completeness", d.get("confidence", 0.0)),
            evidence=[Evidence.from_dict(x) for x in d.get("evidence", [])],
            warnings=list(d.get("warnings", [])),
            missing=[MissingItem.coerce(m) for m in d.get("missing", [])],
            elapsed_ms=d.get("elapsed_ms", 0),
        )


@dataclass(frozen=True)
class AgentAssessment:
    """Agent 的判断 —— 一个 `stance`，外加它评估的是哪一份 `FactBundle`。

    🔴 **不抄事实**：只带 `(task_id, agent)`（哪个决策、哪个 agent 的事实）+
    `fact_ref`（那份 FactBundle 在 `agent_verdicts` 的行号）。这正是补丁路径当年
    要防的 —— Agent 只加一个判断，不用把 15 条 Evidence 重打一遍（L-10）。
    """

    task_id: str
    agent: str
    stance: str
    #: 它评估的 FactBundle 在 `agent_verdicts` 的行号（存储层的 id 引用）。
    #: 纯契约构造时可能还不知道（None），落库时由 `_store` 填上；与 `Evidence.raw_hash`
    #: 一样是「可选、由产出方补」的溯源字段。
    fact_ref: int | None = None

    def __post_init__(self) -> None:
        if not TASK_ID_RE.match(self.task_id):
            raise ValueError(
                f"task_id 必须形如 BIGA-YYYYMMDD-NNN，收到 {self.task_id!r}")
        if not isinstance(self.agent, str) or not self.agent.strip():
            raise ValueError(f"agent 必须是非空字符串，收到 {self.agent!r}")
        if self.stance is None:
            raise ValueError(
                f"[{self.agent}] AgentAssessment 必须带 stance —— 它的存在就是那个判断。"
                "没有判断就不该有 assessment（skill 只产 FactBundle）")
        check_stance_vocab(self.agent, self.stance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent": self.agent,
            "stance": self.stance,
            "fact_ref": self.fact_ref,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentAssessment":
        return cls(task_id=d["task_id"], agent=d["agent"], stance=d["stance"],
                   fact_ref=d.get("fact_ref"))


@dataclass(frozen=True)
class AgentOutcome:
    """`FactBundle` + `AgentAssessment` 的组合视图 —— 下游（risk / card_ops）看的东西。

    🔴 跨两型的铁律在这一刻校验：`assessment` 与 `fact` 必须是同一个
    `(task_id, agent)`（判断不能挂错事实）；UNKNOWN 的事实上不许有方向判断
    （`check_stance_vs_verdict`）。

    `to_agent_verdict()` 把它压回旧消费者认识的 `AgentVerdict` —— 过渡期让
    `card_ops` / `risk_check` / `DecisionCard` **零改动**继续消费（`_store.load_verdict`
    对新旧两种落库形状都返回一个 AgentVerdict，靠的就是这一步）。
    """

    fact: FactBundle
    assessment: AgentAssessment | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.fact, FactBundle):
            raise TypeError(
                f"AgentOutcome.fact 必须是 FactBundle，收到 {type(self.fact).__name__}")
        if self.assessment is not None:
            if not isinstance(self.assessment, AgentAssessment):
                raise TypeError(
                    f"AgentOutcome.assessment 必须是 AgentAssessment，"
                    f"收到 {type(self.assessment).__name__}")
            if self.assessment.task_id != self.fact.task_id \
                    or self.assessment.agent != self.fact.agent:
                raise ValueError(
                    f"assessment（{self.assessment.agent}/{self.assessment.task_id}）"
                    f"与 fact（{self.fact.agent}/{self.fact.task_id}）不是同一个 "
                    "(task_id, agent) —— 判断挂到了别人的事实上")
            check_stance_vs_verdict(
                self.fact.agent, self.assessment.stance, self.fact.verdict)

    @property
    def task_id(self) -> str:
        return self.fact.task_id

    @property
    def agent(self) -> str:
        return self.fact.agent

    @property
    def stance(self) -> str | None:
        return self.assessment.stance if self.assessment else None

    def to_agent_verdict(self) -> AgentVerdict:
        """压回旧消费者认识的 AgentVerdict（事实 + stance）。"""
        return AgentVerdict(
            task_id=self.fact.task_id,
            agent=self.fact.agent,
            status=self.fact.status,
            verdict=self.fact.verdict,
            result=dict(self.fact.result),
            data_completeness=self.fact.data_completeness,
            evidence=self.fact.evidence,
            warnings=self.fact.warnings,
            missing=self.fact.missing,
            elapsed_ms=self.fact.elapsed_ms,
            stance=self.stance,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact": self.fact.to_dict(),
            "assessment": self.assessment.to_dict() if self.assessment else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentOutcome":
        a = d.get("assessment")
        return cls(fact=FactBundle.from_dict(d["fact"]),
                   assessment=AgentAssessment.from_dict(a) if a else None)


class LegacyAdapter:
    """旧 `AgentVerdict` → 新三型。**读路径宽**（§9 兼容策略）。

    🔴 能把历史上**任何一条**已落库的 `AgentVerdict` 还原成 FactBundle +
    （可选）AgentAssessment —— 旧格式随时间自然清零，而不是永久驻留成第二套口径。
    ⚠️ **写路径严不在这里** —— 那由 `_store.save_fact_bundle` / `save_assessment`
    「只收新形状」来保证；这个 Adapter 只做「读回来拆开」这一个方向。
    """

    @staticmethod
    def split(v: AgentVerdict) -> tuple[FactBundle, AgentAssessment | None]:
        fb = FactBundle(
            task_id=v.task_id, agent=v.agent, status=v.status, verdict=v.verdict,
            result=dict(v.result), data_completeness=v.data_completeness,
            evidence=v.evidence, warnings=v.warnings, missing=v.missing,
            elapsed_ms=v.elapsed_ms)
        assessment = None
        if v.stance is not None:
            assessment = AgentAssessment(
                task_id=v.task_id, agent=v.agent, stance=v.stance)
        return fb, assessment

    @staticmethod
    def to_outcome(v: AgentVerdict) -> AgentOutcome:
        fb, assessment = LegacyAdapter.split(v)
        return AgentOutcome(fact=fb, assessment=assessment)
