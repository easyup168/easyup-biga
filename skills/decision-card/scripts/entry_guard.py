"""这次调用是**人**发起的，还是从一个 agent 会话里发起的。

为什么需要它
------------
2026-09-21 21:03 的递归事故（`architecture.md` §9 **L-14**）：出卡入口做的事
是「把编排提示词喂给 `main`」，而 `main` 的角色契约里当时写着「要出卡就跑这条入口」
⇒ 无条件无限递归，187 个会话。

事故当天的两道修复是**契约文字**（改写 `AGENTS.md`）加**单实例锁**。
外部评审指出这还不够，说得对：

> AGENTS.md = Intent / Behavior Guidance
> Code Guard = Safety Invariant
> 两者职责必须分开。

锁只保证「照抄了也只死一次」——它拦的是**并发**，不是**越权**。
一次串行的递归（上一层退出后下一层才起）锁是拦不住的。

⇒ 这里把「谁有权启动顶层流水线」变成一条**代码判据**：

    顶层入口只能由 人 / CLI / cron / 外部 Trigger 启动。
    **agent 会话不得启动它。**

两个信号，为什么都要
--------------------
============  ==================================  ==============================
信号          判据                                 它单独漏掉什么
============  ==================================  ==============================
进程血缘      祖先里有 OpenClaw 运行时              `nohup cmd &` 会被 reparent
                                                   到 `systemd --user`，血缘断掉
                                                   （事故里 195 次有 15 次是它）
环境变量      `OPENCLAW_SERVICE_*` 在 env 里         网关如果是手工 `nohup` 起的，
                                                   这些变量压根不存在
============  ==================================  ==============================

实测对照（2026-09-21，网关走 systemd 单元）::

    网关进程 env   OPENCLAW_SERVICE_KIND / SERVICE_MARKER / SYSTEMD_UNIT / …
    人类 shell     **一个都没有**

⚠️ 不要用 `OPENCLAW_PREPEND_PATH`。它确实是 exec 工具注入的，但那条命令
   **第一件事就是 `unset` 它** —— 等 `bin/biga-card` 跑起来时它已经没了。

三态，以及为什么 UNKNOWN 放行
-----------------------------
红线 R-3 说 `UNKNOWN ≠ PASS`。这里的处理是有意的例外，理由要写清楚：

* 这不是**验证工具**，是**入口**。判不了就拒绝 ⇒ 人也用不了这个系统了。
* 真正兜底的是另外两道：单实例锁（并发）与预算闸门（成本）。
  这一道只负责把「越权」这一类挡掉。
* 所以 `UNKNOWN` 放行，但**必须把依据打出来** ——
  静默放行才是 R-3 要防的东西。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：判断本次调用的发起方类别
- **不覆盖**：要不要拒绝（那是调用方的策略）、并发（锁管）、成本（闸门管）
"""

from __future__ import annotations

import os
import pathlib

__all__ = ["AGENT", "HUMAN", "UNKNOWN", "RUNTIME_MARK", "SERVICE_ENV",
           "classify_caller"]

AGENT = "AGENT"
HUMAN = "HUMAN"
UNKNOWN = "UNKNOWN"

#: 祖先 cmdline 里出现它 ⇒ 那一层是 OpenClaw 运行时。
#: 判据取**路径**而不是进程名：进程名是 `node`，一万个东西都叫 node。
RUNTIME_MARK = "openclaw-biga/runtime"

#: 只要有一个在 env 里，就说明本进程继承自网关服务。
#: ⚠️ 不含凭据类变量（`OPENCLAW_GATEWAY_TOKEN` 等）——
#:    判据不该依赖一个**可能被有意清掉**的敏感值。
SERVICE_ENV = ("OPENCLAW_SERVICE_KIND", "OPENCLAW_SERVICE_MARKER",
               "OPENCLAW_SYSTEMD_UNIT")


def _cmdline(proc: pathlib.Path, pid: int) -> str:
    try:
        return (proc / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", "replace")
    except OSError:
        return ""


def _ppid(proc: pathlib.Path, pid: int) -> int | None:
    """从 `/proc/<pid>/status` 读父进程号。

    ⚠️ 用 `status` 而不是 `stat`：`stat` 的第二个字段是 comm，
       里面可以含空格和括号，按空格切会错位。这类解析 bug 不报错，
       只是给出**另一个合法的 pid** —— 然后血缘就走到别人家去了。
    """
    try:
        for ln in (proc / str(pid) / "status").read_text().splitlines():
            if ln.startswith("PPid:"):
                return int(ln.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def classify_caller(*, pid: int | None = None,
                    environ: dict[str, str] | None = None,
                    proc: pathlib.Path | str = "/proc",
                    max_depth: int = 40) -> tuple[str, str]:
    """返回 ``(AGENT|HUMAN|UNKNOWN, 依据)``。依据是给人看的，必须打出来。"""
    env = os.environ if environ is None else environ
    proc = pathlib.Path(proc)

    hit = [k for k in SERVICE_ENV if env.get(k)]
    if hit:
        return AGENT, f"环境变量 {', '.join(hit)} 存在 ⇒ 继承自网关服务"

    start = os.getpid() if pid is None else pid
    if not (proc / str(start)).is_dir():
        return UNKNOWN, f"读不到 {proc}/{start} ⇒ 血缘无从追溯"

    cur, seen = start, 0
    chain: list[str] = []
    while cur and cur > 1 and seen < max_depth:
        cmd = _cmdline(proc, cur)
        if RUNTIME_MARK in cmd:
            chain.append(f"pid {cur}")
            return AGENT, ("进程血缘里有 OpenClaw 运行时（" + " ← ".join(chain)
                           + "）⇒ 这是 agent 会话里的 exec")
        chain.append(f"pid {cur}")
        nxt = _ppid(proc, cur)
        if nxt is None:
            return UNKNOWN, f"pid {cur} 的父进程读不到 ⇒ 血缘断了"
        cur, seen = nxt, seen + 1

    if seen >= max_depth:
        return UNKNOWN, f"血缘超过 {max_depth} 层还没到 init ⇒ 不再往上找"
    return HUMAN, "血缘里没有运行时，env 里也没有服务标记 ⇒ 人或 cron 发起"
