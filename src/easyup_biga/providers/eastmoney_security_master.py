"""东财全市场 A 股名单 —— `cn.security_master` 的 Provider 适配器。

取自外部 P3-3 实现包，**抓取层按公开参考实现 `a-stock-data` 的实测做法改写过**
（它是一份把每个端点的可用参数、主源/备胎顺序、风控经验记下来的速查库）。

🔴 三处与外部实现不同，每处都有依据
------------------------------------
1. **串行 + 限流，不并发。** 外部实现用 `ThreadPoolExecutor(max_workers=2)` 并发
   翻页且没有任何间隔。全市场约 5400 只 / 每页 100 ⇒ 约 54 页。
   参考实现的东财统一入口是**串行 + 最小间隔 1 秒 + 抖动**，并注明
   「所有 eastmoney.com 接口都应通过它请求，避免高频被封 IP」。
   实测（2026-09-26）：连打十来个请求之后，连**已知可用**的兄弟端点也一起
   返回 502 —— 封的是来源，不是某个接口。⇒ 走 `http.throttle("eastmoney")`。

2. **`fs` 用 `%2B` 上线，不是裸 `+`。** 外部实现 `urlencode(..., safe="+:")`
   让 `+` 原样进 query，而 query 里的裸 `+` 服务端会解成**空格**
   （`m:1+t:2` 变成 `m:1 t:2`）。参考实现把 `fs` 交给 requests 的 params，
   `+` 被编成 `%2B`。⇒ 这里显式编码。

3. **主域是 `push2`，`push2delay` 作备胎。** 外部实现把 `push2delay` 放第一位。
   参考实现的顺序是 `push2` → `push2delay`（后者行情延迟约 15 分钟 ——
   对「名单」这类数据无所谓，但主源顺序没有理由反过来）。

🔴 本适配器**尚未在本机探活成功**
---------------------------------
外部实现的 `TEST_RESULTS.md` 写明 "Live Eastmoney network fetching was not executed"。
本仓库补做探活时（2026-09-26）这个 `clist/get` 端点在三个 host 上都失败，
而**同一分钟内**兄弟端点 `ulist.np` / `push2ex` 都返回了 `rc:0` 真数据；
随后因请求过密被整体限流，未能复验。

⇒ 状态是「**未验证**」，不是「可用」也不是「不可用」。
  用 `python3 tools/verify/security_master_probe.py` 在不被限流时复验。
  在复验通过之前，`bin/biga-security-master` 会失败 —— 那是**正确**的行为：
  fail-closed 好过发布一份来路不明的名单。

边界
----
这个源给的是**当前在册**名单与上市日期，**没有完整退市历史**。
⇒ Point-in-time 从 BigA 第一次成功同步那天起成立，不宣称覆盖之前。
"""
from __future__ import annotations

import json
import math
import urllib.parse
from typing import Any, Mapping, Sequence

from easyup_biga.domain import now_cn

from .http import SourceError, get_json_and_text, throttle
# ⚠️ 结果类型搬到了中立契约里 —— 本适配器与新浪那个共用同一份，
#    否则「一次名单抓取的结果长什么样」会有两个定义（L-3）。
from .security_listing import SecurityListing, SecurityMasterFetchResult

__all__ = [
    "EASTMONEY_SECURITY_MASTER_URL",
    "SecurityMasterFetchResult",
    "fetch_security_master",
    "parse_security_master_pages",
]

EASTMONEY_SECURITY_MASTER_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
_REFERER = "https://quote.eastmoney.com/center/gridlist.html"
#: 主域在前、延迟域作备胎 —— 与参考实现的 `EM_CLIST_HOSTS` 同序。
_HOSTS = (
    "push2.eastmoney.com",
    "push2delay.eastmoney.com",
)
_PATH = "/api/qt/clist/get"

# Shanghai main + STAR, Shenzhen main + ChiNext, Beijing Stock Exchange.
# The filters are Provider-specific and deliberately stay inside this adapter.
_SECURITY_FS = "m:1+t:2,m:1+t:23,m:0+t:6,m:0+t:80,m:0+t:81+s:2048"
_FIELDS = "f12,f14,f13,f26"
_PAGE_SIZE = 100
_MAX_PAGES = 80


def _page_rows(payload: Mapping[str, Any], *, page_no: int) -> tuple[list[dict[str, Any]], int]:
    if payload.get("rc") != 0:
        raise SourceError(
            f"security_master page {page_no}: rc={payload.get('rc')}"
        )
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise SourceError(f"security_master page {page_no}: data is missing")
    diff = data.get("diff")
    if isinstance(diff, Mapping):
        rows = [dict(item) for item in diff.values()]
    elif isinstance(diff, list):
        rows = [dict(item) for item in diff]
    elif diff in (None, []):
        rows = []
    else:
        raise SourceError(
            f"security_master page {page_no}: unsupported data.diff shape"
        )
    try:
        total = int(data.get("total") or 0)
    except (TypeError, ValueError) as exc:
        raise SourceError(
            f"security_master page {page_no}: invalid total={data.get('total')!r}"
        ) from exc
    return rows, total


