"""通达信官网盘后包 —— `cn.equity.daily_bars` 的**历史补数**来源。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：按**指定交易日**下载官网增量包，解析成 `EodBar`
- **不覆盖**：交易日判定（调用方的事）、复权（这是不复权原始 OHLCV）

🔴 它为什么值得单独接一条路 —— 别人都没有的那个能力
--------------------------------------------------
新浪与东财的全市场端点都是**快照**：只能拿「此刻」。
于是「某天的定时器没跑成」= 那天永远补不回来。

这个源不一样 —— 它按日期下载：

| | 新浪 / 东财 | 本适配器 |
|---|---|---|
| 取数方式 | 快照，只能拿此刻 | 按**指定交易日** |
| 补历史 | ❌ | ✅ 实测取到 2023-01-03 |
| 含北交所 | ✅ | ✅（2022-05-06 起包里才有 bj 文件）|
| 风控面 | sina.com.cn / eastmoney.com | **tdx.com.cn**，第三个面 |
| 代价 | JSON | 二进制（`.cod` + `.md1`）|

⇒ 它同时是**第三个风控面**的备胎，和**唯一能补历史**的那条路。

⚠️ **非交易日返回 404，那是正确行为，不是故障。**
探活时用「今天」会在周末读成「源挂了」—— 本仓库的普查工具为此固定
用一个已知交易日（`tools/verify/source_survey.py` 里写着这条教训）。

出处
----
二进制布局的字段偏移是**文件格式的事实**，来自两处公开资料：
公开的 A 股数据源目录 `a-stock-data`（Apache-2.0）的 `tdx_daily_package()`
章节，它又引用 `jing2uo/tdx2db`（MIT）的 `tdx/merge.go`。
本文件是独立实现（校验策略、错误信息、中立行翻译都是本仓库的口径），
不包含对方源码。

分层（照 `sina_eod.py` / `szse.py` 的既定形状）
----------------------------------------------
* `parse_daily_package`：**不联网的纯函数**，能对着已存的 zip 字节重放
* `fetch_daily_package`：联网的薄函数，只负责下载
"""
from __future__ import annotations

import io
import math
import re
import struct
import zipfile
from typing import Any

from .eod_bar import EodBar, EodFetchResult
from .instrument_segments import is_a_share
from .http import SourceError

__all__ = [
    "TDX_PACKAGE_URL",
    "TDX_BJ_FIRST_DAY",
    "parse_daily_package",
    "fetch_daily_package",
]

TDX_PACKAGE_URL = "https://www.tdx.com.cn/products/data/data/g4day/{ymd}.zip"
_REFERER = "https://www.tdx.com.cn/"

#: 盘后包从这一天起才带北交所文件。早于它缺 bj 文件是正常的，
#: 晚于它缺就是**格式变了**，要响亮失败 —— 两者必须分开判。
TDX_BJ_FIRST_DAY = "20220506"

_COD_RECORD = 150
_MD1_BLOCK = 512
_MARKETS = ("sh", "sz", "bj")

#: 每个市场「有收盘价」的行数下限。
#:
#: 🔴 **逐市场核对，不是只看总数。** 只检查总行数会让沪深撑过门槛、
#:    而北交所**静默缺失** —— 那正是「少了一整个市场却不报错」的形状。
#: ⚠️ 这是**过滤后的 A 股个股**下限（实测 2026-09-24：沪 2320 / 深 2907 / 京 350），
#:    不是包里的总行数。取明显低于实测值的整数。
_MIN_PRICED = {"sh": 1_500, "sz": 2_000, "bj": 50}


