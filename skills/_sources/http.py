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

import json
import time
import urllib.error
import urllib.request
from typing import Any

__all__ = ["SourceError", "get_json", "UA", "TIMEOUT", "RETRIES"]

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


def get_json(url: str, *, referer: str) -> dict[str, Any]:
    """取一个返回 JSON 的端点。失败抛 `SourceError`。

    Args:
        url: 完整 URL（含 query）。
        referer: 这些接口会按 Referer 拒绝请求，必须由调用方按源指定 ——
            不给默认值是有意的：默认值会让「忘了设」表现为偶发失败而不是报错。
    """
    endpoint = url.split("?")[0]
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    last: Exception | None = None

    for attempt in range(RETRIES):
        if attempt:
            time.sleep(BACKOFF_SEC[min(attempt - 1, len(BACKOFF_SEC) - 1)])
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            continue
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            # 返回了内容但不是 JSON —— 多半是网关错误页，重试没有意义
            raise SourceError(
                f"{endpoint}: 返回不是合法 JSON（前 200 字符）{body[:200]!r}"
            ) from e

    raise SourceError(
        f"{endpoint}: {RETRIES} 次尝试全部失败，最后一次 "
        f"{type(last).__name__}: {last}"
    ) from last
