"""显式状态机测试（设计文档 §5）—— 状态、合法转移、CAS、只追加、消费方。

这里钉死批 B 的四条判据与四道探针：

  · 恰好 13 个状态，一个不多（不许把评审那 15 个照抄进来）
  · 每个状态「谁写它」（可从 RECEIVED 到达）「谁读它」（--status 说得清）
  · 非法转移（跳步 / 回退 / 并发双写）被 CAS 拒绝           —— P1 / P3
  · decision_runs 不可 UPDATE、run_events 不可 DELETE      —— P2
  · 没有消费方的状态会被抓到                                —— P4
"""

from __future__ import annotations

import pathlib
import re
import sys
import threading
from collections import deque

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "decision-card" / "scripts"))

from _contract import (  # noqa: E402
    INITIAL_STATE,
    LEGAL_TRANSITIONS,
    RUN_STATES,
    TERMINAL_STATES,
    RunState,
    new_run_context,
)
from _store import (  # noqa: E402
    AppendOnlyViolation,
    IllegalTransition,
    UnknownRun,
    connect,
    current_state,
    init_schema,
    open_run,
    run_events,
    run_journey,
    transition,
)

import run_ledger  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    return p


def _open(db, **kw) -> str:
    return open_run(new_run_context(origin="cli", non_interactive=True, **kw), path=db)


# ══ 状态清单：恰好 13 个，一个不多 ═══════════════════════════════════════


class TestStateInventory:
    def test_恰好13个状态且名字与设计文档一致(self):
        """🔴 「照设计文档 §5 那张表，一个不多」。

        写死这 13 个名字，就是不让 `IDENTITY_RESERVED` / `SNAPSHOT_COLLECTING`
        / `NOTIFICATION_PENDING` 之类凭空多出来的状态混进来 —— 多一个就是一条
        L-1 死配置。加/删状态要**同时**改这里，逼人回答「它谁写谁读」。
        """
        assert RUN_STATES == {
            "RECEIVED", "PREFLIGHTED", "SNAPSHOT_FROZEN", "STAGE1_RUNNING",
            "STAGE1_COMPLETED", "RISK_RUNNING", "SYNTHESIZING", "CARD_PERSISTED",
            "COMPLETED", "FAILED", "TIMEOUT", "CANCELLED", "INPUT_REQUIRED",
        }
        assert len(RUN_STATES) == 13

    def test_五个终态(self):
        assert TERMINAL_STATES == {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED",
                                   "INPUT_REQUIRED"}
        assert TERMINAL_STATES <= RUN_STATES

    def test_初始态是RECEIVED(self):
        assert INITIAL_STATE == RunState.RECEIVED


# ══ 「谁写它」：图的可达性 ════════════════════════════════════════════════


def _adjacency() -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for a, b in LEGAL_TRANSITIONS:
        adj.setdefault(a, set()).add(b)
    return adj


