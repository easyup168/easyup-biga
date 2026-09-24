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

import json

import re
from dataclasses import dataclass, field as dc_field
from types import MappingProxyType
from typing import Any, Literal, Mapping, get_args

from .evidence import Evidence
from .missing import MissingItem

__all__ = [
    "ADHOC_TASK_SEQ",
    "CROSS_CHECK_PAIRS",
    "is_adhoc_task_id",
    "SYNTHESIZER_AGENT",
    "STANCE_VOCAB",
    "VETO_STANCE",
    "AgentVerdict",
    "VerdictStatus",
    "VerdictLevel",
    "TASK_ID_RE",
    "new_task_id",
    "check_fact_invariants",
    "check_stance_vocab",
    "check_stance_vs_verdict",
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
#: 🔴 **被声明的重复事实** —— 裁定 15 的受控例外。
#:
#: 裁定 15 说「同一个事实只能有一个生产方」。但有两种情况必须重复：
#:
#: 1. **溯源字段**（`trade_date`）—— 每个 skill 都要自报它说的是哪一天
#: 2. **跨源校验点**（本表）—— 两个 agent 从同一个源独立取同一个值，
#:    **不一致本身就是信息**：它说明两者看到的不是同一份数据
#:
#: ⚠️ 允许重复的**前提是有人核对**。声明在这里却没人查，
#:    就退化成裁定 15 要防的那种情况：两个数悄悄不一样，没人知道。
#:    由 `tests/test_field_single_producer.py` 钉死：
#:    出现在本表里的字段，必须同时出现在 risk 的核对清单里。
#:
#: 格式：``(agentA, fieldA, agentB, fieldB, 说明)``
CROSS_CHECK_PAIRS: tuple[tuple[str, str, str, str, str], ...] = (
    ("market", "sh_close", "technical", "close", "上证收盘价"),
)

#: 🔴 Stage 拓扑（`STAGE1_AGENTS` / `STAGE2_AGENTS`）批 K 起从 `_contract/registry.py`
#:    的 `AGENT_REGISTRY` **派生**，不再在这里手写元组 —— 它俩曾是 roster 散落
#:    五处里的两处。仍然 `from _contract import STAGE1_AGENTS` 取用，位置换了、
#:    名字没变。`STANCE_VOCAB`（下面）**保持独立**：它是各 Specialist 的 stance
#:    词表，不是 roster；批 K 只对 Registry 断言它的 key 集是子集关系。

#: 🔴 综合判官 —— 确定性编排批 C-II 的 Stage 3 判断者（**唯一定义**）。
#:
#: 它不是 Specialist：不产 AgentVerdict、不进 STAGE1/STAGE2、不进 STANCE_VOCAB。
#: 它读冻结 verdict、产出 Card 的 status/headline/synthesis。名字单点定义在这里，
#: orchestrator / `tests/_consistency.SUPPORT_AGENTS` / 运行时配置都引用它，不手抄。
#:
#: 结构性安全（比让 orchestrator spawn main 当判官强的全部原因）：它在配置里
#: `subagents.allowAgents=[]`，是叶子节点，**没有任何 spawn 能力** —— 不是「这次
#: 调用被限制」，是「这个 agent 从来没有这个能力」。
SYNTHESIZER_AGENT = "synthesizer"

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
    # 🔴 news 的词表描述的是**消息面**，不是行情。
    #    不用「多头/空头」那套 —— 那会诱导 agent 从快讯里的数字
    #    反推行情结论，而行情是 market/technical 的事实（裁定 15）。
    "news": ("重大利好", "重大利空", "多空交织", "平静", "无法判定"),
    # 制衡层：它不描述市场，它描述「这单能不能做」
    "risk": ("放行", "警示", VETO_STANCE, "无法判定"),
}