def _market_rows(
    cod: bytes, md1: bytes, *, market: str, ymd: str
) -> list[EodBar]:
    if len(cod) % _COD_RECORD or len(md1) % _MD1_BLOCK:
        raise SourceError(
            f"tdx {ymd}/{market}: 代码表或行情块长度不是整块，文件可能被截断")
    if len(cod) // _COD_RECORD != len(md1) // _MD1_BLOCK:
        raise SourceError(
            f"tdx {ymd}/{market}: 代码表 {len(cod) // _COD_RECORD} 条、"
            f"行情块 {len(md1) // _MD1_BLOCK} 块，对不上")

    rows: list[EodBar] = []
    codes: set[str] = set()
    seqs: set[int] = set()
    for offset in range(0, len(cod), _COD_RECORD):
        record = cod[offset:offset + _COD_RECORD]
        # ⚠️ 用 `replace` 解码会把坏字节变成 '�' **放行**；
        #    所以解完必须再用正则确认它真是 6 位数字。
        code = record[0:6].rstrip(b"\x00 ").decode("ascii", "replace")
        if not re.fullmatch(r"[0-9]{6}", code):
            raise SourceError(
                f"tdx {ymd}/{market}: 代码表出现非 6 位数字代码 {code!r}，格式可能已变")
        seq = struct.unpack("<H", record[32:34])[0]
        if code in codes or seq in seqs:
            raise SourceError(
                f"tdx {ymd}/{market}: 代码表有重复的代码或行情块序号"
                f"（{code!r}, seq={seq}）")
        codes.add(code)
        seqs.add(seq)

        block = md1[seq * _MD1_BLOCK:(seq + 1) * _MD1_BLOCK]
        if len(block) != _MD1_BLOCK:
            raise SourceError(f"tdx {ymd}/{market}{code}: 行情块越界（seq={seq}）")
        prev_close = struct.unpack("<d", block[4:12])[0]
        open_, high, low, close = struct.unpack("<4d", block[12:44])
        amount = struct.unpack("<d", block[72:80])[0]
        # 🔴 NaN 能绕过 `close <= 0` 被当成有价记录计进下限 ⇒ 先判有限性。
        if not all(math.isfinite(value) for value in
                   (prev_close, open_, high, low, close, amount)):
            raise SourceError(
                f"tdx {ymd}/{market}{code}: 行情块出现非有限数值，文件可能已损坏")
        if close <= 0:
            # 通达信自编板块指数（880/881 等）当日无价、整块为 0，不是证券。
            continue
        volume = struct.unpack("<Q", block[56:64])[0]
        raw_name = record[40:72].split(b"\x00")[0]
        try:
            name = raw_name.decode("gbk").strip()
        except UnicodeDecodeError as exc:
            raise SourceError(
                f"tdx {ymd}/{market}{code}: 名称不是 GBK，文件可能已损坏") from exc
        if not name:
            raise SourceError(
                f"tdx {ymd}/{market}{code}: 有价格却没有名称，文件可能已损坏")

        # 🔴 **带上市场**，不要按代码拍平。
        #    实测 2026-09-24 的包里 840 个代码在两个市场都存在 ——
        #    `000001` 在 sh 是上证指数、在 sz 是平安银行。
        #    拍平会让上证指数的 3888 点变成平安银行的「股价」，而且不报错。
        if not is_a_share(code, market=market):
            # 包里还有基金 / 债券 / B 股 / 通达信自编板块指数（`880001` 等），
            # 它们不属于 `cn.equity.daily_bars`。
            continue
        rows.append(EodBar(
            symbol=code, name=name, market_hint=market,
            open=round(open_, 4), high=round(high, 4), low=round(low, 4),
            close=round(close, 4), prev_close=round(prev_close, 4),
            # 个股 volume 单位是「股」、amount 是「元」。
            volume=volume, amount=round(amount, 2),
            change_amount=(round(close - prev_close, 4)
                           if prev_close > 0 else None),
            change_percent=(round((close - prev_close) / prev_close * 100, 4)
                            if prev_close > 0 else None),
        ))

    # ⚠️ 下限是按**过滤后的 A 股个股**算的，不是包里的总行数 ——
    #    只看总数会让「少了一整个市场」被基金债券的行数盖过去。
    if len(rows) < _MIN_PRICED[market]:
        raise SourceError(
            f"tdx {ymd}/{market}: 只有 {len(rows)} 条有价记录"
            f"（下限 {_MIN_PRICED[market]}），文件可能残缺或格式已变")
    return rows


