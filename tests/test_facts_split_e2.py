"""批 E-II：market/sector/technical/news 四个 skill 从产 AgentVerdict 迁到产 FactBundle。

E-I 只迁了 emotion（不读冻结快照、不进 CROSS_CHECK 的那个）。这一批第一次让**读冻结
快照**（market/sector/technical）与**参与 CROSS_CHECK**（market/technical）的 Specialist
走新形状 —— 这两件事 E-I 都没真正测过。六道探针钉住它：

  P1  四个 skill 各自产出合法的 FactBundle（不是 AgentVerdict），落库后 kind='fact'
  P2  CROSS_CHECK 穿透新形状：market/technical 都产 FactBundle 之后，同一个
      evidence_set_id 不报冲突；不同的 evidence_set_id 报冲突，且冲突信息带「冻结集」+
      两个 es-id 值（复用 E-I 锚死的那条判据，不退化成只断言「报了冲突」）
  P3  ①的裁定：四个 skill 历史上靠 --add-missing 补的限制，全部由 skill 自己检测写进
      FactBundle.missing（不经过 amend）；而 market.trend.no_history 是**范围外的判断
      边界**（不是数据缺口），skill 一次都不产它
  P4  回归：risk（未迁）+ emotion（E-I 已迁）+ 这四个新迁的 → card_ops 聚合六个不丢
  P5  amend_verdict.py 对四个 skill 的 fact 行正确路由到 _assess_fact：--stance 成功、
      --add-missing 被拒（证明 kind=='fact' 判据是通用的，不是 emotion 专属）
  P6  第五交付物验收：修好的 agents/market/AGENTS.md 模板里不再有 --add-missing 命令，
      且它现在教的那条 --stance 命令对一条真实 market fact 行确实跑得通（rc=0）

全部离线（禁网围栏兜底）。
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import sys
from datetime import date, datetime, timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

from _contract import (  # noqa: E402
    CN_TZ,
    STANCE_VOCAB,
    AgentAssessment,
    AgentVerdict,
    Evidence,
    FactBundle,
    MissingItem,
    new_task_id,
    now_cn,
)
from _snapshot import SnapshotCoordinator  # noqa: E402
from _sources import BreadthResult, parse_index_daily  # noqa: E402
from _sources.sina_news import NewsFeed, NewsItem  # noqa: E402
from _store import (  # noqa: E402
    init_schema,
    load_verdict_ids_for_run,
    load_outcome,
    load_verdict,
    load_verdict_meta,
    save_assessment,
    save_fact_bundle,
    save_verdict,
)
from _provenance import open_test_run  # noqa: E402

TID = "BIGA-20260302-001"

#: 批 O：fact 行要归属到一次真实的执行尝试（`load_verdict_ids_for_run`
#: 按 run 取；`TestRunIdNamespace` 要求 run_id 追得到 decision_runs）。
_RID = "e" * 32


def _load(name: str, rel: str):
    # 🔴 幂等：已经有别的测试文件 `_load` 过就**复用那个实例**，绝不用新实例覆写
    #    sys.modules。否则 orchestrator.py 在它自己 import 时绑定的 card_ops 与本文件
    #    覆写后的不是同一个对象 —— test_orchestrator 的 monkeypatch.setattr(card_ops,
    #    "persist", …) 打在新实例上、orchestrator 却调旧实例，patch 静默落空（全量里
    #    才复现的跨文件污染，见 test_run_id_capture.py / 教程第 32 章）。
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


market = _load("market_calc", "skills/market-calc/scripts/market_calc.py")
sector = _load("sector_calc", "skills/sector-calc/scripts/sector_calc.py")
technical = _load("technical_calc", "skills/technical-calc/scripts/technical_calc.py")
news = _load("news_scan", "skills/news-scan/scripts/news_scan.py")
risk = _load("risk_check", "skills/risk-check/scripts/risk_check.py")
card_ops = _load("card_ops", "skills/decision-card/scripts/card_ops.py")
amend = _load("amend_verdict", "skills/decision-card/scripts/amend_verdict.py")


# ───────────────────────────────────────── 共享桩：冻结日线 + 假快讯


def _rows(symbol: str, n: int) -> list[dict]:
    off = 0.0 if symbol == "sh000001" else 1000.0
    base = date(2026, 3, 2)
    return [{"day": (base + timedelta(days=i)).strftime("%Y-%m-%d"),
             "open": 3000.0 + off + i * 0.5, "high": 3010.0 + off + i * 0.5,
             "low": 2990.0 + off + i * 0.5, "close": 3000.0 + off + i * 0.5,
             "volume": 10_000_000 + i} for i in range(n)]


def _fake_daily(symbol, *, bars):
    rows = _rows(symbol, bars)
    return parse_index_daily(symbol, rows, raw_text=json.dumps(rows))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    open_test_run(p, run_id=_RID, decision_id=TID)     # 批 O + P1-1：fact/esid 须归属到真实执行尝试
    return p


def _freeze(db, tid=TID, bars=120):
    coord = SnapshotCoordinator(fetcher=_fake_daily, path=db)
    return coord.freeze_index_daily(tid, ["sh000001", "sz399106"], bars=bars)


def _freeze_breadth(db, tmp_path, esid, monkeypatch, result):
    """把一份**指定内容**的涨跌家数冻进这份 EvidenceSet。

    桩打在 `decision_client` 的取数函数上 —— 那是 P3-6 之后真正的出网边界。
    """
    import easyup_biga.data.decision_client as dc

    monkeypatch.setattr(dc, "_fetch_breadth", lambda: result)
    dc.DecisionDataClient(db_path=db, data_root=str(tmp_path / "data")).freeze_required(
        esid, ["cn.market.breadth"], trade_date="20260302")


def _freeze_boards(db, tmp_path, esid, monkeypatch, fetch_boards):
    import easyup_biga.data.decision_client as dc

    monkeypatch.setattr(dc, "_fetch_boards", fetch_boards)
    dc.DecisionDataClient(db_path=db, data_root=str(tmp_path / "data")).freeze_required(
        esid, ["cn.sector.board_snapshot"], trade_date="20260302")


# ─────────────────────────────────────────────────── P1 · 四个 skill 产 FactBundle


class TestP1FactBundleShape:
    """🔴 P1：四个 skill 的返回值是 FactBundle（不是 AgentVerdict），落库 kind='fact'。"""

    def test_market_sector_technical_产FactBundle_读冻结(self, db):
        esid = _freeze(db)
        mv = market.build_fact_bundle(date=None, break_source={"tencent", "breadth"},
                                      store=False, task_id=TID, evidence_set_id=esid)
        sv = sector.build_fact_bundle(break_source={"industry", "concept"},
                                      store=False, task_id=TID, evidence_set_id=esid)
        tv = technical.build_fact_bundle(break_source=set(), store=False,
                                         task_id=TID, evidence_set_id=esid)
        for fb in (mv, sv, tv):
            assert type(fb) is FactBundle, f"{fb.agent} 产的不是 FactBundle 而是 {type(fb).__name__}"
            # FactBundle 减去 stance —— 它根本没有这个属性
            assert not hasattr(fb, "stance")

    def test_news_产FactBundle(self, db):
        # break feed 让它不联网，仍产一份合法 FactBundle（有缺失项，UNKNOWN）
        nv = news.build_fact_bundle(break_source={"feed"}, store=False, task_id=TID)
        assert type(nv) is FactBundle
        assert not hasattr(nv, "stance")

    def test_落库后kind是fact(self, db):
        esid = _freeze(db)
        mv = market.build_fact_bundle(date=None, break_source={"tencent", "breadth"},
                                      store=False, task_id=TID, evidence_set_id=esid)
        fid = save_fact_bundle(mv)
        assert load_verdict_meta(fid)["kind"] == "fact"

    def test_红灯_skill若产AgentVerdict则写路径当场拒(self, db):
        """G-1 红灯：把 skill 的产出退回合体 AgentVerdict，save_fact_bundle 拒绝它 ——
        写路径严（E-I 的 P2 判据）保证「产错类型」不会静默落库。"""
        v = AgentVerdict(task_id=TID, agent="market", status="completed", verdict="PASS",
                         result={"sh_close": 3000.0}, data_completeness=1.0,
                         evidence=[_ev("sh_close", 3000.0)], stance="放量上涨", elapsed_ms=1)
        with pytest.raises(TypeError, match="只接受.*FactBundle"):
            save_fact_bundle(v)  # type: ignore[arg-type]


def _ev(field, value=1.0, *, raw_hash=None, evidence_set_id=None):
    t = now_cn()
    return Evidence(field=field, source=f"sina:kline/{field}", value=value,
                    as_of=t - timedelta(seconds=60), retrieved_at=t,
                    raw_hash=raw_hash, evidence_set_id=evidence_set_id)


# ─────────────────────────────────── P2 · CROSS_CHECK 穿透新形状


class TestP2CrossCheckPenetration:
    """🔴 P2：market/technical 都产 FactBundle 之后，risk 的 CROSS_CHECK 不应感知到任何
    差异 —— evidence_set_id 字段的填法没变，只是承载它的 Evidence 从「AgentVerdict 里的」
    变成「FactBundle 里的」。走真实的 save_fact_bundle → load_verdict（多态）→ risk 路径。
    """

    def test_同一个evidence_set_id_不报冲突(self, db):
        esid = _freeze(db)
        mid = save_fact_bundle(market.build_fact_bundle(
            date=None, break_source={"tencent", "breadth"}, store=False,
            task_id=TID, evidence_set_id=esid))
        tid = save_fact_bundle(technical.build_fact_bundle(
            break_source=set(), store=False, task_id=TID, evidence_set_id=esid))
        v = risk.build_fact_bundle(verdict_ids=[mid, tid], store=False, task_id=TID)
        assert v.result["cross_check_conflict"] == ()

    def test_不同evidence_set_id_报冲突且带冻结集与两个esid(self, db):
        """🔴 两次冻结同一份数据 → es-id 不同、但 raw_hash **相同**（实测）。所以只有
        evidence_set_id 判据能抓到这个冲突（raw_hash 判据会说「一样，放过」）。断言冲突
        由 es-id 判据报出：文案里有「冻结集」+ 两个 es-id 值 —— 退回 raw_hash 兜底的那条
        文案里既没有「冻结集」也没有 es-id（复用 E-I 评审锚死的判据）。"""
        es1 = _freeze(db, tid="BIGA-20260302-001")
        es2 = _freeze(db, tid="BIGA-20260302-002")
        assert es1 != es2
        mid = save_fact_bundle(market.build_fact_bundle(
            date=None, break_source={"tencent", "breadth"}, store=False,
            task_id=TID, evidence_set_id=es1))
        tid = save_fact_bundle(technical.build_fact_bundle(
            break_source=set(), store=False, task_id=TID, evidence_set_id=es2))
        v = risk.build_fact_bundle(verdict_ids=[mid, tid], store=False, task_id=TID)
        xconf = v.result["cross_check_conflict"]
        assert xconf, "不同 evidence_set_id 却没报冲突"
        msg = " ".join(xconf)
        assert "冻结集" in msg and es1 in msg and es2 in msg, (
            f"冲突不是由 evidence_set_id 判据报出的（疑似退回 raw_hash 兜底）：{xconf}")
        assert any(m.code == "risk.upstream.cross_check_conflict" for m in v.missing)


# ─────────────────────────────────── P3 · ①的裁定：skill 自己检测限制


class TestP3SkillSelfDetectsLimits:
    """🔴 P3：历史上靠 `amend_verdict.py --add-missing` 补的限制，现在由 skill 自己在
    build_fact_bundle 里检测写进 FactBundle.missing —— 不经过 amend。

    实测数据库里四个 skill 的 --add-missing 历史（去掉 stance 类）只有三个形状，
    全部对应一个「skill 明知道、可以直接判断」的阈值 / 状态：

      market  涨跌家数 0/0/0     → market.breadth.not_yet_formed（skill 已有）
      market  涨跌家数源不可用    → market.breadth.unavailable（skill 已有）
      sector  盘前板块榜无数据    → sector.board.pre_session（skill 已有，同名同码）

    唯一的例外是 market.trend.no_history —— 见 test_market_trend_是范围外不产。
    """

    def test_market自检涨跌家数0_0_0(self, db, monkeypatch, tmp_path):
        """⚠️ P3-6 之后**打桩点变了**：给了 evidence_set_id 就读冻结快照，
        不再联网 ⇒ monkeypatch `market.fetch_breadth` 打不中任何东西。

        被测的判断本身没变（0/0/0 ⇒ `not_yet_formed`，那个判断在 skill 里），
        变的是数据从哪来。⇒ 桩挪到**数据层的取数边界**，先冻一份 0/0/0 的
        快照，再让 skill 去读它。

        🔴 这正是 L-12「只替换最外层出网边界，不 mock 被测逻辑本身」——
        P3-6 把那条边界从 skill 里挪到了 Data 层，桩跟着挪。
        """
        esid = _freeze(db)
        _freeze_breadth(db, tmp_path, esid, monkeypatch,
                        BreadthResult(0, 0, 0, [], {"rc": 0},
                                      raw_text=json.dumps({"rc": 0})))
        mv = market.build_fact_bundle(date=None, break_source={"tencent"},
                                      store=False, task_id=TID, evidence_set_id=esid)
        assert any(m.code == "market.breadth.not_yet_formed" for m in mv.missing), (
            f"0/0/0 应自检出 not_yet_formed，实际 missing={[m.code for m in mv.missing]}")

    def test_market自检涨跌家数源不可用(self, db, monkeypatch):
        esid = _freeze(db)
        from _sources import SourceError
        def _boom():
            raise SourceError("东财挂了")
        monkeypatch.setattr(market, "fetch_breadth", _boom)
        mv = market.build_fact_bundle(date=None, break_source={"tencent"},
                                      store=False, task_id=TID, evidence_set_id=esid)
        assert any(m.code == "market.breadth.unavailable" for m in mv.missing)

    def test_sector自检盘前板块榜无数据(self, db, monkeypatch, tmp_path):
        esid = _freeze(db)
        from _sources import BoardResult
        # 全部板块涨跌幅为 0 ⇒ nonzero_count==0 ⇒ skill 自己报 pre_session
        def _zero_boards(kind):
            # ⚠️ 用真的 `Board`，不用 SimpleNamespace：P3-6 之后这批对象要经过
            #    冻结→Parquet→读回，缺任何一个字段都会在**写入**时才炸，
            #    而那时的报错指向 decision_client 而不是这个夹具。
            from easyup_biga.providers.eastmoney import Board
            boards = [Board(code=f"BK{i:04d}", name=f"板块{i}", pct=0.0,
                            main_inflow=0.0, advance=0, decline=0, leader=None)
                      for i in range(10)]
            return BoardResult(kind=kind, total=10, raw={"pages": []}, boards=boards,
                               raw_text=json.dumps({"kind": kind, "pages": []}))
        # ⚠️ 同上：P3-6 之后 sector 读冻结的 cn.sector.board_snapshot，
        #    桩挪到数据层的取数边界。
        _freeze_boards(db, tmp_path, esid, monkeypatch, _zero_boards)
        sv = sector.build_fact_bundle(break_source=set(), store=False,
                                      task_id=TID, evidence_set_id=esid)
        assert any(m.code == "sector.board.pre_session" for m in sv.missing)

    def test_technical自检日线不足(self, db, monkeypatch):
        # technical.daily.partial 这类「genuine 数据缺口」：数据源只给 30 根 → MA60/MACD
        # 算不出。走手工调试路径（不给 evidence_set_id），stub 让数据源只返回 30 根。
        monkeypatch.setattr(technical, "fetch_index_daily",
                            lambda symbol, *, bars: _fake_daily(symbol, bars=min(bars, 30)))
        tv = technical.build_fact_bundle(break_source=set(), store=False, task_id=TID)
        codes = {m.code for m in tv.missing}
        assert any(c.startswith("technical.") and "insufficient_bars" in c for c in codes)

    def test_news自检窗口截断(self, db, monkeypatch):
        now = datetime(2026, 9, 21, 10, 30, tzinfo=CN_TZ)
        # 窗口内 20 条、上限 2 条 ⇒ skill 自己报 news.window.truncated
        feed = _news_feed(now, n=20)
        import _contract.evidence as evidence_mod
        monkeypatch.setattr(news, "now_cn", lambda: now)
        monkeypatch.setattr(evidence_mod, "now_cn", lambda: now)
        monkeypatch.setattr(news, "fetch_feed", lambda **_: feed)
        nv = news.build_fact_bundle(break_source=set(), store=False,
                                    task_id=TID, max_items=2)
        assert any(m.code == "news.window.truncated" for m in nv.missing)

    def test_market_trend_是范围外不产(self, db):
        """🔴 ①的核心裁定：market.trend.no_history 不是数据缺口（market 每次都有整段
        日线序列，实测 5 次修订原件都带着 volume_ratio）。它是判断边界（铁律 4，与
        sector 的「板块持续性」同类），归 agent 的自然语言 caveat，skill **一次都不产它**。
        """
        esid = _freeze(db)
        mv = market.build_fact_bundle(date=None, break_source={"tencent", "breadth"},
                                      store=False, task_id=TID, evidence_set_id=esid)
        assert not any(m.code == "market.trend.no_history" for m in mv.missing)
        # 更硬：skill 源码里根本没有这个码（不是「这次没触发」，是「不存在」）
        src = (REPO / "skills/market-calc/scripts/market_calc.py").read_text(encoding="utf-8")
        assert "market.trend.no_history" not in src
        assert "trend" not in src, "market skill 不该冒出任何 trend 概念（那是 agent 的判断）"


def _news_feed(now: datetime, *, n: int = 20) -> NewsFeed:
    items = [NewsItem(id=10_000 - i, at=now - timedelta(seconds=60 * i),
                      text=f"【测试{i}】正文{i}", tags=("市场",), is_quote=False)
             for i in range(n)]
    items.append(NewsItem(id=1, at=now - timedelta(minutes=300),
                          text="很旧的一条", tags=(), is_quote=False))
    return NewsFeed(items=tuple(items), raw={"pages": []}, raw_text="[]")


# ─────────────────────────────────── P4 · 回归：六个 agent 聚合不丢


class TestP4Coexistence:
    """🔴 P4：这一批之后，六个 agent 的落库形状是「五新一旧」——
    market/sector/technical/news/emotion 走新三型（fact + assessment），
    risk 仍走老合体 AgentVerdict。card_ops 必须照常聚合六个，一个都不丢。
    """

    def _save_new(self, agent, *, missing=None):
        stance = next(s for s in STANCE_VOCAB[agent] if s != "无法判定")
        fb = FactBundle(
            task_id=TID, agent=agent,
            status="completed" if not missing else "partial",
            verdict="PASS" if not missing else "WARNING",
            result={f"{agent}_x": 1.0}, data_completeness=1.0,
            evidence=[_ev(f"{agent}_x", 1.0)], missing=list(missing or []))
        fid = save_fact_bundle(fb)
        return save_assessment(AgentAssessment(task_id=TID, agent=agent, stance=stance),
                               fact_id=fid)

    def _save_legacy(self, agent):
        stance = next(s for s in STANCE_VOCAB[agent] if s != "无法判定")
        return save_verdict(AgentVerdict(
            task_id=TID, agent=agent, status="completed", verdict="PASS",
            result={f"{agent}_x": 1.0}, data_completeness=1.0,
            evidence=[_ev(f"{agent}_x", 1.0)], stance=stance, elapsed_ms=1))

    def test_五新一旧_card聚合六个(self, db):
        ids = []
        # market 带一条自检的 missing，确认聚合时没被丢
        ids.append(self._save_new("market",
                                  missing=[MissingItem("涨跌家数源不可用",
                                                       "market.breadth.unavailable")]))
        for ag in ("sector", "technical", "news", "emotion"):
            ids.append(self._save_new(ag))
        ids.append(self._save_legacy("risk"))  # risk 仍产合体 AgentVerdict

        verdicts, refs = card_ops.load_verdicts_and_refs(ids)
        assert {v.agent for v in verdicts} == {
            "market", "sector", "technical", "news", "emotion", "risk"}
        # 新形状那五个的 stance 都压回来了（load_verdict 多态）
        for v in verdicts:
            if v.agent != "risk":
                assert v.stance is not None, f"{v.agent} 的 stance 没被压回 AgentVerdict"

        card = card_ops.synthesize(
            decision_id=TID, verdicts=verdicts,
            judgment=card_ops.Judgment(status="WAIT", headline="核心矛盾", synthesis="理由"),
            model_ref="test", verdict_refs=refs)
        assert len(card.verdicts) == 6
        assert any(m.code == "market.breadth.unavailable" for m in card.missing)


# ─────────────────────────────────── P5 · amend 路由（四个新 agent）


class TestP5AmendRouting:
    """🔴 P5：amend_verdict.py 对四个 skill 的 fact 行走 _assess_fact（kind=='fact'）：
    --stance 成功、--add-missing 被拒。证明这条路是通用的，不是 emotion 专属。"""

    def _fact(self, agent, db):
        fb = FactBundle(task_id=TID, agent=agent, status="completed", verdict="PASS",
                        result={f"{agent}_x": 1.0}, data_completeness=1.0,
                        evidence=[_ev(f"{agent}_x", 1.0)])
        return save_fact_bundle(fb)

    @pytest.mark.parametrize("agent", ["market", "sector", "technical", "news"])
    def test_fact行加stance成功(self, db, agent):
        fid = self._fact(agent, db)
        stance = next(s for s in STANCE_VOCAB[agent] if s != "无法判定")
        rc = amend.main(["--ref", str(fid), "--stance", stance])
        assert rc == 0
        # 判断落在**新的一行**（AgentAssessment），事实没被重打
        meta = load_verdict_meta(fid)
        assert meta["kind"] == "fact"

    @pytest.mark.parametrize("agent", ["market", "sector", "technical", "news"])
    def test_fact行加add_missing被拒(self, db, agent, capsys):
        fid = self._fact(agent, db)
        rc = amend.main(["--ref", str(fid), "--add-missing",
                         f"{agent}.x.y", "限制", "--verdict", "WARNING"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "--stance" in err and "需要注意" in err


# ─────────────────────────────────── P6 · 修好的 market 模板验收


class TestP6MarketTemplateFixed:
    """🔴 P6（第五交付物验收）：market 模板不再教 --add-missing（那条命令现在会被
    _assess_fact 拒、让 agent 下一次真实运行就卡住 —— F9 那种抖动）；它现在教的
    那条 --stance 命令对一条真实 market fact 行确实跑得通。"""

    AGENTS_MD = REPO / "agents/market/AGENTS.md"

    def test_模板的bash命令块里没有add_missing(self):
        text = self.AGENTS_MD.read_text(encoding="utf-8")
        blocks = re.findall(r"```bash\n(.*?)```", text, re.DOTALL)
        amend_blocks = [b for b in blocks if "amend_verdict.py" in b]
        assert amend_blocks, "market 模板里应当仍有一条 amend_verdict.py 命令（--stance）"
        for b in amend_blocks:
            assert "--add-missing" not in b, f"模板的命令块仍在教 --add-missing：\n{b}"
            assert "--verdict" not in b, f"模板的命令块仍在教事后 --verdict 降级：\n{b}"

    def test_模板教的stance命令对真实fact行跑得通(self, db):
        # 造一条真实 market fact 行
        fb = FactBundle(task_id=TID, agent="market", status="completed", verdict="PASS",
                        result={"sh_close": 3000.0}, data_completeness=1.0,
                        evidence=[_ev("sh_close", 3000.0)])
        fid = save_fact_bundle(fb, run_id=_RID)
        # 从模板里抠出那条 amend 命令用的 stance（照抄「缩量上涨」）
        text = self.AGENTS_MD.read_text(encoding="utf-8")
        m = re.search(r"--stance\s+(\S+)", text)
        assert m, "模板里应当有一条 --stance 示例"
        stance = m.group(1)
        assert stance in STANCE_VOCAB["market"], f"模板示例 stance={stance} 不在 market 词表里"
        rc = amend.main(["--ref", str(fid), "--stance", stance])
        assert rc == 0
        # 判断落在**新的一行**（assessment），事实行没被重打
        aid = load_verdict_ids_for_run(_RID)["market"]
        assert aid != fid
        oc = load_outcome(aid)
        assert oc.stance == stance
        assert oc.fact.result["sh_close"] == 3000.0  # 事实原样在
