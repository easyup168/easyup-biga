"""契约层行为测试 —— 四条铁律必须在「构造时拒绝」，而不是事后记日志。

这些测试的共同形状是：**断言非法状态构造不出来**。
理由见 verdict.py 的模块 docstring：静默 fail-open 是本项目最优先防范的失败模式，
唯一可靠的对策是让非法状态根本无法被表示。
"""

from __future__ import annotations

import pathlib
import sys
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

from _contract import (  # noqa: E402
    STANCE_VOCAB,
    VETO_STANCE,
    AgentVerdict,
    DecisionCard,
    Evidence,
    MissingItem,
    new_task_id,
    now_cn,
)

T0 = now_cn()
TID = new_task_id(1, day="20260919")


def ev(field: str = "limit_up", value=42, ago: int = 300, **kw) -> Evidence:
    return Evidence(
        field=field,
        source=kw.pop("source", "biga.db:raw_market_snapshot"),
        value=value,
        as_of=T0 - timedelta(seconds=ago),
        retrieved_at=T0,
        **kw,
    )


def verdict(**kw) -> AgentVerdict:
    # contract-exempt: 构造真 dataclass 的 kwargs，不是第二套契约
    base = dict(
        task_id=TID,
        agent="emotion",
        status="completed",
        verdict="PASS",
        result={"limit_up": 42},
        data_completeness=0.8,
        evidence=[ev()],
    )
    base.update(kw)
    return AgentVerdict(**base)


def _full_roster() -> list[AgentVerdict]:
    """六个已建成 agent 各出一条最小合法判定——大多数测试不关心 roster
    是否齐全，给它们一份「本来就齐」的默认值，省得每条都要单独处理
    F-6 之后 `_check_roster()` 的「缺席且无 missing 解释即拒」判据。"""
    return [verdict(agent=a) for a in sorted(STANCE_VOCAB)]


def _roster_with(risk_stance: str | None = None) -> list[AgentVerdict]:
    """完整 roster，但 risk 的 stance 可以被指定——否决权测试要用。"""
    return [verdict(agent="risk", stance=risk_stance) if a == "risk" else verdict(agent=a)
            for a in sorted(STANCE_VOCAB)]


def _roster_with_verdict(v: AgentVerdict) -> list[AgentVerdict]:
    """完整 roster，但 v.agent 那个位置换成给定的 verdict。

    给需要控制某一个 agent 的 missing/verdict/status（而不是 stance）
    又不想触发 F-8 之后 roster 计数判据的测试用——`_roster_with` 管的
    是 stance，这个管的是其余字段。
    """
    return [v if a == v.agent else verdict(agent=a) for a in sorted(STANCE_VOCAB)]


def card(**kw) -> DecisionCard:
    # contract-exempt: 同上
    base = dict(
        decision_id=TID,
        status="WAIT",
        headline="核心矛盾一句话",
        verdicts=_full_roster(),
        synthesis="",
        model_ref="anthropic/claude-sonnet-5",
    )
    base.update(kw)
    return DecisionCard(**base)


# ---------------------------------------------------------------- Evidence


class TestEvidence:
    def test_naive_datetime_被拒(self):
        with pytest.raises(ValueError, match="tzinfo"):
            Evidence(
                field="x", source="s", value=1,
                as_of=datetime(2026, 9, 19, 10, 0),
                retrieved_at=T0,
            )

    def test_as_of_晚于_retrieved_at_被拒(self):
        with pytest.raises(ValueError, match="晚于"):
            Evidence(
                field="x", source="s", value=1,
                as_of=T0 + timedelta(seconds=1), retrieved_at=T0,
            )

    def test_空_source_被拒(self):
        with pytest.raises(ValueError, match="source"):
            Evidence(field="x", source="  ", value=1, as_of=T0, retrieved_at=T0)

    def test_staleness_sec(self):
        assert ev(ago=90).staleness_sec == 90

    def test_序列化往返(self):
        e = ev(label="涨停家数", calc_version="v1")
        assert Evidence.from_dict(e.to_dict()) == e

    def test_frozen(self):
        with pytest.raises(Exception):
            ev().value = 99  # type: ignore[misc]


# ------------------------------------------------------------ AgentVerdict