#: 🔴 **临时号** —— 序号 0 保留给「不属于任何决策」的运行。
#:
#: 一个 specialist **不该自己编决策号**：决策的身份归 Supervisor 所有。
#: 但手工跑一次 skill 看看输出是常事，那时又确实需要一个合法的 task_id。
#:
#: 所以给它一个**自曝身份**的号：`BIGA-YYYYMMDD-000`。
#: `next_decision_id()` 从 1 开始分配，000 永远不会被真决策占用；
#: 而 `save_verdict()` 拒绝落这种号 —— 临时结果可以看，不可以入账。
#:
#: 原来的默认值是 `new_task_id(1)`，它**看起来像个正经决策号**。
#: 实测后果：五个 specialist 的 verdict 全部写着 `-001`，
#: 两次并发运行的证据混进同一张卡，事后无法分辨。
ADHOC_TASK_SEQ: int = 0


def is_adhoc_task_id(task_id: str) -> bool:
    """这个号是不是「不属于任何决策」的临时号。"""
    return task_id.rsplit("-", 1)[-1] == f"{ADHOC_TASK_SEQ:03d}"


def new_task_id(seq: int, *, day: str | None = None) -> str:
    """生成 ``BIGA-YYYYMMDD-NNN`` 形式的任务号。

    ⚠️ 不要用它给 specialist 造决策号 —— 见 `ADHOC_TASK_SEQ`。
    """
    from .evidence import now_cn

    day = day or now_cn().strftime("%Y%m%d")
    if not re.fullmatch(r"\d{8}", day):
        raise ValueError(f"day 必须是 YYYYMMDD，收到 {day!r}")
    if not 0 <= seq <= 999:
        raise ValueError(f"seq 必须在 0..999，收到 {seq}")
    return f"BIGA-{day}-{seq:03d}"


# ── 契约铁律的**唯一实现** —— 批 E-I 抽出来，FactBundle 与 AgentVerdict 共用 ──
#
# 🔴 为什么抽：批 E-I 把「事实」（FactBundle）与「判断」（AgentAssessment）从
#    AgentVerdict 里拆开。拆开之后 FactBundle 还得守住同样的铁律 —— 如果各写
#    一遍，就是 L-3（同一判据多份实现，某天漂开、且漂开时不报错）。F8 就是
#    `amend_verdict.py` 抄了一遍 stance 校验、连盲区一起抄的实测事故。
#    ⇒ 事实层的铁律只有 `check_fact_invariants` 一份；stance 的只有
#      `check_stance_vocab` + `check_stance_vs_verdict` 两份。谁要校验都调它们。


def _same_value(a: Any, b: Any) -> bool:
    """两个证据值算不算「同一个值」—— 取 canonical JSON，与回放比对同口径。

    🔴 不用 `==`：`1 == 1.0` 为真，但序列化后是 `1` 与 `1.0`，而回放比的是序列化
    之后的字节。两处口径不一致的话，会出现「契约说相等、回放说不等」这种查起来
    最费劲的矛盾。
    🔴 `allow_nan=True`：这里问的是「两个值**一不一样**」，不是「这个值**合不合法**」。
    NaN/Infinity 合不合法由写边界那道严格 JSON 校验回答（`allow_nan=False`），
    与这里是两件事。第一版写成 `allow_nan=False`，于是 `NaN` 序列化失败 → 退回
    `==` → 而 `nan != nan` → 一份 result 与 evidence **本来就相同**的 NaN 被判成
    「对不上」，抢在真正该报错的那道检查前面报了个不相干的错（实测打红了
    `TestP4StrictJSON` 六条）。判据搞混「相等」和「合法」就是这个下场。

    序列化不了的值（非 JSON 类型）退回 `==`，在能判的范围内如实回答。
    """
    try:
        return (json.dumps(a, ensure_ascii=False, sort_keys=True, allow_nan=True)
                == json.dumps(b, ensure_ascii=False, sort_keys=True, allow_nan=True))
    except (TypeError, ValueError):
        return a == b


