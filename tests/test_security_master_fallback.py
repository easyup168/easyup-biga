"""`cn.security_master` 的降级链 —— 2026-09-26 被一次真故障逼出来的。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：新浪备用适配器的解析；主源失败时降级链的**可观察后果**
  （溯源写谁、`provider_attempts` 里留下什么）
- **不覆盖**：真实网络。解析层用离线 fixture，取数层用注入桩（L-12：
  只桩最外层那次取数）
"""
from __future__ import annotations

import json

import pytest

from easyup_biga.data.contracts import DatasetStatus
from easyup_biga.data.datasets.security_master import (
    FALLBACK_PROVIDER_ID,
    PROVIDER_ID,
    SecurityMasterQualityPolicy,
    SecurityMasterService,
    _exchange_and_board,
    normalize_security_master_rows,
)
from easyup_biga.persistence import connect, init_schema
from easyup_biga.providers.http import SourceError
from easyup_biga.providers.security_listing import SecurityListing
from easyup_biga.providers.sina_security_master import (
    parse_security_master_page,
    parse_security_master_pages,
)


def _sina_page(rows):
    return json.dumps([
        {"symbol": s, "code": c, "name": n, "trade": "10.00", "volume": 1}
        for s, c, n in rows
    ], ensure_ascii=False)


# ── 代码前缀：第一次拿真实名单就撞上的那个洞 ────────────────────────────
def test_创业板302前缀必须认得():
    """🔴 `302132 中航成飞` —— 2026-09-26 第一次拿真实全市场名单跑时撞上的。

    创业板早期只有 300/301，后来多出 302 段。在补上它之前，
    这个数据集的**第一次真实同步必然整体失败**（fail-closed 是对的，
    但没人跑到过这一步，所以谁也不知道）。主源走这条路也一样。
    """
    from easyup_biga.data.datasets.security_master import Exchange, SecurityBoard

    assert _exchange_and_board("302132", None) == (Exchange.SZSE, SecurityBoard.CHINEXT)


def test_不认识的前缀一次报全而不是撞上第一个就死():
    """死在第一个的代价不是「慢」：一次只暴露一个新代码段，
    要重跑 N 次、每次等一分多钟才知道全貌。
    ⚠️ fail-closed 本身不放松 —— 有一个不认识就整体不发布。
    """
    rows = [
        SecurityListing(symbol="600000", name="浦发银行"),
        SecurityListing(symbol="900001", name="怪东西甲"),
        SecurityListing(symbol="950002", name="怪东西乙"),
    ]
    with pytest.raises(ValueError) as exc:
        normalize_security_master_rows(
            rows, provider_id="p", raw_artifact_id="raw-x",
            retrieved_at="2026-09-26T18:00:00+08:00")
    message = str(exc.value)
    assert "900001" in message and "950002" in message, message
    assert "2 只" in message


# ── 新浪适配器的解析层 ─────────────────────────────────────────────────
def test_新浪名单解析成中立行():
    page = _sina_page([("sh600000", "600000", "浦发银行"),
                       ("bj920000", "920000", "安徽凤凰")])
    rows = parse_security_master_page(page, page_no=1)
    assert [r.symbol for r in rows] == ["600000", "920000"]
    assert [r.market_hint for r in rows] == ["sh", "bj"]
    assert all(r.list_date is None for r in rows), "这个源不给上市日，不许反推"


def test_新浪重复代码要响亮失败():
    page = _sina_page([("sh600000", "600000", "甲"), ("sh600000", "600000", "甲")])
    with pytest.raises(SourceError, match="duplicate"):
        parse_security_master_pages([page])


def test_新浪空名单不许当成功():
    with pytest.raises(SourceError, match="no securities"):
        parse_security_master_pages(["[]"])


def test_新浪不报总数这件事写在契约里():
    """⚠️ `declared_total != len(records)` 那道交叉校验在降级时**天然失效**
    —— 因为这个源不自报总数，两者恒等。写下来免得有人以为它还在保护什么。
    """
    result = parse_security_master_pages([_sina_page([("sh600000", "600000", "甲")])])
    assert result.total == len(result.rows)
    assert result.provider_id == "sina"
    assert result.source == "sina:security_master/current"


