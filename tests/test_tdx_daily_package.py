"""通达信官网盘后包适配器 —— 二进制解析与 A 股过滤。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：`.cod` / `.md1` 的偏移解析、各条 fail-closed 分支、A 股过滤、
  以及「按市场分文件 ⇒ 同一代码在两市是两只标的」这条
- **不覆盖**：真实下载。

⚠️ 夹具是**合成**的，不是仓库里塞一个 600KB 的真包。
   那样做的话，仓库要带一份二进制资产，而它只能证明「这一天能解开」。

🔴 解析器对**真包**的验证另有其事，且已经做过（2026-09-26）：
   用 2026-09-24 的真包与新浪同日快照**逐只对拍 5556 只**，
   收盘价与开盘价**零处不一致**。合成夹具守的是解析**逻辑**
   （偏移、校验分支、过滤），真包对拍守的是**我对格式的理解**。
   两者都要，谁也不能代替谁。
"""
from __future__ import annotations

import io
import struct
import zipfile

import pytest

from easyup_biga.providers.http import SourceError
from easyup_biga.providers.tdx_daily_package import (
    TDX_BJ_FIRST_DAY,
    parse_daily_package,
)

_COD, _MD1 = 150, 512


def _record(code: str, name: str, seq: int) -> bytes:
    buf = bytearray(_COD)
    buf[0:6] = code.encode("ascii")
    buf[32:34] = struct.pack("<H", seq)
    raw = name.encode("gbk")
    buf[40:40 + len(raw)] = raw
    return bytes(buf)


def _block(prev_close: float, o: float, h: float, low: float, c: float,
           volume: int, amount: float) -> bytes:
    buf = bytearray(_MD1)
    buf[4:12] = struct.pack("<d", prev_close)
    buf[12:44] = struct.pack("<4d", o, h, low, c)
    buf[56:64] = struct.pack("<Q", volume)
    buf[72:80] = struct.pack("<d", amount)
    return bytes(buf)


#: 各市场要造够多少只 A 股，才过得了生产阈值。
_NEEDED = {"sh": 1_600, "sz": 2_100, "bj": 60}
#: ⚠️ 两位前缀 + 4 位序号 = 恰好 6 位。
#:    第一版写了 3 位前缀 + `{index:03d}`，index 到 1000 就变成 7 位，
#:    把 150 字节的记录撑成 151 —— 于是「代码表与行情块对不上」，
#:    报的是**解析器的**错，而错在夹具。
_PREFIX = {"sh": "60", "sz": "00", "bj": "92"}


