"""`cn.market.limit_pool` 的降级源 —— 从全市场快照**自算**三个股池。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：涨跌停的判定（逐板块阈值、容差、停牌）、`row_fields` 的声明、
  以及**降级时 emotion 不许伪造派生值**
- **不覆盖**：取数分页（那复用 `sina_eod`，在它自己的测试里）、
  与主源的数值差（口径本来就不同，见适配器模块头）

🔴 这个 dataset 为什么非要一个非东财的备胎
------------------------------------------
2026-09-26 东财 `push2` 系四台主机同时 502，`cn.market.limit_pool` 是当时
**唯一没有备胎**的 dataset ⇒ emotion 整组 `UNKNOWN`，卡上少了一整个维度。

东财自家还有一条路（付费 AI 接口），但**同属一家** ——
厂商级故障会一起挂。真正的备胎必须换一家。
"""
from __future__ import annotations

import pytest

from easyup_biga.providers.eod_bar import EodBar
from easyup_biga.providers.http import SourceError
from easyup_biga.providers.sina_limit_pool import LIMIT_PCT, count_limit_pools


def _bar(code, market, *, prev, close, high=None, low=None, name="某某股份"):
    pct = (close - prev) / prev * 100 if prev else 0
    return EodBar(symbol=code, name=name, market_hint=market,
                  open=prev, high=high if high is not None else close,
                  low=low if low is not None else close,
                  close=close, prev_close=prev, volume="1", amount="1",
                  change_amount=close - prev, change_percent=f"{pct:.4f}")


def _count(bars):
    return count_limit_pools(bars, raw_text="{}", trade_date="20260924")


class Test逐板块阈值:
    """🔴 涨跌幅上限**按板块**定。用一个 10% 打天下会漏掉一大批。"""

    @pytest.mark.parametrize("code,market,pct", [
        ("600000", "sh", 0.10),   # 沪主板
        ("000001", "sz", 0.10),   # 深主板
        ("300750", "sz", 0.20),   # 创业板
        ("688981", "sh", 0.20),   # 科创板
        ("920748", "bj", 0.30),   # 北交所
    ])
    def test_各板块的涨停都能认出来(self, code, market, pct):
        bars = [_bar(code, market, prev=10.0, close=round(10.0 * (1 + pct), 2))]
        assert _count(bars)["limit_up"].total == 1, (code, pct)

    def test_科创板涨百分之十不算涨停(self):
        """⚠️ 反向的一半：用 10% 打天下会把科创板的 10% 误判成涨停。"""
        bars = [_bar("688981", "sh", prev=10.0, close=11.0)]
        assert _count(bars)["limit_up"].total == 0

    def test_北交所在册(self):
        assert LIMIT_PCT["BSE"] == 0.30


class Test容差:
    """🔴 阈值用 9.9 不用 10.0 —— 涨停价按昨收**四舍五入到分**算，
    实际涨幅经常不是整数。卡在 10.0 上会漏掉一大批。
    """

    def test_涨停价四舍五入导致涨幅不足十也算(self):
        # 昨收 8.13 ⇒ 涨停价 8.94（8.943 → 8.94）⇒ 涨幅 9.963%
        bars = [_bar("600000", "sh", prev=8.13, close=8.94)]
        assert _count(bars)["limit_up"].total == 1

    def test_差得太远仍然不算(self):
        bars = [_bar("600000", "sh", prev=10.0, close=10.95)]   # +9.5%
        assert _count(bars)["limit_up"].total == 0


class Test停牌不计入任何一档:
    """🔴 「没开盘」既不是涨停也不是跌停 —— 不能算成 0 涨幅。"""

    def test_没有收盘价的行被跳过(self):
        bars = [_bar("600000", "sh", prev=10.0, close=0.0)]
        pools = _count(bars)
        assert (pools["limit_up"].total, pools["limit_down"].total) == (0, 0)

    def test_认不出板块的行被跳过而不是默认十个点(self):
        """⚠️ 默认 10% 会把科创/创业的 20% 涨停漏掉，而且**不报错**。"""
        bars = [_bar("600000", "sh", prev=10.0, close=11.0),
                _bar("999999", None, prev=10.0, close=11.0)]
        assert _count(bars)["limit_up"].total == 1

    def test_一只都认不出要响亮失败(self):
        bars = [_bar("999999", None, prev=10.0, close=11.0)]
        with pytest.raises(SourceError, match="不是「今天没涨停」"):
            _count(bars)


class Test只给计数不给明细:
    """🔴 这是整个适配器最要紧的一条。

    第一版给了 `zbc`（炸板次数），判据是「收在涨停价但 `low < 涨停价`」——
    **那量的不是炸板，是「不是一字板」**。除了开盘即封死的票，
    几乎每只涨停股的最低价都低于涨停价。

    实测当场戳穿（20260924 同一天）：

        东财股池      未炸板 22 / 52 = 42%
        那一版        未炸板  5 / 54 =  9%    ← 差了四倍多

    「曾封板、后打开」需要分时/逐笔，单日 OHLC 里没有这个信息。

    > 它不会报错 —— 只会给出一个看起来精确的、错四倍的百分比。
    """

    def test_row_fields_是空集(self):
        bars = [_bar("600000", "sh", prev=10.0, close=11.0)]
        for pool in _count(bars).values():
            assert pool.row_fields == frozenset(), pool.pool

    def test_涨停行里不带lbc也不带zbc(self):
        bars = [_bar("600000", "sh", prev=10.0, close=11.0)]
        row = _count(bars)["limit_up"].rows[0]
        assert "lbc" not in row and "zbc" not in row, row

    def test_跌停只给计数而total仍然正确(self):
        """⚠️ `total` 不能写成 `len(rows)` —— 那会让「没留明细」
        悄悄变成 `total=0`，而 0 是一个**合法的家数**。
        """
        bars = [_bar("600000", "sh", prev=10.0, close=9.0),
                _bar("600001", "sh", prev=10.0, close=9.0)]
        dt = _count(bars)["limit_down"]
        assert (dt.total, dt.rows) == (2, [])