class TestVerdictIronLaw1:
    """铁律 1：UNKNOWN ≠ PASS。算不出来必须说算不出来。"""

    def test_missing非空时不许PASS(self):
        with pytest.raises(ValueError, match="UNKNOWN ≠ PASS"):
            verdict(verdict="PASS", missing=["最高板算不出来"])

    def test_missing非空时不许completed(self):
        with pytest.raises(ValueError, match="partial"):
            verdict(status="completed", verdict="WARNING", missing=["最高板算不出来"])

    def test_missing非空时WARNING加partial是合法的(self):
        v = verdict(status="partial", verdict="WARNING", missing=["最高板算不出来"])
        # 🔴 A1 之后 MissingItem 的 == 只看 code，不回退到文本比较
        #    （见 _contract/missing.py）—— 比较人话内容要显式取 .detail。
        assert [m.detail for m in v.missing] == ["最高板算不出来"]

    def test_UNKNOWN必须说明缺什么(self):
        # 没有 missing 的 UNKNOWN 等于一个没有理由的「不知道」，Card 上会是空的缺失项
        with pytest.raises(ValueError, match="missing 为空"):
            verdict(status="partial", verdict="UNKNOWN", missing=[])

    def test_failed只能配UNKNOWN(self):
        with pytest.raises(ValueError, match="failed"):
            verdict(status="failed", verdict="WARNING", missing=["数据源挂了"])


class TestVerdictIronLaw3:
    """铁律 3：每个 result 字段必须能追到至少一条 Evidence。"""

    def test_无证据的result字段被拒(self):
        with pytest.raises(ValueError, match="无证据支撑"):
            verdict(result={"limit_up": 42, "max_streak": 7}, evidence=[ev("limit_up")])

    def test_空result不需要证据(self):
        v = verdict(result={}, evidence=[], verdict="UNKNOWN",
                    status="partial", missing=["全部数据源不可用"])
        assert v.result == {}

    def test_多条证据支撑同一字段(self):
        v = verdict(
            result={"limit_up": 42},
            evidence=[ev("limit_up", source="em:api/a"), ev("limit_up", source="sina:b")],
        )
        assert len(v.evidence_for("limit_up")) == 2


class TestVerdictIronLaw4:
    """铁律 4：不许自建第二套结构。"""

    def test_鸭子类型的证据被拒(self):
        # contract-exempt: 鸭子类型，用于断言契约会拒绝它
        class FakeEvidence:
            field = "limit_up"

        with pytest.raises(TypeError, match="铁律 4"):
            verdict(evidence=[FakeEvidence()])

    def test_card拒绝非AgentVerdict(self):
        # contract-exempt: 同上
        class FakeVerdict:
            agent = "emotion"
            missing: list[str] = []
            verdict = "PASS"

        with pytest.raises(TypeError, match="铁律 4"):
            card(verdicts=[FakeVerdict()])


class TestVerdictBasics:
    @pytest.mark.parametrize("bad", ["BIGA-2026919-001", "biga-20260919-001",
                                     "BIGA-20260919-1", "", "BIGA-20260919-0001"])
    def test_task_id格式(self, bad):
        with pytest.raises(ValueError, match="task_id"):
            verdict(task_id=bad)

    @pytest.mark.parametrize("bad", [-0.1, 1.1])
    def test_data_completeness范围(self, bad):
        with pytest.raises(ValueError, match="data_completeness"):
            verdict(data_completeness=bad)

    def test_未知verdict值被拒(self):
        with pytest.raises(ValueError, match="verdict"):
            verdict(verdict="OK")

    def test_max_staleness_取最旧那条(self):
        v = verdict(evidence=[ev(ago=10), ev(ago=900)])
        assert v.max_staleness_sec == 900

    def test_无证据时max_staleness为负一(self):
        v = verdict(result={}, evidence=[], verdict="UNKNOWN",
                    status="partial", missing=["无数据"])
        assert v.max_staleness_sec == -1

    def test_序列化往返(self):
        v = verdict(status="partial", verdict="WARNING",
                    missing=["最高板"], warnings=["炸板率偏高"])
        assert AgentVerdict.from_dict(v.to_dict()).to_dict() == v.to_dict()

    def test_历史卡只有confidence字段仍能还原(self):
        """A7：confidence → data_completeness，from_dict 双键兼容。

        探针：造一份「只有旧字段名」的字典（模拟 A7 之前落库的原件），
        断言 from_dict 仍能读出正确的值——不是靠巧合，是靠双键兼容那行代码。
        """
        # contract-exempt: 手搓的是「历史落库格式」，故意只带旧键名
        old = dict(task_id=TID, agent="emotion", status="completed",
                  verdict="PASS", result={"limit_up": 42}, confidence=0.75,
                  evidence=[ev().to_dict()], warnings=[], missing=[],
                  elapsed_ms=1, stance=None)
        assert AgentVerdict.from_dict(old).data_completeness == 0.75

    def test_新字段优先于旧字段(self):
        """两个键都在时（理论上不该发生，但要有明确行为）：新键说了算。"""
        # contract-exempt: 同上
        d = dict(task_id=TID, agent="emotion", status="completed",
                 verdict="PASS", result={"limit_up": 42},
                 data_completeness=0.9, confidence=0.1,
                 evidence=[ev().to_dict()], warnings=[], missing=[],
                 elapsed_ms=1, stance=None)
        assert AgentVerdict.from_dict(d).data_completeness == 0.9

    def test_new_task_id(self):
        assert new_task_id(7, day="20260919") == "BIGA-20260919-007"
        with pytest.raises(ValueError):
            new_task_id(1000, day="20260919")


