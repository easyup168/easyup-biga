#!/usr/bin/env python3
"""biga_card_trigger —— 飞书 `/card` 命令**直达**的工具，main 全程不参与（批 G-II）。

它是 `skills/card/SKILL.md` 的 `command-dispatch: tool` 指向的那个工具。链路：

    飞书用户发 `/card`（owner 白名单）
      → OpenClaw 命令层识别为 skill 命令、`command-dispatch: tool`
      → **绕过 queue + model**，直接派发到这个 MCP 工具（slash-commands 文档）
      → handle_card_trigger() → inbound.accept_trigger() → 异步拉起 bin/biga-card
      → 立刻回一句 ACK

🔴 **main 的 LLM 在这条链上没有一步。** 派发决定是 OpenClaw 命令层做的（config，
   不是模型）；这个工具是纯 Python。把 main 的 system prompt 整个清空，`/card`
   照样出卡 —— 这正是探针 P2 的反事实检验。与「教 main 认出出卡请求再转发」相反：
   main 压根收不到这条消息（命令消息在命令层就被消费了）。

🔴 幂等键从哪来
----------------
`decision_runs.trigger_id` 要接**真实飞书 event id**（分发提示词硬约束）。命令派发
给工具的入参是 `{command, commandName, skillName}`，OpenClaw 还会带上一层调用上下文。
`_resolve_trigger_id` 从入参/上下文里认这个稳定 id；认不到就**fail-closed 报错**
（不静默编一个 —— 编出来的 id 让「重投去重」这条保证悄悄失效，比报错糟）。
具体从哪个字段取，在 live 收尾（探针 P6）对着真实一条 event 核实后钉定。

分层是有意的：`handle_card_trigger` 是**纯函数**，离线可测（P1/P3 直接喂入参断言
「同一个 event 只受理一次」）；MCP 协议那层薄，只做 stdio 收发。
"""

from __future__ import annotations

import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve()
# inbound.py 在 decision-card/scripts；契约/库在 skills 根。
sys.path.insert(0, str(_HERE.parent.parent.parent / "decision-card" / "scripts"))
sys.path.insert(0, str(_HERE.parent.parent.parent.parent / "skills"))

from inbound import accept_trigger  # noqa: E402

__all__ = ["handle_card_trigger", "resolve_trigger_id", "TriggerIdUnavailable", "build_server"]

#: 飞书 event / 消息 id 可能落在这些键上（命令派发上下文 / MCP 调用参数）。
#: 🔴 live（P6）对着真实一条 event 核实后收敛到确实存在的那个，别留一堆猜的。
_EVENT_ID_KEYS = (
    "trigger_id", "eventId", "event_id", "messageId", "message_id",
    "feishuEventId", "feishu_event_id",
)


class TriggerIdUnavailable(RuntimeError):
    """入参里认不出稳定的飞书 event id —— fail-closed，不编一个。"""


def resolve_trigger_id(arguments: dict) -> str:
    """从命令派发入参里定出幂等键（飞书 event id）。认不到就抛，不静默编。

    在**顶层**和一个可能的 `context`/`meta` 子字典里都找一遍（不同 OpenClaw 版本
    把调用上下文放的位置可能不同）——认到第一个非空的就用。
    """
    pools = [arguments]
    for nest in ("context", "meta", "_meta", "invocation"):
        sub = arguments.get(nest)
        if isinstance(sub, dict):
            pools.append(sub)
    for pool in pools:
        for k in _EVENT_ID_KEYS:
            v = pool.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    raise TriggerIdUnavailable(
        "认不出飞书 event id 做幂等键 —— 入参里没有 "
        f"{list(_EVENT_ID_KEYS)} 中任何一个。\n"
        "  不编一个假的（那会让「同一 event 重投只跑一次」这条保证静默失效）。\n"
        "  live 核实真实 event 的字段名后，补进 _EVENT_ID_KEYS（见 P6）。")


def handle_card_trigger(arguments: dict) -> str:
    """受理一次飞书 `/card`，返回投回飞书的 ACK 文本。**纯函数，离线可测。**

    幂等 + 异步都在 `inbound.accept_trigger` 里：同一个 event id 第二次到达只回
    「已在处理」、不重跑（P1）；受理后立刻返回、不等 170~200s 的出卡（P3）。
    """
    trigger_id = resolve_trigger_id(arguments or {})
    ack = accept_trigger(origin="feishu", trigger_id=trigger_id)
    return ack.message


def build_server():
    """造 FastMCP server，注册 `biga_card_trigger` 工具。（真跑时才 import mcp。）"""
    from mcp.server.fastmcp import FastMCP  # 延迟 import：离线测 handle_ 不需要 mcp

    server = FastMCP("biga-card-trigger")

    @server.tool(
        name="biga_card_trigger",
        description=("触发一次 A 股决策出卡（幂等 + 异步）。飞书 /card 命令直达此工具，"
                     "不经过 main 的判断。重复的同一请求不会重复出卡。"))
    def biga_card_trigger(command: str = "", commandName: str = "/card",
                          skillName: str = "card", **context) -> str:
        # command-arg-mode: raw ⇒ OpenClaw 传 {command, commandName, skillName}，
        # 其余调用上下文（含 event id）落在 **context。全部并起来给解析器认幂等键。
        args = {"command": command, "commandName": commandName,
                "skillName": skillName, **context}
        try:
            return handle_card_trigger(args)
        except TriggerIdUnavailable as e:
            # 报回飞书一句能自解释的话，而不是让工具异常冒泡成一条运行时错误。
            return f"没能受理出卡请求：{e}"

    return server


if __name__ == "__main__":
    build_server().run()