class TestWhoWritesIt:
    """每个状态都要能被某条合法转移产生 —— 否则没有代码能进入它（死配置）。"""

    def test_转移的两端都是合法状态(self):
        for a, b in LEGAL_TRANSITIONS:
            assert a in RUN_STATES, f"转移起点 {a} 不是合法状态"
            assert b in RUN_STATES, f"转移终点 {b} 不是合法状态"

    def test_每个状态都可从RECEIVED到达(self):
        """🔴 「谁写它」的结构性判据：BFS 从 RECEIVED 出发能不能走到它。

        走不到 = 没有任何转移序列能产生它 = 它永远不会出现 = 死配置。
        RECEIVED 自己是入口（open_run 写），不需要入边。
        """
        adj = _adjacency()
        seen = {INITIAL_STATE}
        q = deque([INITIAL_STATE])
        while q:
            for nxt in adj.get(q.popleft(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    q.append(nxt)
        unreachable = RUN_STATES - seen
        assert not unreachable, f"这些状态从 RECEIVED 走不到（没人能写它）：{sorted(unreachable)}"

    def test_终态无出边(self):
        adj = _adjacency()
        stuck = {s for s in TERMINAL_STATES if adj.get(s)}
        assert not stuck, f"终态不该有出边（进去就出不来）：{sorted(stuck)}"

    def test_非终态都有出边(self):
        adj = _adjacency()
        dead = {s for s in RUN_STATES - TERMINAL_STATES if not adj.get(s)}
        assert not dead, f"这些非终态没有出边，会永远卡住：{sorted(dead)}"


# ══ 「谁读它」：--status 消费方（P4 的落点）═══════════════════════════════


class TestWhoReadsIt:
    """每个状态都要有读取方。批 B 里通用读取方是 `biga-card --status`，
    它的知识就是 `run_ledger.STATE_MEANING`。"""

    def test_每个状态都有消费方(self):
        """🔴 P4：STATE_MEANING 必须覆盖每一个状态。

        往 RUN_STATES 加一个状态却不在 STATE_MEANING 里给它一句话 ——
        `--status` 说不清它，也就是「没有读取方」—— 这条会红。
        """
        missing = RUN_STATES - set(run_ledger.STATE_MEANING)
        assert not missing, f"这些状态没有读取方（STATE_MEANING 缺）：{sorted(missing)}"

    def test_describe_state对每个状态都给得出话(self):
        for s in RUN_STATES:
            assert run_ledger.describe_state(s)  # 非空

    def test_没有消费方的状态describe会抛错(self):
        """描述一个不存在的状态必须炸 —— 这正是 P4 探针弄坏后 describe 的表现。"""
        with pytest.raises(run_ledger.NoConsumerForState):
            run_ledger.describe_state("SOME_UNREGISTERED_STATE")

    def test_STATE_MEANING没有多余条目(self):
        """反向：消费方不该给一个根本不存在的状态编解释（那是漂移的另一头）。"""
        extra = set(run_ledger.STATE_MEANING) - RUN_STATES
        assert not extra, f"STATE_MEANING 里有 RUN_STATES 之外的状态：{sorted(extra)}"


# ══ open_run + transition 的正常路径 ═════════════════════════════════════


class TestHappyPath:
    def test_open写头与初始事件(self, db):
        rid = _open(db)
        assert current_state(rid, path=db) == RunState.RECEIVED
        evs = run_events(rid, path=db)
        assert len(evs) == 1
        assert evs[0]["from_state"] is None and evs[0]["to_state"] == "RECEIVED"

    def test_同一run_id不能open两次(self, db):
        ctx = new_run_context(origin="cli", non_interactive=True)
        open_run(ctx, path=db)
        with pytest.raises(IllegalTransition, match="已经开过"):
            open_run(ctx, path=db)

    def test_编排主干可以一路走到COMPLETED(self, db):
        rid = _open(db)
        chain = [
            (RunState.RECEIVED, RunState.PREFLIGHTED),
            (RunState.PREFLIGHTED, RunState.SNAPSHOT_FROZEN),
            (RunState.SNAPSHOT_FROZEN, RunState.STAGE1_RUNNING),
            (RunState.STAGE1_RUNNING, RunState.STAGE1_COMPLETED),
            (RunState.STAGE1_COMPLETED, RunState.RISK_RUNNING),
            (RunState.RISK_RUNNING, RunState.SYNTHESIZING),
            (RunState.SYNTHESIZING, RunState.CARD_PERSISTED),
            (RunState.CARD_PERSISTED, RunState.COMPLETED),
        ]
        for frm, to in chain:
            transition(rid, frm, to, path=db)
        assert current_state(rid, path=db) == RunState.COMPLETED

    def test_legacy粗粒度路径合法(self, db):
        """bin/biga-card 走的那条：预检后直接观测到卡落库。"""
        rid = _open(db)
        transition(rid, RunState.RECEIVED, RunState.PREFLIGHTED, path=db)
        transition(rid, RunState.PREFLIGHTED, RunState.CARD_PERSISTED,
                   detail={"decision_id": "BIGA-20260922-007"}, path=db)
        transition(rid, RunState.CARD_PERSISTED, RunState.COMPLETED, path=db)
        j = run_journey(rid, path=db)
        assert j["current_state"] == RunState.COMPLETED
        # detail 里带回了发现的决策号（run 头那侧占号时它还不存在）
        persisted = [e for e in j["events"] if e["to_state"] == "CARD_PERSISTED"][0]
        assert persisted["detail"]["decision_id"] == "BIGA-20260922-007"

    def test_run_events序列能完整复述走过的路(self, db):
        """判据：一条运行的 run_events 序列可以完整复述它走过的路。"""
        rid = _open(db)
        transition(rid, RunState.RECEIVED, RunState.PREFLIGHTED, path=db)
        transition(rid, RunState.PREFLIGHTED, RunState.INPUT_REQUIRED, path=db)
        seq = [(e["from_state"], e["to_state"]) for e in run_events(rid, path=db)]
        assert seq == [(None, "RECEIVED"), ("RECEIVED", "PREFLIGHTED"),
                       ("PREFLIGHTED", "INPUT_REQUIRED")]

    def test_从每个在途态都能进失败终态(self, db):
        """失败/超时/取消可以从任何在途状态发生。"""
        for term in (RunState.FAILED, RunState.TIMEOUT, RunState.CANCELLED):
            rid = _open(db)
            transition(rid, RunState.RECEIVED, term, path=db)
            assert current_state(rid, path=db) == term


# ══ CAS：非法转移一律拒绝（跳步 / 回退 / expected 错 / unknown）═════════


class TestTransitionRejects:
    def test_跳步被拒(self, db):
        rid = _open(db)
        with pytest.raises(IllegalTransition, match="不是合法转移"):
            transition(rid, RunState.RECEIVED, RunState.COMPLETED, path=db)

    def test_回退被拒(self, db):
        rid = _open(db)
        transition(rid, RunState.RECEIVED, RunState.PREFLIGHTED, path=db)
        # PREFLIGHTED → RECEIVED 不在图里
        with pytest.raises(IllegalTransition, match="不是合法转移"):
            transition(rid, RunState.PREFLIGHTED, RunState.RECEIVED, path=db)

    def test_expected传错被拒(self, db):
        """图里 RECEIVED→PREFLIGHTED 合法，但 run 现在在 RECEIVED，
        谎称它在 PREFLIGHTED 会被 compare 挡下。"""
        rid = _open(db)
        with pytest.raises(IllegalTransition, match="CAS 失败|现在在"):
            transition(rid, RunState.PREFLIGHTED, RunState.CARD_PERSISTED, path=db)

    def test_终态出不去(self, db):
        rid = _open(db)
        transition(rid, RunState.RECEIVED, RunState.CANCELLED, path=db)
        with pytest.raises(IllegalTransition):
            transition(rid, RunState.CANCELLED, RunState.PREFLIGHTED, path=db)

    def test_未知run被拒(self, db):
        with pytest.raises(UnknownRun, match="不存在"):
            transition("no-such-run", RunState.RECEIVED, RunState.PREFLIGHTED, path=db)


# ══ P1：并发同转移只有一个成功 ═══════════════════════════════════════════


class TestConcurrentCAS:
    def test_并发同转移只有一个成功(self, db):
        """🔴 P1：两个进程/线程对同一 run 做同一个转移，只有一个能落地。

        真正的仲裁是 UNIQUE(run_id, seq)：两边都读到 seq=1、都算 seq=2，
        唯一约束只让一个 INSERT 成功，另一个撞约束 → IllegalTransition。
        """
        rid = _open(db)
        N = 8
        barrier = threading.Barrier(N)
        results: list[str] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker():
            barrier.wait()  # 尽量同时开跑，制造真正的竞争
            try:
                transition(rid, RunState.RECEIVED, RunState.PREFLIGHTED, path=db)
                with lock:
                    results.append("ok")
            except Exception as e:  # noqa: BLE001
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 1, f"应恰好一个成功，实际 {len(results)}"
        assert len(errors) == N - 1
        assert all(isinstance(e, IllegalTransition) for e in errors)
        assert current_state(rid, path=db) == RunState.PREFLIGHTED
        # 只追加了一条转移事件（RECEIVED + PREFLIGHTED = 2 行）
        assert len(run_events(rid, path=db)) == 2


# ══ P2：只追加 —— run 表不可改写 ═════════════════════════════════════════


class TestAppendOnly:
    def test_UPDATE_decision_runs被拒(self, db):
        _open(db)
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(db) as c:
                c.execute("UPDATE decision_runs SET origin='x'")

    def test_DELETE_run_events被拒(self, db):
        _open(db)
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(db) as c:
                c.execute("DELETE FROM run_events")

    def test_UPDATE_run_events被拒(self, db):
        """状态转移日志改了，CAS 的地基就没了。"""
        _open(db)
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(db) as c:
                c.execute("UPDATE run_events SET to_state='COMPLETED'")

    def test_evidence_sets也受保护(self, db):
        """批 B 不写它，但触发器现在就得在（F1：建表时就带，别等有生产方）。"""
        with connect(db) as c:  # 先放一行进去（追加允许）
            c.execute(
                "INSERT INTO evidence_sets "
                "(evidence_set_id, frozen_at, manifest_json, created_at) "
                "VALUES ('es-1','t','{}','t')")
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(db) as c:
                c.execute("UPDATE evidence_sets SET manifest_json='{}'")


# ══ bin/biga-card 的 _move 调用都得是合法转移 ═══════════════════════════════
#
# bin/biga-card 是 bash，它记 run 的每一步 `_move FROM TO` 里的状态名是**字面量**，
# 不经类型检查。而 legacy 出卡路径受总闸拦着、跑一次要 3 分钟 / $1.2，端到端
# 覆盖不到它 —— 一个 `_move PREFLGHTED …` 的拼写错误会 best-effort 地静默失败，
# 没有任何测试会红。这条守卫把那些字面量捞出来，逐一核对是否在 LEGAL_TRANSITIONS 里。


_MOVE_RE = re.compile(r"^\s*_move\s+([A-Z_]+)\s+([A-Z_]+)", re.MULTILINE)


class TestBigaCardWiring:
    def test_biga_card里的每个_move都是合法转移(self):
        card = (REPO / "bin" / "biga-card").read_text(encoding="utf-8")
        pairs = _MOVE_RE.findall(card)
        assert pairs, "一个 _move 都没扫到 —— 要么脚本变了，要么正则坏了（等于没测）"
        bad = [(f, t) for f, t in pairs if (f, t) not in LEGAL_TRANSITIONS]
        assert not bad, (
            f"bin/biga-card 里这些 _move 不是合法转移：{bad}\n"
            "  bash 字面量不经类型检查，而 legacy 出卡路径端到端测不到 —— "
            "拼错一个状态名就静默失效。")

    def test_biga_card用到的状态都真实存在(self):
        card = (REPO / "bin" / "biga-card").read_text(encoding="utf-8")
        used = {s for pair in _MOVE_RE.findall(card) for s in pair}
        ghost = used - RUN_STATES
        assert not ghost, f"bin/biga-card 用了不存在的状态名：{sorted(ghost)}"
