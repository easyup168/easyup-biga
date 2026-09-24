"""stale_run_reaper：扫非终态 run + 按 created_at 判过期 + CAS 安全收敛（外部评审 §14）。

探针：
  P1  只有非终态 + 超过阈值的 run 会被找到（`find_stale_runs`，纯读）。
  P2  `dry_run=True`（默认）不改任何状态；`--apply`/`dry_run=False` 才真的转移。
  P3  真转移会同时入队一条 `run_failed` 通知（`enqueue_run_failed` 幂等）。
  P4  TOCTOU：候选生成之后、真正转移之前状态变了（已经到终态 / 变成别的非终态）
      ⇒ 不误覆盖（CAS 拒绝）。

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

from _contract import RunState, new_run_context, now_cn  # noqa: E402
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

    def test_TOCTOU_扫描后已经自己到终态_不覆盖(self, db, monkeypatch):
        """🔴 P4：`find_stale_runs` 扫到的时候还是非终态，但在真正 `reap` 之前
        run 自己已经正常走到 COMPLETED 了——不能被"收成 TIMEOUT"覆盖掉一个已经
        成功的结果。这里用手工构造候选列表（绕过 `find_stale_runs` 的实时扫描）
        来模拟这个时间窗口。"""
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

    def test_TOCTOU_扫描后状态变成别的非终态_CAS拒绝不覆盖(self, db, monkeypatch):
        """扫描时是 PREFLIGHTED，真正处理前已经正常推进到 STAGE1_RUNNING（还在
        跑）——`transition()` 的 CAS 用扫描时的旧状态做 expected，会被拒绝。"""
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