def parse_security_master_pages(
    payloads: Sequence[Mapping[str, Any]],
    raw_text_pages: Sequence[str],
    *,
    retrieved_at: str | None = None,
) -> SecurityMasterFetchResult:
    """Validate and combine deterministic page results.

    This parser is public so fixture tests can exercise the real normalization
    boundary without network access.
    """
    if not payloads or len(payloads) != len(raw_text_pages):
        raise SourceError("security_master: payload/text page counts do not match")

    rows: list[Mapping[str, Any]] = []
    declared_total = 0
    for page_no, payload in enumerate(payloads, start=1):
        page, total = _page_rows(payload, page_no=page_no)
        rows.extend(page)
        if total:
            if declared_total and total != declared_total:
                raise SourceError(
                    "security_master: total changed while paging "
                    f"({declared_total} -> {total})"
                )
            declared_total = total

    if not rows:
        raise SourceError("security_master: provider returned no securities")
    if declared_total and len(rows) != declared_total:
        raise SourceError(
            f"security_master: provider declared total={declared_total}, "
            f"received={len(rows)}"
        )

    return SecurityMasterFetchResult(
        total=declared_total or len(rows),
        # 🔴 翻译成 **provider 中立行** 就在这里做。
        #    归一化层不再认识 `f12` 这些名字 —— 见 `security_listing.py` 模块头。
        rows=tuple(
            SecurityListing(
                symbol=item.get("f12"),
                name=item.get("f14"),
                market_hint=item.get("f13"),
                list_date=item.get("f26"),
            )
            for item in rows
        ),
        raw={"pages": [dict(item) for item in payloads]},
        # Each element is the exact response text of one page.  Encoding the
        # outer list does not rewrite the contents of any page string.
        raw_text=json.dumps(list(raw_text_pages), ensure_ascii=False),
        retrieved_at=retrieved_at or now_cn().isoformat(),
    )


def _fetch_page(page_no: int, page_size: int) -> tuple[dict[str, Any], str]:
    query = urllib.parse.urlencode(
        {
            "pn": page_no,
            "pz": page_size,
            "po": 1,
            "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2,
            "invt": 2,
            "fid": "f12",
            "fs": _SECURITY_FS,
            "fields": _FIELDS,
        },
        # 🔴 **不给 `+` 开 safe**。query 里的裸 `+` 服务端解成空格，
        #    `m:1+t:2` 会变成 `m:1 t:2`。冒号不是保留字符、留着可读性即可。
        safe=":,",
    )
    errors: list[str] = []
    for host in _HOSTS:
        try:
            throttle("eastmoney")      # 🔴 见模块头第 1 条
            payload, text = get_json_and_text(
                f"https://{host}{_PATH}?{query}",
                referer=_REFERER,
            )
            if not isinstance(payload, Mapping):
                raise SourceError("security_master: response is not an object")
            return dict(payload), text
        except SourceError as exc:
            errors.append(f"{host}: {exc}")
    raise SourceError(
        f"security_master page {page_no}: all hosts failed — " + " | ".join(errors)
    )


def fetch_security_master(
    *,
    page_size: int = _PAGE_SIZE,
    max_pages: int = _MAX_PAGES,
) -> SecurityMasterFetchResult:
    """Fetch the complete currently-listed A-share universe."""
    if page_size < 1 or page_size > 100:
        # This endpoint silently caps large page sizes.  Keep the known-safe cap.
        raise ValueError("page_size must be between 1 and 100")
    if max_pages < 1:
        raise ValueError("max_pages must be positive")

    first_payload, first_text = _fetch_page(1, page_size)
    first_rows, total = _page_rows(first_payload, page_no=1)
    if not first_rows or total < 1:
        raise SourceError("security_master: first page is empty")

    page_count = math.ceil(total / page_size)
    if page_count > max_pages:
        raise SourceError(
            f"security_master: requires {page_count} pages, max_pages={max_pages}"
        )

    payloads: list[dict[str, Any]] = [first_payload]
    texts: list[str] = [first_text]
    # 🔴 **串行**翻页。并发 + 零间隔是最快被封的走法（见模块头第 1 条）。
    #    约 54 页 × 1 秒多一点 ≈ 一分钟，对一个每天跑一次的同步任务完全可接受。
    for page_no in range(2, page_count + 1):
        payload, text = _fetch_page(page_no, page_size)
        payloads.append(payload)
        texts.append(text)

    return parse_security_master_pages(
        payloads,
        texts,
        retrieved_at=now_cn().isoformat(),
    )