class Test炸板口径与主源不同:
    def test_摸到涨停未封住算炸板(self):
        bars = [_bar("600000", "sh", prev=10.0, close=10.5, high=11.0)]
        assert _count(bars)["broken_board"].total == 1

    def test_没摸到涨停不算(self):
        bars = [_bar("600000", "sh", prev=10.0, close=10.5, high=10.6)]
        assert _count(bars)["broken_board"].total == 0

    def test_收在涨停的不算炸板(self):
        """它进涨停池，不进炸板池 —— 两个池互斥。"""
        bars = [_bar("600000", "sh", prev=10.0, close=11.0, high=11.0)]
        pools = _count(bars)
        assert (pools["limit_up"].total, pools["broken_board"].total) == (1, 0)


def test_三个池共用同一份raw():
    """🔴 它们出自**同一次观测** —— raw 必须是那一份快照，
    不是算完的结果（「凑出来的溯源比没有溯源更糟」）。
    """
    bars = [_bar("600000", "sh", prev=10.0, close=11.0)]
    pools = _count(bars)
    assert {p.raw_text for p in pools.values()} == {"{}"}
    assert all(p.raw["derived_from"] == "sina_eod snapshot" for p in pools.values())


# ──────────────────────────────────────────────────────────────────────
# 降级接线 —— 判据打在**冻结之后库里有什么**
# ──────────────────────────────────────────────────────────────────────

def _client(tmp_path, monkeypatch, *, em_ok: bool):
    import easyup_biga.data.decision_client as dc
    from easyup_biga.persistence import init_schema, save_evidence_set

    pytest.importorskip("pyarrow")
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "biga.db"
    init_schema(db)
    save_evidence_set(evidence_set_id="es-lp", decision_id=None,
                      manifest={"kind": "t"}, path=db)

    def em(name, date, **kw):
        if not em_ok:
            raise SourceError("push2ex 502")
        from easyup_biga.providers.eastmoney import PoolResult
        return PoolResult(pool=name, requested_date=date, qdate=date,
                          total=2, rows=[{"lbc": 2, "zbc": 0}] * 2,
                          raw={}, raw_text="{}")

    monkeypatch.setattr(dc, "_fetch_pool", em)
    monkeypatch.setattr(dc, "_sina_all_pools", lambda d: _count([
        _bar("600000", "sh", prev=10.0, close=11.0),
        _bar("600001", "sh", prev=10.0, close=9.0),
    ]))
    return dc.DecisionDataClient(db_path=db, data_root="data"), db


def test_主源挂了由新浪自算供数且账本记下降级(tmp_path, monkeypatch):
    from easyup_biga.persistence import connect
    client, db = _client(tmp_path, monkeypatch, em_ok=False)
    assert client.freeze_required(
        "es-lp", ["cn.market.limit_pool"], trade_date="20260924").complete

    with connect(db, readonly=True) as conn:
        provider = conn.execute(
            "SELECT provider_id FROM dataset_partitions "
            "WHERE dataset_id='cn.market.limit_pool'").fetchone()["provider_id"]
        attempts = [(str(r["provider_id"]), str(r["provider_role"]), str(r["status"]))
                    for r in conn.execute(
                        "SELECT provider_id,provider_role,status FROM provider_attempts "
                        "ORDER BY attempt_no")]
    assert provider == "sina_limit_pool"
    assert attempts == [("eastmoney", "PRIMARY", "FAILED_RETRYABLE"),
                        ("sina_limit_pool", "FALLBACK", "SUCCEEDED")]


def test_降级读回来时带出供数方与字段声明(tmp_path, monkeypatch):
    """🔴 `row_fields` 必须**落库再读回来**。

    不落的话，读取方只能靠「rows 空不空」推断 ——
    而空是合法状态（真的 0 家）。
    """
    client, _ = _client(tmp_path, monkeypatch, em_ok=False)
    client.freeze_required("es-lp", ["cn.market.limit_pool"], trade_date="20260924")
    result, _raw_hash, served_by = client.read_pool("es-lp", "limit_up")
    assert served_by == "sina_limit_pool"
    assert result.row_fields == frozenset(), result.row_fields
    assert result.total == 1


def test_主源正常时字段声明照旧(tmp_path, monkeypatch):
    """⚠️ 反向的一半：不能把主源的明细也声明没了。"""
    client, _ = _client(tmp_path, monkeypatch, em_ok=True)
    client.freeze_required("es-lp", ["cn.market.limit_pool"], trade_date="20260924")
    result, _h, served_by = client.read_pool("es-lp", "limit_up")
    assert served_by == "eastmoney"
    assert result.row_fields == frozenset({"lbc", "zbc"})
