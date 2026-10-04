#!/usr/bin/env python3
"""FeishuDeliverer —— 把一条外发通知投到飞书（批 G-II，真投递方）。

它实现 `notify_worker.Deliverer` 协议（`channel` + `deliver(...)`），是批 G-I 当初
把投递做成**可替换接口**、而不是写死 `StdoutDeliverer` 的那个「以后要接的真飞书
adapter」。`notify_worker.py --deliverer feishu` 用它把 Card 完成 / UNKNOWN / 风控
否决 / 运行失败四类通知发回飞书。

🔴 落地修正（P6 live 真跑，2026-09-23）：不走独立 HTTP 客户端 + 独立凭据
------------------------------------------------------------------------
最初这个类自己实现了两步飞书 API 调用（取 `tenant_access_token` → 发
`im/v1/messages`），凭据从 `BIGA_FEISHU_APPID`/`APPSECRET`/`OWNER_ID` 三个环境变量
读。P6 真跑第一次触发时，这条路径没有任何东西会给 `notify_worker.py` 注入这三个
环境变量——`notify_worker.py` 至今没有调度方（这条缺口本就已知），意味着也没有
"谁负责在正确环境里跑它"这件事本身也没有答案。

而**网关这一刻已经在用一条真实、已认证的飞书连接**——它刚收到并处理了触发出卡的
那条消息，也用同一条连接把 ACK 转达回去了。独立实现一套 HTTP 客户端 + 独立凭据
是重复发明这条连接已经在做的事，还额外背了一个"这三个环境变量到底谁来设、从哪
读"的悬而未决的问题。

⇒ 改成 shell 一次 `bin/biga message send --channel feishu --target <open_id>
--message <text>`——走网关自己已经认证好的飞书通道，**不需要 appId/appSecret**。
唯一还需要的是「发给谁」（`BIGA_FEISHU_OWNER_ID`，一个标识符，不是凭据），且它
本来就有处可去（`channels.feishu.allowFrom[0]`，与判定「谁能给这个机器人发消息」
的那个值同源）。这与本仓库其余所有技能的形态一致（R-1：一切经 `bin/biga`，绝不
裸 `openclaw`；`apply_config.py`/`inbound.py` 都是同一个 `_run_biga` idiom）。

🔴 收尾修正（2026-09-24）：收件人也改成「问网关」，不再等人注入环境变量
--------------------------------------------------------------------------
上面那段留了半个尾巴：appId/appSecret 从环境变量改成了「问网关」，收件人却还
停在 `BIGA_FEISHU_OWNER_ID` 上——而**同一个理由**在它身上一样成立，当时只是没
一并改掉。代价随后就到了：`notify-worker-biga.service`（批 G-II 之后才装的调度
方）从来没注入过这个变量 ⇒ 每一次投递都栽在 `_require_config()` 上，Card 全部
积压在 `notification_outbox` 里，人在飞书端只看到「没收到」。

⇒ 收件人现在按 显式传值 > `BIGA_FEISHU_OWNER_ID` > `channels.feishu.allowFrom[0]`
  三级解析（`_resolve_receive_id`）。正常情况走第三级——**不需要任何人记得注入
  什么**。环境变量降级成排查/覆盖用的逃生口，不再是唯一来源。

> 通用原则：一个「由运行环境注入」的必填值，如果没有任何代码负责注入它，
> 那它不是配置项，是一颗定时炸弹。要么让程序自己去有权威答案的地方取，
> 要么让装调度方的那段代码负责写进去——不能两边都指望对方。

🔴 凭据与密钥纪律（公开仓库）
------------------------------
仓库里**一个凭据都不落**。收件人 `open_id` 仓库里只有它的**位置**
（`OWNER_CONFIG_PATH`），值在仓库外的 `openclaw.json` 里——与 CLAUDE.md
「凭据记在仓库外」一致（真实飞书接入 `channels.feishu` 那块本就在那里，且这个类
完全不碰 appId/appSecret，那两个值只存在于网关自己的配置里，这个脚本从头到尾
看不到）。

🔴 可测：子进程调用是注入点
----------------------------
`runner` 参数默认真的 `subprocess.run`（真跑 `bin/biga`），测试注入一个假的、只
记录调用参数——于是「发的是不是对的命令、参数里有没有决策号、失败会不会抛」全部
离线可断言，真实飞书投递只在 live 收尾那一次走（探针 P6）。这与 orchestrator 的
`attach=`、SnapshotCoordinator 的 fetcher、`inbound.py` 的 launcher 是同一套依赖
注入做法。

不做什么（分发提示词「不要做」）
--------------------------------
不实现飞书完整功能面 —— 富文本卡片、按钮交互、@、话题回复都不做。一条**文本
消息**把「哪个决策、什么结论、缺没缺、失败没失败」说清楚就够；这一批只需证明
「event → 投递接口 → 飞书 → 送达」这条链闭环。
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
from typing import Any, Callable

__all__ = ["FeishuDeliverer", "FeishuError"]

#: 一次子进程调用的形状：`(argv) -> CompletedProcess`。抽成类型别名，
#: 让「真 subprocess.run」和「测试假实现」是同一个形状（与 http/fetcher/launcher
#: 那几处依赖注入同一套做法）。
Runner = Callable[[list], "subprocess.CompletedProcess"]

#: 收件人在 live 配置里的**位置**（不是值）。值在仓库外的 `openclaw.json` 里，
#: 与判定「谁能给这个机器人发消息」的白名单同源 —— 仓库里只落这条路径字符串，
#: 一个可识别 id 都不落（公开仓库纪律）。
OWNER_CONFIG_PATH = "channels.feishu.allowFrom"


def _run_biga(cmd: list) -> subprocess.CompletedProcess:
    """经 `bin/biga` 跑一条子命令。绝不裸 `openclaw`（R-1，与 apply_config.py 同一 idiom）。"""
    return subprocess.run(cmd, capture_output=True, text=True)


class FeishuError(RuntimeError):
    """投递失败（`bin/biga message send` 非零退出，或缺配置）。

    worker 据此记一条 failed（不重跑决策、不连累其余通知）。

    Attributes:
        retryable: 这类失败下次重试有没有意义（2026-09-24，外部评审 §11）。
            缺收件人这类**配置错误**不可重试——下次还是同一份 live 配置，
            再试还是同样落空，白白耗掉 `notify_worker.MAX_ATTEMPTS`；
            `bin/biga message send` 非零退出可能是网络抖动/飞书临时限流，
            值得重试。
    """

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class FeishuDeliverer:
    """把一条通知发成一条飞书文本消息。满足 `notify_worker.Deliverer` 协议。

    走网关自己已认证的飞书通道（`bin/biga message send`），不持有、不需要
    appId/appSecret ——那两个值只存在于网关自己的 `openclaw.json` 里。
    收件人同理：默认也向网关要（`_resolve_receive_id`），不依赖任何人注入环境变量。
    """

    channel = "feishu"

    def __init__(
        self,
        *,
        receive_id: str | None = None,
        biga_bin: str | None = None,
        runner: Runner = _run_biga,
    ) -> None:
        # 🔴 唯一还需要的输入：发给谁。这是个标识符，不是凭据。显式传值 > 环境变量
        #    > live 配置（见 `_resolve_receive_id`）—— 三级都落空才报错。
        self._explicit_receive_id = receive_id or ""
        self._resolved_receive_id: str | None = None
        # 🔴 R-1：一切经 bin/biga，绝不裸 openclaw —— 与 apply_config.py 同一个
        #    _BIGA 取值 idiom（env 覆盖只为测试）。
        self._biga = biga_bin or os.environ.get(
            "BIGA", str(pathlib.Path.home() / ".openclaw-biga" / "bin" / "biga"))
        self._runner = runner

    # ── 收件人解析 ────────────────────────────────────────────────────────
    def _receive_id_from_config(self) -> str:
        """从 live 配置读收件人（`channels.feishu.allowFrom[0]`）。读不到返回 ""。

        🔴 为什么要有这条回退（2026-09-24，docs/troubleshooting/
        feishu-streaming-card-400.md 的**第二个**故障）：原来只认
        `BIGA_FEISHU_OWNER_ID` 这个环境变量，而 `notify-worker-biga.service`
        从来没有注入过它 ⇒ **每一次**投递都栽在 `_require_config()` 上，
        Card 全部积压在 outbox 里，人在飞书端只看到"没收到"。

        这正是这个类的 docstring 早就写下、却只做了一半的那个修正：appId/appSecret
        当初就是这样从「三个谁也不负责注入的环境变量」改成「问网关自己」的，
        收件人被留在了原地。⇒ 补齐——它本来就有处可去，就是这条路径。

        读不到不抛：「没配飞书」与「配了但读失败」对调用方是同一件事（没人可发），
        统一由 `_require_config()` 报那条指路的错，不在这里分两种口径。
        """
        r = self._runner([self._biga, "config", "get", OWNER_CONFIG_PATH])
        if r.returncode != 0:
            return ""
        try:
            val = json.loads((r.stdout or "").strip() or "null")
        except json.JSONDecodeError:
            return ""
        # allowFrom 正常是数组；容忍写成裸字符串的配置，不为这点差异报错。
        if isinstance(val, str):
            return val.strip()
        if isinstance(val, list) and val and isinstance(val[0], str):
            return val[0].strip()
        return ""

    def _resolve_receive_id(self) -> str:
        """显式传值 > `BIGA_FEISHU_OWNER_ID` > live 配置。解析一次后缓存。

        缓存的理由：一次 worker 运行会投多条通知，没必要为每条都 shell 出去问一遍
        同一个值；而这个值在一次进程生命周期内不会变。
        """
        if self._resolved_receive_id is None:
            self._resolved_receive_id = (
                self._explicit_receive_id
                or os.environ.get("BIGA_FEISHU_OWNER_ID", "").strip()
                or self._receive_id_from_config())
        return self._resolved_receive_id

    # ── 配置自检 ──────────────────────────────────────────────────────────
    def _require_config(self) -> str:
        receive_id = self._resolve_receive_id()
        if not receive_id:
            # 🔴 报错指路（dev-workflow §8）：缺什么、去哪配，而不是一句裸异常。
            raise FeishuError(
                "飞书投递缺收件人：三个来源都没给出值。\n"
                f"  ① 显式 receive_id ② 环境变量 BIGA_FEISHU_OWNER_ID\n"
                f"  ③ live 配置 {OWNER_CONFIG_PATH}[0]（正常情况下走这条）\n"
                "  值不放仓库（公开仓库纪律），它在仓库外的 openclaw.json 里，\n"
                "  与出卡触发的 owner 白名单同源。先查第 ③ 条读不读得到：\n"
                f"    bin/biga config get {OWNER_CONFIG_PATH}\n"
                "  读不到说明飞书压根没接上（网关侧配置缺失），补它，而不是绕过去\n"
                "  export 一个环境变量——那样两处会各有一份收件人，早晚对不上。",
                retryable=False)
        return receive_id

    # ── Deliverer 协议 ───────────────────────────────────────────────────
    def deliver(self, *, event_type: str, aggregate: str,
                payload: dict[str, Any]) -> None:
        """把一条通知发成一条飞书文本消息。成功返回 None；任何失败抛 `FeishuError`。

        worker 约定：投递成功返回 None、失败抛异常（据此记一条 status='failed'，
        不连累其余、也不重跑决策）——所以这里所有失败路径都抛，绝不静默吞。
        """
        receive_id = self._require_config()
        text = self._render(event_type, aggregate, payload)
        cmd = [self._biga, "message", "send", "--channel", "feishu",
               "--target", receive_id, "--message", text]
        r = self._runner(cmd)
        if r.returncode != 0:
            raise FeishuError(
                f"飞书发消息失败：bin/biga message send 退出码 {r.returncode}\n"
                f"{(r.stderr or r.stdout or '').strip()}\n"
                f"（event={event_type} aggregate={aggregate}）",
                retryable=True)

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
