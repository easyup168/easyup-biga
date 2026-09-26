#!/usr/bin/env python3
"""数据源普查 —— 对候选端点逐个探活，产出一张可复跑的可用性表。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：**这台机器、此刻**能不能取到各候选源，以及它给回来的形状对不对
- **不覆盖**：数据对不对（那要交叉校验）、长期稳定性（那要连续观测）

🔴 为什么要有这个工具，而不是每次手敲 curl
------------------------------------------
2026-09-26 换源时连着踩了两次「看起来可靠的结论其实没验过」：

1. 按「字段更齐」选了东财 `datacenter-web` 当 security_master 备胎，
   探活才发现它是**机构信息表**（含早已退市的），口径根本不对
2. 按「新浪给的是手」写了 `×100`，实测单位其实是**股** ——
   那会让全市场成交量大 100 倍，而且**不报错**

⇒ 换源的依据必须是**这台机器上此刻跑出来的结果**，不是目录里的描述。

⚠️ 一次失败什么都证明不了
-------------------------
同一域名下多个端点、以及不同域名的对照组，一起跑才分得出
「我被限流了」和「那一组挂了」—— 这两件事下一步完全相反
（前者该等，后者该走降级链）。所以本工具**总是整批跑**，不支持只探一个。

🔴 串行 + 限流。并发零间隔是最快把自己封掉的走法，而一旦被封，
   结论就再也复验不了（2026-09-24 实测踩过）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(pathlib.Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _verdict as _v  # noqa: E402

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
#: 同一域名两次请求之间至少隔这么久。
_GAP_SEC = 1.5


@dataclass(frozen=True, slots=True)
class Probe:
    """一个候选端点。

    `serves` 写的是**本仓库哪个 dataset 可能用它** —— 不是「它能提供什么」。
    这个方向是故意的：一个没有消费方的源不该出现在这张表里
    （那是 L-1「零消费方」的形状）。
    """
    key: str
    layer: str
    source: str
    serves: str
    url: str
    referer: str
    #: 形状判据。拿到正文后回答「这确实是我要的东西吗」。
    #: 🔴 只判 HTTP 200 是不够的：限流页、空壳 JSON、登录跳转都会是 200。
    shape: Callable[[str], str]
    note: str = ""


def _cls_signed_url(rn: int = 5) -> str:
    """财联社的探针 URL **必须带签名**，否则探的是一个我们永远不会发的请求。

    🔴 这里原来写死了一条**无签名**的 URL，判据是「正文里有 `data`」——
    那两处合起来是 L-13：探针查的不是它声称在查的东西。
    错误响应 `{"errno":...,"msg":...,"data":...}` 里照样有 `data`
    ⇒ **签名坏掉的那天它仍然绿**。

    ⚠️ 签名算法与 `providers/cls_news._sign` 同源，但这里**不 import 它** ——
    探针的价值在于独立复现；import 过来就变成「自己验自己」。
    """
    import hashlib
    params = {"appName": "CailianpressWeb", "os": "web", "sv": "7.7.5",
              "last_time": "", "refresh_type": "1", "rn": str(rn)}
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sign = hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()
    return f"https://www.cls.cn/v1/roll/get_roll_list?{qs}&sign={sign}"


def _cls_roll_shape(text: str) -> str:
    """判据打在 `errno == 0` **且** `roll_data` 非空上。

    实测 `rn>50` 会返回 `errno=0` + 空数组 —— 只判 `errno` 会漏掉它。
    """
    import json
    try:
        obj = json.loads(text)
    except ValueError:
        return "❌ 不是 JSON"
    if obj.get("errno"):
        return f"❌ errno={obj['errno']} msg={obj.get('msg')!r}"
    rows = (obj.get("data") or {}).get("roll_data") or []
    if not rows:
        return "❌ errno=0 但 roll_data 为空"
    return f"{len(rows)} 条电报，level={rows[0].get('level')!r}"


def _zip_members(min_members: int) -> Callable[[bytes | str], str]:
    """判据打在**能不能解开**上，不在「有多少字节」上。

    ⚠️ 一个 404 页面也有字节数。只看长度的探针在上游改成「返回错误页而不是
    404」的那天会静默变绿。
    """
    def check(text: Any) -> str:
        import io
        import zipfile

        raw = text if isinstance(text, bytes) else str(text).encode("latin-1", "ignore")
        archive = zipfile.ZipFile(io.BytesIO(raw))
        names = archive.namelist()
        if len(names) < min_members:
            raise ValueError(f"包里只有 {len(names)} 个文件（需要 ≥{min_members}）")
        return f"{len(raw) // 1024} KB / {len(names)} 个文件"
    return check


def _json_rows(min_rows: int, key: str | None = None) -> Callable[[str], str]:
    def check(text: str) -> str:
        payload: Any = json.loads(text)
        if key is not None:
            for part in key.split("."):
                payload = payload[part]
        if not isinstance(payload, (list, dict)):
            raise ValueError("不是数组/对象")
        size = len(payload)
        if size < min_rows:
            raise ValueError(f"只有 {size} 条（需要 ≥{min_rows}）")
        return f"{size} 条"
    return check


def _contains(needle: str, label: str) -> Callable[[str], str]:
    def check(text: str) -> str:
        if needle not in text:
            raise ValueError(f"正文里找不到 {needle!r}")
        return label
    return check


SINA_MKT = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
EM_REF = "https://quote.eastmoney.com/"

PROBES: tuple[Probe, ...] = (
    # ── 全市场名单 / 日线 ────────────────────────────────────────────────
    Probe("sina_hs_a_simple", "全市场截面", "新浪", "cn.equity.daily_bars(主) / cn.security_master(备)",
          f"{SINA_MKT}/Market_Center.getHQNodeDataSimple?page=1&num=500&sort=symbol&asc=1&node=hs_a&_s_r_a=srt",
          "https://vip.stock.finance.sina.com.cn/mkt/", _json_rows(400),
          "num=500 真给 500；非 Simple 版静默截到 100"),
    Probe("em_clist", "全市场截面", "东财 push2", "cn.equity.daily_bars(备) / cn.security_master(主)",
          "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=20&fs=m:1+t:2&fields=f12,f14",
          EM_REF, _contains('"f12"', "有 f12 字段")),
    # 🔴 探针**固定用一个已知的历史交易日**，不是「今天」。
    #    这个端点在非交易日返回 404 —— 用「今天」探活，周末跑一次就会读成
    #    「源挂了」，而它好好的。实测踩过：第一版写了 20260925，
    #    那天是中秋休市，404 被我当成故障查了半天。
    Probe("tdx_package", "全市场截面", "通达信官网", "cn.equity.daily_bars(历史补数候选)",
          "https://www.tdx.com.cn/products/data/data/g4day/20230103.zip",
          "https://www.tdx.com.cn/", _zip_members(6),
          "🔴 唯一能按**指定交易日**取的全市场日线 ⇒ 可补历史；含北交所"),
    # ── 对照组：分辨「我被限流」与「那一组挂了」────────────────────────
    Probe("em_push2ex", "对照组", "东财 push2ex", "cn.market.limit_pool(主)",
          "https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=5&sort=fbt:asc&date=20260925",
          EM_REF, _contains('"rc"', "有 rc 字段")),
    Probe("em_push2his", "对照组", "东财 push2his", "cn.index.daily_bars(候选)",
          "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001&fields1=f1&fields2=f51,f53&klt=101&fqt=1&end=20500101&lmt=5",
          EM_REF, _contains('"rc"', "有 rc 字段")),
    Probe("em_datacenter", "对照组", "东财 datacenter", "（口径不合，仅作对照）",
          "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_BASIC_ORGINFO&columns=SECUCODE&pageNumber=1&pageSize=1&sortColumns=SECURITY_CODE&sortTypes=1",
          "https://data.eastmoney.com/", _contains('"success"', "有 success 字段"),
          "机构信息表，含已退市 ⇒ **不能**当在册名单用"),
    Probe("em_ulist", "涨跌家数", "东财 push2", "cn.market.breadth(主)",
          "https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&fields=f12,f104,f105,f106&secids=1.000001,0.399001",
          EM_REF, _contains('"f104"', "有涨家数字段")),
    Probe("sina_breadth_derivable", "涨跌家数", "新浪", "cn.market.breadth(候选备胎：自算)",
          f"{SINA_MKT}/Market_Center.getHQNodeDataSimple?page=1&num=500&sort=symbol&asc=1&node=hs_a&_s_r_a=srt",
          "https://vip.stock.finance.sina.com.cn/mkt/", _contains("changepercent", "每行带涨跌幅"),
          "⚠️ 它不直接给涨跌家数，但全市场每行都带 changepercent ⇒ 可自算。"
          "自算=派生，要按派生数据集的规矩引上游血缘，不能当采集源"),
    # ── 指数 ────────────────────────────────────────────────────────────
    Probe("tencent_quote", "指数/实时", "腾讯", "cn.index.realtime_quote(主)",
          "https://qt.gtimg.cn/q=sh000001,sz399001", "https://gu.qq.com/",
          _contains("v_sh000001", "有上证行情")),
    Probe("sina_hq", "指数/实时", "新浪 hq", "cn.index.realtime_quote(候选备胎)",
          "https://hq.sinajs.cn/list=sh000001,sz399001", "https://finance.sina.com.cn/",
          _contains("hq_str_sh000001", "有上证行情")),
    Probe("sina_kline", "指数/日线", "新浪", "cn.index.daily_bars(主)",
          "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=sh000001&scale=240&ma=no&datalen=5",
          "https://finance.sina.com.cn/", _json_rows(3)),
    # ── 交易日历 ────────────────────────────────────────────────────────
    Probe("sina_calendar", "交易日历", "新浪", "cn.trading_calendar(主)",
          "https://finance.sina.com.cn/realstock/company/klc_td_sh.txt",
          "https://finance.sina.com.cn/", lambda t: f"{len(t)} 字节"),
    Probe("szse_calendar", "交易日历", "深交所官方", "cn.trading_calendar(备)",
          "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList?month=2026-09&random=0.1",
          "https://www.szse.cn/", _contains("data", "有 data"),
          "🔴 本机连不通（TCP 握手后挂死）—— 见 providers/szse.py 模块头"),
    # ── 快讯 ────────────────────────────────────────────────────────────
    Probe("cls_roll", "快讯", "财联社", "cn.news.flash(主)",
          _cls_signed_url(), "https://www.cls.cn/", _cls_roll_shape,
          "签名纯本地可算、零 key —— URL 由 `_cls_signed_url()` 现算"),
    Probe("sina_7x24", "快讯", "新浪", "cn.news.flash(备)",
          "https://zhibo.sina.com.cn/api/zhibo/feed?zhibo_id=152&page_size=5&dire=f",
          "https://finance.sina.com.cn/7x24/", _contains("result", "有 result")),
    Probe("jin10_flash", "快讯", "金十", "cn.news.flash(候选，口径未核)",
          "https://www.jin10.com/flash_newest.js", "https://www.jin10.com/",
          lambda t: f"{len(t)} 字节"),
    # ── 板块 ────────────────────────────────────────────────────────────
    Probe("em_boards", "板块", "东财 push2", "cn.sector.board_snapshot(主)",
          "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5&fs=m:90+t:2&fields=f12,f14,f3",
          EM_REF, _contains('"f12"', "有 f12 字段")),
    Probe("ths_hot", "板块", "同花顺", "cn.sector.board_snapshot(候选备胎)",
          "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=hour&list_type=normal",
          "https://dq.10jqka.com.cn/", _contains("data", "有 data")),
)


@dataclass
class Outcome:
    probe: Probe
    ok: bool
    detail: str
    elapsed_ms: int
    http: str = ""


def _fetch(probe: Probe, timeout: int) -> Outcome:
    request = urllib.request.Request(
        probe.url, headers={"User-Agent": _UA, "Referer": probe.referer})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            status = str(response.status)
    except urllib.error.HTTPError as exc:
        return Outcome(probe, False, f"HTTP {exc.code}",
                       int((time.monotonic() - started) * 1000), str(exc.code))
    except Exception as exc:  # noqa: BLE001 —— 网络层什么都可能抛
        return Outcome(probe, False, type(exc).__name__,
                       int((time.monotonic() - started) * 1000))
    elapsed = int((time.monotonic() - started) * 1000)
    # ⚠️ 二进制包（zip）不能先解码 —— 解码会毁掉字节。
    #    判据函数自己决定要文本还是原始字节。
    payload: Any = body
    if not probe.url.endswith(".zip"):
        for encoding in ("utf-8", "gbk"):
            try:
                payload = body.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            payload = repr(body[:200])
    try:
        detail = probe.shape(payload)
    except Exception as exc:  # noqa: BLE001
        # 🔴 200 但形状不对 ⇒ 算失败。限流页、空壳 JSON、登录跳转都会是 200。
        return Outcome(probe, False, f"形状不符：{exc}", elapsed, status)
    return Outcome(probe, True, detail, elapsed, status)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--markdown", action="store_true", help="输出 Markdown 表")
    parser.add_argument("--only-layer", default=None)
    args = parser.parse_args(argv)

    probes = [p for p in PROBES
              if args.only_layer is None or p.layer == args.only_layer]
    results: list[Outcome] = []
    for index, probe in enumerate(probes):
        if index:
            time.sleep(_GAP_SEC)
        results.append(_fetch(probe, args.timeout))

    if args.markdown:
        print("| 层 | 源 | 端点 | 服务于 | 结果 | 耗时 |")
        print("|---|---|---|---|---|---|")
        for item in results:
            mark = "✅" if item.ok else "❌"
            print(f"| {item.probe.layer} | {item.probe.source} | `{item.probe.key}` | "
                  f"{item.probe.serves} | {mark} {item.detail} | {item.elapsed_ms}ms |")
    else:
        for item in results:
            mark = "✅" if item.ok else "❌"
            print(f"  {mark} {item.probe.key:20} {item.probe.source:14} "
                  f"{item.detail:28} {item.elapsed_ms:>6}ms")
            if item.probe.note:
                print(f"       ⤷ {item.probe.note}")

    failed = [item for item in results if not item.ok]
    total = len(results)
    print(f"\n可用 {total - len(failed)} / {total}")
    if not failed:
        return _v.PASS
    # 🔴 全挂 ⇒ 大概率是本机网络，不是各家源同时出事 ⇒ UNKNOWN 而不是 FAIL。
    #    「我连不上」和「它坏了」是两件事，而屏幕上长得一样（红线 R-3）。
    if len(failed) == total:
        print("🔶 全部失败 —— 先怀疑本机网络，而不是所有源同时出事")
        return _v.UNKNOWN
    return _v.FAIL


if __name__ == "__main__":
    raise SystemExit(main())
