"""批 G-I 外发通知：outbox 同事务入队 + 幂等 + 只追加 + worker 投递 + 状态机。

探针清单（设计文档 §6 批 G-I 的 P1–P5）：

  P1  同事务：写 outbox 那步失败 ⇒ Card 也不落库（要么一起成功要么一起回滚）
  P2  幂等：同一 (event_type, aggregate) 入队两次只生效一行 ⇒ 不会投两次
  P3  只追加：outbox 与 deliveries 两张新表 UPDATE/DELETE 都被拒
  P4  worker：四类事件各自构造一个场景，断言 worker 真调了投递接口、payload 带决策号
  P5  NOTIFICATION_PENDING：跳过它直达 COMPLETED 非法；COMPLETED 不依赖投递结果

另加 `card_event_type` 分类判据的单测（risk_block / card_unknown / card_completed）。

🔴 这些是**新守卫的探针**：每一条都能通过「把被守的东西弄坏 ⇒ 它报红」来验证
   （见 CHANGELOG 批 G-I 的探针记录）。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))
sys.path.insert(0, str(REPO / "skills" / "decision-card" / "scripts"))

from _contract import (  # noqa: E402
    CARD_COMPLETED,
    CARD_UNKNOWN,
    NOTIFY_FAILURE_STATES,
    RISK_BLOCK,
    RUN_FAILED,
    VETO_STANCE,
    AgentVerdict,
    DecisionCard,
    LEGAL_TRANSITIONS,
    MissingItem,
    RunState,
    card_event_type,
    new_run_context,
    new_task_id,
)
from _store import (  # noqa: E402
    AppendOnlyViolation,
    connect,
    enqueue_run_failed,
    init_schema,
    list_deliveries,
    load_online_card,
    open_run,
    record_delivery,
    save_card_with_notifications,
    transition,
    undelivered_notifications,
)
from _store.db import _insert_notification  # noqa: E402

import notify_worker  # noqa: E402

DAY = "20260923"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    return p


# ── 卡构造器（满 6 人 roster，各字段满足契约铁律）────────────────────────────
_STANCES = {"market": "缩量上涨", "emotion": "修复", "sector": "主线明确",
            "technical": "多头", "news": "平静", "risk": "放行"}


def _verdict(agent, did, *, verdict="PASS", stance=None, missing=(), status="completed"):
    # contract-exempt: 直接造真 dataclass
    return AgentVerdict(
        task_id=did, agent=agent, status=status, verdict=verdict,
        result={}, data_completeness=0.9, evidence=[],
        stance=stance if stance is not None else _STANCES[agent],
        missing=list(missing),
    )


def _card(did, *, status="WAIT", verdicts=None, missing=()):
    return DecisionCard(
        decision_id=did, status=status, headline="核心矛盾一句话",
        verdicts=verdicts if verdicts is not None
        else [_verdict(a, did) for a in sorted(_STANCES)],
        synthesis="", model_ref="anthropic/claude-sonnet-5",
        missing=list(missing),
    )


def _clean_card(did):
    """全 PASS、无缺失、无否决 ⇒ card_completed。"""
    return _card(did)


def _did(seq):
    return new_task_id(seq, day=DAY)


# ════════════════════════════════ card_event_type ═══════════════════════════
class TestCardEventType:
    def test_否决优先_risk_block(self):
        did = _did(1)
        vs = [_verdict(a, did) for a in sorted(_STANCES)]
        # risk 否决：stance=VETO_STANCE，verdict 不能是 UNKNOWN（跨型铁律），status=BLOCK
        vs = [_verdict("risk", did, verdict="WARNING", stance=VETO_STANCE)
              if v.agent == "risk" else v for v in vs]
        card = _card(did, status="BLOCK", verdicts=vs)
        assert card_event_type(card) == RISK_BLOCK

    def test_UNKNOWN_verdict_是card_unknown(self):
        did = _did(2)
        miss = MissingItem("情绪源不可用", "emotion.src")
        vs = [_verdict(a, did) for a in sorted(_STANCES)]
        vs = [_verdict("emotion", did, verdict="UNKNOWN", stance="无法判定",
                       status="partial", missing=[miss])
              if v.agent == "emotion" else v for v in vs]
        # 契约铁律：verdict 的缺失项必须上浮到 card.missing。
        card = _card(did, status="WAIT", verdicts=vs, missing=[miss])
        assert card_event_type(card) == CARD_UNKNOWN

    def test_有缺失项_是card_unknown(self):
        did = _did(3)
        # 全 PASS 但 Supervisor 追加了一条缺失项 ⇒ 不是干净卡
        card = _card(did, status="WAIT", missing=[MissingItem("risk 未审", "supervisor.x")])
        assert card_event_type(card) == CARD_UNKNOWN

    def test_干净卡_是card_completed(self):
        assert card_event_type(_clean_card(_did(4))) == CARD_COMPLETED

    def test_否决盖过UNKNOWN(self):
        """既有否决又有 UNKNOWN ⇒ 仍归 risk_block（否决优先级最高）。"""
        did = _did(5)
        vs = [_verdict(a, did) for a in sorted(_STANCES)]
        vs = [_verdict("risk", did, verdict="WARNING", stance=VETO_STANCE)
              if v.agent == "risk" else v for v in vs]
        vs = [_verdict("news", did, verdict="UNKNOWN", stance="无法判定",
                       status="partial", missing=[MissingItem("无快讯", "news.x")])
              if v.agent == "news" else v for v in vs]
        card = _card(did, status="BLOCK", verdicts=vs, missing=[MissingItem("无快讯", "news.x")])
        assert card_event_type(card) == RISK_BLOCK


# ════════════════════════════ P1：同事务原子性 ══════════════════════════════
class TestP1SameTransaction:
    def test_合法通知_卡与outbox一起入库(self, db):
        did = _did(10)
        card = _clean_card(did)
        rid = save_card_with_notifications(
            card, [{"event_type": CARD_COMPLETED, "aggregate": did,
                    "payload": {"decision_id": did}}], path=db)
        assert rid > 0
        assert load_online_card(did, path=db) is not None
        pend = undelivered_notifications(path=db)
        assert [(r["event_type"], r["aggregate"]) for r in pend] == [(CARD_COMPLETED, did)]

    def test_非法event_type_卡也不落库(self, db):
        """🔴 P1 核心：outbox 那步失败（非法 event_type）⇒ 整个事务回滚 ⇒ Card 不在库里。"""
        did = _did(11)
        card = _clean_card(did)
        with pytest.raises(ValueError, match="不在白名单"):
            save_card_with_notifications(
                card, [{"event_type": "not_a_real_event", "aggregate": did,
                        "payload": {"decision_id": did}}], path=db)
        # 卡也不能落库 —— 不能有「卡进去了、通知没进去」的中间态
        assert load_online_card(did, path=db) is None
        with connect(db, readonly=True) as conn:
            assert conn.execute("SELECT COUNT(*) FROM decision_records").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM notification_outbox").fetchone()[0] == 0

    def test_payload非严格JSON_卡也不落库(self, db):
        """写边界重校验在同一事务里：NaN 载荷 ⇒ 抛 ⇒ 卡回滚。"""
        did = _did(12)
        card = _clean_card(did)
        with pytest.raises(ValueError):
            save_card_with_notifications(
                card, [{"event_type": CARD_COMPLETED, "aggregate": did,
                        "payload": {"x": float("nan")}}], path=db)
        assert load_online_card(did, path=db) is None


# ════════════════════════════ P2：幂等 ══════════════════════════════════════
class TestP2Idempotent:
    def test_同键入队两次只一行(self, db):
        """enqueue_run_failed 同一个 run 调两次（如 _fail 与 reaper 都触发）⇒ 只一行。"""
        ctx = new_run_context(origin="cli", non_interactive=True, decision_id=_did(20))
        open_run(ctx, path=db)
        first = enqueue_run_failed(ctx.run_id, reason="boom", path=db)
        second = enqueue_run_failed(ctx.run_id, reason="又炸了一次", path=db)
        assert first is not None
        assert second is None  # 幂等命中：第二次是 no-op
        with connect(db, readonly=True) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM notification_outbox WHERE event_type=? AND aggregate=?",
                (RUN_FAILED, ctx.run_id)).fetchone()[0]
        assert n == 1

    def test_低层入队冲突返回None(self, db):
        did = _did(21)
        with connect(db) as conn:
            a = _insert_notification(conn, event_type=CARD_UNKNOWN, aggregate=did,
                                     payload={"decision_id": did})
            b = _insert_notification(conn, event_type=CARD_UNKNOWN, aggregate=did,
                                     payload={"decision_id": did})
        assert a is not None and b is None


# ════════════════════════════ P3：只追加 ════════════════════════════════════
class TestP3AppendOnly:
    def _seed(self, db):
        did = _did(30)
        with connect(db) as conn:
            oid = _insert_notification(conn, event_type=CARD_UNKNOWN, aggregate=did,
                                       payload={"decision_id": did})
        record_delivery(outbox_id=oid, attempt=1, status="failed",
                        channel="stdout", error="试一下", path=db)
        return oid

    def test_outbox不能UPDATE(self, db):
        self._seed(db)
        with pytest.raises(AppendOnlyViolation):
            with connect(db) as conn:
                conn.execute("UPDATE notification_outbox SET event_type='x'")

    def test_outbox不能DELETE(self, db):
        self._seed(db)
        with pytest.raises(AppendOnlyViolation):
            with connect(db) as conn:
                conn.execute("DELETE FROM notification_outbox")

    def test_deliveries不能UPDATE(self, db):
        self._seed(db)
        with pytest.raises(AppendOnlyViolation):
            with connect(db) as conn:
                conn.execute("UPDATE notification_deliveries SET status='delivered'")

    def test_deliveries不能DELETE(self, db):
        self._seed(db)
        with pytest.raises(AppendOnlyViolation):
            with connect(db) as conn:
                conn.execute("DELETE FROM notification_deliveries")


# ════════════════════════════ P4：worker 投递 ═══════════════════════════════
class _RetryableError(RuntimeError):
    """带 `retryable` 属性的假异常 —— 模拟 `feishu_deliverer.FeishuError` 的形状。"""

    def __init__(self, message, *, retryable):
        super().__init__(message)
        self.retryable = retryable


class _Recorder:
    """记录调用的假投递接口。

    `fail=True` 时每次投递都抛（模拟飞书抽风，不带 `retryable` 属性 —— 测普通
    异常的默认行为）；`fail_retryable` 显式给 `True`/`False` 时抛带该标记的异常。
    """
    channel = "test"

    def __init__(self, *, fail=False, fail_retryable=None):
        self.calls: list[dict] = []
        self.fail = fail
        self.fail_retryable = fail_retryable

    def deliver(self, *, event_type, aggregate, payload):
        self.calls.append({"event_type": event_type, "aggregate": aggregate,
                           "payload": payload})
        if self.fail_retryable is not None:
            raise _RetryableError("桩投递故意失败（分类）", retryable=self.fail_retryable)
        if self.fail:
            raise RuntimeError("桩投递故意失败")


class TestP4Worker:
    def _seed_four(self, db):
        """四类事件各入队一条，返回 {event_type: 对应决策号}。"""
        expect = {}
        # 三类卡事件：走真入队路径（save_card_with_notifications），event_type 显式给全四类里
        # 的三种（分类判据由 TestCardEventType 单独钉，这里只关心 worker 投得出去）。
        for et in (CARD_COMPLETED, CARD_UNKNOWN, RISK_BLOCK):
            did = _did(40 + len(expect))
            save_card_with_notifications(
                _clean_card(did),
                [{"event_type": et, "aggregate": did, "payload": {"decision_id": did}}],
                path=db)
            expect[et] = did
        # run_failed：走真入队路径（enqueue_run_failed）
        ctx = new_run_context(origin="cli", non_interactive=True, decision_id=_did(49))
        open_run(ctx, path=db)
        enqueue_run_failed(ctx.run_id, reason="boom", path=db)
        expect[RUN_FAILED] = ctx.decision_id
        return expect

    def test_四类事件都被投出且payload带决策号(self, db):
        expect = self._seed_four(db)
        rec = _Recorder()
        summary = notify_worker.deliver_pending(rec, path=db)
        assert summary == {"pending": 4, "delivered": 4, "failed": 0, "abandoned": 0}
        got = {c["event_type"] for c in rec.calls}
        assert got == {CARD_COMPLETED, CARD_UNKNOWN, RISK_BLOCK, RUN_FAILED}
        # 🔴 每一条 payload 都带对应决策号
        for c in rec.calls:
            assert c["payload"].get("decision_id") == expect[c["event_type"]]
        # 投完之后没有待投的了（幂等：再跑一次 0 条）
        assert undelivered_notifications(path=db) == []
        assert notify_worker.deliver_pending(_Recorder(), path=db)["pending"] == 0

    def test_投递失败_记failed且留在队列里可重投(self, db):
        self._seed_four(db)
        summary = notify_worker.deliver_pending(_Recorder(fail=True), path=db)
        assert summary["delivered"] == 0 and summary["failed"] == 4
        # 失败不投成 ⇒ 仍在待投队列（下次可重投），且各记了一条 failed
        pend = undelivered_notifications(path=db)
        assert len(pend) == 4
        for r in pend:
            assert r["attempt_count"] == 1  # 试过一次
            attempts = list_deliveries(r["outbox_id"], path=db)
            assert [a["status"] for a in attempts] == ["failed"]
        # 换一个好的投递接口重投 ⇒ 全部投成，attempt 递增到 2
        ok = _Recorder()
        assert notify_worker.deliver_pending(ok, path=db)["delivered"] == 4
        assert undelivered_notifications(path=db) == []

    def test_non_retryable错误立刻放弃_不留在队列里(self, db):
        """🔴 2026-09-24（外部评审 §11）：配置类错误重试不会变好——立刻 abandoned，
        不再无意义地占着 MAX_ATTEMPTS 次机会。探针：把 `not retryable` 这个判据
        删掉，这条就会退化成跟普通失败一样留在队列里、变红。"""
        self._seed_four(db)
        summary = notify_worker.deliver_pending(
            _Recorder(fail_retryable=False), path=db)
        assert summary == {"pending": 4, "delivered": 0, "failed": 0, "abandoned": 4}
        assert undelivered_notifications(path=db) == [], \
            "non-retryable 放弃之后不该再出现在待投列表里"

    def test_retryable错误达到上限后放弃(self, db):
        """试满 MAX_ATTEMPTS 次还没成功 ⇒ 放弃，不再无限期重试。"""
        self._seed_four(db)
        outbox_ids = [r["outbox_id"] for r in undelivered_notifications(path=db)]
        bad = _Recorder(fail_retryable=True)
        for _ in range(notify_worker.MAX_ATTEMPTS - 1):
            summary = notify_worker.deliver_pending(bad, path=db)
            assert summary["failed"] == 4 and summary["abandoned"] == 0, \
                "没到上限之前应该还是 failed（留着重试），不是 abandoned"
        # 第 MAX_ATTEMPTS 次：达到上限，放弃
        summary = notify_worker.deliver_pending(bad, path=db)
        assert summary["abandoned"] == 4 and summary["failed"] == 0
        assert undelivered_notifications(path=db) == []
        for oid in outbox_ids:
            statuses = [a["status"] for a in list_deliveries(oid, path=db)]
            assert statuses == ["failed"] * (notify_worker.MAX_ATTEMPTS - 1) + ["abandoned"]

    def test_不带retryable属性的异常默认当作可重试(self, db):
        """普通异常（没有 `.retryable`）不能被当成"已放弃"处理——R-3 方向：
        判不了就按更保守的一侧走（继续重试），不要武断放弃。"""
        self._seed_four(db)
        summary = notify_worker.deliver_pending(_Recorder(fail=True), path=db)
        assert summary["failed"] == 4 and summary["abandoned"] == 0

    def test_main有真失败时退出码非零(self, db, monkeypatch):
        """🔴 2026-09-24（外部评审 §11）：曾经无论失败多少条 main() 都 return 0，
        systemd（Type=oneshot，只看退出码）因此把失败批次误判为成功。探针：把
        `return 1 if ... else 0` 改回恒定 `return 0`，这条立刻变红。"""
        monkeypatch.setattr(notify_worker, "deliver_pending",
                            lambda *a, **k: {"pending": 4, "delivered": 0,
                                             "failed": 4, "abandoned": 0})
        assert notify_worker.main(["--deliverer", "stdout"]) == 1

    def test_main只有abandoned没有failed时退出码为零(self, db, monkeypatch):
        """abandoned 已经处理完了（不会再自动重试）——不该让调用方以为这次调用坏了。"""
        monkeypatch.setattr(notify_worker, "deliver_pending",
                            lambda *a, **k: {"pending": 4, "delivered": 0,
                                             "failed": 0, "abandoned": 4})
        assert notify_worker.main(["--deliverer", "stdout"]) == 0

    def test_worker默认桩不抛(self, db, capsys):
        """默认 StdoutDeliverer 能把队列清空（L-1：outbox 有一个真能跑的读取方）。"""
        self._seed_four(db)
        summary = notify_worker.deliver_pending(path=db)
        assert summary["delivered"] == 4
        assert "[notify]" in capsys.readouterr().out


# ════════════════════════ P5：NOTIFICATION_PENDING 转移 ══════════════════════
class TestP5NotificationPendingTransition:
    def test_跳过NOTIFICATION_PENDING直达COMPLETED非法(self):
        assert (RunState.CARD_PERSISTED, RunState.COMPLETED) not in LEGAL_TRANSITIONS

    def test_必经路径合法(self):
        assert (RunState.CARD_PERSISTED, RunState.NOTIFICATION_PENDING) in LEGAL_TRANSITIONS
        assert (RunState.NOTIFICATION_PENDING, RunState.COMPLETED) in LEGAL_TRANSITIONS

    def _drive_to(self, db, rid, upto):
        chain = [
            (RunState.RECEIVED, RunState.PREFLIGHTED),
            (RunState.PREFLIGHTED, RunState.SNAPSHOT_FROZEN),
            (RunState.SNAPSHOT_FROZEN, RunState.STAGE1_RUNNING),
            (RunState.STAGE1_RUNNING, RunState.STAGE1_COMPLETED),
            (RunState.STAGE1_COMPLETED, RunState.RISK_RUNNING),
            (RunState.RISK_RUNNING, RunState.SYNTHESIZING),
            (RunState.SYNTHESIZING, RunState.CARD_PERSISTED),
            (RunState.CARD_PERSISTED, RunState.NOTIFICATION_PENDING),
            (RunState.NOTIFICATION_PENDING, RunState.COMPLETED),
        ]
        for frm, to in chain:
            transition(rid, frm, to, path=db)
            if to == upto:
                return

    def test_从CARD_PERSISTED跳到COMPLETED被CAS拒(self, db):
        from _store import IllegalTransition
        ctx = new_run_context(origin="cli", non_interactive=True)
        open_run(ctx, path=db)
        self._drive_to(db, ctx.run_id, RunState.CARD_PERSISTED)
        with pytest.raises(IllegalTransition):
            transition(ctx.run_id, RunState.CARD_PERSISTED, RunState.COMPLETED, path=db)

    def test_COMPLETED不依赖投递结果(self, db):
        """🔴 一条 outbox 行还没投递（undelivered 非空），Run 照样能到 COMPLETED。

        证明 COMPLETED 的达成不等 worker —— 一次飞书 API 抽风不会把 Run 卡在非终态。
        """
        did = _did(50)
        save_card_with_notifications(
            _clean_card(did),
            [{"event_type": CARD_COMPLETED, "aggregate": did,
              "payload": {"decision_id": did}}], path=db)
        ctx = new_run_context(origin="cli", non_interactive=True, decision_id=did)
        open_run(ctx, path=db)
        self._drive_to(db, ctx.run_id, RunState.COMPLETED)
        from _store import current_state
        assert current_state(ctx.run_id, path=db) == RunState.COMPLETED
        # 通知仍在待投队列（worker 还没跑）——终态与投递解耦
        assert len(undelivered_notifications(path=db)) == 1


# ════════════════════════ run_failed 入队的两条产生路径 ══════════════════════
class TestRunFailedEnqueue:
    def test_enqueue_run_failed_payload带决策号(self, db):
        ctx = new_run_context(origin="cli", non_interactive=True, decision_id=_did(60))
        open_run(ctx, path=db)
        enqueue_run_failed(ctx.run_id, reason="采集异常", path=db)
        [row] = undelivered_notifications(path=db)
        assert row["event_type"] == RUN_FAILED
        assert row["aggregate"] == ctx.run_id
        assert row["payload"]["decision_id"] == ctx.decision_id
        assert row["payload"]["reason"] == "采集异常"

    def test_未open的run不能入队run_failed(self, db):
        with pytest.raises(ValueError, match="不在 decision_runs"):
            enqueue_run_failed("nonexistent-run-id", path=db)

    def test_run_ledger_move到终态时入队(self, db, monkeypatch):
        """bash 侧：run_ledger move --to CANCELLED ⇒ 入队一条 run_failed。"""
        import run_ledger
        ctx = new_run_context(origin="cli", non_interactive=True, decision_id=_did(61))
        open_run(ctx, path=db)
        transition(ctx.run_id, RunState.RECEIVED, RunState.PREFLIGHTED, path=db)
        rc = run_ledger.main(["move", ctx.run_id, "--expect", RunState.PREFLIGHTED,
                              "--to", RunState.CANCELLED,
                              "--detail", '{"reason":"并发锁"}'])
        assert rc == 0
        [row] = undelivered_notifications(path=db)
        assert row["event_type"] == RUN_FAILED
        assert row["payload"]["reason"] == "并发锁"

    def test_CANCELLED在失败通知状态集里(self):
        assert NOTIFY_FAILURE_STATES == {RunState.FAILED, RunState.TIMEOUT,
                                         RunState.CANCELLED}
