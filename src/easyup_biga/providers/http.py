"""采集层的 HTTP 客户端 —— 重试 / 退避 / 请求头的**唯一实现**。

为什么单独一层
--------------
BigA 的数据源是公开行情接口，它们的失败方式高度一致：偶发 TLS 握手超时、
连接被服务端重置、返回网关错误页而不是 JSON。应对方式（重试几次、退避多久、
带什么 User-Agent 与 Referer）是**同一套判据**。

每个数据源各写一遍，就会有几套慢慢漂开的重试策略 ——
而漂开时不会报错：某个源悄悄变成「只重试一次」，表现只是偶尔多一条 missing。
L-3 说的「同一判据多份实现」不只针对业务逻辑，也针对这种基础设施判据。

🔴 重试耗尽后必须抛错
---------------------
绝不允许「重试几次还不行就返回 0」—— 那会把一次网络抖动变成一条虚假的市场事实，
而且上层再也没有机会知道这里出过问题。
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from typing import Any

__all__ = ["SourceError", "get_json", "get_json_and_text", "get_text",
           "UA", "TIMEOUT", "RETRIES"]

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36")
TIMEOUT = 15

#: 重试次数与退避。实测这些公开端点会偶发 TLS 握手超时 / 连接被重置，
#: 单次失败不代表数据源挂了。
RETRIES = 3
BACKOFF_SEC = (0.8, 2.0)


class SourceError(RuntimeError):
    """数据源不可用或返回了无法解释的内容。

    🔴 采集层遇到问题一律抛这个，绝不返回一个「兜底值」。
    返回兜底值 = 让上层无法区分「真的是这个数」和「没取到」。
    """


def get_text(url: str, *, referer: str, encoding: str = "utf-8") -> str:
    """取一个返回文本的端点（重试 / 退避在这里，只此一处）。失败抛 `SourceError`。

    Args:
        url: 完整 URL（含 query）。
        referer: 这些接口会按 Referer 拒绝请求，必须由调用方按源指定 ——
            不给默认值是有意的：默认值会让「忘了设」表现为偶发失败而不是报错。
        encoding: 腾讯行情返回 GBK，新浪返回 UTF-8。**猜错编码不会报错**，
            只会让中文名变成乱码 —— 所以它是显式参数。
    """
    endpoint = url.split("?")[0]
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    last: Exception | None = None

    for attempt in range(RETRIES):
        if attempt:
            time.sleep(BACKOFF_SEC[min(attempt - 1, len(BACKOFF_SEC) - 1)])
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode(encoding, errors="replace")
        # 🔴 `http.client.HTTPException` 必须在里面。
        #
        #    `IncompleteRead` 继承自 `HTTPException` + `ValueError`，
        #    **不继承 OSError** —— 于是它穿透了这层重试，
        #    以一个裸 traceback 的形式炸掉整个 skill。
        #
        #    实测（2026-09-21 15:14）：新浪 7x24 返回被截断的 chunked 响应，
        #    `news_scan.py` 直接崩溃 —— 而按本项目的口径它应该产出
        #    `news.feed.unavailable` 这条缺失项，让卡照常出、只是标着「不知道」。
        #
        #    ⚠️ 这条影响**全部六个 skill**，因为重试只此一处。
        except (urllib.error.URLError, TimeoutError, OSError,
                http.client.HTTPException) as e:
            last = e
            continue

    raise SourceError(
        f"{endpoint}: {RETRIES} 次尝试全部失败，最后一次 "
        f"{type(last).__name__}: {last}"
    ) from last


def get_json_and_text(url: str, *, referer: str) -> tuple[Any, str]:
    """取一个返回 JSON 的端点，**同时交出解析结果与原始响应文本**。失败抛 `SourceError`。

    为什么解析结果与原文要成对交出
    ------------------------------
    🔴 `content_sha256`（raw 层的内容指纹、`Evidence.raw_hash` 指向的那个）要能
    证明**数据源发来的字节**，而不是我们自己重排后的字节。原始文本一旦在这里被
    `json.loads` 吃掉、只留下 Python 对象，落库时就只能 `json.dumps` 重新序列化
    —— 键序 / 空白 / 浮点表示全部换成我们自己的，指纹于是指向一个数据源从未发过
    的字符串（批 I / 数据架构 §9）。所以这里把 payload 与 body **一起**返回，
    由调用方把 `body` 一路带到 `save_raw_snapshot(raw_text=...)`。

    ⚠️ 这是「文本级」原始性，不是「字节级」：`get_text` 已按 `encoding` 参数把响应
    体解码成 `str`（默认 utf-8）。若某个源实际是别的编码，那一步的错在这里看不出来
    —— 那是一个独立问题，不在本层解决。

    ⚠️ 返回的 payload 类型是 `Any` 而不是 `dict`：新浪日线返回的是**数组**。
    调用方必须自己确认拿到的形状对不对 —— 这正是各适配器该做的事。
    """
    endpoint = url.split("?")[0]
    body = get_text(url, referer=referer)
    try:
        return json.loads(body), body
    except json.JSONDecodeError as e:
        # 返回了内容但不是 JSON —— 多半是网关错误页，重试没有意义
        raise SourceError(
            f"{endpoint}: 返回不是合法 JSON（前 200 字符）{body[:200]!r}"
        ) from e


def get_json(url: str, *, referer: str) -> Any:
    """取一个返回 JSON 的端点，只要解析结果。失败抛 `SourceError`。

    🔴 需要把原始响应文本落进 raw 层的调用方（六个 collector 的采集路径）**不要用
    这个** —— 用 `get_json_and_text`，否则原文在这里就地丢弃，`content_sha256` 又会
    退回「我们自己重排后的指纹」（批 I 要修的正是这个）。这个薄封装留给「只算个数、
    不落 raw」的一次性用途。
    """
    return get_json_and_text(url, referer=referer)[0]