def check_fact_invariants(
    *,
    agent: str,
    task_id: str,
    status: str,
    verdict: str,
    result: Mapping[str, Any],
    data_completeness: float,
    evidence: tuple[Evidence, ...],
    missing: tuple[MissingItem, ...],
    elapsed_ms: int,
) -> None:
    """事实层的三条铁律 + 字段合法性。**只校验 skill 能独立算出来的东西**，
    不含任何 stance 判断（那在 `check_stance_*`）。入参是**已归一化**的值。
    """
    if not TASK_ID_RE.match(task_id):
        raise ValueError(f"task_id 必须形如 BIGA-YYYYMMDD-NNN，收到 {task_id!r}")
    if not isinstance(agent, str) or not agent.strip():
        raise ValueError(f"agent 必须是非空字符串，收到 {agent!r}")
    if status not in _STATUSES:
        raise ValueError(f"status 必须是 {sorted(_STATUSES)} 之一，收到 {status!r}")
    if verdict not in _LEVELS:
        raise ValueError(f"verdict 必须是 {sorted(_LEVELS)} 之一，收到 {verdict!r}")
    if not isinstance(data_completeness, (int, float)) \
            or not 0.0 <= data_completeness <= 1.0:
        raise ValueError(
            f"data_completeness 必须在 0..1，收到 {data_completeness!r}")
    if elapsed_ms < 0:
        raise ValueError(f"elapsed_ms 不能为负，收到 {elapsed_ms}")
    for e in evidence:
        if not isinstance(e, Evidence):
            raise TypeError(
                f"evidence 必须全部是 _contract.Evidence，收到 {type(e).__name__} —— "
                "不许自建第二套证据结构（铁律 4）"
            )

    # --- 铁律 3：result 的每个键都要有证据 ---
    covered = {e.field for e in evidence}
    orphan = sorted(set(result) - covered)
    if orphan:
        raise ValueError(
            f"[{agent}] result 字段无证据支撑: {orphan} —— "
            f"已有证据覆盖 {sorted(covered)}。无据之言不入 Card（铁律 3）"
        )

    # --- 铁律 3 的另一半：有证据还不够，证据得**支持这个值**（批 P，外部评审 §16）---
    #
    # 🔴 在这条之前，契约只查「result 的键有没有同名 Evidence」，不查两边的**值**。
    #    于是这样一份事实能被原样接受并落库上卡：
    #
    #        result["market.x"] = 100
    #        Evidence(field="market.x", value=1)
    #
    #    一个卖点是「证据可追溯」的系统，最不能有的就是这个 —— 卡面上那个数
    #    与它声称的证据对不上，而每一道既有检查都通过。这是 R-3 的形状：
    #    看起来被验过，其实没有。
    #
    # 判据取 canonical JSON 而不是 `==`：`1 == 1.0` 为真但两者序列化后不同，
    # 而回放比对走的是序列化后的字节 —— 判等口径必须与回放口径一致，否则会出现
    # 「契约说相等、回放说不等」。
    # ⚠️ 实测生产库 411 个 result 字段：canonical JSON **全部相等**，0 个例外 ——
    #    这条加上去不会误伤任何存量数据（它拦的是将来手搓/LLM 填值那类路径）。
    #
    # 同名证据不止一条时判据是「**至少有一条**对得上」：多条同名证据意味着这个值
    # 有多个来源，逐条判等无从谈起；但也不能因此放行 —— 否则加一条对不上的装饰性
    # 证据就能绕过整条检查。
    # ⚠️ 「这个值是好几条证据**推导**出来的」这种情况应当显式声明
    #    `input_evidence_ids`（评审 §16.1），而不是靠一条同名证据冒充直接事实 ——
    #    那一半留给下一批（实测 244/411 条证据是 `source=derived:*`，涉及六个
    #    skill、39 种 (agent, field)，是独立的一块工作，不是契约层改一改的事）。
    unsupported = []
    for key, value in result.items():
        same_named = [e for e in evidence if e.field == key]
        if not same_named:
            continue                       # 上面的 orphan 检查已经拦过了
        if not any(_same_value(e.value, value) for e in same_named):
            unsupported.append(
                f"{key}: result={value!r}，同名证据的值是 "
                + " / ".join(repr(e.value) for e in same_named))
    if unsupported:
        raise ValueError(
            f"[{agent}] result 的值与它自己的证据对不上：\n  "
            + "\n  ".join(unsupported)
            + "\n  「有同名证据」不等于「证据支持这个值」——"
            "对不上的话卡面那个数就是无据之言（铁律 3）。\n"
            "  ⚠️ 判据是 canonical JSON，与回放比对同口径：`1` 与 `1.0` 算**不同**"
            "（两边要么都写整数、要么都写浮点）。\n"
            "  如果这个值是多条证据推导出来的，别用一条同名证据冒充直接事实。"
        )

    # --- 铁律 1：算不出来不许说 PASS ---
    if missing:
        if verdict == "PASS":
            raise ValueError(
                f"[{agent}] missing={missing} 非空却给出 verdict='PASS' —— "
                "UNKNOWN ≠ PASS，算不出来必须说算不出来（铁律 1）"
            )
        if status == "completed":
            raise ValueError(
                f"[{agent}] missing={missing} 非空却声称 status='completed' —— "
                "应为 'partial' 或 'failed'"
            )

    # --- 反向：声称 UNKNOWN 就必须说清楚缺什么 ---
    if verdict == "UNKNOWN" and not missing:
        raise ValueError(
            f"[{agent}] verdict='UNKNOWN' 但 missing 为空 —— "
            "判断不出来必须列出缺了什么，否则 Card 上的「缺失项」是空的，"
            "读者看到的就是一个没有理由的 UNKNOWN"
        )

    # --- failed 不该带结论 ---
    if status == "failed" and verdict != "UNKNOWN":
        raise ValueError(
            f"[{agent}] status='failed' 时 verdict 只能是 'UNKNOWN'，"
            f"收到 {verdict!r}"
        )


