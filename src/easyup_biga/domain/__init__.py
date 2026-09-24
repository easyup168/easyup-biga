"""BigA 契约层 —— Evidence / AgentVerdict / DecisionCard 的唯一实现。

🔴 铁律 4：契约只有一份实现。任何 agent 或脚本不得自建第二套。
   由 tests/test_contract_single_impl.py 用 AST 全仓扫描钉死。

用法::

    from easyup_biga.domain import Evidence, AgentVerdict, DecisionCard, now_cn

仓库内既有代码写的是 `from _contract import ...` —— skills/_contract 薄壳
会 re-export 本包，两种写法等价。壳是给**存量调用方**的兼容层，新代码用
上面那行绝对导入（批 U-I：本包内部已全部改成绝对导入，不再经薄壳）。
"""

from .card import DECISION_ID_RE, CardStatus, DecisionCard
from .evidence import CN_TZ, EVIDENCE_KINDS, SHA256_RE, Evidence, now_cn
from .provenance import (DERIVED_PREFIX, ORIGIN_KINDS, OriginRef, evidence_origins,
                         fact_origin, raw_origins, resolve_provenance,
                         underlying_source, verdict_origins)
from .facts import AgentAssessment, AgentOutcome, FactBundle, LegacyAdapter
from .missing import (
    ABSENT_REASONS,
    absent_agent_code,
    absent_agent_missing,
    absent_agent_of,
    LEGACY_CODE, MissingItem,
)
from .notify import (
    CARD_COMPLETED,
    CARD_EVENT_TYPES,
    CARD_UNKNOWN,
    NOTIFICATION_EVENT_TYPES,
    NOTIFY_FAILURE_STATES,
    RISK_BLOCK,
    RUN_FAILED,
    card_event_type,
)
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
from .registry import (
    AGENT_REGISTRY,
    EXPECTED_ROSTER,
    RISK_AGENT,
    SNAPSHOT_INDEX_AGENTS,
    STAGE1_AGENTS,
    STAGE2_AGENTS,
    AgentDefinition,
)
from .verdict_ref import CONTRACT_VERSION, VerdictRef
from .verdict import (
    ADHOC_TASK_SEQ,
    CROSS_CHECK_PAIRS,
    SYNTHESIZER_AGENT,
    STANCE_VOCAB,
    TASK_ID_RE,
    VETO_STANCE,
    AgentVerdict,
    VerdictLevel,
    VerdictStatus,
    check_fact_invariants,
    check_stance_vocab,
    check_stance_vs_verdict,
    is_adhoc_task_id,
    new_task_id,
)

__all__ = [
    "CN_TZ",
    "DERIVED_PREFIX",
    "EVIDENCE_KINDS",
    "SHA256_RE",
    "ORIGIN_KINDS",
    "OriginRef",
    "evidence_origins",
    "fact_origin",
    "raw_origins",
    "verdict_origins",
    "resolve_provenance",
    "underlying_source",
    "CONTRACT_VERSION",
    "LEGACY_CODE",
    "MissingItem",
    "ABSENT_REASONS",
    "absent_agent_code",
    "absent_agent_missing",
    "absent_agent_of",
    "CARD_COMPLETED",
    "CARD_UNKNOWN",
    "RISK_BLOCK",
    "RUN_FAILED",
    "NOTIFICATION_EVENT_TYPES",
    "CARD_EVENT_TYPES",
    "NOTIFY_FAILURE_STATES",
    "card_event_type",
    "VerdictRef",
    "FactBundle",
    "AgentAssessment",
    "AgentOutcome",
    "LegacyAdapter",
    "check_fact_invariants",
    "check_stance_vocab",
    "check_stance_vs_verdict",
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
    "AgentDefinition",
    "AGENT_REGISTRY",
    "STAGE1_AGENTS",
    "STAGE2_AGENTS",
    "RISK_AGENT",
    "SNAPSHOT_INDEX_AGENTS",
    "EXPECTED_ROSTER",
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
