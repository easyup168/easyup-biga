"""批 G-II FeishuDeliverer：把外发通知投成一条飞书消息（真投递方，离线测）。

它实现批 G-I 留的 `Deliverer` 协议（`channel` + `deliver`）。这里离线验它的**请求
形状**与**失败语义**（HTTP 传输是注入点，不出网 —— 真飞书 API 只在 live P6 走一次）：

  · 取 tenant_access_token → 发 im/v1/messages 两步、header/body 形状对
  · payload 里带决策号（worker P4 的同款判据：投出去的东西能追到哪个决策）
  · token 缓存（不每条都换）
  · 飞书返回非零 code / HTTP 错 ⇒ 抛 FeishuError（worker 据此记 failed、不重跑决策）
  · 缺凭据 ⇒ 报错指路（不静默）
  · 四类事件各渲染成一句人话

🔴 公开仓库纪律：这个类只有取值/发送逻辑，凭据从环境变量读、仓库里不落 ——
   测试也只用假值（`app_id="a"` 之类），不碰任何真实凭据。
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))
sys.path.insert(0, str(REPO / "skills" / "decision-card" / "scripts"))

from feishu_deliverer import FeishuDeliverer, FeishuError  # noqa: E402


class _MockHttp:
    """记录每次调用，按 URL 返回预设的飞书响应。可注入自定义响应覆盖默认。"""

    def __init__(self, overrides=None):
        self.calls = []
        self._over = overrides or {}

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        for frag, resp in self._over.items():
            if frag in url:
                return resp
        if "tenant_access_token" in url:
            return 200, {"code": 0, "tenant_access_token": "t-XYZ", "expire": 7200}
        if "im/v1/messages" in url:
            return 200, {"code": 0, "data": {"message_id": "om_x"}}
        return 500, {"code": 99, "msg": "unexpected"}


def _dev(http, **kw):
    base = dict(app_id="cli_x", app_secret="sec", receive_id="ou_owner", http=http)
    base.update(kw)
    return FeishuDeliverer(**base)


class TestDeliver:

    def test_两步请求形状正确_带决策号(self):
        m = _MockHttp()
        _dev(m).deliver(event_type="card_completed", aggregate="BIGA-20260923-001",
                        payload={"decision_id": "BIGA-20260923-001", "status": "WAIT",
                                 "headline": "量能不足"})
        assert len(m.calls) == 2
        tok, msg = m.calls
        assert tok["body"]["app_id"] == "cli_x"
        assert "im/v1/messages?receive_id_type=open_id" in msg["url"]
        assert msg["headers"]["Authorization"] == "Bearer t-XYZ"
        assert msg["body"]["receive_id"] == "ou_owner" and msg["body"]["msg_type"] == "text"
        text = json.loads(msg["body"]["content"])["text"]
        assert "BIGA-20260923-001" in text and "WAIT" in text

    def test_token缓存_第二条不再换token(self):
        m = _MockHttp()
        d = _dev(m)
        d.deliver(event_type="card_completed", aggregate="D1",
                  payload={"decision_id": "D1", "status": "BUY"})
        d.deliver(event_type="risk_block", aggregate="D1",
                  payload={"decision_id": "D1", "status": "AVOID"})
        token_calls = sum(1 for c in m.calls if "tenant_access_token" in c["url"])
        assert token_calls == 1

    def test_飞书返回非零code_抛FeishuError(self):
        m = _MockHttp(overrides={"im/v1/messages": (200, {"code": 230002, "msg": "bot not in chat"})})
        with pytest.raises(FeishuError, match="230002"):
            _dev(m).deliver(event_type="card_completed", aggregate="D1",
                            payload={"decision_id": "D1", "status": "BUY"})

    def test_取token失败_抛FeishuError(self):
        m = _MockHttp(overrides={"tenant_access_token": (200, {"code": 99, "msg": "bad app"})})
        with pytest.raises(FeishuError, match="tenant_access_token"):
            _dev(m).deliver(event_type="card_completed", aggregate="D1",
                            payload={"decision_id": "D1", "status": "BUY"})

    def test_缺凭据_报错指路_不静默(self, monkeypatch):
        for v in ("BIGA_FEISHU_APPID", "BIGA_FEISHU_APPSECRET", "BIGA_FEISHU_OWNER_ID"):
            monkeypatch.delenv(v, raising=False)
        d = FeishuDeliverer(app_id="", app_secret="", receive_id="", http=_MockHttp())
        with pytest.raises(FeishuError, match="缺凭据"):
            d.deliver(event_type="card_completed", aggregate="D1", payload={})

    def test_凭据从环境变量读(self, monkeypatch):
        monkeypatch.setenv("BIGA_FEISHU_APPID", "env_app")
        monkeypatch.setenv("BIGA_FEISHU_APPSECRET", "env_sec")
        monkeypatch.setenv("BIGA_FEISHU_OWNER_ID", "ou_env")
        m = _MockHttp()
        FeishuDeliverer(http=m).deliver(event_type="card_completed", aggregate="D1",
                                        payload={"decision_id": "D1", "status": "BUY"})
        assert m.calls[0]["body"]["app_id"] == "env_app"
        assert m.calls[1]["body"]["receive_id"] == "ou_env"

    def test_channel名是feishu(self):
        assert FeishuDeliverer(app_id="a", app_secret="b", receive_id="c").channel == "feishu"


class TestRender:

    @pytest.mark.parametrize("event,payload,must", [
        ("card_completed", {"decision_id": "D1", "status": "WAIT", "headline": "h"}, ["已出卡", "WAIT"]),
        ("card_unknown", {"decision_id": "D1", "status": "WAIT", "missing_count": 3}, ["缺失", "3"]),
        ("risk_block", {"decision_id": "D1", "status": "AVOID"}, ["否决", "AVOID"]),
        ("run_failed", {"decision_id": "D1", "run_id": "r-1", "reason": "预算耗尽"}, ["失败", "r-1", "预算耗尽"]),
    ])
    def test_四类事件各渲染成一句人话(self, event, payload, must):
        m = _MockHttp()
        _dev(m).deliver(event_type=event, aggregate=payload.get("run_id", "D1"), payload=payload)
        text = json.loads(m.calls[-1]["body"]["content"])["text"]
        for frag in must:
            assert frag in text, f"{event} 的消息里应有 {frag!r}：{text!r}"