# ── 降级链的可观察后果 ─────────────────────────────────────────────────
@pytest.fixture()
def degraded(tmp_path, monkeypatch):
    """主源抛 SourceError、备用源正常 —— 只桩最外层那两次取数。"""
    import easyup_biga.data.datasets.security_master as sm

    db = tmp_path / "biga.db"
    init_schema(db)
    monkeypatch.setattr(sm, "fetch_security_master",
                        lambda: (_ for _ in ()).throw(SourceError("主源 502")))
    monkeypatch.setattr(
        sm, "sina_fetch_security_master",
        lambda: parse_security_master_pages(
            [_sina_page([("sh600000", "600000", "浦发银行"),
                         ("sz000001", "000001", "平安银行"),
                         ("bj920000", "920000", "安徽凤凰")])],
            retrieved_at=sm.now_cn().isoformat()))
    return db


#: ⚠️ 放宽行数下限：本文件测的是**降级接线**，不是「全市场至少多少只」。
#:    用真实的 1000 下限会让 3 行夹具判 QUARANTINED，红在一个无关的判据上。
def _service(db):
    return SecurityMasterService(
        path=db, quality_policy=SecurityMasterQualityPolicy(minimum_rows=1))


def test_主源挂了由备用源供数(degraded):
    result = _service(degraded).sync()
    assert result.status is DatasetStatus.COMPLETE
    assert result.row_count == 3


def test_溯源写的是实际供数方而不是primary(degraded):
    """🔴 写 primary 会让**降级过的那天在库里看起来像正常的一天**：
    数据来自备用源，`provider_id` 却指着主源。
    """
    _service(degraded).sync()
    with connect(degraded, readonly=True) as conn:
        provider = conn.execute(
            "SELECT provider_id FROM dataset_partitions WHERE dataset_id='cn.security_master'"
        ).fetchone()["provider_id"]
        facts = {str(r["provider_id"]) for r in
                 conn.execute("SELECT provider_id FROM fact_security_master")}
    assert provider == FALLBACK_PROVIDER_ID
    assert facts == {FALLBACK_PROVIDER_ID}


def test_主源失败那一半必须落进provider_attempts(degraded):
    """🔴 不落表 ⇒ `drills.provider_fallback_drill()` 结构上永远取不到证据。

    它要求「PRIMARY 失败过 **且** FALLBACK 在同一次 run 里成功」，
    而失败那一半从不落表的话，这项演练只能靠手工记账 —— 那记的是意图。
    """
    from easyup_biga.data.drills import provider_fallback_drill

    result = _service(degraded).sync()
    with connect(degraded, readonly=True) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT provider_id,provider_role,status FROM provider_attempts "
            "WHERE data_run_id=? ORDER BY attempt_no", (result.data_run_id,))]
    assert [(r["provider_id"], r["provider_role"], r["status"]) for r in rows] == [
        (PROVIDER_ID, "PRIMARY", "FAILED_RETRYABLE"),
        (FALLBACK_PROVIDER_ID, "FALLBACK", "SUCCEEDED"),
    ]
    assert provider_fallback_drill(result.data_run_id, path=degraded).passed


def test_没有降级时仍然只记一条primary成功(tmp_path, monkeypatch):
    """对照组：单源数据集的既有行为不能被上面那条改掉。"""
    import easyup_biga.data.datasets.security_master as sm

    db = tmp_path / "biga.db"
    init_schema(db)
    monkeypatch.setattr(
        sm, "fetch_security_master",
        lambda: parse_security_master_pages(
            [_sina_page([("sh600000", "600000", "浦发银行")])],
            retrieved_at=sm.now_cn().isoformat()))
    result = _service(db).sync()
    with connect(db, readonly=True) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT provider_role,status FROM provider_attempts WHERE data_run_id=?",
            (result.data_run_id,))]
    assert [(r["provider_role"], r["status"]) for r in rows] == [("PRIMARY", "SUCCEEDED")]


def test_备用源缺上市日会体现在质量指标上(degraded):
    """缺一个可空字段是**看得见的缺**，不是隐藏的缺。"""
    result = _service(degraded).sync()
    with connect(degraded, readonly=True) as conn:
        metrics = json.loads(conn.execute(
            "SELECT metrics_json FROM quality_reports WHERE quality_report_id=?",
            (result.quality_report_id,)).fetchone()["metrics_json"])
    assert metrics["missing_list_date_count"] == 3
