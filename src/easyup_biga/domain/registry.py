"""AGENT_REGISTRY —— Agent 名册的**唯一源**（数据架构 §16 / 确定性编排批 K）。

它要收编的事故**已经发生过一次**：2026-09-21，`news` 进了契约的 Stage 1 名单、
agent 也建好了，但运行时白名单里没有它 ⇒ 只 spawn 了四个，**没有任何报错**，
Card 照常产出，只是少了一个领域。当时的应对是 `tests/test_roster_matches_config.py`
做数据驱动**对账** —— 那是对的，但它是对账，不是单一源。

批 K 开工前的设计探活（2026-09-23）普查出 roster 实际散在**五处**：
`_contract` 的 `STAGE1_AGENTS`/`STAGE2_AGENTS`、`orchestrator.py` 的 `RISK_AGENT`
与 `SNAPSHOT_INDEX_AGENTS` 两个独立字面量、`STANCE_VOCAB` 的 key 集、
`tools/verify/adapter_spike.py` 的 `STAGE1`（零测试覆盖）、以及仓库外的运行时
配置。这份文件把前四处**结构性**收成一处：名册只在这里手写一次，其余全部**派生**。

为什么是一个类 + `vars()` 内省，而不是几个平行的手写元组
------------------------------------------------------------
照抄 `skills/_contract/run.py` 里 `RunState` → `RUN_STATES` 已经验证过的形状：
一份权威（类属性）+ 内省派生（`frozenset`/`tuple`），改名册只改这一处。
那个文件的 docstring 原话就是「两处各写一份清单必然漂」—— 批 K 治的是同一个病，
所以不发明新写法。类的 `__dict__` 自 Python 3.7 起保留定义顺序 ⇒ `vars()` 迭代
出来的顺序 == 定义顺序 ⇒ `STAGE1_AGENTS` 的**元素顺序**与旧手写元组逐项一致
（批 K 探针 P1 钉这条）。

字段只装「已经在生产路径上有消费方」的
--------------------------------------
`AgentDefinition` 只有四个字段，每个都有一个**已经在跑**的消费方：

  * `stage`          —— `STAGE1_AGENTS`/`STAGE2_AGENTS` 由它派生
  * `spawned`        —— `RISK_AGENT`（唯一会被 spawn 的 Stage 2）与
                        `EXPECTED_ROSTER`（absent_agents 的权威）由它派生
  * `reads_snapshot` —— `SNAPSHOT_INDEX_AGENTS` 由它派生

🔴 **不装** 外部材料示意稿里的 `required_datasets` 之类字段：它绑定 Dataset
Registry，而裁定表已明确把 Dataset/Provider Registry **推迟到批 K 之后**
（现在只有一个 dataset 走完全链，注册表会比被注册的东西还大）。装一个没有
消费方的字段就是一条 L-1 死配置 —— 本仓库最优先防范的失败模式。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "AgentDefinition",
    "AGENT_REGISTRY",
    "STAGE1_AGENTS",
    "STAGE2_AGENTS",
    "RISK_AGENT",
    "SNAPSHOT_INDEX_AGENTS",
    "EXPECTED_ROSTER",
]


@dataclass(frozen=True)
class AgentDefinition:
    """名册里的一条 —— 一个 agent 的**结构性事实**（不是它的判断内容）。

    Attributes:
        agent_id: agentId，例如 ``market``。
        stage: 1 = 分析层并行扇出；2 = 制衡层（读 Stage 1 的冻结证据）。
        spawned: 🔴 **这个 agent 真的会被 spawn 吗。** `discipline` 在册（它是
            Stage 2 的一员）但 `spawned=False` —— 裁定 13：它的输入是人的交易
            行为史，BigA 不下单不接账户，现在没有输入源，故从不被 spawn。
            这一位是 `discipline` 不进 `EXPECTED_ROSTER`（absent_agents 的权威）
            的唯一依据，Registry 引入后这条区分必须继续成立（批 K 探针 P5）。
        reads_snapshot: 消费 `SnapshotCoordinator` 冻结的指数日线吗。只有
            market/sector/technical 读 `fetch_index_daily`；编排器据此决定给
            谁的任务文本加 `--evidence-set-id`（emotion/news 的 skill 没有这个
            参数，加了也不认）。谁给某个 skill 接了 `--evidence-set-id`，谁把
            它的 `reads_snapshot` 置 True —— 漂了编排器探针会抓到（三条 Evidence
            反查不到同一个冻结集）。
    """

    agent_id: str
    stage: int
    spawned: bool
    reads_snapshot: bool


class AgentRegistry:
    """名册的**唯一手写处**。加/删/改 agent 只动这里，下面所有派生自动跟随。

    🔴 **定义顺序即 `STAGE1_AGENTS` 的元素顺序** —— 与批 K 之前的手写元组
    ``("market", "sector", "news", "technical", "emotion")`` 逐项对齐，
    保证收编是**空操作**（探针 P1），不是顺手重排了扇出顺序。
    """

    #: —— Stage 1：分析层，五路并行扇出 ——
    market = AgentDefinition("market", stage=1, spawned=True, reads_snapshot=True)
    sector = AgentDefinition("sector", stage=1, spawned=True, reads_snapshot=True)
    news = AgentDefinition("news", stage=1, spawned=True, reads_snapshot=False)
    technical = AgentDefinition("technical", stage=1, spawned=True, reads_snapshot=True)
    emotion = AgentDefinition("emotion", stage=1, spawned=True, reads_snapshot=False)
    #: —— Stage 2：制衡层 ——
    risk = AgentDefinition("risk", stage=2, spawned=True, reads_snapshot=False)
    #: 🔴 在册但**故意不 spawn**（裁定 13）。删掉它 `STAGE2_AGENTS` 就会少一个，
    #:    而 `test_roster_matches_config.py` 的反向检查、`tests/_consistency.py`
    #:    的 `discipline` 例外都以「它在契约名单里、但没建好目录」为前提。
    discipline = AgentDefinition("discipline", stage=2, spawned=False, reads_snapshot=False)


#: 全体名册 —— 从 `AgentRegistry` 的属性派生，避免手抄第二份（照 `RUN_STATES` 形状）。
AGENT_REGISTRY: tuple[AgentDefinition, ...] = tuple(
    v for k, v in vars(AgentRegistry).items()
    if not k.startswith("_") and isinstance(v, AgentDefinition)
)

#: Stage 拓扑 —— 收编前散在 `_contract/verdict.py`。Stage 1 并行扇出（分析层），
#: Stage 2 读 Stage 1 的**冻结证据**做制衡。放在契约层而不是各自的 skill 里：
#: 判断「谁该和谁并行」「谁必须在谁之后」的地方不止一处（risk-check 算覆盖率、
#: latency_report 判并行），各写一份就会漂 —— 而漂开的表现是**并行判据在正确
#: 行为上报红**，然后那个检查就被忽略了。
STAGE1_AGENTS: tuple[str, ...] = tuple(d.agent_id for d in AGENT_REGISTRY if d.stage == 1)
STAGE2_AGENTS: tuple[str, ...] = tuple(d.agent_id for d in AGENT_REGISTRY if d.stage == 2)

#: 会读冻结日线的 Specialist —— 收编前是 `orchestrator.py` 的独立 `frozenset`。
SNAPSHOT_INDEX_AGENTS: frozenset[str] = frozenset(
    d.agent_id for d in AGENT_REGISTRY if d.reads_snapshot)

#: 🔴 **期望作答的 roster** —— 真的会被 spawn、因此理应回一条判定的 agent。
#:    `DecisionCard.absent_agents` 的权威就是它：某个该到的 agent 没在卡上，
#:    才叫「缺席」。`discipline` 在册但不 spawn ⇒ 不在这里 ⇒ 永远不会被判成
#:    「缺席」（否则每张卡都常驻一条它的缺失噪音，那是裁定 13 要防的）。
#:    今天它与 `STANCE_VOCAB` 的 key 集相等（都是那六个 spawn 的 agent），
#:    由 `tests/test_agent_registry.py` 钉住 —— 但权威是这里，`STANCE_VOCAB`
#:    只对它断言子集关系（批 K：不能因为 Registry 存在就把 discipline 带回权威）。
EXPECTED_ROSTER: tuple[str, ...] = tuple(d.agent_id for d in AGENT_REGISTRY if d.spawned)

def _sole_spawned_stage2(registry: tuple[AgentDefinition, ...]) -> str:
    """Stage 2 里**唯一真正会被 spawn 的那个**（制衡层入口）—— 收编前是
    `orchestrator.py` 的独立字面量 ``RISK_AGENT = "risk"``。

    🔴 fail-closed：`discipline` 也在 Stage 2，但 `spawned=False`，所以「Stage 2
    且 spawned」今天恰好只有 risk 一个。哪天有人把 discipline 改成 spawned
    （Phase 3 接上输入源），这里会在 import 时当场炸 —— 强迫改名册的人想清楚
    「谁是那个被编排器 `ad.start()` 的制衡层入口」，而不是让 `RISK_AGENT` 静默
    取到两个里的第一个（那是 R-3：算不准了却给个看似正常的答案）。

    抽成纯函数只为**可测**：`tests/test_agent_registry.py` 拿一个「两个 spawn 的
    Stage 2」的假名册喂进来，断言它真的抛 —— 而不用在测试里重抄一遍判据（L-3）。
    """
    spawned = tuple(d.agent_id for d in registry if d.stage == 2 and d.spawned)
    if len(spawned) != 1:
        raise RuntimeError(
            f"AGENT_REGISTRY 里 Stage 2 恰好应有一个会被 spawn 的制衡 agent，"
            f"实际是 {list(spawned)} —— RISK_AGENT 的派生前提破了。"
            f"改名册时必须同时确定谁是编排器 spawn 的那个制衡层入口。")
    return spawned[0]


RISK_AGENT: str = _sole_spawned_stage2(AGENT_REGISTRY)
