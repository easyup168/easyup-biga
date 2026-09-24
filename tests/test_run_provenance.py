"""批 N：Run Provenance —— 一张在线卡必须说得清「哪次执行、哪份数据、哪些原件」。

外部评审 B 节（Run Provenance Closure）§6-9。本批做它的**可加固那一半**：
把血缘变成可查询的列 + 在写边界强制，**不动** `latest_verdict_ids()` 的聚合口径
（那是 B-2/B-4/B-5，与另一条在途修复冲突，见 CHANGELOG）。

探针清单：

  P1  schema v16：三个 capture 列 + `ux_evidence_set_per_run` 真的存在。
  P2  一个 Run 至多一套 EvidenceSet（评审 §9），第二套撞唯一约束。
  P3  在线卡缺 `run_id`/`evidence_set_id` ⇒ **落库拒绝**；回放（replay_of 非空）不受约束。
  P4  落库之后这两个值真的在**列**里（不是只躺在 card_json 里 —— 那是 SQL 答不了的形状）。
  P5  `agent_runs.orchestration_run_id` 真的落进去，且
      `decision_runs → agent_runs → runtime` 这条链能用一句 SQL 走通（评审 §8）。
  P6  契约层：`input_verdict_refs` 非空时必须恰好覆盖每条判定（缺/多/重都拒）。
  P7  契约层：ref 的 run 与卡的 run 对不上 ⇒ 新卡拒、旧卡可读并记 `provenance_warning`
      （三段式，同 `_check_identity`）。
  P8  库层：`verify_verdict_refs` 抓 foreign decision / foreign run —— 判据取
      **库里那一行**的 task_id/run_id，不是卡上那份拷贝（拷贝是被验证方自己出具的）。
  P9  在线落库会真的跑 P8 那道核对并据此拒绝（守卫接进了真实路径，不是只写了个函数）。
  P10 旧 standalone `synthesize.py` 落库要求显式给这两个 id，给不出只能 --no-store。

🔴 每条都做过 sabotage 验证（改坏被守的东西 ⇒ 必须变红），记录见 CHANGELOG 批 N。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
from datetime import timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))

from _contract import (  # noqa: E402
    AgentVerdict,
    DecisionCard,
    Evidence,
    FactBundle,
    VerdictRef,
    now_cn,
)
from _store import (  # noqa: E402
    connect,
    init_schema,
    load_verdict_meta,
    record_verdict_run,
    save_card,
    save_verdict,
    save_evidence_set,
    save_fact_bundle,
    verify_verdict_refs,
)
from _provenance import (  # noqa: E402
    TEST_EVIDENCE_SET_ID, TEST_RUN_ID, open_test_run, provenance_for)

DID = "BIGA-20260924-001"
CONTRACT_VERSION = "contract/1"

#: 批 O：卡构造器拿不到 fixture，而在线卡的 refs 必须指向真落库的行。
_DB: list = []


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    open_test_run(p, decision_id=DID)
    _DB[:] = [p]
    return p


def _ev(field="x"):
    t = now_cn()
    return Evidence(field=field, source="derived:x", value=1.0,
                    as_of=t - timedelta(seconds=60), retrieved_at=t)


def _verdict(agent="market", did=DID) -> AgentVerdict:
    return AgentVerdict(task_id=did, agent=agent, status="completed", verdict="PASS",
                        result={"x": 1.0}, data_completeness=1.0, evidence=[_ev()],
                        stance="放量上涨" if agent == "market" else "无法判定")


def _card(**kw) -> DecisionCard:
    # contract-exempt: 拼的是构造真 DecisionCard 的 kwargs，不是第二套契约
    base = dict(
        decision_id=DID, status="WAIT", headline="h", synthesis="", model_ref="m",
        verdicts=[_verdict()], expected_roster=("market",),
        run_id=TEST_RUN_ID, evidence_set_id=TEST_EVIDENCE_SET_ID)
    base.update(kw)
    # 批 O：`input_verdict_refs` 进了在线卡必填。多数用例不关心它具体是什么，
    # 只要「有且对得齐」—— 默认给一份指向真落库行的（显式传了就不覆盖）。
    if _DB and "input_verdict_refs" not in kw:
        base["input_verdict_refs"] = _refs_matching(base)
    return DecisionCard(**base)


def _refs_matching(base) -> list[VerdictRef]:
    """给 `base["verdicts"]` 里每个 agent 造一条对得上的 ref（真落库）。

    ⚠️ run_id 用卡自己声明的那个（`base["run_id"]`），不是 `provenance_for` 派生的 ——
    否则血缘检查会当场判「ref 属于别的执行尝试」，而那是这些用例要测的**别的**东西。
    """
    t = now_cn()
    out = []
    for v in base["verdicts"]:
        fb = FactBundle(task_id=base["decision_id"], agent=v.agent, status="completed",
                        verdict="PASS", result={f"{v.agent}_x": 1.0},
                        data_completeness=1.0, evidence=[_ev(f"{v.agent}_x")], missing=[])
        vid = save_fact_bundle(fb, run_id=base["run_id"], path=_DB[0])
        out.append(VerdictRef(agent=v.agent, verdict_id=vid,
                              content_sha256=load_verdict_meta(vid, path=_DB[0])["content_sha256"],
                              contract_version=CONTRACT_VERSION, run_id=base["run_id"]))
    return out


def _ref(agent="market", vid=1, sha=None, run=TEST_RUN_ID) -> VerdictRef:
    return VerdictRef(agent=agent, verdict_id=vid, content_sha256=sha or "a" * 64,
                      contract_version=CONTRACT_VERSION, run_id=run)


# ══════════════════════════════════════════════════════════ P1 / P2 · schema
class TestSchemaV16:

    @pytest.mark.parametrize("table,column", [
        ("decision_records", "run_id"),
        ("decision_records", "evidence_set_id"),
        ("agent_runs", "orchestration_run_id"),
    ])
    def test_capture列存在且是TEXT(self, db, table, column):
        with connect(db, readonly=True) as c:
            cols = {r[1]: (r[2] or "").upper() for r in c.execute(f"PRAGMA table_info({table})")}
        assert column in cols, f"{table}.{column} 不存在 —— 血缘还是只能解 card_json"
        assert cols[column] == "TEXT"

    def test_一个run至多一套evidence_set(self, db):
        """评审 §9「Every Run has exactly one EvidenceSet」的「至多」那一半。"""
        save_evidence_set(evidence_set_id="es-1", decision_id=DID,
                          manifest={"a": 1}, run_id=TEST_RUN_ID, path=db)
        # 🔴 不 import sqlite3（`tests/test_no_raw_sqlite.py` 的判据：一切 DB 访问
        #    走 `_store`）。判据落在报错文本上 —— 要的是「唯一约束拦下了」这件事，
        #    不是异常类本身。
        with pytest.raises(Exception) as e:
            save_evidence_set(evidence_set_id="es-2", decision_id=DID,
                              manifest={"b": 2}, run_id=TEST_RUN_ID, path=db)
        assert "UNIQUE" in str(e.value).upper(), e.value

    def test_没有run_id的切片不受唯一约束(self, db):
        """分区索引的另一半：`run_id IS NULL` 的历史行可以并存多条。

        🔴 非平凡：`UNIQUE(run_id)` 不加 WHERE 也能让这条过（SQLite 里多行 NULL
        不算重复），所以它证明不了分区子句存在 —— 上面那条才是判据。这条钉的是
        「别把历史行一起约束死」。
        """
        save_evidence_set(evidence_set_id="es-1", decision_id=DID,
                          manifest={"a": 1}, path=db)
        save_evidence_set(evidence_set_id="es-2", decision_id=DID,
                          manifest={"b": 2}, path=db)
        with connect(db, readonly=True) as c:
            n = c.execute("SELECT COUNT(*) FROM evidence_sets WHERE run_id IS NULL").fetchone()[0]
        assert n == 2


# ══════════════════════════════════════════════ P3 / P4 / P5 · 写边界与可查询
class TestOnlineCardRequiresProvenance:

    @pytest.mark.parametrize("missing_field", [
        "run_id", "evidence_set_id",
        # 🔴 批 O（评审 §7.2 第四条）：refs 也进必填 —— 一张答不出「用了哪些原件」
        #    的在线卡不该落库。批 N 当时留了它，理由（会废掉 verdict_refs=None 那条
        #    旧路径）在 B-2 之后不再成立：这次运行用了哪些原件现在是确定可答的。
        "input_verdict_refs",
    ])
    def test_在线卡缺血缘字段则落库拒绝(self, db, missing_field):
        card = _card(**{missing_field: None if missing_field != "input_verdict_refs" else []})
        with pytest.raises(ValueError, match="拒绝落库"):
            save_card(card, path=db)

    def test_回放不受这条约束(self, db):
        """回放本来就不是一次执行尝试 —— `comparable()` 比较时连 run_id 都剥掉。

        要求它带 run_id 等于逼回放捏造一个（L-8：历史永不改写）。
        """
        rid = save_card(_card(), path=db)
        replayed = _card(run_id=None, evidence_set_id=None,
                         input_verdict_refs=[], from_store=True)
        assert save_card(replayed, replay_of=rid, path=db) > 0

    def test_落库之后血缘在列里而不是只在card_json里(self, db):
        """🔴 评审 §7-8 的落点：要的是「一句 SQL 能答」，不是「解开 JSON 能看到」。"""
        save_card(_card(), path=db)
        with connect(db, readonly=True) as c:
            row = c.execute("SELECT run_id, evidence_set_id FROM decision_records "
                            "WHERE decision_id=? AND replay_of IS NULL", (DID,)).fetchone()
        assert row["run_id"] == TEST_RUN_ID
        assert row["evidence_set_id"] == TEST_EVIDENCE_SET_ID

    def test_账本行指得回编排执行尝试(self, db):
        """评审 §8：decision_runs.run_id → agent_runs.orchestration_run_id。"""
        v = _verdict()
        record_verdict_run(v, decision_id=DID, started_at="t", finished_at="t",
                           runtime_run_id="openclaw-xyz",
                           orchestration_run_id=TEST_RUN_ID, path=db)
        with connect(db, readonly=True) as c:
            rows = c.execute(
                """SELECT ar.agent, ar.runtime_run_id
                     FROM decision_runs dr
                     JOIN agent_runs ar ON ar.orchestration_run_id = dr.run_id
                    WHERE dr.run_id = ?""", (TEST_RUN_ID,)).fetchall()
        assert [(r["agent"], r["runtime_run_id"]) for r in rows] == [("market", "openclaw-xyz")], \
            "「某次 BigA Run 启动了哪些运行时 Run」必须是一句 join，不是按决策号做文本匹配"


# ══════════════════════════════════════════════════════ P6 / P7 · 契约层血缘
class TestCardRefLineage:

    def test_refs为空是历史卡常态_不算不一致(self, db):
        assert _card(input_verdict_refs=[]).provenance_warning == ""

    @pytest.mark.parametrize("refs,why", [
        ([], "空"),
        ([_ref("market"), _ref("news")], "多出 news"),
        ([_ref("market"), _ref("market", vid=2)], "market 两条"),
    ])
    def test_refs非空时必须恰好覆盖每条判定(self, db, refs, why):
        if not refs:                       # 空是合法的历史形状，单独一条测
            return
        with pytest.raises(ValueError, match="血缘有问题"):
            _card(input_verdict_refs=refs)

    def test_缺一条ref被拒(self, db):
        with pytest.raises(ValueError, match="缺"):
            _card(verdicts=[_verdict("market"), _verdict("news")],
                  expected_roster=("market", "news"),
                  missing=[f"占位{i}" for i in range(2)],
                  input_verdict_refs=[_ref("market")])

    def test_新卡装着别的run的原件被拒(self, db):
        with pytest.raises(ValueError, match="cross-run|别的执行尝试"):
            _card(input_verdict_refs=[_ref("market", run="another-run")])

    def test_旧卡可读但把问题显示在卡面上(self, db):
        """三段式的中段 —— 历史卡读不回来比读回来带警告更糟（`_check_identity` 先例）。"""
        c = _card(from_store=True, input_verdict_refs=[_ref("market", run="another-run")])
        assert "血缘有问题" in c.provenance_warning

    def test_ref没有run_id时不判(self, db):
        """R-3：`None` 是「不知道」不是「不一致」—— 历史行没有这一列的值。"""
        assert _card(input_verdict_refs=[_ref("market", run=None)]).provenance_warning == ""


# ═══════════════════════════════════════════════════ P8 / P9 · 库层核对与接线
class TestVerifyRefsAgainstStore:

    def _saved(self, db, agent="market", did=DID, run=TEST_RUN_ID):
        """落一条**带 run_id 的** fact 原件（`save_verdict` 没有这个参数，
        run_id 只有 fact 路径带得下来 —— 批 J-I 的设计）。"""
        fb = FactBundle(task_id=did, agent=agent, status="completed", verdict="PASS",
                        result={"x": 1.0}, data_completeness=1.0, evidence=[_ev()],
                        missing=[])
        vid = save_fact_bundle(fb, run_id=run, path=db)
        return vid, load_verdict_meta(vid, path=db)["content_sha256"]

    def test_一致时无问题(self, db):
        vid, sha = self._saved(db)
        assert verify_verdict_refs(_card(input_verdict_refs=[_ref(vid=vid, sha=sha)]),
                                   path=db) == []

    def test_原件属于别的决策时报红(self, db):
        """判据取**库里那一行**的 task_id —— 卡上那份拷贝是被验证方自己出具的。"""
        vid, sha = self._saved(db, did="BIGA-20260924-099")
        problems = verify_verdict_refs(
            _card(from_store=True, input_verdict_refs=[_ref(vid=vid, sha=sha)]), path=db)
        assert any("属于决策" in p for p in problems), problems

    def test_原件属于别的run时报红(self, db):
        """🔴 cross-run 污染的最后一道：卡是新的、证据来自上一次运行。"""
        vid, sha = self._saved(db, run="earlier-run")
        problems = verify_verdict_refs(
            _card(from_store=True,
                  input_verdict_refs=[_ref(vid=vid, sha=sha, run="earlier-run")]),
            path=db)
        assert any("cross-run" in p for p in problems), problems

    def test_引用自称的run与库里那行对不上时报红(self, db):
        vid, sha = self._saved(db)
        problems = verify_verdict_refs(
            _card(from_store=True,
                  input_verdict_refs=[_ref(vid=vid, sha=sha, run=TEST_RUN_ID)]
                  ), path=db)
        assert problems == []          # 前置断言：一致时不该报
        forged = VerdictRef(agent="market", verdict_id=vid, content_sha256=sha,
                            contract_version=CONTRACT_VERSION, run_id="made-up")
        problems = verify_verdict_refs(
            _card(from_store=True, input_verdict_refs=[forged]), path=db)
        assert any("引用与原件对不上" in p for p in problems), problems

    def test_在线落库真的会跑这道核对(self, db):
        """🔴 守卫必须接进真实路径 —— 只写了个函数没人调，是 L-1 那种死配置。"""
        vid, sha = self._saved(db, did="BIGA-20260924-099")
        with pytest.raises(ValueError, match="引用核对不过"):
            save_card(_card(input_verdict_refs=[_ref(vid=vid, sha=sha)]), path=db)


# ══════════════════════════════════════════════════ P10 · 旧 standalone 路径
class TestLegacySynthesizeCLI:

    def _run(self, db, *extra):
        import os
        # 🔴 用带 stance 的 AgentVerdict（不是 FactBundle）：synthesize.py 在落库之前
        #    先查「给了判断就必须有 stance」，没 stance 会在这道更早的闸门上退出，
        #    那样这两条测的就不是本批新加的那道了（探针纪律：先确认命中的是目标条件）。
        vid = save_verdict(_verdict(), path=db)
        # 只有 1 个 agent 到场，另外 5 个天然缺席 —— roster 判据按计数比较。
        em = []
        for i in range(5):
            em += ["--extra-missing", "supervisor.agent_offline", f"占位{i}"]
        return subprocess.run(
            [sys.executable, str(REPO / "skills/decision-card/scripts/synthesize.py"),
             "--verdict-ids", str(vid), "--status", "WAIT", "--headline", "h",
             "--model-ref", "m", *em, "--json", *extra],
            capture_output=True, text=True,
            env={**os.environ, "BIGA_DB_PATH": str(db)})

    def test_落库必须显式给两个id(self, db):
        r = self._run(db)
        assert r.returncode == 1
        assert "--run-id" in r.stderr and "--no-store" in r.stderr, \
            "报错要指出这条命令上该怎么办，不只是说不行"

    def test_只渲染不落库时放行(self, db):
        assert self._run(db, "--no-store").returncode == 0
