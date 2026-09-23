#!/usr/bin/env python3
"""FeishuDeliverer —— 把一条外发通知投到飞书（批 G-II，真投递方）。

它实现 `notify_worker.Deliverer` 协议（`channel` + `deliver(...)`），是批 G-I 当初
把投递做成**可替换接口**、而不是写死 `StdoutDeliverer` 的那个「以后要接的真飞书
adapter」。`notify_worker.py --deliverer feishu` 用它把 Card 完成 / UNKNOWN / 风控
否决 / 运行失败四类通知发回飞书。

🔴 凭据与密钥纪律（公开仓库）
------------------------------
仓库里**一个凭据都不落**。`app_id` / `app_secret` / 收件人 `open_id` 全部从
**环境变量**读（`BIGA_FEISHU_APPID` / `BIGA_FEISHU_APPSECRET` / `BIGA_FEISHU_OWNER_ID`），
由运行环境从它的密钥库注入 —— 与 CLAUDE.md「凭据记在仓库外」一致（真实飞书接入
`channels.feishu` 那块本就在仓库外的 `openclaw.json` 里）。这个类只有**取值逻辑与
发送逻辑**，没有任何具体值。

🔴 可测：HTTP 传输是注入点
--------------------------
`http` 参数默认走 `urllib`（真发网络），测试注入一个假的、只记录请求 ——
于是「投的是不是对的接口、body 里有没有决策号、失败会不会抛」全部离线可断言，
真实飞书 API 只在 live 收尾那一次走（探针 P6）。这与 orchestrator 的 `attach=`、
SnapshotCoordinator 的 fetcher、notify_worker 的 deliverer 是同一套依赖注入做法。

不做什么（分发提示词「不要做」）
--------------------------------
不实现飞书完整功能面 —— 富文本卡片、按钮交互、@、话题回复都不做。一条**文本
消息**把「哪个决策、什么结论、缺没缺、失败没失败」说清楚就够；这一批只需证明
「event → 投递接口 → 飞书 → 送达」这条链闭环。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

__all__ = ["FeishuDeliverer", "FeishuError", "urllib_http"]

#: 飞书开放平台基址。海外租户是 open.larksuite.com —— 也从环境变量覆盖，不写死租户。
_DEFAULT_BASE = "https://open.feishu.cn"

#: 一次 HTTP 调用：`(method, url, headers, body_dict) -> (http_status, resp_dict)`。
#: 抽成类型别名，是为了让「真 urllib」和「测试假实现」是同一个形状。
Http = Callable[[str, str, dict, "dict | None"], "tuple[int, dict]"]


class FeishuError(RuntimeError):
    """飞书 API 返回了非零 `code`，或 HTTP 层失败。worker 据此记一条 failed（不重跑决策）。"""


def urllib_http(method: str, url: str, headers: dict,
                body: dict | None) -> tuple[int, dict]:
    """默认 HTTP 传输：标准库 urllib，无第三方依赖。真发网络。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"_raw": raw}