class TestVerdictFrozen:
    """A2：AgentVerdict frozen=True + list/dict → tuple/Mapping。

    只 frozen 挡得住 `v.x = ...` 重新赋值，挡不住「对同一个可变对象原地改」——
    `tests/test_write_boundary.py` P1 的原始探针就是 `v.missing.append(...)`，
    它不重跑 `__post_init__`，写边界重校验才是最后一道防线。
    这里补的是 A2 自己的那一半：list/dict 换成 tuple/Mapping 之后，
    `.append()` 这类原地修改从**语法上**就不再存在。
    """

    def test_赋值被拒(self):
        with pytest.raises(Exception):
            verdict().verdict = "WARNING"  # type: ignore[misc]

    def test_missing不能原地追加(self):
        with pytest.raises(AttributeError):
            verdict().missing.append(MissingItem("x", "market.turnover.unavailable"))

    def test_evidence不能原地追加(self):
        with pytest.raises(AttributeError):
            verdict().evidence.append(ev())

    def test_warnings不能原地追加(self):
        with pytest.raises(AttributeError):
            verdict(warnings=["w"]).warnings.append("another")

    def test_result不能原地写入(self):
        with pytest.raises(TypeError):
            verdict().result["limit_up"] = 999


class TestMissingItemFrozen:
    """A2 补漏（评审 F-5）：`MissingItem` 不是 dataclass，是手写的 `str`
    子类，`__slots__` 只声明存储位置，从不阻止赋值。

    `TestVerdictFrozen` 测的是「容器不能原地追加」（`tuple` 没有
    `.append()`），从没测过「容器里的元素本身不能被改」——
    `v.missing[0].code = "换一个"` 在只做了前者的情况下完全不受影响。
    这是同一个 L-13 形状：探针测了容器，没测容器里的元素。

    `.code` 正是 A1 建立的身份信号（`__eq__`/`__hash__` 都只看它）：
    能改 `.code` 就等于能把一条"真实缺数据"的证据静默重分类成任意
    别的缺失原因，且不触发任何 `__post_init__` 校验（A3 写边界重校验
    只检查"这是不是一个合法的 MissingItem"，不检查"code 有没有被换过"）。
    """

    def test_code不能被重新赋值(self):
        m = MissingItem("测试", "market.turnover.unavailable")
        with pytest.raises(FrozenInstanceError):
            m.code = "supervisor.agent_offline"  # type: ignore[misc]

    def test_code不能被删除(self):
        m = MissingItem("测试", "market.turnover.unavailable")
        with pytest.raises(FrozenInstanceError):
            del m.code

    def test_装在tuple里的元素同样不可变(self):
        """回归场景：正是评审指出的那条路——`v.missing[0].code = ...`。"""
        v = verdict(status="partial", verdict="WARNING", missing=["最高板算不出来"])
        with pytest.raises(FrozenInstanceError):
            v.missing[0].code = "换一个身份"  # type: ignore[misc]

    def test_构造阶段不受影响(self):
        """反面：`__new__` 内部的 `object.__setattr__` 不该被这条挡住。"""
        m = MissingItem("测试", "market.turnover.unavailable")
        assert m.code == "market.turnover.unavailable"


# ------------------------------------------------------------ DecisionCard


