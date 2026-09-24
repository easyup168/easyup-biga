"""risk-check 行为测试 —— 全部离线。

制衡层的测试比别人更重要：它是唯一能单方面拦住结论的地方，
**而它坏掉的方式是「安静地放行」** —— 放行与通过的日志长得一模一样。
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys
from datetime import datetime, timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "risk-check" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _contract import CN_TZ, VETO_STANCE, AgentVerdict, Evidence  # noqa: E402

# 🔴 幂等：已经有别的测试文件 `_load` 过 risk_check 就**复用那个实例**，绝不用
#    新实例覆写 sys.modules。`orchestrator.py` 自己 `from risk_check import
#    build_fact_bundle`，它在**它自己**import 时绑定的对象与本文件覆写后的
#    不是同一个——同一个坑见 test_run_id_capture.py / 教程第 32 章（那次的
#    载体是 card_ops，risk_check 是 orchestrator.py 直接 import 的第二个同类名字）。
if "risk_check" in sys.modules:
    rc = sys.modules["risk_check"]
else:
    spec = importlib.util.spec_from_file_location("risk_check", SCRIPTS / "risk_check.py")
    rc = importlib.util.module_from_spec(spec)
    sys.modules["risk_check"] = rc
    spec.loader.exec_module(rc)

AS_OF = datetime(2026, 9, 18, 15, 0, tzinfo=CN_TZ)
GOT = datetime(2026, 9, 18, 15, 1, tzinfo=CN_TZ)


def up(agent="market", result=None, stance="放量上涨", missing=None,
       verdict="PASS", status="completed", as_of=AS_OF, got=GOT,
       task_id="BIGA-20260918-001"):
    result = dict(result or {"trade_date": "20260918"})
    ev = [Evidence(field=k, source=f"derived:{agent}", value=v,
                   as_of=as_of, retrieved_at=got, calc_version="v1")
          for k in result for v in [result[k]]]
    # contract-exempt: 拼的是构造 AgentVerdict 的 kwargs，不是第二套契约
    return AgentVerdict(task_id=task_id, agent=agent, status=status,
                        verdict=verdict, result=result, data_completeness=1.0,
                        evidence=ev, warnings=[], missing=list(missing or []),
                        elapsed_ms=1, stance=stance)


@pytest.fixture()
def wired(monkeypatch):
    """按 id 喂上游判定。"""
    store: dict[int, AgentVerdict] = {}
    monkeypatch.setattr(rc, "load_verdict", lambda vid: store.get(vid))
    monkeypatch.setattr(rc, "now_cn",
                        lambda: datetime(2026, 9, 18, 18, 0, tzinfo=CN_TZ))
    return store


def build(ids, **kw):
    kw.setdefault("store", False)
    kw.setdefault("task_id", "BIGA-20260918-001")
    return rc.build_fact_bundle(verdict_ids=list(ids), **kw)


class TestNeverCollects:
    """🔴 结构上保证 risk 不采数据 —— 靠人记得是不够的。"""

    def test_不import任何采集层(self):
        tree = ast.parse((SCRIPTS / "risk_check.py").read_text(encoding="utf-8"))
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module.split(".")[0])
        assert "_sources" not in mods, \
            "risk 不许自己采数据 —— 它要审的是别人据以下结论的那份证据"
        assert not (mods & {"urllib", "requests", "httpx", "socket"}), \
            f"risk 出现了网络依赖：{mods}"

    def test_只通过load_verdict拿输入(self):
        src = (SCRIPTS / "risk_check.py").read_text(encoding="utf-8")
        assert "load_verdict" in src
        for forbidden in ("fetch_", "market_calc", "emotion_calc"):
            assert forbidden not in src, f"出现了 {forbidden}"


class TestCoverage:
    def test_覆盖率按应到五个算(self, wired):
        wired[1], wired[2] = up("market"), up("emotion", stance="修复")
        v = build([1, 2])
        assert v.result["coverage_ratio"] == 0.4
        assert v.result["upstream_agents"] == ("emotion", "market")

    def test_缺席的agent必须进missing(self, wired):
        wired[1] = up("market")
        v = build([1])
        m = [x for x in v.missing if x.code == "risk.upstream.coverage_incomplete"]
        assert m and "sector" in m[0] and "emotion" in m[0]

    def test_覆盖不足时不是PASS(self, wired):
        """🔴 说不上话不许当作放行。"""
        wired[1] = up("market")
        assert build([1]).verdict != "PASS"

    def test_取不到的上游id如实报出(self, wired):
        wired[1] = up("market")
        v = build([1, 999])
        assert any(x.code == "risk.upstream.verdict_not_found" for x in v.missing)

    def test_一个都拿不到时是failed(self, wired):
        v = build([999])
        assert (v.status, v.verdict) == ("failed", "UNKNOWN")
        assert v.result == {} and v.evidence == ()


class TestThresholds:
    def test_炸板率过高被触发(self, wired):
        wired[1] = up("emotion", stance="衰退",
                      result={"trade_date": "20260918", "broken_rate": 0.62})
        v = build([1])
        assert "risk.emotion.broken_rate_high" in v.result["tripped_thresholds"]
        assert any("62" in w or "0.62" in w for w in v.warnings)

    def test_量能两端都能触发(self, wired):
        wired[1] = up("market", result={"trade_date": "20260918", "volume_ratio": 2.4})
        assert "risk.market.volume_spike" in build([1]).result["tripped_thresholds"]
        wired[1] = up("market", result={"trade_date": "20260918", "volume_ratio": 0.3})
        assert "risk.market.volume_dry" in build([1]).result["tripped_thresholds"]

    def test_没碰到就是空列表不是缺失(self, wired):
        wired[1] = up("market", result={"trade_date": "20260918", "volume_ratio": 1.0})
        assert build([1]).result["tripped_thresholds"] == ()

    def test_有可比字段时没越线就是空列表(self, wired):
        """上游给了可比字段、但都没越线 ⇒ `[]` 名副其实：比过了，没触发。"""
        wired[1] = up("market", result={"trade_date": "20260918", "volume_ratio": 1.0})
        assert build([1]).result["tripped_thresholds"] == ()

    def test_一个可比字段都没有时根本不产出这个字段(self, wired):
        """🔴 批 5：以前无论如何都报 `[]`，而空列表在卡面上读起来是
        「比过了，没有触发」—— 实际是「没东西可比」。

        同一个 `[]` 表示两件相反的事，而且方向最坏：把**未知**显示成**安全**。
        现在不产出该字段，并进一条 missing。
        """
        wired[1] = up("market")          # 只有 trade_date，没有任何阈值字段
        v = build([1])
        assert "tripped_thresholds" not in v.result
        codes = [m.code for m in v.missing]
        assert "risk.thresholds.nothing_to_check" in codes, codes


class TestUpstreamIntegrity:
    def test_交易日不一致进missing(self, wired):
        wired[1] = up("market", result={"trade_date": "20260918"})
        wired[2] = up("emotion", stance="修复", result={"trade_date": "20260917"})
        v = build([1, 2])
        assert v.result["trade_date_consistent"] is False
        assert any(x.code == "risk.upstream.trade_date_inconsistent" for x in v.missing)

    def test_上游stance互斥被指出(self, wired):
        wired[1] = up("market", stance="放量上涨")
        wired[2] = up("emotion", stance="恐慌")
        v = build([1, 2])
        assert v.result["stance_conflict"]
        assert any("互斥" in w for w in v.warnings)

    def test_上游没给stance就无法审阅(self, wired):
        wired[1] = up("market", stance=None)
        v = build([1])
        assert any(x.code == "risk.upstream.stance_absent" for x in v.missing)

    def test_as_of取最旧的那条(self, wired):
        old = AS_OF - timedelta(hours=3)
        wired[1] = up("market", as_of=old, got=GOT)
        wired[2] = up("emotion", stance="修复", as_of=AS_OF, got=GOT)
        assert all(e.as_of == old for e in build([1, 2]).evidence), \
            "risk 的结论不可能比它最旧的输入更新鲜"


class TestFreshness:
    def test_收盘后不报陈旧(self, wired):
        """🔴 第一版在这里数「超过 120s 的证据条数」，
        结果非交易日必然全红 —— 一个周末必报的指标等于没有指标。"""
        wired[1] = up("market")
        v = build([1])
        assert v.result["session_live"] is False
        assert "stale_evidence_count" not in v.result
        assert v.result["max_evidence_age_sec"] > 0   # 年龄照报，解读交给 Agent
        assert v.result["max_source_lag_sec"] >= 0  # 取数滞后另报，两者不是一回事

    def test_盘中且同日才算时段进行中(self, wired, monkeypatch):
        monkeypatch.setattr(rc, "now_cn",
                            lambda: datetime(2026, 9, 18, 10, 0, tzinfo=CN_TZ))
        wired[1] = up("market", as_of=datetime(2026, 9, 18, 9, 58, tzinfo=CN_TZ),
                      got=datetime(2026, 9, 18, 9, 59, tzinfo=CN_TZ))
        assert build([1]).result["session_live"] is True


class TestContractShape:
    def test_字段数与data_completeness分母一致(self, wired):
        # ⚠️ 必须带一个阈值可比字段：批 5 起 `tripped_thresholds` 只在**比过了**
        #    的时候产出，缺了它这个夹具描述的就不是「齐备」了。
        wired[1] = up("market", result={"trade_date": "20260918", "volume_ratio": 1.0})
        wired[2] = up("emotion", stance="修复")
        wired[3] = up("sector", stance=None, verdict="UNKNOWN", status="partial",
                      missing=["x"])
        wired[4] = up("news", stance=None, verdict="UNKNOWN", status="partial",
                      missing=["x"])
        wired[5] = up("technical", stance=None, verdict="UNKNOWN", status="partial",
                      missing=["x"])
        v = build([1, 2, 3, 4, 5])
        assert len(v.result) == rc._EXPECTED_FIELDS
        assert v.data_completeness == 1.0

    def test_否决这个词与契约层同源(self):
        text = (REPO / "agents" / "risk" / "AGENTS.md").read_text(encoding="utf-8")
        assert VETO_STANCE in text, "契约里必须出现否决这个词，否则 Agent 不知道能填"


class TestStageTopology:
    """Stage 拓扑只有一处定义，且并行判据必须认得 Stage 2。"""

    @staticmethod
    def _lr():
        # 🔴 幂等：已经有别的测试文件加载过 latency_report 就**复用那个实例**，
        #    绝不用新实例覆写 sys.modules——`test_verify_exit_codes.py` 自己
        #    `import latency_report as lr` 并对它做四处 monkeypatch，两份对象
        #    分裂的话 patch 会打偏（同一个坑见 test_run_id_capture.py / 教程
        #    第 32 章，三轮对抗性复核指出这份文件自己另一处加载点补了这道检查，
        #    这一处漏网）。
        if "latency_report" in sys.modules:
            return sys.modules["latency_report"]
        p = REPO / "tools" / "verify" / "latency_report.py"
        spec = importlib.util.spec_from_file_location("latency_report", p)
        m = importlib.util.module_from_spec(spec)
        sys.modules["latency_report"] = m
        spec.loader.exec_module(m)
        return m

    @staticmethod
    def _turn(agent, start_s, dur_s):
        from dataclasses import dataclass as _dc

        @_dc
        class T:
            agent_id: str
            started_at: datetime
            ended_at: datetime
        base = datetime(2026, 9, 18, 10, 0, tzinfo=CN_TZ)
        return T(agent, base + timedelta(seconds=start_s),
                 base + timedelta(seconds=start_s + dur_s))

    def test_risk在stage1之后不算串行(self, capsys):
        """🔴 这是误报过的形状：risk 本就该在 Stage 1 之后，
        把它算进 Stage 1 会让并行判据在**正确行为**上报红 ——
        而一个在正确行为上报红的检查，很快就没人看了。"""
        lr = self._lr()
        turns = [self._turn("market", 0, 30), self._turn("emotion", 0, 25),
                 self._turn("risk", 40, 17)]
        assert lr._parallel_report(turns) is True
        out = capsys.readouterr().out
        assert "真并行" in out and "Stage 2" in out

    def test_risk与stage1重叠要报红(self, capsys):
        """重叠 = 它读的是还没冻结的证据。这不是性能问题，是正确性问题。"""
        lr = self._lr()
        turns = [self._turn("market", 0, 30), self._turn("emotion", 0, 25),
                 self._turn("risk", 10, 17)]
        lr._parallel_report(turns)
        out = capsys.readouterr().out
        assert "尚未冻结" in out

    def test_拓扑只有一处定义(self):
        """risk-check 与 latency_report 都用它 —— 各写一份就会漂。"""
        from _contract import STAGE1_AGENTS, STAGE2_AGENTS
        src = (SCRIPTS / "risk_check.py").read_text(encoding="utf-8")
        assert "STAGE1_AGENTS" in src
        assert '"market", "sector"' not in src, "不许在 skill 里再抄一份名单"
        assert set(STAGE1_AGENTS) & set(STAGE2_AGENTS) == set()
        assert "risk" in STAGE2_AGENTS


# ══ 外部评审 P1-2：Stage 边界的身份守卫 ═════════════════════════
#
# risk 原本检查覆盖率、交易日、陈旧度、阈值、stance 冲突、跨源校验 ——
# **唯独没有检查上游判定属不属于这次决策**。
#
# 于是可以出现：五个 Specialist 全在、trade_date 一致、coverage_ratio=1.0，
# 而它们分别来自三个不同的决策。
#
# ⚠️ 合成阶段那道闸门拦得住最终的卡，但拦不住这件事：
#    错误的 risk 判定**已经生成、而且可能已经落库**。
#    制衡层在污染的输入上得出的结论，事后拒绝那张卡也撤销不了。


class TestForeignDecisionUpstream:
    def test_上游来自别的决策要报缺失(self, wired):
        wired[1] = up("market", task_id="BIGA-20260918-001")
        wired[2] = up("emotion", stance="修复", task_id="BIGA-20260918-002")
        v = build([1, 2], task_id="BIGA-20260918-001")
        codes = [m.code for m in v.missing]
        assert "risk.upstream.foreign_decision" in codes

    @staticmethod
    def _full(wired, task_id: str):
        """五个 Stage 1 全齐、交易日一致、还踩了一条阈值。

        `volume_ratio=3.0` 会触发 `risk.market.volume_spike` ——
        故意加上，为了看「从别人决策的证据里算出来的告警」有没有漏出来。
        """
        rows = [("market", "放量上涨", {"trade_date": "20260918", "volume_ratio": 3.0}),
                ("emotion", "修复", None), ("sector", "主线明确", None),
                ("technical", "多头", None), ("news", "平静", None)]
        for i, (a, st, res) in enumerate(rows, start=1):
            wired[i] = up(a, stance=st, result=res, task_id=task_id)
        return list(range(1, 6))

    def test_归属不成立时不给倾向(self, wired):
        ids = self._full(wired, "BIGA-20260918-999")
        v = build(ids, task_id="BIGA-20260918-001")
        assert v.verdict == "UNKNOWN", \
            "覆盖率满 + 交易日一致 ⇒ 原来会给 PASS/WARNING，那正是评审指出的洞"
        assert v.status == "failed"

    def test_归属不成立时一个派生指标都不许留(self, wired):
        """🔴 外部深度评审：第一版是**半硬**的 fail-closed。

        它检测到 foreign 之后照样把全套指标算完，最后才把 `verdict`
        压成 UNKNOWN。落库的判定于是长成这样：

            verdict: UNKNOWN
            result:  coverage_ratio=1.0, trade_date_consistent=True, …
            data_completeness: 0.9

        每个数字都是**把两次决策的证据混在一起**算出来的，而它们和正常
        判定逐字段同形。任何不去读 `verdict` 的消费方都会照常用它们。

        > `verdict` 说「不知道」，`result` 说得头头是道。
        > 只要有一个消费方读后者不读前者，fail-closed 就漏了。

        判据是**差分**：同一批上游，只改 `task_id`。
        正常那次算出的字段，这次一个都不能出现。
        """
        ids = self._full(wired, "BIGA-20260918-001")
        good = build(ids, task_id="BIGA-20260918-001")
        assert len(good.result) >= 5, f"正常路径本该算出一堆字段：{good.result}"

        ids = self._full(wired, "BIGA-20260918-999")
        bad = build(ids, task_id="BIGA-20260918-001")
        leaked = sorted(set(bad.result) & set(good.result))
        assert leaked == [], (
            f"归属不成立，却仍然给出了派生指标：{leaked}\n"
            f"  值：{ {k: bad.result[k] for k in leaked} }\n"
            "  这些数是把两次决策的证据混起来算的，和正常判定逐字段同形。")

    def test_归属不成立时置信度是零(self, wired):
        """`data_completeness` 原来由 `len(result)/_EXPECTED_FIELDS` 算 ——
        字段算得越多越「自信」，而这里字段越多恰恰意味着污染越深。"""
        ids = self._full(wired, "BIGA-20260918-999")
        assert build(ids, task_id="BIGA-20260918-001").data_completeness == 0.0

    def test_归属不成立时不许发出阈值告警(self, wired):
        """`volume_ratio=3.0` 来自**别的决策**。

        照样报「异常放量」就是把另一次决策的市场状态说成这一次的 ——
        而 `warnings` 是直接印在 Card 上的。
        """
        ids = self._full(wired, "BIGA-20260918-999")
        v = build(ids, task_id="BIGA-20260918-001")
        assert not any("volume_spike" in w or "放量" in w for w in v.warnings), \
            f"从别人的证据里算出了告警：{v.warnings}"
        assert "tripped_thresholds" not in v.result

    def test_仍然留下足够排查的信息(self, wired):
        """🔴 fail-closed 不等于一片空白 —— 报错要指路。

        「谁串进来了」是关于**这次混淆**的事实，不是关于市场的事实，
        所以它可以留，而且必须留：否则读卡的人只知道「不知道」。

        ⚠️ 它也得有证据（铁律 3）。第一版塞进 `result` 却给了空 evidence，
           契约层当场拒绝构造 —— 那次报错是对的。
        """
        ids = self._full(wired, "BIGA-20260918-999")
        v = build(ids, task_id="BIGA-20260918-001")
        assert v.result["foreign_task_ids"] == ("BIGA-20260918-999",)
        assert v.result["upstream_attribution"]["market"] == "BIGA-20260918-999"
        assert {e.field for e in v.evidence} == set(v.result), \
            "result 的每个键都要有证据（铁律 3）"
        assert any(m.code == "risk.upstream.foreign_decision" for m in v.missing)

    def test_同一次决策的上游不受影响(self, wired):
        wired[1] = up("market", task_id="BIGA-20260918-001")
        wired[2] = up("emotion", stance="修复", task_id="BIGA-20260918-001")
        v = build([1, 2], task_id="BIGA-20260918-001")
        assert "risk.upstream.foreign_decision" not in [m.code for m in v.missing]


# ───────────────────────────── 阈值可达性巡检（外部评审 F7 的后半段）
#
# F7 有两半，第一半（设计文档点名了从未存在的 `reachability.py`）已经
# 结构性修掉了。**但风险本体当时一条测试都没加** —— 而复查正确地指出：
# 台账把整条 F7 标成「✅ 已修」，容易让人以为风险已经解除。
#
# 风险本体是 L-7「死配置」：`THRESHOLDS` 有 6 条，其中
# `breadth_weak` / `streak_extreme` / `limit_down_many` **从未被任何测试
# 触发过**。未来任何一次重构（给 `max_streak` 换单位、给 `advance_ratio`
# 改字段名）如果让某条再也匹配不上，它会「照常参与计算，只是永远不生效」——
# risk 从此对这类风险永久沉默，而 Card 会一直显得「风险已核查、无异常」。
#
# 🔴 判据 **parametrize 到 `THRESHOLDS` 本身**，不是手写六条：
#    新加一条阈值自动被纳入，写死六条的话第七条又会是下一个 F7。

#: 上游字段由谁产出 —— 用来验证阈值不是在等一个没人生产的字段。
_FIELD_OWNER = {
    "broken_rate": "emotion", "max_streak": "emotion",
    "limit_down_count": "emotion",
    "volume_ratio": "market", "advance_ratio": "market",
}


def _crossing_value(op: str, bound: float) -> float:
    """造一个刚好越过这条线的值。"""
    return bound + 1.0 if op == ">" else bound - 0.05


class TestThresholdReachability:
    @pytest.mark.parametrize("field,op,bound,code,why", rc.THRESHOLDS,
                             ids=[t[3] for t in rc.THRESHOLDS])
    def test_每条阈值都能被真的触发(self, wired, field, op, bound, code, why):
        """🔴 「配了但从未命中」和「配对了但今天没触发」，日志长得一模一样。"""
        owner = _FIELD_OWNER[field]
        result = {"trade_date": "20260918", field: _crossing_value(op, bound)}
        stance = {"market": "放量上涨", "emotion": "亢奋"}[owner]
        wired[1] = up(agent=owner, result=result, stance=stance)
        v = build([1])
        assert code in v.result["tripped_thresholds"], (
            f"{code} 配在 THRESHOLDS 里，但喂进越线的值也没触发 —— \n"
            f"  字段 {field!r} 由 {owner} 产出，检查两边的字段名与单位是否还对得上。\n"
            f"  L-7：死配置会照常参与计算，只是永远不生效。")

    @pytest.mark.parametrize("field,op,bound,code,why", rc.THRESHOLDS,
                             ids=[t[3] for t in rc.THRESHOLDS])
    def test_没越线就不该触发(self, wired, field, op, bound, code, why):
        """反方向 —— 否则上一条可以靠「永远触发」平凡通过。"""
        owner = _FIELD_OWNER[field]
        safe = bound - 0.01 if op == ">" else bound + 0.01
        wired[1] = up(agent=owner, result={"trade_date": "20260918", field: safe},
                      stance={"market": "放量上涨", "emotion": "亢奋"}[owner])
        assert code not in build([1]).result["tripped_thresholds"]

    def test_每条阈值的字段都有生产方(self):
        """阈值在等一个没人产出的字段 = 它永远不会命中，而且不报错。"""
        missing = [t[0] for t in rc.THRESHOLDS if t[0] not in _FIELD_OWNER]
        assert not missing, (
            f"这些阈值字段没登记生产方：{missing}\n"
            "  先确认是谁产出它 —— 如果没人产出，这条阈值是死的。")

    @pytest.mark.parametrize("field,owner", sorted(_FIELD_OWNER.items()))
    def test_生产方真的在产出这个字段(self, field, owner):
        """🔴 不信 `_FIELD_OWNER` 这张手写表，去上游源码里查字面量。

        这条才是防重构的那一条：给字段改名之后，
        阈值不会报错，只会**从此再也不命中**。
        """
        script = {"emotion": "emotion-calc/scripts/emotion_calc.py",
                  "market": "market-calc/scripts/market_calc.py"}[owner]
        src = (REPO / "skills" / script).read_text(encoding="utf-8")
        names = {n.args[0].value
                 for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.Call) and n.args
                 and (getattr(n.func, "id", "") or getattr(n.func, "attr", ""))
                 in ("add", "add_live")
                 and isinstance(n.args[0], ast.Constant)
                 and isinstance(n.args[0].value, str)}
        assert field in names, (
            f"{owner} 不再产出 {field!r}（现有字面量字段 {len(names)} 个）——\n"
            f"  而 THRESHOLDS 仍在等它。这条阈值现在是死的，"
            f"且不会有任何东西报错。")
