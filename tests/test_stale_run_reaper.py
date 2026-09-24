"""stale_run_reaper：扫非终态 run + 按最后一次推进时刻判过期 + CAS 安全收敛
（外部评审 §14）。

探针：
  P1  只有非终态 + 超过阈值的 run 会被找到（`find_stale_runs`，纯读）。
  P2  `dry_run=True`（默认）不改任何状态；`--apply`/`dry_run=False` 才真的转移。
  P3  真转移会同时入队一条 `run_failed` 通知（`enqueue_run_failed` 幂等）。
  P4  TOCTOU：候选生成之后、真正转移之前状态变了（已经到终态 / 变成别的非终态）
      ⇒ 不误覆盖（CAS 拒绝）。
  P5  判据是「最后一次推进」不是「起跑时刻」：起跑很久但刚推进的 run
      不该被误杀（三轮复核真机复现的真实缺陷）。

这是纯函数工具（`docs/guide/orchestration-kickoff-prompt.md` 明确的范围），
不接调度——不测任何 cron/systemd timer。
"""

from __future__ import annotations

import pathlib
import sys
from datetime import timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))
sys.path.insert(0, str(REPO / "tools" / "maintenance"))

from _contract import (  # noqa: E402
    RunContext,
    RunState,
    new_run_context,
    new_run_id,
    new_trigger_id,
    now_cn,
)
from _store import (  # noqa: E402
    init_schema,
    open_run,
    run_events,
    transition,
    undelivered_notifications,
)

import stale_run_reaper as reaper  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    return p


def _open(db, **kw) -> str:
    return open_run(new_run_context(origin="cli", non_interactive=True, **kw), path=db)


def _age_past_threshold(monkeypatch, threshold_sec, extra_sec=1):
    """把 `stale_run_reaper.now_cn()` 的返回值往未来推，让已开的 run 看起来很旧。"""
    real_now = reaper.now_cn()
    future = real_now + timedelta(seconds=threshold_sec + extra_sec)
    monkeypatch.setattr(reaper, "now_cn", lambda: future)


def _open_with_old_created_at(db, decision_id, age_sec) -> str:
    """开一个 `created_at` 是「很久以前」的 run——`decision_runs` 只追加，
    事后改不了 `created_at`，所以只能在构造 `RunContext` 时就把它定成旧值
    （`new_run_context()` 自动盖的是真实当前时刻，这里绕开它）。用来验证
    「起跑很久，但最后一次推进是刚才」不该被误判成 stale（三轮复核真机复现）。
    """
    old_created = (now_cn() - timedelta(seconds=age_sec)).isoformat()
    ctx = RunContext(trigger_id=new_trigger_id("cli"), decision_id=decision_id,
                     run_id=new_run_id(), evidence_set_id=None, origin="cli",
                     non_interactive=True, created_at=old_created)
    return open_run(ctx, path=db)


class TestFindStaleRuns:

    def test_未超过阈值不算stale(self, db):
        _open(db, decision_id="BIGA-20260101-001")
        assert reaper.find_stale_runs(1200, path=db) == []

    def test_超过阈值且非终态才算stale(self, db, monkeypatch):
        rid = _open(db, decision_id="BIGA-20260101-001")
        _age_past_threshold(monkeypatch, 1200)
        stale = reaper.find_stale_runs(1200, path=db)
        assert len(stale) == 1
        assert stale[0]["run_id"] == rid
        assert stale[0]["state"] == RunState.RECEIVED
        assert stale[0]["age_sec"] >= 1200

    def test_已经是终态的不算stale(self, db, monkeypatch):
        rid = _open(db, decision_id="BIGA-20260101-001")
        transition(rid, RunState.RECEIVED, RunState.PREFLIGHTED, path=db)
        transition(rid, RunState.PREFLIGHTED, RunState.FAILED, path=db)
        _age_past_threshold(monkeypatch, 1200)
        assert reaper.find_stale_runs(1200, path=db) == []

    def test_只读_不改变任何状态(self, db, monkeypatch):
        rid = _open(db, decision_id="BIGA-20260101-001")
        _age_past_threshold(monkeypatch, 1200)
        reaper.find_stale_runs(1200, path=db)
        assert run_events(rid, path=db)[-1]["to_state"] == RunState.RECEIVED

    def test_起跑很久但刚推进_不算stale(self, db):
        """🔴 2026-09-24（三轮复核，真机 PoC）：第一版判据用
        `decision_runs.created_at`（起跑时刻），不是最后一次推进的时刻——
        一个起跑很久、但刚刚才正常推进的 run 会被误判成 stale。这里构造
        「起跑于 1300s 前，但最后一条转移是现在」，判据必须看后者。"""
        rid = _open_with_old_created_at(db, "BIGA-20260101-001", age_sec=1300)
        transition(rid, RunState.RECEIVED, RunState.PREFLIGHTED, path=db)
        transition(rid, RunState.PREFLIGHTED, RunState.SNAPSHOT_FROZEN, path=db)
        transition(rid, RunState.SNAPSHOT_FROZEN, RunState.STAGE1_RUNNING, path=db)
        assert reaper.find_stale_runs(1200, path=db) == [], \
            "起跑很久不该算 stale——刚才还在正常推进"

    def test_起跑很久且真的没再推进_算stale(self, db, monkeypatch):
        """对照组：起跑很久、且最后一次推进也是很久以前（真的卡住了）——
        这种才该被判成 stale，确认修法没有连带失效。"""
        rid = _open_with_old_created_at(db, "BIGA-20260101-001", age_sec=1300)
        _age_past_threshold(monkeypatch, 1200)  # 把「现在」推到最后一次事件之后
        stale = reaper.find_stale_runs(1200, path=db)
        assert len(stale) == 1 and stale[0]["run_id"] == rid


