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

from .missing import LEGACY_CODE, MissingItem
from .registry import EXPECTED_ROSTER
from .verdict import VETO_STANCE, AgentVerdict
from .verdict_ref import VerdictRef

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


#: 全角 → 半角，用于判断两条缺失项是不是同一句话。
#: 🔴 LLM 重述时最先变的就是标点 —— 实测 `：，` 被打成 `:,`。
_PUNCT = str.maketrans("：，。；、（）「」“”‘’！？　", ":,.;,()\"\"\"\"\'\'!? ")


def _norm_text(text: str) -> str:
    """归一化到「说的是不是同一句话」的粒度：标点折叠 + 去掉所有空白。"""
    return "".join(str(text).translate(_PUNCT).split()).lower()


@dataclass(frozen=True)
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
    verdicts: tuple[AgentVerdict, ...]
    synthesis: str
    model_ref: str
    missing: tuple[MissingItem, ...] = dc_field(default_factory=tuple)
    generated_at: str = ""
    elapsed_ms: int = 0
    #: 🔴 只有 `from_dict()` 会设成 True —— 表示「这是从库里读回来的历史记录」。
    #:
    #: 存在的理由见 `_check_identity()`：身份约束对**新造的卡**必须是硬拒绝，
    #: 但对**已经落库的旧卡**只能是提示 —— 否则历史卡再也回放不了，
    #: 而「能不能重建当时看到的东西」比形式一致更重要。
    from_store: bool = False
    #: 历史卡的身份问题记在这里，由 `render()` 显示。新卡永远为空（它直接被拒）。
    identity_warning: str = ""
    #: 旧卡里检出「同一件事报了两遍」时的提示（新卡直接拒）
    restate_warning: str = ""
    #: 旧卡里检出「同一个 agent 出现了不止一条判定」时的提示（新卡直接拒）
    roster_warning: str = ""
    #: 🔴 A6：这张卡用的每条判定原件，指向 `agent_verdicts` 的哪一行 + 当时的哈希。
    #: 历史卡没有这份数据（`from_dict` 缺省成空 tuple）——它是可追加的证据，
    #: 不是必需品；核对逻辑见 `_store.verify_verdict_refs()`。
    input_verdict_refs: tuple[VerdictRef, ...] = dc_field(default_factory=tuple)
    #: 🔴 批 J-I（可选、默认 None）：这张卡是哪次编排执行尝试（`RunContext.run_id`）
    #: 合成出来的。在线路径由 `card_ops.synthesize(run_id=ctx.run_id)` 填；历史卡与手工
    #: 合成没有 ctx，为 None（capture 不 enforce）。回放**不**给历史卡凭空捏一个 run_id。
    run_id: str | None = None
    #: 🔴 批 K（可选、默认 None）：**生成时冻结的期望 roster** —— 这张卡合成那一刻，
    #: `AGENT_REGISTRY` 算出的「理应作答的 agent」名单（`EXPECTED_ROSTER`，即会被
    #: spawn 的那几个）。`absent_agents` 优先读它，而不是现算现取**今天**的 Registry。
    #:
    #: 为什么要冻结：`absent_agents` 曾是 `@property`，每次读都用**当下**的名册去减。
    #: 一张三个月前的卡今天重新加载，若这期间 roster 变过（加了 agent、或某个一度
    #: 下线），同一张历史卡的这个字段会在不同时间点给出不同答案，而 `card_json`
    #: 本身没变 —— 一处静默漂移。冻结进 `card_json` 把「当时期望谁」钉死在卡里。
    #:
    #: 在线路径由 `card_ops.synthesize(expected_roster=EXPECTED_ROSTER)` 填；老卡
    #: （`card_json` 里没有这个字段）为 None ⇒ `absent_agents` 回退到读今天的
    #: Registry（「有就用、缺就退回」，同 J-I run_id / E-I LegacyAdapter 的形状）。
    #: 🔴 **不给老卡回填**（raw/历史永不改写，L-8）—— 缺就靠回退兜底，不事后补写。
    expected_roster: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        # 🔴 A2：frozen 之后不能再 `self.x = ...`，改用 object.__setattr__。
        #    verdicts/missing 一并转成 tuple —— 光 frozen 挡得住重新赋值
        #    `card.missing = [...]`，挡不住原地 `card.missing.append(...)`
        #    （tests/test_write_boundary.py 的 P2 探针就是这么绕过旧校验的）。
        object.__setattr__(self, "verdicts", tuple(self.verdicts))
        object.__setattr__(
            self, "missing", tuple(MissingItem.coerce(m) for m in self.missing))
        object.__setattr__(
            self, "input_verdict_refs", tuple(self.input_verdict_refs))
        # 🔴 批 K：冻结名单可空；给了就 tuple 化 —— 光 frozen 挡不住调用方原地
        #    `card.expected_roster.append(...)`（同 verdicts/missing 的处理）。
        if self.expected_roster is not None:
            object.__setattr__(self, "expected_roster", tuple(self.expected_roster))

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
        for r in self.input_verdict_refs:
            if not isinstance(r, VerdictRef):
                raise TypeError(
                    f"input_verdict_refs 必须全部是 _contract.VerdictRef，"
                    f"收到 {type(r).__name__}（铁律 4）"
                )
        if not self.headline.strip():
            raise ValueError("headline 不能为空 —— Card 必须给出一句话的核心矛盾")
        if not self.model_ref.strip():
            raise ValueError("model_ref 不能为空 —— 回放对比要靠它标定这次是谁合成的")
        # 批 J-I：run_id 可空（capture 不 enforce）；给了就必须是非空字符串。
        if self.run_id is not None and (not isinstance(self.run_id, str) or not self.run_id.strip()):
            raise ValueError(
                f"run_id 要么是 None，要么是非空字符串，收到 {self.run_id!r}")
        # 批 K：expected_roster 可空；给了就必须是一串非空字符串（agentId）。
        if self.expected_roster is not None and not all(
                isinstance(a, str) and a.strip() for a in self.expected_roster):
            raise ValueError(
                f"expected_roster 要么是 None，要么是一串非空 agentId 字符串，"
                f"收到 {self.expected_roster!r}")

        if not self.generated_at:
            from .evidence import now_cn

            object.__setattr__(self, "generated_at", now_cn().isoformat())

        self._check_restated_missing()

        # --- 缺失项必须完整上浮：漏报一条，Card 就在掩盖它自己不知道的事 ---
        upstream = {m for v in self.verdicts for m in v.missing}
        dropped = sorted(upstream - set(self.missing))
        if dropped:
            raise ValueError(
                f"Verdict 报告的缺失项没有上浮到 Card: {dropped} —— "
                "缺失项必须逐条显示，汇总时丢弃等于静默 fail-open"
            )

        self._check_identity()
        self._check_roster()

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

    def _check_restated_missing(self) -> None:
        """同一件事不许在 `missing[]` 里出现两遍 —— 裁定 15 + L-10。

        🔴 实测（`BIGA-20260921-021`，飞书触发的第一张卡）：

            risk.upstream.coverage_incomplete  Stage 1 缺席：news，这些领域的风险本次没有被看过
            risk.coverage.insufficient         Stage 1 缺席:news,这些领域的风险本次没有被看过

        同一段话两个代码。**注意标点**：全角 `：，` vs 半角 `:,` ——
        后者是 LLM 重打出来的，这正是 L-10 要防的「让 LLM 搬运结构化数据」。

        为什么判据落在**正文**而不是代码前缀
        ------------------------------------
        六份 specialist 契约**全都**指示 agent 加自己命名空间下的缺失代码，
        而那多数是合法的：skill 报「数据没取到」，agent 报「我据此判断不了」
        —— 那是两件事（约定 S-2）。按命名空间判会把六条全误伤。

        真正可判定的信号是**这两条说的是同一句话**。
        ⇒ 归一化标点与空白后比较，重复即拒。

        ⚠️ 旧卡只警告不拒（「新卡严格，旧卡可读」）——
        库里已经有这样的卡，回放不该因此崩掉。
        """
        # 🔴 判据是 **(命名空间, 正文)**，不是光看正文。
        #
        #    因为「两个不同的源各自报『数据源不可用』」是**两件事** ——
        #    合并会让统计少一条（`tests/test_stance_and_traceability.py`
        #    里那条测试专门钉着这个反例，第一版判据当场把它判红了）。
        #
        #    真正的违例是**同一个生产方把同一句话说了两遍**。
        seen: dict[tuple[str, str], MissingItem] = {}
        dup: list[tuple[str, str, str]] = []
        for m in self.missing:
            ns = m.code.split(".")[0]
            if ns in ("", LEGACY_CODE):        # 旧数据没有命名空间，不参与
                continue
            key = (ns, _norm_text(m))
            if key in seen and seen[key].code != m.code:
                dup.append((seen[key].code, m.code, str(m)[:40]))
            seen.setdefault(key, m)
        if not dup:
            return
        detail = "；".join(f"{a} 与 {b} 都在说「{t}…」" for a, b, t in dup)
        msg = (f"Card {self.decision_id} 的缺失项里同一件事报了两遍：{detail}\n"
               f"  同一个事实只能有一个生产方（裁定 15）。\n"
               f"  多半是契约让 agent 重述了 skill 已经报出的缺失 —— "
               f"而重述会把标点和措辞改掉，事后没法聚合。")
        if not self.from_store:
            raise ValueError(msg)
        object.__setattr__(self, "restate_warning", msg)

    def _check_identity(self) -> None:
        """🔴 卡上的每一条判定，都必须属于这张卡。

        外部评审 P1-1（2026-09-21）：`synthesize.py` 里已经有一道
        「所有 verdict 的 task_id 必须一致」的检查，但**契约层没有**。
        于是可以构造「卡 001 装着 999 的 verdict」并落库 —— 实测通过。

        守卫在编排层就只守得住走编排层的那条路。回放、将来的 API、
        测试辅助代码、手工构造 —— 每一条都能重新打开这个洞。

        ⚠️ **新卡严格，旧卡可读。**
        评审建议的验收是「Replay 也无法绕过」，但实测已落库 31 张卡里有
        **20 张**的 verdict 写着别的号（Stage 0 占号是后来才加的）。
        一刀切会让那 20 张永远读不出来。

        这与 `synthesize.py` 里 stance 检查的取舍是同一条：
        **能不能重建「当时看到的东西」优先于形式一致。**

        ⇒ 三段式：
          · 新造的卡        —— 直接拒绝
          · 从库里读的旧卡   —— 可读，但把问题记下来并**显示在卡面上**
          · 落库（`save_card`）—— 永远拒绝，见 `_store/db.py`
        """
        foreign = [(v.agent, v.task_id) for v in self.verdicts
                   if v.task_id != self.decision_id]
        if not foreign:
            return
        detail = "；".join(f"{a} 的判定写着 {t}" for a, t in foreign)
        if not self.from_store:
            raise ValueError(
                f"Card {self.decision_id} 装着不属于它的判定：{detail}\n"
                "  一张卡上的每一条判定都必须属于同一次决策，否则证据无处归属。\n"
                "  怎么办：决策编号由 Stage 0 占下（new_decision.py），"
                "沿 Stage 1/2/3 一路下传。")
        object.__setattr__(self, "identity_warning", (
            f"本卡的判定编号与卡号不一致（{detail}）—— "
            "它早于「Stage 0 统一占号」，证据归属无法核实"))

    def _check_roster(self) -> None:
        """🔴 设计文档 §6 A5：拒绝同一个 agent 出现不止一条判定；
        roster 不全且没有任何缺失项解释，同样拒绝。

        **重复**没有对应的合法场景：`verdicts=[market的判定, market的判定]`
        能直接构造（评审 §8 实测），没有任何字段说得清「该信哪一条」。

        **缺席**不一样——它在 Phase 1/2 是**已知且合法**的常态（single-agent
        walking skeleton；agent 掉线时 Supervisor 用 `supervisor.agent_offline`
        / `agent_no_response` 显式登记，见 `ORCHESTRATION.md`）。第一版做法是
        「缺席永不硬拒，只由 `absent_agents` 报告事实」——评审 F-6 指出这与
        设计表格「缺席也报红」的字面探针不一致，往上交后裁定为折中方案：

            roster 不全 且 self.missing 完全为空 ⇒ 拒绝
            roster 不全 但 self.missing 非空 ⇒ 放行

        F-6 落地后的第二轮独立复核（F-8）指出这条判据本身还留了一个口子：
        「非空」只要求**至少一条**解释，不管缺席了几个 agent——5 个 agent
        静默缺席，只要随便挂一条跟它们毫无关系的 `missing`（比如
        `market.turnover.stale`），一样能通过。真正需要收紧的不是"要不要
        精确对应"（按文本猜哪条 missing 对应哪个缺席 agent 仍然是 L-13
        要防的「按字符串形状分类」，这一半判断不动），而是有一个**不需要
        比较任何文本、只需要计数**就能拿到的信号：

            roster 不全 且 len(self.missing) < len(self.absent_agents) ⇒ 拒绝
            roster 不全 且 len(self.missing) >= len(self.absent_agents) ⇒ 放行

        为什么是"计数"而不是"精确对应"：`missing` 项的 code 前缀是发起方的
        命名空间（`supervisor.*`/`market.*`……），不是缺席 agent 的名字——
        用文本去猜"这条 missing 说的是不是那个缺席的 agent"，是本仓库
        反复踩过的「按字符串形状分类」陷阱（L-13）。计数不比较任何文本，
        只问"缺席几个、解释了几条"，对按 `ORCHESTRATION.md` 约定正确操作
        的路径（每个掉线 agent 各自登记一条）恒真，只在**漏报**时命中。
        解释得准不准，仍然留给人看卡面上的 `absent_agents` 那一行
        （已接进 `render()`）与各条 missing 自己判断——这条不变。

        🔴 在批 C 的 `DecisionOrchestrator` 把"要不要登记缺席"从 Supervisor
        LLM 的提示词自觉行为改成程序强制之前，"缺席登记"这件事今天完全
        依赖 LLM 老实执行 `ORCHESTRATION.md` 里的约定——这条计数判据是那个
        假设成立之前的一道结构性兜底，不是替代它。

        两条判据的宽严不能因为都叫「roster 问题」就合并成一套。

        ⚠️ 新卡严格，旧卡可读 —— 与 `_check_identity` / `_check_restated_missing`
        同一套三段式：能不能重建「当时看到的东西」优先于形式一致。
        """
        agents = [v.agent for v in self.verdicts]
        dupes = sorted({a for a in agents if agents.count(a) > 1})
        problems = []
        if dupes:
            problems.append(
                f"这些 agent 出现了不止一条判定：{dupes} —— "
                "一次决策里每个 agent 只能给一条判定，多出来的那条不知道该信哪个。")
        if self.absent_agents and len(self.missing) < len(self.absent_agents):
            problems.append(
                f"缺席 {list(self.absent_agents)}（{len(self.absent_agents)} 个），"
                f"但 missing 只有 {len(self.missing)} 条 —— "
                "缺席必须每个都显式登记（如 supervisor.agent_offline / "
                "agent_no_response），不许用一条解释掩盖多个缺席。")
        if not problems:
            return
        msg = f"Card {self.decision_id} 的 roster 有问题：" + "；".join(problems)
        if not self.from_store:
            raise ValueError(msg)
        object.__setattr__(self, "roster_warning", msg)

    # --- 便捷查询 ---

    def verdict_of(self, agent: str) -> AgentVerdict | None:
        for v in self.verdicts:
            if v.agent == agent:
                return v
        return None

    @property
    def absent_agents(self) -> tuple[str, ...]:
        """期望作答的 roster 里，这张卡没有判定的那些 agent。

        🔴 批 K：权威从「现算现取今天的 `STANCE_VOCAB` key 集」改成**优先读卡上
        生成时冻结的 `expected_roster`**。老卡（`card_json` 里没有这个字段 ⇒
        `expected_roster is None`）才回退到读今天的 `EXPECTED_ROSTER`（Registry
        派生）——「有就用、缺就退回」，同 J-I 的 run_id、E-I 的 LegacyAdapter。

        为什么要冻结：作为 `@property` 现算，一张历史卡的这个字段会随**当下**
        roster 变化而变，而 `card_json` 没变 —— 同一张卡在不同时间给出不同答案。
        冻结把「当时期望谁」钉进卡里。今天 `EXPECTED_ROSTER` 与 `STANCE_VOCAB`
        的 key 集相等（`tests/test_agent_registry.py` 钉住），所以对**今天**生成
        的卡，这次改动是空操作（探针 P1/P3）；`discipline` 不 spawn ⇒ 不在
        `EXPECTED_ROSTER` ⇒ 永不被判「缺席」（裁定 13，探针 P5）。

        🔴 评审 F-6/F-8 之后：非空**不代表**允许构造——`_check_roster()`
        按计数比较 `self.missing` 的条数与这里返回的缺席数，条数不够
        才拒绝（新卡）/ 警告（旧卡）。这里只算「谁缺席、缺几个」，不
        判断「缺席有没有被解释」，也**不**去猜「哪一条 missing 对应
        哪个缺席的 agent」——那需要按自由文本匹配 agent 名，是本仓库
        反复踩过的「按字符串形状分类」陷阱（L-13）。判据只比数量，
        不比内容。
        """
        roster = self.expected_roster if self.expected_roster is not None else EXPECTED_ROSTER
        return tuple(sorted(set(roster) - {v.agent for v in self.verdicts}))

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
        # 🔴 评审 F-6：absent_agents 曾经是零消费方——算出来了，但 render()
        #    不读它，missing_ledger.py / risk_check.py / bin/biga-card 也不读，
        #    于是「roster 是否齐全」这份事实实际上无处可查。
        #    缺席该不该硬拒是另一个尚待裁定的问题（见 TODO.md），
        #    但无论那条怎么定，"缺席是不是事实"不该只存在于一个没人读的属性里。
        if self.absent_agents:
            lines.append(f"已建成 roster 缺席：{', '.join(self.absent_agents)}")
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
        for w in (self.identity_warning, self.restate_warning, self.roster_warning):
            if w:
                warns.insert(0, ("card", w))
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
            "input_verdict_refs": [r.to_dict() for r in self.input_verdict_refs],
            "run_id": self.run_id,
            # 🔴 批 K：冻结名单序列化成 list（老卡 None ⇒ 缺这个键，from_dict 用 .get 兜）。
            "expected_roster": list(self.expected_roster)
            if self.expected_roster is not None else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, from_store: bool = True) -> "DecisionCard":
        """反序列化。默认 `from_store=True`（库里读回来的历史卡，三段式从宽）。

        ⚠️ 曾经这里写的是「`_store.save_card()` 会传 `from_store=card.from_store`」——
        **不成立**，那正是追加 4（A-I 评审复核）揪出的洞：`card.from_store` 是对象
        自己的属性，合法构造后 `card.from_store = True` 不会报错，会把这一层
        「新卡严格」悄悄绕开。`save_card()` 现在从**调用参数**
        `replay_of is not None` 推导档位，不看 `card.from_store`——
        `card_ops.persist()` 是唯一调用点，在线路径永远不传、
        `replay.py --store` 永远传原始 `record_id`，这个决定不受 card 对象
        本身状态影响。A2 把 `DecisionCard` 冻结之后，`card.from_store = True`
        本身也会直接 `FrozenInstanceError`——这里只是把文字改回和代码一致。
        """
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
            from_store=from_store,
            # 历史卡没有这个字段 —— 缺省成空 tuple，不是缺失的证据引用。
            input_verdict_refs=[VerdictRef.from_dict(x)
                                 for x in d.get("input_verdict_refs", [])],
            # 🔴 批 J-I：历史卡的 JSON 里没有这个键 —— `.get` 缺省 None，
            #    回放据此**不给历史卡凭空捏一个 run_id**（P4）。
            run_id=d.get("run_id"),
            # 🔴 批 K：老卡的 JSON 里没有这个键 —— `.get` 缺省 None，
            #    `absent_agents` 据此回退到读今天的 Registry（不给老卡回填，L-8）。
            expected_roster=d.get("expected_roster"),
        )
