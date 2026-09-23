"""批 G-II FeishuDeliverer：把外发通知投成一条飞书消息（真投递方，离线测）。

它实现批 G-I 留的 `Deliverer` 协议（`channel` + `deliver`）。这里离线验它的**命令
形状**与**失败语义**（子进程调用是注入点，不真跑 `bin/biga` —— 真投递只在 live
收尾走一次，探针 P6）：

  · 命令一定经 `bin/biga message send --channel feishu`（R-1：绝不裸 openclaw）
  · payload 里带决策号（worker P4 的同款判据：投出去的东西能追到哪个决策）
  · `bin/biga` 非零退出 ⇒ 抛 FeishuError（worker 据此记 failed、不重跑决策）
  · 缺收件人 ⇒ 报错指路（不静默）
  · 四类事件各渲染成一句人话

🔴 落地修正（P6 live 真跑，2026-09-23）：不再有 appId/appSecret 这两个凭据——
   走网关自己已认证的飞书通道（`bin/biga message send`），这个类现在完全看不到
   它们。旧版测试里 `app_id="cli_x"` 之类的假值随之整批删掉，不是漏测，是那两个
   参数已经不存在了。

🔴 公开仓库纪律：这个类现在只有一个可配置值（收件人 open_id），从环境变量读、
   仓库里不落——测试也只用假值（`receive_id="ou_owner"`），不碰任何真实凭据。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))
sys.path.insert(0, str(REPO / "skills" / "decision-card" / "scripts"))

from feishu_deliverer import FeishuDeliverer, FeishuError  # noqa: E402


class _FakeRunner:
    """记录每次调用的完整 argv，按需返回预设的 CompletedProcess。"""

    class _Result:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def __init__(self, returncode=0, stderr="", config_stdout=None,
                 config_returncode=0):
        self.calls: list[list] = []
        self._returncode = returncode
        self._stderr = stderr
        # `config get` 与 `message send` 走同一个注入点，但答案不同 ——
        # 分开设，才能单独验「收件人从 live 配置读」这条回退。
        self._config_stdout = config_stdout
        self._config_returncode = config_returncode

    def __call__(self, cmd: list):
        self.calls.append(cmd)
        if "config" in cmd and "get" in cmd:
            return self._Result(returncode=self._config_returncode,
                                stdout=self._config_stdout or "")
        return self._Result(returncode=self._returncode, stderr=self._stderr)


def _dev(runner, **kw):
    base = dict(receive_id="ou_owner", biga_bin="bin/biga", runner=runner)
    base.update(kw)
    return FeishuDeliverer(**base)


class TestDeliver:

    def test_命令形状正确_经bin_biga_带决策号(self):
        r = _FakeRunner()
        _dev(r).deliver(event_type="card_completed", aggregate="BIGA-20260923-001",
                        payload={"decision_id": "BIGA-20260923-001", "status": "WAIT",
                                 "headline": "量能不足"})
        assert len(r.calls) == 1
        cmd = r.calls[0]
        assert cmd[0] == "bin/biga", "必须经 bin/biga，绝不裸 openclaw（R-1）"
        assert cmd[1:5] == ["message", "send", "--channel", "feishu"]
        assert "--target" in cmd and cmd[cmd.index("--target") + 1] == "ou_owner"
        msg = cmd[cmd.index("--message") + 1]
        assert "BIGA-20260923-001" in msg and "WAIT" in msg

    def test_不再出现appId或appSecret(self):
        """🔴 落地修正的核心断言：这个类现在完全看不到 appId/appSecret ——
        构造函数不接受它们，命令行参数里也不会出现。"""
        import inspect
        sig = inspect.signature(FeishuDeliverer.__init__)
        assert "app_id" not in sig.parameters and "app_secret" not in sig.parameters

    def test_biga命令失败_抛FeishuError(self):
        r = _FakeRunner(returncode=1, stderr="channel not connected")
        with pytest.raises(FeishuError, match="channel not connected"):
            _dev(r).deliver(event_type="card_completed", aggregate="D1",
                            payload={"decision_id": "D1", "status": "BUY"})

    def test_缺收件人_报错指路_不静默(self, monkeypatch):
        """三级来源全落空才报错，且报错要指出该去查哪一级。"""
        monkeypatch.delenv("BIGA_FEISHU_OWNER_ID", raising=False)
        d = FeishuDeliverer(receive_id="", runner=_FakeRunner(config_stdout=""))
        with pytest.raises(FeishuError, match="缺收件人") as e:
            d.deliver(event_type="card_completed", aggregate="D1", payload={})
        assert "config get" in str(e.value), "报错要给出能直接粘的排查命令"

    def test_收件人回退到live配置_不需要任何人注入环境变量(self, monkeypatch):
        """🔴 2026-09-24 的真故障：调度方 notify-worker-biga.service 从没注入过
        BIGA_FEISHU_OWNER_ID ⇒ 每次投递都失败、Card 全积压在 outbox 里。
        这条钉住「环境变量缺席时照样能发」——探针：把这条回退删掉就必须变红。"""
        monkeypatch.delenv("BIGA_FEISHU_OWNER_ID", raising=False)
        r = _FakeRunner(config_stdout='[\n  "ou_from_config"\n]')
        FeishuDeliverer(biga_bin="bin/biga", runner=r).deliver(
            event_type="card_completed", aggregate="D1",
            payload={"decision_id": "D1", "status": "BUY"})
        assert r.calls[0][1:] == ["config", "get", "channels.feishu.allowFrom"], \
            "收件人要向网关要，路径与 owner 白名单同源"
        send = r.calls[-1]
        assert send[send.index("--target") + 1] == "ou_from_config"

    def test_环境变量优先于live配置(self, monkeypatch):
        """环境变量降级成逃生口，但仍然压过配置——排查时能临时改投给自己。"""
        monkeypatch.setenv("BIGA_FEISHU_OWNER_ID", "ou_env")
        r = _FakeRunner(config_stdout='["ou_from_config"]')
        FeishuDeliverer(runner=r).deliver(
            event_type="card_completed", aggregate="D1",
            payload={"decision_id": "D1", "status": "BUY"})
        assert not any("config" in c for c in r.calls), "有环境变量就别多问一次网关"
        assert r.calls[0][r.calls[0].index("--target") + 1] == "ou_env"

    def test_收件人只解析一次_多条通知不重复问网关(self, monkeypatch):
        monkeypatch.delenv("BIGA_FEISHU_OWNER_ID", raising=False)
        r = _FakeRunner(config_stdout='["ou_from_config"]')
        d = FeishuDeliverer(biga_bin="bin/biga", runner=r)
        for i in range(3):
            d.deliver(event_type="card_completed", aggregate=f"D{i}",
                      payload={"decision_id": f"D{i}", "status": "BUY"})
        assert sum(1 for c in r.calls if "config" in c) == 1

    @pytest.mark.parametrize("stdout,rc", [
        ("not json", 0),          # 配置读回来不是 JSON
        ("[]", 0),                # 白名单是空的
        ("null", 0),              # 这个键压根没配
        ('["ou_x"]', 1),          # bin/biga 自己失败了
    ])
    def test_配置读不到就报错_不静默发给空目标(self, monkeypatch, stdout, rc):
        """🔴 R-3：算不出来必须说算不出来。绝不能把 --target 传成空字符串
        然后让 bin/biga 去报一个八竿子打不着的错。"""
        monkeypatch.delenv("BIGA_FEISHU_OWNER_ID", raising=False)
        r = _FakeRunner(config_stdout=stdout, config_returncode=rc)
        with pytest.raises(FeishuError, match="缺收件人"):
            FeishuDeliverer(biga_bin="bin/biga", runner=r).deliver(
                event_type="card_completed", aggregate="D1", payload={})
        assert not any("message" in c for c in r.calls), "没收件人就不该发出任何消息"

    def test_收件人从环境变量读(self, monkeypatch):
        monkeypatch.setenv("BIGA_FEISHU_OWNER_ID", "ou_env")
        r = _FakeRunner()
        FeishuDeliverer(runner=r).deliver(
            event_type="card_completed", aggregate="D1",
            payload={"decision_id": "D1", "status": "BUY"})
        cmd = r.calls[0]
        assert cmd[cmd.index("--target") + 1] == "ou_env"

    def test_biga_bin也可以从环境变量读(self, monkeypatch):
        """R-1 同一 idiom：`BIGA` env 覆盖只为测试，生产走默认的
        ~/.openclaw-biga/bin/biga（与 apply_config.py 的 _BIGA 完全一致）。"""
        monkeypatch.setenv("BIGA", "/tmp/fake-biga")
        r = _FakeRunner()
        FeishuDeliverer(receive_id="ou_x", runner=r).deliver(
            event_type="card_completed", aggregate="D1",
            payload={"decision_id": "D1", "status": "BUY"})
        assert r.calls[0][0] == "/tmp/fake-biga"

    def test_channel名是feishu(self):
        assert FeishuDeliverer(receive_id="c").channel == "feishu"


class TestRender:

    @pytest.mark.parametrize("event,payload,must", [
        ("card_completed", {"decision_id": "D1", "status": "WAIT", "headline": "h"}, ["已出卡", "WAIT"]),
        ("card_unknown", {"decision_id": "D1", "status": "WAIT", "missing_count": 3}, ["缺失", "3"]),
        ("risk_block", {"decision_id": "D1", "status": "AVOID"}, ["否决", "AVOID"]),
        ("run_failed", {"decision_id": "D1", "run_id": "r-1", "reason": "预算耗尽"}, ["失败", "r-1", "预算耗尽"]),
    ])
    def test_四类事件各渲染成一句人话(self, event, payload, must):
        r = _FakeRunner()
        _dev(r).deliver(event_type=event, aggregate=payload.get("run_id", "D1"), payload=payload)
        cmd = r.calls[-1]
        text = cmd[cmd.index("--message") + 1]
        for frag in must:
            assert frag in text, f"{event} 的消息里应有 {frag!r}：{text!r}"
