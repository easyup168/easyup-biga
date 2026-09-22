"""readback_check.py —— 毒行巡检的行为测试（设计文档 §6 批 A-I，P6）。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：巡检工具在「干净库」「手工造的毒行」两种库状态下的判定
- **不覆盖**：为什么会产生毒行、写入时该怎么拦（那是写边界重校验本身，
  见 `test_write_boundary.py`）—— 本文件只测「巡检能不能发现它」

探针
----
毒行只能在**测试库**上手工造：`agent_verdicts` / `decision_records` 都是
只追加表，写错的行删不掉。这里全程用 `tmp_path` + `BIGA_DB_PATH`，
不碰 `data/biga.db`。
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))
sys.path.insert(0, str(REPO / "tools" / "verify"))

from _contract import AgentVerdict, new_task_id, now_cn  # noqa: E402
from _store import connect, init_schema, save_verdict  # noqa: E402

import _verdict as _v  # noqa: E402
import readback_check as rc  # noqa: E402

TID = new_task_id(1, day="20260922")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "t.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    return p


def _legal_verdict(**kw) -> AgentVerdict:
    # contract-exempt: 构造真 dataclass 的 kwargs
    base = dict(
        task_id=TID, agent="market", status="completed", verdict="PASS",
        result={}, confidence=0.9, evidence=[], stance="放量上涨",
    )
    base.update(kw)
    return AgentVerdict(**base)


class TestCleanDbReportsZero:
    def test_干净库0条毒行(self, db):
        save_verdict(_legal_verdict(), path=db)
        assert rc.scan_verdicts(db) == []
        assert rc.scan_cards(db) == []
        assert rc.main([]) == _v.PASS


class TestPoisonVerdictDetected:
    """探针：绕开 `save_verdict` 直接裸 INSERT 一行非法 verdict，断言巡检报红。"""

    def test_手工插入的毒行会被抓到(self, db):
        legal = _legal_verdict().to_dict()
        legal["missing"] = [{"code": "market.turnover.unavailable", "detail": "手工造的毒行"}]
        # verdict 仍是 "PASS"：missing 非空 + PASS 违反铁律 1 —— 正规读取路径必炸
        blob = json.dumps(legal, ensure_ascii=False, sort_keys=True)
        with connect(db) as conn:
            conn.execute(
                """INSERT INTO agent_verdicts
                   (task_id, agent, verdict_json, content_sha256, created_at)
                   VALUES (?,?,?,?,?)""",
                (TID, "market", blob, "0" * 64, now_cn().isoformat()),
            )

        bad = rc.scan_verdicts(db)
        assert len(bad) == 1
        assert "ValueError" in bad[0][1] and "铁律 1" in bad[0][1]
        assert rc.scan_cards(db) == [], "没碰 decision_records，不该报它有毒行"
        assert rc.main([]) == _v.FAIL


class TestPoisonCardDetected:
    """同上，换成 `decision_records`：手工造一张「BUY 却带缺失项」的卡。"""

    def test_手工插入的毒卡会被抓到(self, db):
        v = _legal_verdict().to_dict()
        # status=BUY + missing 非空：违反铁律 2 —— 绕过契约层与写边界直接落库。
        # 这里恰恰不能构造真 DecisionCard 对象，否则 __post_init__ 会先一步拒绝它。
        # contract-exempt: 故意手搓一份非法的 DecisionCard 字典，模拟毒行
        card = {
            "decision_id": TID, "status": "BUY", "headline": "手工造的毒卡",
            "verdicts": [v],
            "missing": [{"code": "market.turnover.unavailable", "detail": "手工造的毒卡"}],
            "synthesis": "", "model_ref": "m",
            "generated_at": now_cn().isoformat(), "elapsed_ms": 0,
        }
        blob = json.dumps(card, ensure_ascii=False, sort_keys=True)
        with connect(db) as conn:
            conn.execute(
                """INSERT INTO decision_records
                   (decision_id, replay_of, status, headline, model_ref,
                    missing_count, card_json, generated_at, elapsed_ms, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (TID, None, "BUY", "手工造的毒卡", "m", 1, blob,
                 now_cn().isoformat(), 0, now_cn().isoformat()),
            )

        bad = rc.scan_cards(db)
        assert len(bad) == 1
        assert "ValueError" in bad[0][2] and "铁律 2" in bad[0][2]
        assert rc.scan_verdicts(db) == [], "没碰 agent_verdicts，不该报它有毒行"
        assert rc.main([]) == _v.FAIL
