"""非交互出卡的 `ask_user` 死锁看门狗。

为什么需要它
------------
`$BIGA agent` 是**非交互**调用。Supervisor 一旦调 `ask_user`，没有任何人能回答：

    stalled session: sessionKey=agent:main:card-214144 state=processing
      age=646s reason=blocked_tool_call activeTool=ask_user recovery=none

`recovery=none` —— **运行时自己不会救它**。这不是偶发 timeout，
是确定性死锁：只要非交互执行触发 `ask_user`，结果就一定是这样。

目标不是「让 `ask_user` 永远不被调用」
--------------------------------------
外部评审的判断是对的，也和实际探查一致：这个运行时**没有**按次禁用工具的能力

* `agent` 子命令没有任何 tool 参数
* `agent exec`（隔离 headless 模式）spawn 不了 specialist，出卡需要网关
* 配置级 `tools.deny` 的粒度是**按 agent** ⇒ 会把交互式 `main`
  （飞书、TUI）的这个能力一起砍掉

硬改运行时内部机制的成本和风险都不值得。⇒ 目标降级为：

> 不要求 `ask_user` 不出现，而是保证它**即使出现也只能造成有限、
> 可检测、可审计的失败**，不能造成永久死锁。

🔴 数据来源只有一个，而且是日志
-------------------------------
找过三处，只有第三处真的带这个信息：

==============================  ========================================
`session_state_events`（state 库）  只有 `created` / `child_spawned` /
                                    `run_completed`，**没有** blocked 状态
`sessions list --json`（网关 RPC）  22 个字段里没有 `state` / `activeTool`
`journalctl` 的 `[diagnostic]` 行   ✅ 唯一带 `reason` / `activeTool` 的
==============================  ========================================

⚠️ 所以这里是**解析日志**，明知它比结构化接口脆弱。
   代价是可控的：格式变了 ⇒ 解析不出来 ⇒ 看门狗不开火 ⇒
   退化到硬超时兜底（那一道不依赖日志）。**不会误杀**。

🔴 而正因为脆弱，判据必须对着**真实那一行**测，不能对着我以为的格式测。
   本项目刚在这件事上栽过：检测器查 `tool_use` / `input`，
   真实 transcript 是 `toolCall` / `arguments` ⇒ 扫出 0 条，
   于是推出「递归假设不成立」这个错误结论（§9 L-13）。
   ⇒ `tests/fixtures/stalled-session.log` 是**实际事故那一行**，只抹了 id。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：从 journal 认出「我这次运行卡在非交互 `ask_user` 上」
- **不覆盖**：取消动作（调用方做，见 `bin/biga-card`）、别的阻塞工具
  （硬超时兜着）、交互式会话（那里 `ask_user` 是正常能力）
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

__all__ = ["UNIT", "ASK_USER_TOOL", "DEFAULT_BLOCKED_SEC", "Stalled",
           "parse_stalled", "find_blocked"]

#: BigA 自己的网关单元。🔴 写死成带 `-biga` 后缀的名字 ——
#: 红线 R-2：默认名 `openclaw-gateway.service` 属于同机另一套实例。
UNIT = "openclaw-gateway-biga.service"

ASK_USER_TOOL = "ask_user"

#: 阻塞多久算死锁。
#: 🔴 取 30s 而不是几分钟：非交互会话里**根本不存在**一个能回答的人，
#:    所以没有「再等等也许他就答了」这种可能性。
#:    留一点余量只是为了避开「工具刚启动、状态还没稳定」那一瞬。
DEFAULT_BLOCKED_SEC = 30

#: 真实那一行的形状（2026-09-21 实测，见模块 docstring）::
#:
#:     [diagnostic] stalled session: sessionId=… sessionKey=agent:main:card-214144
#:       state=processing age=646s queueDepth=0 reason=blocked_tool_call
#:       classification=blocked_tool_call activeWorkKind=tool_call
#:       lastProgress=tool:ask_user:started lastProgressAge=121s
#:       activeTool=ask_user activeToolCallId=… activeToolAge=121s recovery=none
#:
#: ⚠️ 按 `key=value` 逐个抓，不按字段顺序 —— 顺序变了不该让解析失败。
_KV = re.compile(r"(\w+)=([^\s]+)")


@dataclass(frozen=True)
class Stalled:
    """一条 `stalled session` 诊断行里我们关心的部分。"""

    session_key: str
    state: str
    reason: str
    active_tool: str | None
    active_tool_age_sec: int
    recovery: str

    @property
    def is_ask_user_deadlock(self) -> bool:
        """🔴 三个条件缺一不可。

        * `reason=blocked_tool_call` —— 慢和卡住不是一回事
        * `activeTool=ask_user`      —— 别的工具由硬超时兜，不该在这里误杀
        * `recovery=none`            —— 运行时说它自己不会救；它要是会救就别插手
        """
        return (self.reason == "blocked_tool_call"
                and self.active_tool == ASK_USER_TOOL
                and self.recovery == "none")


def parse_stalled(line: str) -> Stalled | None:
    """解析一行诊断。不是这种行就返回 `None`（不抛异常 —— journal 里全是别的行）。"""
    if "stalled session" not in line:
        return None
    kv = dict(_KV.findall(line))
    key = kv.get("sessionKey")
    if not key:
        return None

    def _sec(name: str) -> int:
        raw = kv.get(name, "")
        m = re.match(r"^(\d+)s?$", raw)
        return int(m.group(1)) if m else 0

    return Stalled(
        session_key=key,
        state=kv.get("state", ""),
        reason=kv.get("reason", ""),
        active_tool=kv.get("activeTool"),
        # 用 `activeToolAge` 而不是 `age`：`age` 是整个会话的年龄，
        # 一次正常出卡跑 3 分钟，拿它判会把正常运行也算成卡住。
        active_tool_age_sec=_sec("activeToolAge"),
        recovery=kv.get("recovery", ""),
    )


def _journal(since: str, unit: str) -> list[str]:
    try:
        out = subprocess.run(
            ["journalctl", "--user", "-u", unit, "--since", since, "--no-pager"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    return (out.stdout or "").splitlines()


def find_blocked(session_key: str, *, since: str = "-10min",
                 threshold_sec: int = DEFAULT_BLOCKED_SEC,
                 unit: str = UNIT,
                 lines: list[str] | None = None) -> Stalled | None:
    """`session_key` 这次运行是不是卡在非交互 `ask_user` 上了。

    Args:
        session_key: 只看**我们自己这一次**。看全局等于替别人做决定 ——
            交互式会话里 `ask_user` 是正常能力，不能连坐。
        lines: 测试注入用；缺省时真的去读 journal。

    Returns:
        判定成立的那一条，否则 `None`。
    """
    rows = _journal(since, unit) if lines is None else lines
    latest: Stalled | None = None
    for ln in rows:
        s = parse_stalled(ln)
        if s is None or s.session_key != session_key:
            continue
        if not s.is_ask_user_deadlock:
            continue
        # journal 每 30s 打一条，取**最新**的（年龄最大）
        if latest is None or s.active_tool_age_sec >= latest.active_tool_age_sec:
            latest = s
    if latest is not None and latest.active_tool_age_sec >= threshold_sec:
        return latest
    return None
