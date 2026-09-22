"""BigA 契约层 —— Evidence / AgentVerdict / DecisionCard 的唯一实现。

🔴 铁律 4：契约只有一份实现。任何 agent 或脚本不得自建第二套。
   由 tests/test_contract_single_impl.py 用 AST 全仓扫描钉死。

用法::

    from _contract import Evidence, AgentVerdict, DecisionCard, now_cn
"""

from .card import DECISION_ID_RE, CardStatus, DecisionCard
from .evidence import CN_TZ, Evidence, now_cn
from .missing import LEGACY_CODE, MissingItem
from .run import (
    INITIAL_STATE,
    LEGAL_TRANSITIONS,
    RUN_ORIGINS,
    RUN_STATES,
    TERMINAL_STATES,
    RunContext,
    RunState,
    new_evidence_set_id,
    new_run_context,
    new_run_id,
    new_trigger_id,
)
from .verdict_ref import CONTRACT_VERSION, VerdictRef
from .verdict import (
    ADHOC_TASK_SEQ,
    CROSS_CHECK_PAIRS,
    STAGE1_AGENTS,
    STAGE2_AGENTS,
    SYNTHESIZER_AGENT,
    STANCE_VOCAB,
    TASK_ID_RE,
    VETO_STANCE,
    AgentVerdict,
    VerdictLevel,
    VerdictStatus,
    is_adhoc_task_id,
    new_task_id,
)

__all__ = [
    "CN_TZ",
    "CONTRACT_VERSION",
    "LEGACY_CODE",
    "MissingItem",
    "VerdictRef",
    "INITIAL_STATE",
    "LEGAL_TRANSITIONS",
    "RUN_ORIGINS",
    "RUN_STATES",
    "TERMINAL_STATES",
    "RunContext",
    "RunState",
    "new_evidence_set_id",
    "new_run_context",
    "new_run_id",
    "new_trigger_id",
    "ADHOC_TASK_SEQ",
    "CROSS_CHECK_PAIRS",
    "STAGE1_AGENTS",
    "STAGE2_AGENTS",
    "SYNTHESIZER_AGENT",
    "STANCE_VOCAB",
    "VETO_STANCE",
    "DECISION_ID_RE",
    "TASK_ID_RE",
    "AgentVerdict",
    "CardStatus",
    "DecisionCard",
    "Evidence",
    "VerdictLevel",
    "VerdictStatus",
    "is_adhoc_task_id",
    "new_task_id",
    "now_cn",
]