def _package(ymd: str = "20260924", *, extra=None, skip_market=None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for market, count in _NEEDED.items():
            if market == skip_market:
                continue
            records, blocks = [], []
            for index in range(count):
                code = f"{_PREFIX[market]}{index:04d}"
                records.append(_record(code, f"票{index}", len(blocks)))
                blocks.append(_block(10.0, 10.1, 10.5, 9.8, 10.3,
                                     100_000 + index, 1_030_000.0))
            for code, name, blk in (extra or {}).get(market, ()):
                records.append(_record(code, name, len(blocks)))
                blocks.append(blk)
            archive.writestr(f"{market}{ymd[2:]}.cod", b"".join(records))
            archive.writestr(f"{market}{ymd[2:]}.md1", b"".join(blocks))
    return out.getvalue()


# ── 主路径 ─────────────────────────────────────────────────────────────────
def test_解析出各市场的A股并带上市场():
    result = parse_daily_package(_package(), "20260924")
    markets = {str(row.market_hint) for row in result.rows}
    assert markets == {"sh", "sz", "bj"}
    assert len(result.rows) == sum(_NEEDED.values())


def test_显式声明业务日_这正是能补历史的原因():
    """🔴 快照型端点（新浪/东财）给不出业务日 ⇒ 只能发当天。
    这个源知道自己是哪一天的 ⇒ 调用方可以补历史。
    """
    result = parse_daily_package(_package("20230103"), "20230103")
    assert result.effective_trade_date == "20230103"
    assert result.provider_id == "tdx"


def test_这个源不自报总数():
    """⚠️ 「自报总数 vs 实收」那道交叉校验在这条路上失效。"""
    result = parse_daily_package(_package(), "20260924")
    assert result.declared_total == len(result.rows)


# ── A 股过滤 + 跨市场同码 ──────────────────────────────────────────────────
def test_同一代码在两个市场是两只标的():
    """🔴 实测：2026-09-24 的真包里 **840 个代码在两个市场都存在**。

    最有名的是 `000001` —— 沪市是上证指数、深市是平安银行。
    按代码拍平会让上证指数的点位变成平安银行的「股价」，**而且不报错**。
    ⇒ 沪市那条必须被判成「不是 A 股个股」而丢掉。
    """
    pkg = _package(extra={"sh": [("000001", "上证指数",
                                  _block(3936.5, 3925.3, 3930.5, 3888.4, 3888.4,
                                         438_530_412, 7.8e11))]})
    rows = parse_daily_package(pkg, "20260924").rows
    sh_000001 = [r for r in rows if str(r.symbol) == "000001"
                 and str(r.market_hint) == "sh"]
    assert sh_000001 == [], "沪市 000001 是上证指数，不该进 A 股日线"


def test_通达信自编板块指数不进结果():
    """`880001 总市值` 这类在真包里有 1120 条，**全在 sh 文件里**。
    它们曾被本仓库的号段表误当成北交所（`88` 曾在 BSE 那一组里）。
    """
    pkg = _package(extra={"sh": [("880001", "总市值",
                                  _block(1.0, 1.0, 1.0, 1.0, 1.0, 1, 1.0))]})
    rows = parse_daily_package(pkg, "20260924").rows
    assert not [r for r in rows if str(r.symbol) == "880001"]


# ── fail closed 分支 ───────────────────────────────────────────────────────
def test_缺市场文件要响亮失败():
    with pytest.raises(SourceError, match="缺少"):
        parse_daily_package(_package(skip_market="sz"), "20260924")


def test_北交所文件在启用日之前缺失是正常的():
    """⚠️ 「那天还没有北交所」和「格式变了」必须分开判。"""
    early = (int(TDX_BJ_FIRST_DAY) - 1)
    ymd = str(early)
    pkg = _package(ymd, skip_market="bj")
    result = parse_daily_package(pkg, ymd)
    assert {str(r.market_hint) for r in result.rows} == {"sh", "sz"}


def test_有价记录太少要响亮失败():
    """🔴 逐市场核对，不是只看总数 ——
    只看总数会让沪深撑过门槛、而北交所**静默缺失**。
    """
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for market in ("sh", "sz", "bj"):
            count = 5 if market == "bj" else _NEEDED[market]
            records, blocks = [], []
            for index in range(count):
                records.append(_record(f"{_PREFIX[market]}{index:04d}",
                                       f"票{index}", index))
                blocks.append(_block(10.0, 10.1, 10.5, 9.8, 10.3, 1, 1.0))
            archive.writestr(f"sh260924.cod" if market == "sh"
                             else f"{market}260924.cod", b"".join(records))
            archive.writestr(f"{market}260924.md1", b"".join(blocks))
    with pytest.raises(SourceError, match="有价记录"):
        parse_daily_package(out.getvalue(), "20260924")


def test_非有限数值要响亮失败():
    """🔴 NaN 能绕过 `close <= 0` 被当成有价记录计进下限 ⇒ 先判有限性。"""
    pkg = _package(extra={"sz": [("003999", "坏票",
                                  _block(float("nan"), 1.0, 1.0, 1.0, 1.0, 1, 1.0))]})
    with pytest.raises(SourceError, match="非有限数值"):
        parse_daily_package(pkg, "20260924")


def test_不是zip要说清可能发生了什么():
    with pytest.raises(SourceError, match="不是 zip"):
        parse_daily_package(b"<html>404</html>", "20260924")


def test_日期格式不对是调用方的错不是源的错():
    with pytest.raises(ValueError, match="YYYYMMDD"):
        parse_daily_package(_package(), "2026-09-24")


# ── 补历史的 point-in-time 边界 ────────────────────────────────────────────
def test_补历史时universe按交易日解析而不是按取回时刻(tmp_path, monkeypatch):
    """🔴 这两者在当日跑时几乎一样，**补历史时天差地别**。

    盘后包能取到 2023-01-03 的日线，而那次取回发生在今天 ⇒
    用 `retrieved_at` 解析 universe 会把 2023 年的行情配上**今天的**在册名单。
    那是 point-in-time 的静默违反：不会报错，只会让回测里出现一批
    当时还没上市的票（幸存者偏差，而幸存者偏差只会让回测**好看**）。

    ⚠️ 判据是「**拿哪个时刻去查**」，不是「有没有报错」——
    所以这里把 `security_universe_at` 换成记录参数的桩。
    """
    import easyup_biga.data.eod_pipeline as pipeline
    from easyup_biga.domain import now_cn

    seen: list[str] = []

    def _spy(cutoff, **kwargs):
        seen.append(cutoff)
        return []

    monkeypatch.setattr(pipeline, "security_universe_at", _spy)
    monkeypatch.setattr(pipeline, "is_trading_day", lambda *a, **k: True)

    def _fetch():
        result = parse_daily_package(_package("20230103"), "20230103")
        return type(result)(
            rows=result.rows, raw_text=result.raw_text,
            retrieved_at=now_cn().isoformat(),
            declared_total=result.declared_total,
            effective_trade_date="20230103", provider_id="tdx")

    with pytest.raises(RuntimeError, match="Security Master"):
        pipeline.run_eod_bundle("20230103", db_path=tmp_path / "x.db",
                                data_root=str(tmp_path), fetcher=_fetch)

    assert seen == ["2023-01-03T23:59:59+08:00"], (
        f"universe 用了 {seen!r} 去查 —— 补历史必须按交易日，不是取回时刻")


def test_快照型provider不许服务历史日(monkeypatch):
    """🔴 新浪与东财的全市场端点给的都是「此刻」，它们**根本无法**回答
    「2023-01-03 收盘是多少」。

    让它们参与补历史，结果要么被日期校验拦下（好），
    要么在某个没想到的路径上把今天的价当成那天的（灾难）。

    ⚠️ 退出方式是抛 `SourceError` 而不是从链里删掉 —— 这样它会落进
    `provider_attempts`，库里能查到「那天为什么没走主源」。
    """
    from easyup_biga.data.datasets.eod_daily_bars import _snapshot_only_today

    fetch = _snapshot_only_today("sina_eod", lambda: "不该被调用", "20230103")
    with pytest.raises(SourceError, match="只能取「此刻」"):
        fetch()
