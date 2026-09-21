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
from .missing import MissingItem

__all__ = [
    "STAGE1_AGENTS",
    "STAGE2_AGENTS",
    "STANCE_VOCAB",
    "VETO_STANCE",
    "AgentVerdict",
    "VerdictStatus",
    "VerdictLevel",
    "TASK_ID_RE",
    "new_task_id",
]

VerdictStatus = Literal["completed", "partial", "failed"]
#: 🔴 只描述**数据完整度**，不含任何业务判断。
#:
#: 这里原本还有一个 `BLOCK`（制衡层的否决）。它被移到 `stance` 去了，理由是
#: **一个字段装不下两件事**：risk 数据完整且要否决时，`verdict` 填 `BLOCK`
#: 就再也说不出「它的数据是全的」—— 而「否决」与「凭什么否决」恰恰都需要知道。
VerdictLevel = Literal["PASS", "WARNING", "UNKNOWN"]

_STATUSES: frozenset[str] = frozenset(get_args(VerdictStatus))
_LEVELS: frozenset[str] = frozenset(get_args(VerdictLevel))

#: 任务号格式 BIGA-YYYYMMDD-NNN
TASK_ID_RE = re.compile(r"^BIGA-\d{8}-\d{3}$")

#: 各 Agent 的方向判断词表 —— **唯一定义**。
#:
#: 🔴 为什么要固定词表：`stance` 存在的意义是「能被聚合」。
#:    今天写「偏强」、明天写「震荡偏强」、后天写「结构性走强」，
#:    三个月后它就是一列自由文本，Phase 4 拿它做不了任何相关性检验。
#:
#: ⚠️ 这张表与各 agent `AGENTS.md` 里的判断表是同一套口径，
#:    由 `tests/test_stance_vocab.py` 钉死两边一致 —— 否则改了一边忘了另一边，
#:    agent 会给出一个契约层拒绝的词，然后花几轮去猜。
#: Stage 拓扑 —— **唯一定义**。
#:
#: Stage 1 并行扇出（分析层），Stage 2 读 Stage 1 的**冻结证据**做制衡。
#: 放在契约层而不是各自的 skill 里：判断「谁该和谁并行」「谁必须在谁之后」
#: 的地方不止一处（risk-check 算覆盖率、latency_report 判并行），
#: 各写一份就会漂 —— 而漂开的表现是**并行判据在正确行为上报红**，
#: 然后那个检查就被忽略了。
STAGE1_AGENTS = ("market", "sector", "news", "technical", "emotion")
STAGE2_AGENTS = ("risk", "discipline")

#: 🔴 制衡层的否决。它是一个**权限**，不是一句措辞 ——
#:    `DecisionCard` 用它拦住 BUY，所以这个字面量只许有一处定义。
#:    写死成常量而不是散在各处的字符串：改了词表却忘了改判据，
#:    否决权会**静默失效**，而那是本项目最怕的 fail-open。
VETO_STANCE = "否决"

STANCE_VOCAB: dict[str, tuple[str, ...]] = {
    "market": ("放量上涨", "缩量上涨", "缩量调整", "放量下跌", "分化", "无法判定"),
    "emotion": ("冰点", "修复", "亢奋", "衰退", "恐慌", "无法判定"),
    "sector": ("主线明确", "轮动分散", "普跌无主线", "无法判定"),
    "technical": ("多头", "空头", "震荡", "无法判定"),
    # 制衡层：它不描述市场，它描述「这单能不能做」
    "risk": ("放行", "警示", VETO_STANCE, "无法判定"),
}


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
            每条是一个 `MissingItem`（机器可读代码 + 人话）。
        elapsed_ms: 本次耗时，用于延迟预算核算。
        stance: 🔴 **方向判断**，由 Agent 给，skill 不填。

            与 `verdict` 严格分离：

            === ============================ ==========================
            字段   回答的问题                    谁来填
            === ============================ ==========================
            verdict 这次判断**有效吗**（数据全不全）  skill
            stance  判断**是什么**（市场偏哪边）      Agent
            === ============================ ==========================

            `status=PASS` + `stance=缩量调整` = 数据完整、判断有效、市场偏弱。
            两者混在一起，「没发现问题」和「看多」就分不开了。

            ⚠️ 它落库是为了 Phase 4：要检验 Card 的区分力，就必须能把
            **方向判断**与 T+5 结果做相关。只写在自然语言回复里，等于每跑一次丢一次。
    """

    task_id: str
    agent: str
    status: VerdictStatus
    verdict: VerdictLevel
    result: dict[str, Any] = dc_field(default_factory=dict)
    confidence: float = 0.0
    evidence: list[Evidence] = dc_field(default_factory=list)
    warnings: list[str] = dc_field(default_factory=list)
    missing: list[MissingItem] = dc_field(default_factory=list)
    elapsed_ms: int = 0
    stance: str | None = None

    def __post_init__(self) -> None:
        # 缺失项统一收成 MissingItem（裸字符串是 Phase 1 遗留，按 legacy 收编）
        self.missing = [MissingItem.coerce(m) for m in self.missing]

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

        # --- stance：方向判断，与数据完整度分开 ---
        if self.stance is not None:
            vocab = STANCE_VOCAB.get(self.agent)
            if vocab is not None and self.stance not in vocab:
                raise ValueError(
                    f"[{self.agent}] stance={self.stance!r} 不在该 agent 的词表里 "
                    f"{list(vocab)} —— 方向判断必须可聚合，自由发挥的措辞没法做统计")
            if vocab is None and (not self.stance.strip() or len(self.stance) > 16):
                raise ValueError(
                    f"[{self.agent}] stance 必须是 1..16 字的短词，收到 {self.stance!r}")
            if self.verdict == "UNKNOWN" and self.stance not in (None, "无法判定"):
                raise ValueError(
                    f"[{self.agent}] verdict='UNKNOWN' 却给出 stance={self.stance!r} —— "
                    "数据都不够，方向是从哪来的？（UNKNOWN ≠ 有判断）")

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
            "missing": [m.to_dict() for m in self.missing],
            "elapsed_ms": self.elapsed_ms,
            "stance": self.stance,
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
            missing=[MissingItem.coerce(m) for m in d.get("missing", [])],
            elapsed_ms=d.get("elapsed_ms", 0),
            stance=d.get("stance"),
        )
