"""BigA 契约层 —— Evidence / AgentVerdict / DecisionCard 的唯一实现。

🔴 铁律 4：契约只有一份实现。任何 agent 或脚本不得自建第二套。
   由 tests/test_contract_single_impl.py 用 AST 全仓扫描钉死。

用法::

    from _contract import Evidence, AgentVerdict, DecisionCard, now_cn
"""

from .card import DECISION_ID_RE, CardStatus, DecisionCard
from .evidence import CN_TZ, Evidence, now_cn
from .missing import LEGACY_CODE, MissingItem
from .verdict import (
    CROSS_CHECK_PAIRS,
    STAGE1_AGENTS,
    STAGE2_AGENTS,
    STANCE_VOCAB,
    TASK_ID_RE,
    VETO_STANCE,
    AgentVerdict,
    VerdictLevel,
    VerdictStatus,
    new_task_id,
)

__all__ = [
    "CN_TZ",
    "LEGACY_CODE",
    "MissingItem",
    "CROSS_CHECK_PAIRS",
    "STAGE1_AGENTS",
    "STAGE2_AGENTS",
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
    "new_task_id",
    "now_cn",
]