class FeishuDeliverer:
    """把一条通知发成一条飞书文本消息。满足 `notify_worker.Deliverer` 协议。"""

    channel = "feishu"

    def __init__(
        self,
        *,
        app_id: str | None = None,
        app_secret: str | None = None,
        receive_id: str | None = None,
        receive_id_type: str = "open_id",
        base_url: str | None = None,
        http: Http = urllib_http,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # 🔴 全部从环境变量兜底 —— 仓库里不落任何具体值（公开仓库纪律）。
        self._app_id = app_id or os.environ.get("BIGA_FEISHU_APPID", "")
        self._app_secret = app_secret or os.environ.get("BIGA_FEISHU_APPSECRET", "")
        self._receive_id = receive_id or os.environ.get("BIGA_FEISHU_OWNER_ID", "")
        self._receive_id_type = receive_id_type
        self._base = (base_url or os.environ.get("BIGA_FEISHU_BASE_URL")
                      or _DEFAULT_BASE).rstrip("/")
        self._http = http
        self._clock = clock
        # tenant_access_token 缓存：飞书的 token 约 2 小时过期，别每条通知都换一次。
        self._token: str | None = None
        self._token_exp: float = 0.0

    # ── 凭据自检 ──────────────────────────────────────────────────────────
    def _require_config(self) -> None:
        missing = [name for name, val in (
            ("BIGA_FEISHU_APPID", self._app_id),
            ("BIGA_FEISHU_APPSECRET", self._app_secret),
            ("BIGA_FEISHU_OWNER_ID", self._receive_id),
        ) if not val]
        if missing:
            # 🔴 报错指路（dev-workflow §8）：缺什么、去哪配，而不是一句裸异常。
            raise FeishuError(
                "飞书投递缺凭据：环境变量 " + ", ".join(missing) + " 没设。\n"
                "  它们**不放仓库**（公开仓库纪律）——由运行环境从密钥库注入。\n"
                "  真实值见仓库外 ~/.openclaw-biga 的飞书接入配置；"
                "本地排查可临时 export 后再跑 notify_worker --deliverer feishu。")

    # ── token（缓存 + 过期刷新）──────────────────────────────────────────
    def _tenant_token(self) -> str:
        now = self._clock()
        if self._token and now < self._token_exp:
            return self._token
        status, resp = self._http(
            "POST", f"{self._base}/open-apis/auth/v3/tenant_access_token/internal",
            {"Content-Type": "application/json"},
            {"app_id": self._app_id, "app_secret": self._app_secret})
        if status != 200 or resp.get("code") != 0 or not resp.get("tenant_access_token"):
            raise FeishuError(
                f"取 tenant_access_token 失败：http={status} code={resp.get('code')} "
                f"msg={resp.get('msg')!r}")
        self._token = resp["tenant_access_token"]
        # 提前 60s 视作过期，避开「刚好卡在边界」。expire 单位是秒。
        self._token_exp = now + max(0, int(resp.get("expire", 7200)) - 60)
        return self._token

    # ── Deliverer 协议 ───────────────────────────────────────────────────
    def deliver(self, *, event_type: str, aggregate: str,
                payload: dict[str, Any]) -> None:
        """把一条通知发成一条飞书文本消息。成功返回 None；任何失败抛 `FeishuError`。

        worker 约定：投递成功返回 None、失败抛异常（据此记一条 status='failed'，
        不连累其余、也不重跑决策）——所以这里所有失败路径都抛，绝不静默吞。
        """
        self._require_config()
        token = self._tenant_token()
        text = self._render(event_type, aggregate, payload)
        status, resp = self._http(
            "POST",
            f"{self._base}/open-apis/im/v1/messages?receive_id_type={self._receive_id_type}",
            {"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            {"receive_id": self._receive_id, "msg_type": "text",
             # 🔴 飞书的坑：content 必须是**字符串化的 JSON**，不是嵌套对象。
             "content": json.dumps({"text": text}, ensure_ascii=False)})
        if status != 200 or resp.get("code") != 0:
            raise FeishuError(
                f"飞书发消息失败：http={status} code={resp.get('code')} "
                f"msg={resp.get('msg')!r}（event={event_type} aggregate={aggregate}）")

    # ── 消息文本（四类事件各一句人话）────────────────────────────────────
    @staticmethod
    def _render(event_type: str, aggregate: str, payload: dict[str, Any]) -> str:
        did = payload.get("decision_id") or aggregate
        status = payload.get("status")
        headline = payload.get("headline") or ""
        miss = payload.get("missing_count")
        if event_type == "card_completed":
            head = f"✅ 决策 {did} 已出卡：{status}"
        elif event_type == "card_unknown":
            head = f"🔶 决策 {did}：{status}（{miss} 项缺失，结论以不确定为主）"
        elif event_type == "risk_block":
            head = f"🔴 决策 {did}：风控否决 —— {status}"
        elif event_type == "run_failed":
            reason = payload.get("reason") or "未给出原因"
            rid = payload.get("run_id") or "?"
            return (f"❌ 出卡运行失败：{did}（run {rid}）\n原因：{reason}\n"
                    f"查死在哪一步：bin/biga-card --status {rid}")
        else:
            head = f"决策 {did}：{event_type}"
        lines = [head]
        if headline:
            lines.append(headline)
        lines.append(f"看完整卡：bin/biga-card --show {did}")
        return "\n".join(lines)
