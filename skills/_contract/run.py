"""RunContext 与显式状态机 —— 运行身份的唯一定义（设计文档 §4 / §5）。

契约层是 Evidence / AgentVerdict / DecisionCard / RunContext 的**唯一实现**。
任何 agent 或脚本不得自建第二套（由 tests/test_contract_single_impl.py 钉死）。

为什么需要它
------------
在此之前只有 `decision_id` 一个身份，它被迫同时承担五件事，后果实测过三次：

  · 飞书事件重投 = 重跑一次决策（没有 `trigger_id` 做幂等键）
  · 硬超时收掉一次运行后重试，两次尝试挤在同一个 `decision_id` 上，事后分不开
  · 「所有 Specialist 看的是同一份数据」这句话无法验证（没有 `evidence_set_id`）

⇒ 把身份拆开：一次外部请求（`trigger_id`）→ 一次业务决策（`decision_id`）→
  这次决策的某一次执行尝试（`run_id`）→ 被冻结的数据切片（`evidence_set_id`）。

状态机为什么在契约层而不是 `_store`
------------------------------------
`transition()`（CAS 落库）在 `_store`；但**哪些状态合法、哪条转移合法**
是领域规则，不是存储细节。放在契约层，`_store.runs` 与 `run_ledger.py`
（`biga-card --status`）共用同一份 —— 两处各写一份状态清单必然漂
（`STANCE_VOCAB` / 白名单字段独立踩过三次，见 `tests/_consistency.py`）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .card import DECISION_ID_RE
from .evidence import now_cn

__all__ = [
    "RunState",
    "RUN_STATES",
    "TERMINAL_STATES",
    "INITIAL_STATE",
    "LEGAL_TRANSITIONS",
    "RUN_ORIGINS",
    "RunContext",
    "new_run_id",
    "new_trigger_id",
    "new_run_context",
]


class RunState:
    """一次运行可能处在的 13 个状态 —— **照设计文档 §5 那张表，一个不多**。

    🔴 评审 §13 原文列了 15 个。这里少两个，是有意的：

      · 去掉 `IDENTITY_RESERVED` —— 与 `PREFLIGHTED` 在本系统里是同一瞬间
        （Stage 0 占号在预检里），没有代码能单独进入它、也没有消费方读它。
      · 去掉 `SNAPSHOT_COLLECTING` —— 并入 `PREFLIGHTED → SNAPSHOT_FROZEN`
        的转移，中间态无人读。

    凭空多一个状态就是一条 L-1 死配置。`NOTIFICATION_PENDING` 同理**推迟到
    批 G**（outbox 存在之前它没有消费方）。

    `RUN_STATES` 从这个类的属性**派生**，不手抄第二份 —— 加/删状态只改这里。
    """

    #: 触发网关接下一次请求。谁写：Trigger Gateway（批 B 里是 `bin/biga-card`）。
    RECEIVED = "RECEIVED"
    #: 五道守卫全过（熔断→ownership→锁→预算→第一次付费之前）。
    PREFLIGHTED = "PREFLIGHTED"
    #: SnapshotCoordinator 冻结了这次决策的数据切片（批 D）。
    SNAPSHOT_FROZEN = "SNAPSHOT_FROZEN"
    #: Stage 1 五个 Specialist 并行扇出中（批 C）。
    STAGE1_RUNNING = "STAGE1_RUNNING"
    #: Stage 1 全部返回。
    STAGE1_COMPLETED = "STAGE1_COMPLETED"
    #: 制衡层 risk 审查中。
    RISK_RUNNING = "RISK_RUNNING"
    #: Supervisor 合成 Card 中。
    SYNTHESIZING = "SYNTHESIZING"
    #: Card 已写进 `decision_records`（Repository）。
    CARD_PERSISTED = "CARD_PERSISTED"
    #: 整条链路正常收尾。
    COMPLETED = "COMPLETED"
    #: 执行失败（守卫拒绝 / spawn 核验未过 / 采集异常）。
    FAILED = "FAILED"
    #: 超过硬超时预算。
    TIMEOUT = "TIMEOUT"
    #: 被主动收掉（守卫拦下 / 人工中止 / 并发锁）。
    CANCELLED = "CANCELLED"
    #: 卡在非交互 `ask_user` 上（对应 `bin/biga-card` 退出码 5）。
    #: 🔴 **不与 `FAILED` 合并** —— 一个要人去看提示词，一个要人去等。
    INPUT_REQUIRED = "INPUT_REQUIRED"


#: 全部合法状态 —— 从 `RunState` 的属性派生，避免手抄出第二份清单。
RUN_STATES: frozenset[str] = frozenset(
    v for k, v in vars(RunState).items()
    if not k.startswith("_") and isinstance(v, str)
)

#: 终态：进去就出不来（无出边）。
TERMINAL_STATES: frozenset[str] = frozenset({
    RunState.COMPLETED,
    RunState.FAILED,
    RunState.TIMEOUT,
    RunState.CANCELLED,
    RunState.INPUT_REQUIRED,
})

#: 运行的第一个状态。它没有入边（是入口），其余状态都必须可从它到达。
INITIAL_STATE: str = RunState.RECEIVED

#: 触发来源。origin 决定幂等策略（飞书按 event id 去重、CLI 每次独立），
#: 未知来源是 bug 不是合法输入 ⇒ 白名单，fail closed。
#: `feishu` 在批 G 才有生产方，`cron` 在 Phase 3 —— 但它们是**输入空间**里
#: 设计上就存在的取值，不是「建了没人用的字段」，所以现在就登记。
RUN_ORIGINS: frozenset[str] = frozenset({"cli", "feishu", "cron"})


# ── 合法转移图 ────────────────────────────────────────────────────────────
#
# 🔴 状态机的价值全在这张图：它同时回答「谁写它」（有入边 = 可被某条转移产生）
#    和「什么转移非法」（不在图里 = 跳步/回退/乱跳，CAS 当场拒绝）。
#
# 编排主干（批 C 的 Orchestrator 走这条细粒度链）：
_ORCHESTRATED = [
    (RunState.RECEIVED, RunState.PREFLIGHTED),
    (RunState.PREFLIGHTED, RunState.SNAPSHOT_FROZEN),
    (RunState.SNAPSHOT_FROZEN, RunState.STAGE1_RUNNING),
    (RunState.STAGE1_RUNNING, RunState.STAGE1_COMPLETED),
    (RunState.STAGE1_COMPLETED, RunState.RISK_RUNNING),
    (RunState.RISK_RUNNING, RunState.SYNTHESIZING),
    (RunState.SYNTHESIZING, RunState.CARD_PERSISTED),
    (RunState.CARD_PERSISTED, RunState.COMPLETED),
]

# 🔴 批 B 曾有一条 legacy 粗边 `PREFLIGHTED → CARD_PERSISTED`，专给「编排整个交给
#    一个被 spawn 的 LLM、中间态对 CLI 不透明」的老路径用。批 C-II 的
#    DecisionOrchestrator 自己驱动 8 步细粒度链，那条粗边**没有调用方了** ——
#    已删除，不留一条恒不被走的死边（L-7）。跳过中间态现在一律是非法转移。
#    （`tools/verify/` 无引用、`bin/biga-card` 已收缩、grep 全仓无残留 —— 见批 C-II 探针 P4。）

#: 在途状态（非终态）—— 失败/超时/取消可以从其中任何一个发生。
_IN_FLIGHT = tuple(s for s in (
    RunState.RECEIVED, RunState.PREFLIGHTED, RunState.SNAPSHOT_FROZEN,
    RunState.STAGE1_RUNNING, RunState.STAGE1_COMPLETED, RunState.RISK_RUNNING,
    RunState.SYNTHESIZING, RunState.CARD_PERSISTED,
))

_FAILURE = [
    (s, t) for s in _IN_FLIGHT
    for t in (RunState.FAILED, RunState.TIMEOUT, RunState.CANCELLED)
]

# INPUT_REQUIRED 只可能在 agent 被 spawn 之后（PREFLIGHTED 起），
# 采集/编排/合成期间调 `ask_user` 而没人应答。RECEIVED 时还没起 agent。
_INPUT_REQUIRED = [
    (s, RunState.INPUT_REQUIRED) for s in (
        RunState.PREFLIGHTED, RunState.SNAPSHOT_FROZEN, RunState.STAGE1_RUNNING,
        RunState.STAGE1_COMPLETED, RunState.RISK_RUNNING, RunState.SYNTHESIZING,
    )
]

#: (from_state, to_state) 全体合法转移。`transition()` 只认这里有的。
LEGAL_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    _ORCHESTRATED + _FAILURE + _INPUT_REQUIRED
)


def new_run_id() -> str:
    """一次执行尝试的唯一 id。

    用 uuid4 而不是 `<decision_id>#N`：`run_id` 在 `open_run` 时就要存在，
    而 legacy 路径那一刻还不知道 `decision_id`（LLM 的 Stage 0 才占号）。
    run 的身份必须独立于它属于哪个决策。
    """
    return uuid.uuid4().hex


def new_trigger_id(origin: str) -> str:
    """一次外部请求的幂等键。

    CLI 每次调用是一次独立请求（不去重）；飞书会把 event id 传进来当
    `trigger_id`（天然幂等，批 G）。这里给 CLI/cron 生成一个带时间与随机尾巴的。
    """
    return f"{origin}-{now_cn().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class RunContext:
    """一次运行的完整身份 —— 五个身份拆开之后打成一个包，随运行流转。

    Attributes:
        trigger_id: 一次外部请求（飞书消息 / CLI / cron）。幂等键。
        decision_id: 业务上的一次决策 ``BIGA-YYYYMMDD-NNN``。
            🔴 **可空**：legacy 路径在 RECEIVED 时还不知道号（LLM 的 Stage 0
            才占），此时为 None，占到号后记进 `run_events` 的 detail。
            批 C 的 Orchestrator 在开 run 之前就占号，那时它非空。
        run_id: 这次决策的**某一次执行尝试**。重试 / 回放各是一个新的 run_id。
        evidence_set_id: 被冻结的数据切片 id。批 D 的 SnapshotCoordinator
            填它；批 B 建表但不产生 ⇒ 现在恒为 None。
        origin: `cli` / `feishu` / `cron`。
        non_interactive: 这次运行有没有人能回答 `ask_user`。
            CLI/cron 非交互 ⇒ True ⇒ `ask_user` 必死锁 ⇒ INPUT_REQUIRED。
        created_at: 北京时间 ISO 串（`now_cn().isoformat()`）。
    """

    trigger_id: str
    decision_id: str | None
    run_id: str
    evidence_set_id: str | None
    origin: str
    non_interactive: bool
    created_at: str

    def __post_init__(self) -> None:
        for name in ("trigger_id", "run_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or not v.strip() or v != v.strip() or any(c.isspace() for c in v):
                raise ValueError(
                    f"RunContext.{name} 必须是非空、无空白字符的字符串，收到 {v!r}")
        if self.decision_id is not None and not DECISION_ID_RE.match(self.decision_id):
            raise ValueError(
                f"RunContext.decision_id 要么是 None（还没占号），"
                f"要么形如 BIGA-YYYYMMDD-NNN，收到 {self.decision_id!r}")
        if self.evidence_set_id is not None and (
                not isinstance(self.evidence_set_id, str) or not self.evidence_set_id.strip()):
            raise ValueError(
                f"RunContext.evidence_set_id 要么是 None（未冻结），"
                f"要么是非空字符串，收到 {self.evidence_set_id!r}")
        if self.origin not in RUN_ORIGINS:
            raise ValueError(
                f"RunContext.origin 必须是 {sorted(RUN_ORIGINS)} 之一，收到 {self.origin!r} —— "
                "未知来源当作 bug 拒绝，不静默接受（fail closed）")
        # 🔴 严格 bool：0/1/None 都不是 bool。non_interactive 决定 ask_user 会不会
        #    死锁，含糊不得。
        if not isinstance(self.non_interactive, bool):
            raise TypeError(
                f"RunContext.non_interactive 必须是 bool，收到 {type(self.non_interactive).__name__}")
        if not isinstance(self.created_at, str) or not self.created_at.strip():
            raise ValueError(f"RunContext.created_at 必须是非空字符串，收到 {self.created_at!r}")
        try:
            dt = datetime.fromisoformat(self.created_at)
        except ValueError as e:
            raise ValueError(
                f"RunContext.created_at 不是合法 ISO 时间：{self.created_at!r}") from e
        if dt.tzinfo is None:
            # naive datetime 是时区错位的头号来源（见 Evidence 的同款校验）。
            raise ValueError(
                f"RunContext.created_at 必须带时区（用 now_cn().isoformat()），"
                f"收到 naive 时间 {self.created_at!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "decision_id": self.decision_id,
            "run_id": self.run_id,
            "evidence_set_id": self.evidence_set_id,
            "origin": self.origin,
            "non_interactive": self.non_interactive,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RunContext":
        return cls(
            trigger_id=d["trigger_id"],
            decision_id=d.get("decision_id"),
            run_id=d["run_id"],
            evidence_set_id=d.get("evidence_set_id"),
            origin=d["origin"],
            non_interactive=d["non_interactive"],
            created_at=d["created_at"],
        )


def new_run_context(
    *,
    origin: str,
    non_interactive: bool,
    decision_id: str | None = None,
    evidence_set_id: str | None = None,
    trigger_id: str | None = None,
    run_id: str | None = None,
) -> RunContext:
    """造一个新 RunContext，自动补 `trigger_id` / `run_id` / `created_at`。

    `trigger_id` / `run_id` 可传入（飞书把 event id 当 trigger_id；测试要固定值），
    留空则自动生成。
    """
    return RunContext(
        trigger_id=trigger_id or new_trigger_id(origin),
        decision_id=decision_id,
        run_id=run_id or new_run_id(),
        evidence_set_id=evidence_set_id,
        origin=origin,
        non_interactive=non_interactive,
        created_at=now_cn().isoformat(),
    )
