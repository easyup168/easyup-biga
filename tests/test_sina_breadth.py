"""`cn.market.breadth` 的降级源 —— 从全市场快照**自算**家数。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：计数规则（尤其是「平盘」与「停牌」的分界）、口径差异、降级接线
- **不覆盖**：取数分页（那复用 `sina_eod`，在它自己的测试里）
"""
from __future__ import annotations

import pytest

from easyup_biga.providers.eod_bar import EodBar
from easyup_biga.providers.http import SourceError
from easyup_biga.providers.sina_breadth import count_breadth


def _bar(code, market, pct, close=10.0):
    return EodBar(symbol=code, market_hint=market, close=close, change_percent=pct)


def test_按市场分桶并合计():
    rows = [_bar("600000", "sh", "1.2"), _bar("600001", "sh", "-0.5"),
            _bar("000001", "sz", "0.000"), _bar("920000", "bj", "3.0")]
    result = count_breadth(rows, raw_text="{}")
    assert (result.advance, result.decline, result.flat) == (2, 1, 1)
    assert [m["code"] for m in result.per_market] == ["sh", "sz", "bj"]


def test_涨跌幅为零是平盘不是缺失():
    """🔴 我自己埋过这个 bug：把 `"0.000"` 写进了统一的空值表，
    于是**平盘家数恒为 0**。

    实测抓到：全市场首页 2000 只里有 58 只 `changepercent == "0.000"`，
    它们有成交量、有真实价格，是货真价实的平盘。

    > 一个恒为 0 的计数不会报错，只会让人以为那天市场没有平盘。

    价格和涨跌幅的「0」含义**相反**：开高低收为 0 是没成交，涨跌幅为 0 是平盘。
    """
    rows = [_bar(f"60{i:04d}", "sh", "0.000") for i in range(3)]
    assert count_breadth(rows, raw_text="{}").flat == 3


def test_停牌按有没有价判而不是按涨跌幅():
    """🔴 停牌票的涨跌幅可能是 `0.000`，按涨跌幅判会把它算成**平盘** ——
    于是停牌多的日子看起来像市场很平静。那正是 R-3 的「算不出来却给个值」。
    """
    rows = [_bar("600000", "sh", "1.0"),
            _bar("600001", "sh", "0.000", close=None)]   # 停牌
    result = count_breadth(rows, raw_text="{}")
    assert (result.advance, result.decline, result.flat) == (1, 0, 0)


def test_没有市场标识要响亮失败():
    """家数必须能按市场拆开，否则与主源的 `per_market` 对不上。"""
    with pytest.raises(SourceError, match="没有可识别的市场标识"):
        count_breadth([_bar("600000", None, "1.0")], raw_text="{}")


def test_整个快照没有涨跌幅是字段变了不是全市场停牌():
    rows = [_bar("600000", "sh", None), _bar("600001", "sh", None)]
    with pytest.raises(SourceError, match="更可能是字段变了"):
        count_breadth(rows, raw_text="{}")


def test_raw是依据的那份快照而不是算完的结果():
    """🔴 存结果当 raw = 自己证明自己（本仓库 v0.9.2 刚修掉一批）。"""
    snapshot = '[{"symbol":"sh600000"}]'
    result = count_breadth([_bar("600000", "sh", "1.0")], raw_text=snapshot)
    assert result.raw_text == snapshot


# ── 降级接线 ───────────────────────────────────────────────────────────────
def test_breadth有了真跑通过的备用源():
    from easyup_biga.data.failover import provider_chain

    assert [p for p, _ in provider_chain("cn.market.breadth")] == [
        "eastmoney", "sina_breadth"]


def test_整条链失败仍然是可降级的失败():
    """🔴 「每一个 provider 都失败了」本来就是一种**取数失败**。

    `ProviderChainExhausted` 的基类原来是 `RuntimeError`，而
    `freeze_required` 只接 `(SourceError, ValueError)` 当可降级失败。
    于是给 breadth 配上备胎的那一刻，「两个源都挂」从「这条数据缺失」
    变成了**「整张卡出不来」** —— **加备胎反而让系统更脆**。

    ⚠️ 下一个给别的 dataset 配备胎的人会走到同一个路口：
    **降级链的终点必须仍然是一次可降级的失败。**
    """
    from easyup_biga.data.failover import ProviderChainExhausted

    assert issubclass(ProviderChainExhausted, SourceError)


def test_主源挂了由备用源供数且溯源写实际供数方(tmp_path, monkeypatch):
    import easyup_biga.data.decision_client as dc
    from easyup_biga.data.contracts import DatasetStatus
    from easyup_biga.persistence import connect, init_schema, save_evidence_set

    pytest.importorskip("pyarrow")
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "biga.db"
    init_schema(db)
    save_evidence_set(evidence_set_id="es-bd", decision_id=None,
                      manifest={"kind": "t"}, path=db)
    monkeypatch.setattr(dc, "_fetch_breadth",
                        lambda: (_ for _ in ()).throw(SourceError("主源 502")))
    monkeypatch.setattr(
        dc, "_sina_fetch_breadth",
        lambda: count_breadth([_bar("600000", "sh", "1.0"),
                               _bar("000001", "sz", "-1.0")], raw_text="[]"))
    client = dc.DecisionDataClient(db_path=db, data_root="data")
    assert client.freeze_required(
        "es-bd", ["cn.market.breadth"], trade_date="20260926").complete

    with connect(db, readonly=True) as conn:
        provider = conn.execute(
            "SELECT provider_id FROM dataset_partitions "
            "WHERE dataset_id='cn.market.breadth'").fetchone()["provider_id"]
        attempts = [(str(r["provider_id"]), str(r["provider_role"]), str(r["status"]))
                    for r in conn.execute(
                        "SELECT provider_id,provider_role,status FROM provider_attempts "
                        "ORDER BY attempt_no")]
    assert provider == "sina_breadth"
    assert attempts == [("eastmoney", "PRIMARY", "FAILED_RETRYABLE"),
                        ("sina_breadth", "FALLBACK", "SUCCEEDED")]
    _ = DatasetStatus