class TestCardIronLaw2:
    """铁律 2：missing 非空 ⇒ 不得给 BUY。"""

    def test_missing非空时不许BUY(self):
        # 🔴 满 roster，让 _check_roster() 通过、真正测到后面那条铁律 2
        # ——否则 1 个 agent + 1 条 missing 会先被 F-8 之后的计数判据拒绝。
        v = verdict(status="partial", verdict="WARNING", missing=["最高板"])
        with pytest.raises(ValueError, match="不得给买入结论"):
            card(status="BUY", verdicts=_roster_with_verdict(v), missing=["最高板"])

    def test_missing非空时WAIT是合法的(self):
        v = verdict(status="partial", verdict="WARNING", missing=["最高板"])
        assert card(status="WAIT", verdicts=_roster_with_verdict(v),
                    missing=["最高板"]).status == "WAIT"

    def test_缺失项必须上浮到Card(self):
        # Verdict 说缺了东西，Card 却没列出来 —— 这是静默 fail-open 的典型形状
        # （这条检查在 _check_roster() 之前跑，不受 F-8 计数判据影响，
        #  不需要满 roster）。
        v = verdict(status="partial", verdict="WARNING", missing=["最高板"])
        with pytest.raises(ValueError, match="没有上浮"):
            card(verdicts=[v], missing=[])

    def test_Card可以追加自己发现的缺失项(self):
        v = verdict(status="partial", verdict="WARNING", missing=["最高板"])
        c = card(verdicts=_roster_with_verdict(v),
                 missing=["最高板", "板块扩散度覆盖率不足"])
        assert len(c.missing) == 2


class TestCardVeto:
    """制衡层的否决权。

    🔴 判据是 `stance == VETO_STANCE`，不是 `verdict`。
    `verdict` 只说数据全不全 —— 一个字段装不下「数据完整」和「我要否决」两件事：
    risk 数据完整且要否决时，`verdict` 填 BLOCK 就再也说不出它的数据是全的，
    而「凭什么否决」恰恰需要知道。
    """

    def test_否决不可被合成阶段绕过(self):
        with pytest.raises(ValueError, match="否决权"):
            card(status="BUY", verdicts=_roster_with(risk_stance=VETO_STANCE))

    def test_否决配AVOID是合法的(self):
        c = card(status="AVOID", verdicts=_roster_with(risk_stance=VETO_STANCE))
        assert c.status == "AVOID"

    def test_否决配WAIT被拒(self):
        """WAIT 是「再看看」，否决是「不要做」。把后者显示成前者就是软化制衡层。"""
        with pytest.raises(ValueError, match="否决必须体现"):
            card(status="WAIT", verdicts=_roster_with(risk_stance=VETO_STANCE))

    def test_否决这个词只有一处定义(self):
        """改了词表却忘了改判据，否决权会**静默失效** —— 那是最怕的 fail-open。"""
        from _contract import STANCE_VOCAB
        assert VETO_STANCE in STANCE_VOCAB["risk"]

    def test_数据不全的risk同样拦不住BUY(self):
        """L-2 买入侧 fail-closed：risk 说不上话时，不许当作放行。"""
        # 🔴 满 roster，让 _check_roster() 通过、测到后面那条铁律 2。
        unknown = verdict(agent="risk", verdict="UNKNOWN", status="partial",
                          stance="无法判定", missing=["风险面 —— 上游证据不足"])
        with pytest.raises(ValueError, match="铁律 2"):
            card(status="BUY", verdicts=_roster_with_verdict(unknown),
                 missing=["风险面 —— 上游证据不足"])


