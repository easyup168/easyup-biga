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
sys.path.insert(0, str(REPO / "skills"))
sys.path.insert(0, str(SCRIPTS))

from _contract import CN_TZ, VETO_STANCE, AgentVerdict, Evidence  # noqa: E402

spec = importlib.util.spec_from_file_location("risk_check", SCRIPTS / "risk_check.py")
rc = importlib.util.module_from_spec(spec)
sys.modules["risk_check"] = rc
spec.loader.exec_module(rc)

AS_OF = datetime(2026, 9, 18, 15, 0, tzinfo=CN_TZ)
GOT = datetime(2026, 9, 18, 15, 1, tzinfo=CN_TZ)


def up(agent="market", result=None, stance="放量上涨", missing=None,
       verdict="PASS", status="completed", as_of=AS_OF, got=GOT):
    result = dict(result or {"trade_date": "20260918"})
    ev = [Evidence(field=k, source=f"derived:{agent}", value=v,
                   as_of=as_of, retrieved_at=got, calc_version="v1")
          for k in result for v in [result[k]]]
    # contract-exempt: 拼的是构造 AgentVerdict 的 kwargs，不是第二套契约
    return AgentVerdict(task_id="BIGA-20260918-001", agent=agent, status=status,
                        verdict=verdict, result=result, confidence=1.0,
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
    return rc.build_verdict(verdict_ids=list(ids), **kw)


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
        assert v.result["upstream_agents"] == ["emotion", "market"]

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
        assert v.result == {} and v.evidence == []


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
        assert build([1]).result["tripped_thresholds"] == []

    def test_字段缺失不算触发(self, wired):
        """上游没给这个字段 ≠ 阈值没被碰到，但也不能算碰到了。"""
        wired[1] = up("market")
        assert build([1]).result["tripped_thresholds"] == []


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
        assert v.result["max_staleness_sec"] > 0   # 年龄照报，解读交给 Agent

    def test_盘中且同日才算时段进行中(self, wired, monkeypatch):
        monkeypatch.setattr(rc, "now_cn",
                            lambda: datetime(2026, 9, 18, 10, 0, tzinfo=CN_TZ))
        wired[1] = up("market", as_of=datetime(2026, 9, 18, 9, 58, tzinfo=CN_TZ),
                      got=datetime(2026, 9, 18, 9, 59, tzinfo=CN_TZ))
        assert build([1]).result["session_live"] is True


class TestContractShape:
    def test_字段数与confidence分母一致(self, wired):
        wired[1] = up("market")
        wired[2] = up("emotion", stance="修复")
        wired[3] = up("sector", stance=None, verdict="UNKNOWN", status="partial",
                      missing=["x"])
        wired[4] = up("news", stance=None, verdict="UNKNOWN", status="partial",
                      missing=["x"])
        wired[5] = up("technical", stance=None, verdict="UNKNOWN", status="partial",
                      missing=["x"])
        v = build([1, 2, 3, 4, 5])
        assert len(v.result) == rc._EXPECTED_FIELDS
        assert v.confidence == 1.0

    def test_否决这个词与契约层同源(self):
        text = (REPO / "agents" / "risk" / "AGENTS.md").read_text(encoding="utf-8")
        assert VETO_STANCE in text, "契约里必须出现否决这个词，否则 Agent 不知道能填"
