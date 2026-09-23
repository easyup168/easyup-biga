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

🔴 凭据与密钥纪律（公开仓库）
------------------------------
仓库里**一个凭据都不落**。收件人 `open_id` 从**环境变量**读
（`BIGA_FEISHU_OWNER_ID`），由运行环境注入——与 CLAUDE.md「凭据记在仓库外」一致
（真实飞书接入 `channels.feishu` 那块本就在仓库外的 `openclaw.json` 里，且这个类
现在完全不碰 appId/appSecret，那两个值只存在于网关自己的配置里，这个脚本从头到尾
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

import os
import pathlib
import subprocess
from typing import Any, Callable

__all__ = ["FeishuDeliverer", "FeishuError"]

#: 一次子进程调用的形状：`(argv) -> CompletedProcess`。抽成类型别名，
#: 让「真 subprocess.run」和「测试假实现」是同一个形状（与 http/fetcher/launcher
#: 那几处依赖注入同一套做法）。
Runner = Callable[[list], "subprocess.CompletedProcess"]


def _run_biga(cmd: list) -> subprocess.CompletedProcess:
    """经 `bin/biga` 跑一条子命令。绝不裸 `openclaw`（R-1，与 apply_config.py 同一 idiom）。"""
    return subprocess.run(cmd, capture_output=True, text=True)


class FeishuError(RuntimeError):
    """投递失败（`bin/biga message send` 非零退出，或缺配置）。

    worker 据此记一条 failed（不重跑决策、不连累其余通知）。
    """


class FeishuDeliverer:
    """把一条通知发成一条飞书文本消息。满足 `notify_worker.Deliverer` 协议。

    走网关自己已认证的飞书通道（`bin/biga message send`），不持有、不需要
    appId/appSecret ——那两个值只存在于网关自己的 `openclaw.json` 里。
    """

    channel = "feishu"

    def __init__(
        self,
        *,
        receive_id: str | None = None,
        biga_bin: str | None = None,
        runner: Runner = _run_biga,
    ) -> None:
        # 🔴 唯一还需要的输入：发给谁。这是个标识符，不是凭据 —— 与判定「谁能给
        #    这个机器人发消息」的 channels.feishu.allowFrom[0] 同源（仓库外，不落库）。
        self._receive_id = receive_id or os.environ.get("BIGA_FEISHU_OWNER_ID", "")
        # 🔴 R-1：一切经 bin/biga，绝不裸 openclaw —— 与 apply_config.py 同一个
        #    _BIGA 取值 idiom（env 覆盖只为测试）。
        self._biga = biga_bin or os.environ.get(
            "BIGA", str(pathlib.Path.home() / ".openclaw-biga" / "bin" / "biga"))
        self._runner = runner

    # ── 配置自检 ──────────────────────────────────────────────────────────
    def _require_config(self) -> None:
        if not self._receive_id:
            # 🔴 报错指路（dev-workflow §8）：缺什么、去哪配，而不是一句裸异常。
            raise FeishuError(
                "飞书投递缺收件人：环境变量 BIGA_FEISHU_OWNER_ID 没设。\n"
                "  它不放仓库（公开仓库纪律）——由运行环境注入。\n"
                "  真实值见仓库外 ~/.openclaw-biga 的飞书接入配置\n"
                "  （channels.feishu.allowFrom[0]，与出卡触发的 owner 白名单同源）；\n"
                "  本地排查可临时 export 后再跑 notify_worker --deliverer feishu。")

    # ── Deliverer 协议 ───────────────────────────────────────────────────
    def deliver(self, *, event_type: str, aggregate: str,
                payload: dict[str, Any]) -> None:
        """把一条通知发成一条飞书文本消息。成功返回 None；任何失败抛 `FeishuError`。

        worker 约定：投递成功返回 None、失败抛异常（据此记一条 status='failed'，
        不连累其余、也不重跑决策）——所以这里所有失败路径都抛，绝不静默吞。
        """
        self._require_config()
        text = self._render(event_type, aggregate, payload)
        cmd = [self._biga, "message", "send", "--channel", "feishu",
               "--target", self._receive_id, "--message", text]
        r = self._runner(cmd)
        if r.returncode != 0:
            raise FeishuError(
                f"飞书发消息失败：bin/biga message send 退出码 {r.returncode}\n"
                f"{(r.stderr or r.stdout or '').strip()}\n"
                f"（event={event_type} aggregate={aggregate}）")

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
