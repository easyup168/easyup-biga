"""契约层行为测试 —— 四条铁律必须在「构造时拒绝」，而不是事后记日志。

这些测试的共同形状是：**断言非法状态构造不出来**。
理由见 verdict.py 的模块 docstring：静默 fail-open 是本项目最优先防范的失败模式，
唯一可靠的对策是让非法状态根本无法被表示。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from _contract import (
    VETO_STANCE,
    AgentVerdict,
    DecisionCard,
    Evidence,
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
        confidence=0.8,
        evidence=[ev()],
    )
    base.update(kw)
    return AgentVerdict(**base)


def card(**kw) -> DecisionCard:
    # contract-exempt: 同上
    base = dict(
        decision_id=TID,
        status="WAIT",
        headline="核心矛盾一句话",
        verdicts=[verdict()],
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
        assert v.missing == ["最高板算不出来"]

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
    def test_confidence范围(self, bad):
        with pytest.raises(ValueError, match="confidence"):
            verdict(confidence=bad)

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

    def test_new_task_id(self):
        assert new_task_id(7, day="20260919") == "BIGA-20260919-007"
        with pytest.raises(ValueError):
            new_task_id(1000, day="20260919")


# ------------------------------------------------------------ DecisionCard


class TestCardIronLaw2:
    """铁律 2：missing 非空 ⇒ 不得给 BUY。"""

    def test_missing非空时不许BUY(self):
        v = verdict(status="partial", verdict="WARNING", missing=["最高板"])
        with pytest.raises(ValueError, match="不得给买入结论"):
            card(status="BUY", verdicts=[v], missing=["最高板"])

    def test_missing非空时WAIT是合法的(self):
        v = verdict(status="partial", verdict="WARNING", missing=["最高板"])
        assert card(status="WAIT", verdicts=[v], missing=["最高板"]).status == "WAIT"

    def test_缺失项必须上浮到Card(self):
        # Verdict 说缺了东西，Card 却没列出来 —— 这是静默 fail-open 的典型形状
        v = verdict(status="partial", verdict="WARNING", missing=["最高板"])
        with pytest.raises(ValueError, match="没有上浮"):
            card(verdicts=[v], missing=[])

    def test_Card可以追加自己发现的缺失项(self):
        v = verdict(status="partial", verdict="WARNING", missing=["最高板"])
        c = card(verdicts=[v], missing=["最高板", "板块扩散度覆盖率不足"])
        assert len(c.missing) == 2


class TestCardVeto:
    """制衡层的否决权。

    🔴 判据是 `stance == VETO_STANCE`，不是 `verdict`。
    `verdict` 只说数据全不全 —— 一个字段装不下「数据完整」和「我要否决」两件事：
    risk 数据完整且要否决时，`verdict` 填 BLOCK 就再也说不出它的数据是全的，
    而「凭什么否决」恰恰需要知道。
    """

    def test_否决不可被合成阶段绕过(self):
        blocked = verdict(agent="risk", stance=VETO_STANCE)
        with pytest.raises(ValueError, match="否决权"):
            card(status="BUY", verdicts=[blocked])

    def test_否决配AVOID是合法的(self):
        blocked = verdict(agent="risk", stance=VETO_STANCE)
        assert card(status="AVOID", verdicts=[blocked]).status == "AVOID"

    def test_否决配WAIT被拒(self):
        """WAIT 是「再看看」，否决是「不要做」。把后者显示成前者就是软化制衡层。"""
        blocked = verdict(agent="risk", stance=VETO_STANCE)
        with pytest.raises(ValueError, match="否决必须体现"):
            card(status="WAIT", verdicts=[blocked])

    def test_否决这个词只有一处定义(self):
        """改了词表却忘了改判据，否决权会**静默失效** —— 那是最怕的 fail-open。"""
        from _contract import STANCE_VOCAB
        assert VETO_STANCE in STANCE_VOCAB["risk"]

    def test_数据不全的risk同样拦不住BUY(self):
        """L-2 买入侧 fail-closed：risk 说不上话时，不许当作放行。"""
        unknown = verdict(agent="risk", verdict="UNKNOWN", status="partial",
                          stance="无法判定", missing=["风险面 —— 上游证据不足"])
        with pytest.raises(ValueError, match="铁律 2"):
            card(status="BUY", verdicts=[unknown], missing=["风险面 —— 上游证据不足"])


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
        assert "⚠ 缺失项（1）" in card(verdicts=[v], missing=["最高板"]).render()

    def test_render含免责声明(self):
        assert "不构成投资建议" in card().render()

    def test_verdict_of(self):
        c = card()
        assert c.verdict_of("emotion") is not None
        assert c.verdict_of("risk") is None

    def test_is_complete(self):
        assert card().is_complete is True
        v = verdict(status="partial", verdict="WARNING", missing=["最高板"])
        assert card(verdicts=[v], missing=["最高板"]).is_complete is False

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