class TestReap:

    def test_dry_run默认不真的转移(self, db, monkeypatch):
        """🔴 探针：把 `dry_run=True` 分支删掉（或者默认值改成 False），这条会红
        ——run_events 最新状态会变成 TIMEOUT 而不是停在 RECEIVED。"""
        rid = _open(db, decision_id="BIGA-20260101-001")
        _age_past_threshold(monkeypatch, 1200)
        results = reaper.reap(1200, path=db)  # dry_run 默认 True
        assert results[0]["action"] == "would-reap"
        assert run_events(rid, path=db)[-1]["to_state"] == RunState.RECEIVED, \
            "dry-run 不该真的转移状态"

    def test_apply真的收成TIMEOUT且入队通知(self, db, monkeypatch):
        rid = _open(db, decision_id="BIGA-20260101-001")
        transition(rid, RunState.RECEIVED, RunState.PREFLIGHTED, path=db)
        transition(rid, RunState.PREFLIGHTED, RunState.SNAPSHOT_FROZEN, path=db)
        transition(rid, RunState.SNAPSHOT_FROZEN, RunState.STAGE1_RUNNING, path=db)
        _age_past_threshold(monkeypatch, 1200)
        results = reaper.reap(1200, dry_run=False, path=db)
        assert results[0]["action"] == "reaped"
        assert run_events(rid, path=db)[-1]["to_state"] == RunState.TIMEOUT
        # enqueue_run_failed 幂等：一条 run_failed 通知已入队
        pend = undelivered_notifications(path=db)
        assert len(pend) == 1
        assert pend[0]["payload"]["run_id"] == rid

    def test_两个stale_run各自独立处理(self, db, monkeypatch):
        r1 = _open(db, decision_id="BIGA-20260101-001")
        r2 = _open(db, decision_id="BIGA-20260101-002")
        _age_past_threshold(monkeypatch, 1200)
        results = reaper.reap(1200, dry_run=False, path=db)
        assert {r["run_id"] for r in results} == {r1, r2}
        assert all(r["action"] == "reaped" for r in results)
        assert run_events(r1, path=db)[-1]["to_state"] == RunState.TIMEOUT
        assert run_events(r2, path=db)[-1]["to_state"] == RunState.TIMEOUT

    def test_扫描后已经自己到终态_不覆盖(self, db, monkeypatch):
        """P4：`find_stale_runs` 扫到的时候还是非终态，但在真正 `reap` 之前
        run 自己已经正常走到终态了——不能被"收成 TIMEOUT"覆盖掉一个已经
        终结的结果。这里用手工构造候选列表（绕过 `find_stale_runs` 的实时扫描）
        来模拟这个时间窗口。

        ⚠️ 2026-09-24（对抗性复核指出的命名问题）：这条测试**不**验证 TOCTOU
        安全的 `expected` 取值来源（真正验证那个的是下面
        `test_扫描后状态变成别的非终态_CAS拒绝不覆盖`）——探针证实了这一点：
        把 `reap()` 里的 `expected` 从"扫描时的旧状态"改回"实时重读当前状态"
        这个已知有缺陷的第一版实现，这条测试**依然通过**。原因是终态在
        `LEGAL_TRANSITIONS` 里没有任何出边（见 `domain/run.py::TERMINAL_STATES`
        与 `_FAILURE`/`_ORCHESTRATED` 的构造），`transition(rid, 任何终态, TIMEOUT)`
        无论 `expected` 传的是新是旧都必然是非法转移——这条测试实际验证的是
        「终态在状态机层面已经出不去」这条 DAG 性质，不是 reaper 自己的
        TOCTOU 保护。名字曾经写着 `TOCTOU_` 前缀，容易让人误以为两条测试各自
        独立覆盖了 TOCTOU 的两半——已去掉，只保留下面那条真正承担这个职责。"""
        rid = _open(db, decision_id="BIGA-20260101-001")
        transition(rid, RunState.RECEIVED, RunState.PREFLIGHTED, path=db)
        # 模拟"扫描时还是非终态"的候选（数据已经过时）
        stale_snapshot = [{"run_id": rid, "decision_id": "BIGA-20260101-001",
                           "state": RunState.PREFLIGHTED, "age_sec": 1300}]
        monkeypatch.setattr(reaper, "find_stale_runs", lambda *a, **k: stale_snapshot)
        # 真实世界里它已经推进到了终态
        transition(rid, RunState.PREFLIGHTED, RunState.CANCELLED, path=db)
        results = reaper.reap(1200, dry_run=False, path=db)
        assert results[0]["action"] == "skipped-cas-conflict"
        assert run_events(rid, path=db)[-1]["to_state"] == RunState.CANCELLED, \
            "已经到终态的不该被 reaper 覆盖"

    def test_扫描后状态变成别的非终态_CAS拒绝不覆盖(self, db, monkeypatch):
        """🔴 这条才是真正验证 TOCTOU 安全的 `expected` 取值来源（见上一条测试
        新加的说明）：扫描时是 PREFLIGHTED，真正处理前已经正常推进到
        STAGE1_RUNNING（还在跑，非终态）——如果 `reap()` 错误地实时重读当前
        状态当 `expected`（而不是用扫描时的旧状态），会读到 STAGE1_RUNNING，
        而 `(STAGE1_RUNNING, TIMEOUT)` 本身合法，`transition()` 会真的把一个
        正常运行中的 run 转去 TIMEOUT——这正是原始设计缺陷（见模块 docstring）。
        用扫描时的旧状态 `PREFLIGHTED` 做 `expected`，CAS 因为对不上现在的
        真实状态而拒绝，才是这里应该发生的事。"""
        rid = _open(db, decision_id="BIGA-20260101-001")
        transition(rid, RunState.RECEIVED, RunState.PREFLIGHTED, path=db)
        stale_snapshot = [{"run_id": rid, "decision_id": "BIGA-20260101-001",
                           "state": RunState.PREFLIGHTED, "age_sec": 1300}]
        monkeypatch.setattr(reaper, "find_stale_runs", lambda *a, **k: stale_snapshot)
        transition(rid, RunState.PREFLIGHTED, RunState.SNAPSHOT_FROZEN, path=db)
        transition(rid, RunState.SNAPSHOT_FROZEN, RunState.STAGE1_RUNNING, path=db)
        results = reaper.reap(1200, dry_run=False, path=db)
        assert results[0]["action"] == "skipped-cas-conflict"
        assert run_events(rid, path=db)[-1]["to_state"] == RunState.STAGE1_RUNNING, \
            "还在正常推进的不该被 reaper 打断"


class TestCLI:

    def test_没有stale_run时打印提示_退出码0(self, db, capsys):
        assert reaper.main(["--threshold-sec", "1200"]) == 0
        assert "没有发现" in capsys.readouterr().out

    def test_默认dry_run_打印候选不真转移(self, db, monkeypatch, capsys):
        rid = _open(db, decision_id="BIGA-20260101-001")
        _age_past_threshold(monkeypatch, 1200)
        rc = reaper.main(["--threshold-sec", "1200"])
        assert rc == 0
        out = capsys.readouterr().out
        assert rid in out and "would-reap" in out and "dry-run" in out
        assert run_events(rid, path=db)[-1]["to_state"] == RunState.RECEIVED

    def test_apply真的转移(self, db, monkeypatch):
        rid = _open(db, decision_id="BIGA-20260101-001")
        _age_past_threshold(monkeypatch, 1200)
        assert reaper.main(["--threshold-sec", "1200", "--apply"]) == 0
        assert run_events(rid, path=db)[-1]["to_state"] == RunState.TIMEOUT
