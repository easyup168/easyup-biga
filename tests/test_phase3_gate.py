"""P3-17 · Phase 3 发布闸门。

覆盖 / 不覆盖
-------------
- 覆盖：代码面与上线证据**分成两个结论**、发布标记在没过时拒绝落盘、
  红灯文案能自己说清「是写错了」还是「那一段还没做」
- **不覆盖**：`tools/verify/phase3_acceptance.py` 的三态退出码 ——
  那条在 `test_verify_tools.py` 的口径里（本文件只测库函数）

🔴 为什么专门测「两个结论分开」
------------------------------
外部实现把两类错误堆进一个 list，再用 `e.startswith("five consecutive")`
之类的前缀捞回来算 `code_ready`。那是把判据挂在**文案**上：改个措辞，
结论就翻面，而不会有任何测试红。这里的断言直接打在两个字段上。
"""
from __future__ import annotations

import json

import pytest

from easyup_biga.data.acceptance import DRILL_TYPES, record_acceptance_event
from easyup_biga.data.finalizer import (
    REQUIRED_DATASETS,
    RELEASE_TAG,
    _code_checks,
    evaluate_phase3_release,
    write_release_marker,
)

REPO = __import__("pathlib").Path(__file__).resolve().parents[1]


def _full_ledger(path, days):
    for day in days:
        record_acceptance_event(path, "EOD_COMPLETE", trade_date=day)
    for kind in DRILL_TYPES:
        record_acceptance_event(path, kind)


def test_P4G0后代码面没有里程碑缺口():
    checks, errors = _code_checks(REPO)
    assert not [e for e in errors if e.startswith("P3-")], errors
    assert any("P3-6 Specialist Provider 边界已收口" in c for c in checks)
    assert any("P3-7 AgentRegistry" in c for c in checks)

def test_里程碑标注覆盖到每一个必需dataset():
    """探针：往 REQUIRED_DATASETS 加一条却忘了标里程碑，这条会红。"""
    assert all(v and v.startswith("P3-") for v in REQUIRED_DATASETS.values())


def test_代码面与上线证据是两个独立结论(tmp_path):
    ledger = tmp_path / "acceptance.jsonl"
    status = evaluate_phase3_release(
        repo=REPO, acceptance_ledger=ledger, db_path=tmp_path / "nope.db")
    # P4-G0 后代码面已闭环；证据账本仍为空，因此 Live Gate 独立不过。
    assert status.code_ready is True
    assert status.live_ready is False
    assert status.release_ready is False
    # 🔴 两边的错误不许串门
    assert status.code_errors == ()
    assert not any(e.startswith("P3-") for e in status.live_errors)
    assert any("演练" in e or "交易日" in e for e in status.live_errors)


def test_代码面齐了但交易日连续性判不出依然不放行(tmp_path):
    """探针：把上线证据凑满，`release_ready` 仍必须是 False。

    这是这套闸门唯一真正的用途 —— **两把钥匙**。少测这条，
    「凑齐演练就能发版」的路就是通的。
    """
    ledger = tmp_path / "acceptance.jsonl"
    days = ["20260921", "20260922", "20260923", "20260924", "20260925"]
    _full_ledger(ledger, days)
    status = evaluate_phase3_release(
        repo=REPO, acceptance_ledger=ledger, db_path=tmp_path / "nope.db")
    assert status.live_ready is False      # 读不到日历 ⇒ 连续性判不出来（R-3）
    assert status.code_ready is True
    assert status.release_ready is False


def test_没过就拒绝落发布标记(tmp_path):
    ledger = tmp_path / "acceptance.jsonl"
    status = evaluate_phase3_release(
        repo=REPO, acceptance_ledger=ledger, db_path=tmp_path / "nope.db")
    marker = tmp_path / "release.json"
    with pytest.raises(RuntimeError, match="拒绝落发布标记"):
        write_release_marker(status, marker)
    assert not marker.exists(), "拒绝了却还是写了文件 —— 那比不拒绝更糟"


def test_两道闸门都过才写标记(tmp_path):
    """用一个**构造出来的**通过态证明写入分支本身是对的。

    ⚠️ 构造的是 `Phase3ReleaseStatus`，不是伪造证据 —— 被测的是
    `write_release_marker` 的分支，不是闸门的判定。
    """
    from dataclasses import replace

    from easyup_biga.data.acceptance import AcceptanceStatus

    ledger = tmp_path / "acceptance.jsonl"
    base = evaluate_phase3_release(
        repo=REPO, acceptance_ledger=ledger, db_path=tmp_path / "nope.db")
    ok = replace(
        base, code_ready=True, live_ready=True, release_ready=True,
        code_errors=(), live_errors=(),
        live=AcceptanceStatus(True, ("20260925",), {k: True for k in DRILL_TYPES}, ()),
    )
    marker = tmp_path / "release.json"
    write_release_marker(ok, marker)
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["tag"] == RELEASE_TAG
    assert payload["status"] == "READY"
    assert payload["drills"] == {k: True for k in DRILL_TYPES}


def test_provider模块的存在性是从注册表派生的(tmp_path, monkeypatch):
    """探针：注册一个指向空气的 provider，代码面必须红。

    🔴 外部实现用的是一张**硬编码的 7 条 provider→路径 map**：注册了但
    不在 map 里的 provider 一个都不查 —— 守卫查的地方和它声称守的地方
    不是同一处（L-13）。这条钉住「从 `ProviderDefinition.modules` 派生」。
    """
    import easyup_biga.data.finalizer as fin
    from easyup_biga.data.contracts import ProviderDefinition

    ghost = ProviderDefinition(
        provider_id="ghost", title="不存在的数据源", source_prefix="ghost",
        modules=("easyup_biga.providers.ghost",))
    monkeypatch.setattr(fin, "PROVIDER_REGISTRY", dict(fin.PROVIDER_REGISTRY, ghost=ghost))
    _checks, errors = fin._code_checks(REPO)
    assert any("ghost" in e and "不存在的模块" in e for e in errors), errors