class TestCardRoster:
    """A5：Card roster 结构化 —— 拒绝重复 agent；缺席须有缺失项解释才放行。

    评审 F-6 之后的裁定（折中方案）：缺席本身不是错误（Phase 1/2 单 agent
    skeleton、agent 掉线都是合法场景），但**缺席且解释不够**才是错误——
    那正是"静默"两个字要防的东西。

    评审 F-8（对 F-6 的第二轮独立复核）指出："解释不够"最初的判据只问
    「missing 是不是非空」，不问「够不够」——5 个 agent 缺席，随便挂 1 条
    毫不相关的 missing 也能通过。收紧成**计数**：`missing` 条数必须不少于
    缺席 agent 数，不比较任何文本（`_check_roster` 的 docstring 里写了
    为什么不比较文本——L-13）。
    """

    #: F-8 之后的用例需要"缺席几个就给几条解释"——内容不必精确对应
    #: 哪个 agent（判据本来就不比较文本），这里凑够 5 条只是为了可读。
    _FIVE_EXPLANATIONS = [f"{a} agent 尚未上线"
                         for a in ("market", "sector", "technical", "news", "risk")]

    def test_重复agent被拒(self):
        """探针：造一个重复 agent —— 两条判定，同一个 agent 名字。"""
        with pytest.raises(ValueError, match="不止一条判定"):
            card(verdicts=[verdict(agent="market"), verdict(agent="market")])

    def test_旧卡里的重复agent只警告不拒(self):
        """新卡严格，旧卡可读 —— 与 identity/restated-missing 同一套三段式。"""
        c = card(verdicts=[verdict(agent="market"), verdict(agent="market")],
                 from_store=True)
        assert c.roster_warning and "不止一条判定" in c.roster_warning
        assert "不止一条判定" in c.render()

    def test_缺席且无解释被拒(self):
        """探针：造一个缺席 agent，且不给任何 missing 解释——必须报红。

        这条对应设计文档表格字面探针「缺席也报红」；F-6 之前的版本
        在这里只报告不拒绝，评审指出与表格不一致，折中方案把这条补上。
        """
        with pytest.raises(ValueError, match="缺席.*但 missing 只有 0 条"):
            card(verdicts=[verdict(agent="emotion")], missing=[])

    def test_缺席多于missing条数仍被拒(self):
        """F-8 的回归：只解释了一部分缺席，不该被"非空"这么弱的判据放行。

        5 个 agent 缺席，只给 1 条解释——F-6 最初的版本会放行这个场景
        （只问"非空"），F-8 之后必须拒绝（1 < 5）。
        """
        with pytest.raises(ValueError, match="但 missing 只有 1 条"):
            card(verdicts=[verdict(agent="emotion")],
                 missing=["risk agent 尚未上线"])

    def test_缺席数与missing条数相等就放行(self):
        """5 个 agent 缺席、5 条解释——计数够了，哪怕内容不精确对应任何一个。"""
        c = card(verdicts=[verdict(agent="emotion")], missing=self._FIVE_EXPLANATIONS)
        assert set(STANCE_VOCAB) - {"emotion"} == set(c.absent_agents)

    def test_missing条数多于缺席数也放行(self):
        """判据是"不少于"，不是"恰好等于"——多给解释不该被拒。"""
        c = card(verdicts=[verdict(agent="emotion")],
                 missing=self._FIVE_EXPLANATIONS + ["额外一条"])
        assert set(STANCE_VOCAB) - {"emotion"} == set(c.absent_agents)

    def test_旧卡里缺席无解释只警告不拒(self):
        """新卡严格，旧卡可读——与重复 agent 同一套三段式。"""
        c = card(verdicts=[verdict(agent="emotion")], missing=[], from_store=True)
        assert c.roster_warning and "但 missing 只有 0 条" in c.roster_warning

    def test_absent_agents报出缺席(self):
        """探针：同一个缺席场景，`absent_agents` 必须如实报出来。"""
        c = card(verdicts=[verdict(agent="emotion")], missing=self._FIVE_EXPLANATIONS)
        assert "emotion" not in c.absent_agents
        assert set(STANCE_VOCAB) - {"emotion"} == set(c.absent_agents)
        assert len(c.absent_agents) >= 5

    def test_全员到齐时absent_agents为空(self):
        c = card()  # 默认已是满员 roster
        assert c.absent_agents == ()

    def test_缺席出现在卡面上(self):
        """评审 F-6：absent_agents 曾经零消费方——算出来了，但没人读。

        卡面是"谁会去找它"判据下最低成本的消费方：不需要新增巡检工具，
        render() 本来就会被每一次出卡调用。
        """
        c = card(verdicts=[verdict(agent="emotion")], missing=self._FIVE_EXPLANATIONS)
        text = c.render()
        assert "已建成 roster 缺席" in text
        for a in c.absent_agents:
            assert a in text

    def test_全员到齐时卡面不显示缺席行(self):
        assert "已建成 roster 缺席" not in card().render()

    def test_roster参照的是STANCE_VOCAB不是自成一套(self):
        """dev-workflow 第五问：这份「已建成的 roster」清单不该是第二份手抄。"""
        from _consistency import built_specialists
        assert set(STANCE_VOCAB) == built_specialists()