def check_stance_vocab(agent: str, stance: str) -> None:
    """stance 必须取自该 agent 的词表 —— AgentVerdict / AgentAssessment 共用。

    🔴 外部评审 F8：未登记 agent 就是错误，**不是「暂时放宽」**。原来
    `STANCE_VOCAB.get(agent)` 命中才校验、命不中静默放行，`agent="Market"`
    大小写打错也能过。沉默的放宽正是这条要防的东西。
    """
    vocab = STANCE_VOCAB.get(agent)
    if vocab is None:
        raise ValueError(
            f"[{agent}] 这个 agent 没有登记 stance 词表 —— "
            f"在 `_contract/verdict.py` 的 STANCE_VOCAB 里加一行。\n"
            f"  已登记：{sorted(STANCE_VOCAB)}\n"
            f"  （拼写也算：agent 名大小写要与登记的完全一致）")
    if stance not in vocab:
        raise ValueError(
            f"[{agent}] stance={stance!r} 不在该 agent 的词表里 "
            f"{list(vocab)} —— 方向判断必须可聚合，自由发挥的措辞没法做统计")


def check_stance_vs_verdict(agent: str, stance: str | None, verdict: str) -> None:
    """🔴 跨「事实」与「判断」的铁律：UNKNOWN 的事实上不许挂一个方向判断。

    这是批 E-I 拆开 FactBundle / AgentAssessment 之后，唯一一条**跨两型**的约束
    —— 它需要同时知道 `verdict`（来自 FactBundle）和 `stance`（来自 AgentAssessment），
    所以由**组合视图 AgentOutcome**（以及仍然合体的 AgentVerdict）在拼起来那一刻校验。
    """
    if verdict == "UNKNOWN" and stance not in (None, "无法判定"):
        raise ValueError(
            f"[{agent}] verdict='UNKNOWN' 却给出 stance={stance!r} —— "
            "数据都不够，方向是从哪来的？（UNKNOWN ≠ 有判断）")