def parse_daily_package(content: bytes, trade_date: str) -> EodFetchResult:
    """解析一个盘后包。**不联网**，能对着已存的 zip 字节重放。

    🔴 `effective_trade_date` 在这里被**显式声明** —— 这正是这条路与快照型
    端点的根本差别：它知道自己是哪一天的数据，所以调用方可以补历史。
    """
    if len(trade_date) != 8 or not trade_date.isdigit():
        raise ValueError(f"trade_date 必须是 YYYYMMDD，收到 {trade_date!r}")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise SourceError(
            f"tdx {trade_date}: 下载到的不是 zip —— "
            "非交易日或当天包尚未发布时官网返回 404，不会返回坏 zip；"
            "拿到坏 zip 说明上游行为变了") from exc

    names = set(archive.namelist())
    suffix = trade_date[2:]
    rows: list[EodBar] = []
    for market in _MARKETS:
        cod_name, md1_name = f"{market}{suffix}.cod", f"{market}{suffix}.md1"
        if (market == "bj" and trade_date < TDX_BJ_FIRST_DAY
                and cod_name not in names and md1_name not in names):
            # 这天之前的包还没有北交所文件 —— 正常，不是缺失。
            continue
        if cod_name not in names or md1_name not in names:
            raise SourceError(
                f"tdx {trade_date}: 缺少 {cod_name}/{md1_name}，格式可能已变")
        rows.extend(_market_rows(
            archive.read(cod_name), archive.read(md1_name),
            market=market, ymd=trade_date))

    if not rows:
        raise SourceError(f"tdx {trade_date}: 解析结果为空")

    return EodFetchResult(
        rows=tuple(rows),
        # ⚠️ raw 是**这个包的字节本身**，不是解析结果 —— 它才是我们真收到的东西。
        #    但二进制进不了 JSON 文本字段 ⇒ 这里只放可复现的取回指纹，
        #    字节本身由调用方按 RawArtifact 落盘。
        raw_text=f"tdx:g4day/{trade_date}.zip sha-len={len(content)}",
        retrieved_at="",
        # 这个源**不自报总数** ⇒ 「自报 vs 实收」那道交叉校验在这条路上失效。
        declared_total=len(rows),
        # 🔴 显式声明业务日 —— 这就是能补历史的原因。
        effective_trade_date=trade_date,
        provider_id="tdx",
    )


def fetch_daily_package(trade_date: str, *, timeout: int = 60) -> EodFetchResult:
    """下载并解析某个交易日的盘后包。

    ⚠️ 非交易日、或当日包尚未发布（通常收盘后数小时）时官网返回 404 ⇒
    抛 `SourceError`，**不返回空表**。「那天没有数据」和「那天全市场停牌」
    是两件事，返回空表会把前者伪装成后者。
    """
    import urllib.error
    import urllib.request

    from easyup_biga.domain import now_cn

    from .http import UA, throttle

    url = TDX_PACKAGE_URL.format(ymd=trade_date)
    throttle("tdx")
    request = urllib.request.Request(
        url, headers={"User-Agent": UA, "Referer": _REFERER})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SourceError(
                f"tdx {trade_date}: 官网没有这一天的包（HTTP 404）—— "
                "非交易日，或当日包尚未发布（通常收盘后数小时）") from exc
        raise SourceError(f"tdx {trade_date}: HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001
        raise SourceError(f"tdx {trade_date}: {type(exc).__name__}: {exc}") from exc

    result = parse_daily_package(content, trade_date)
    return EodFetchResult(
        rows=result.rows, raw_text=result.raw_text,
        retrieved_at=now_cn().isoformat(),
        declared_total=result.declared_total,
        effective_trade_date=result.effective_trade_date,
        provider_id=result.provider_id,
    )