class TestCardBasics:
    def test_空verdicts被拒(self):
        with pytest.raises(ValueError, match="verdicts 不能为空"):
            card(verdicts=[])

    def test_空headline被拒(self):
        with pytest.raises(ValueError, match="headline"):
            card(headline="   ")

    def test_空model_ref被拒(self):
        # 没有 model_ref 就无法做换模型对比，回放的主要用途之一直接作废
        with pytest.raises(ValueError, match="model_ref"):
            card(model_ref="")

    def test_generated_at自动填充且带时区(self):
        assert "+08:00" in card().generated_at

    def test_render永远显示缺失项段落(self):
        # 「没有缺失项」本身是一条信息，不能因为是空的就不渲染
        assert "缺失项：无" in card().render()
        v = verdict(status="partial", verdict="WARNING", missing=["最高板"])
        assert "⚠ 缺失项（1）" in card(verdicts=_roster_with_verdict(v),
                                     missing=["最高板"]).render()

    def test_render含免责声明(self):
        assert "不构成投资建议" in card().render()

    def test_verdict_of(self):
        c = card()
        assert c.verdict_of("emotion") is not None
        # "discipline" 裁定 13 故意不建，永远不在任何 roster 里——
        # 用它而不是某个已建成的 agent，因为 card() 现在默认满员（F-6）。
        assert c.verdict_of("discipline") is None

    def test_is_complete(self):
        assert card().is_complete is True
        v = verdict(status="partial", verdict="WARNING", missing=["最高板"])
        assert card(verdicts=_roster_with_verdict(v),
                   missing=["最高板"]).is_complete is False

    def test_序列化往返(self):
        c = card()
        assert DecisionCard.from_dict(c.to_dict()).to_dict() == c.to_dict()


class TestF8UnregisteredAgentStance:
    """外部评审 F8：词表校验原来是「命中才查，命不中就放行」。

        agent="market",     stance="超级看多"  → 正确拒绝
        agent="Market",     stance="随便乱写"  → 通过（大小写 typo）
        agent="discipline", stance="瞎编的"    → 通过（Phase 3 还没登记）

    🔴 这是一颗定时炸弹，不是已经发生的事故 ——
    6 个已登记 agent 的约束是真实生效的（评审也验证了这点）。
    但 Phase 3 建 `discipline` 时忘加一行（纯手工步骤，没有清单强制），
    它的 stance 从那天起完全不受约束，而 `AGENTS.md` 里
    「契约层会直接拒绝表外词」这句话，读者会以为对全系统成立。

    ⚠️ 原来唯一的交叉校验用 `parametrize("agent", sorted(STANCE_VOCAB))` ——
    **天然只测「已经正确登记」的 agent**，覆盖不到「忘记登记」这个分支。
    """

    def test_未登记的agent直接拒绝(self):
        with pytest.raises(ValueError, match="没有登记 stance 词表"):
            verdict(agent="discipline", stance="瞎编的方向")

    def test_大小写typo也算未登记(self):
        """`Market` 不是 `market` —— 静默放行时这种错最难查：
        agent 名在日志里长得几乎一样。"""
        with pytest.raises(ValueError, match="没有登记 stance 词表"):
            verdict(agent="Market", stance="随便乱写的词")

    def test_已登记的照常工作(self):
        v = verdict(agent="market", stance="分化")
        assert v.stance == "分化"
        with pytest.raises(ValueError, match="不在该 agent 的词表里"):
            verdict(agent="market", stance="超级看多")

    def test_报错要指出去哪加(self):
        """🔴 报错要指路 —— 只说「不行」的守卫会被绕过，不会被修好。"""
        try:
            verdict(agent="discipline", stance="瞎编的方向")
        except ValueError as e:
            assert "STANCE_VOCAB" in str(e) and "已登记" in str(e)


class TestCardFrozen:
    """A2：DecisionCard frozen=True + list → tuple。

    🔴 追加 4（A-I 评审复核）点名的那一条：`card.from_store = True` 曾经
    不会报错，能把写边界重校验的「新卡严/旧卡宽」判据一起绕开
    （`_store/db.py::save_card` 现在从 `replay_of` 参数推导档位，不再看
    `card.from_store`——但对象本身也不该再允许被这样改，两道防线不冲突）。
    """

    def test_赋值被拒(self):
        with pytest.raises(Exception):
            card().status = "AVOID"  # type: ignore[misc]

    def test_篡改from_store被拒(self):
        with pytest.raises(Exception):
            card().from_store = True  # type: ignore[misc]

    def test_verdicts不能原地追加(self):
        with pytest.raises(AttributeError):
            card().verdicts.append(verdict())

    def test_missing不能原地追加(self):
        v = verdict(status="partial", verdict="WARNING", missing=["最高板"])
        with pytest.raises(AttributeError):
            card(verdicts=_roster_with_verdict(v), missing=["最高板"]).missing.append(
                MissingItem("事后塞的", "market.turnover.unavailable"))