@dataclass(frozen=True)
class AgentVerdict:
    """Specialist → Supervisor 的唯一返回结构。

    Attributes:
        task_id: ``BIGA-YYYYMMDD-NNN``。
        agent: agentId，例如 ``emotion``。
        status: 这次执行本身是否完成。``partial`` = 跑完了但有字段没算出来。
        verdict: 业务判断。``UNKNOWN`` 表示**判断不出来**，与 ``PASS`` 严格区分。
        result: 结构化结论。每个键必须有对应的 Evidence（铁律 3）。
        data_completeness: 0..1。🔴 **字段覆盖率**（`len(result) / 应有字段数`），
            不是模型对自己判断的信心。六个 skill 算的从来都是前者，
            叫 `confidence` 这个名字本身就在暗示一件从没发生过的事——
            改名不改语义，只是把名字改得诚实。
            `from_dict` 双键兼容：历史卡只有 `confidence`，仍然读得进来。
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
    result: Mapping[str, Any] = dc_field(default_factory=dict)
    data_completeness: float = 0.0
    evidence: tuple[Evidence, ...] = dc_field(default_factory=tuple)
    warnings: tuple[str, ...] = dc_field(default_factory=tuple)
    missing: tuple[MissingItem, ...] = dc_field(default_factory=tuple)
    elapsed_ms: int = 0
    stance: str | None = None

    def __post_init__(self) -> None:
        # 🔴 A2：frozen 之后不能再 `self.x = ...`，改用 object.__setattr__。
        #    list/dict → tuple/MappingProxyType：这是在关一扇真实存在过的门 ——
        #    `v.missing.append(...)` 曾经能在不重跑 __post_init__ 的情况下
        #    悄悄改变一个「已经校验过」的对象（tests/test_write_boundary.py 的
        #    P1 探针）。frozen 只挡得住重新赋值 `v.missing = [...]`，挡不住
        #    "对同一个可变对象原地 mutate"——必须两者都做。
        #
        #    ⚠️ 复制一份再包：`MappingProxyType(self.result)` 不复制，
        #    调用方手上那个原始 dict 之后被改了，`self.result` 会跟着变——
        #    那就是另一条同形状的漏洞，只是入口换了个名字。
        object.__setattr__(
            self, "missing", tuple(MissingItem.coerce(m) for m in self.missing))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "result", MappingProxyType(dict(self.result)))

        # 🔴 事实层铁律走**唯一实现**（批 E-I），不在这里重抄一遍 —— FactBundle
        #    用的是同一份 `check_fact_invariants`，改了自动两边一致（防 L-3）。
        check_fact_invariants(
            agent=self.agent, task_id=self.task_id, status=self.status,
            verdict=self.verdict, result=self.result,
            data_completeness=self.data_completeness, evidence=self.evidence,
            missing=self.missing, elapsed_ms=self.elapsed_ms)

        # --- stance：方向判断，与数据完整度分开 ---
        if self.stance is not None:
            check_stance_vocab(self.agent, self.stance)
            check_stance_vs_verdict(self.agent, self.stance, self.verdict)

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
            # 🔴 显式转回 dict：`self.result` 是 MappingProxyType（A2），
            #    json.dumps 只认 dict 的子类，直接塞一个 mappingproxy 进去
            #    会在落库那一刻才炸 TypeError —— 这里转好，问题在源头暴露。
            "result": dict(self.result),
            "data_completeness": self.data_completeness,
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
            # 🔴 A7：双键兼容。历史卡只有 `confidence`（同一个字段覆盖率数字，
            #    改名前的旧名）；`data_completeness` 优先，两个都没有才是 0.0。
            data_completeness=d.get("data_completeness", d.get("confidence", 0.0)),
            evidence=[Evidence.from_dict(x) for x in d.get("evidence", [])],
            warnings=list(d.get("warnings", [])),
            missing=[MissingItem.coerce(m) for m in d.get("missing", [])],
            elapsed_ms=d.get("elapsed_ms", 0),
            stance=d.get("stance"),
        )
