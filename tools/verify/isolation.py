#!/usr/bin/env python3
"""隔离自检 —— 不变式 I-1 / I-2 / R-2 的可执行判据。

为什么需要它
------------
`CLAUDE.md` 的不变式表里，I-1 的验证方式一直写着「`tools/verify/isolation.py`（待建）」。
一条**没有可执行判据的不变式**，等于一条约定 —— 而约定只能约束记得它的人。

Phase 2 把 agent 从 1 个加到 6 个，并发也上去了。这是重跑隔离自检的时机
（Phase 2 出口条件 8）。

🔴 前缀陷阱：`~/.openclaw` 是 `~/.openclaw-biga` 的前缀
--------------------------------------------------------
朴素地写 `path.startswith(NEIGHBOUR)` 会把 BigA **自己的每一个文件**
都判成越界 —— 得到一个 100% 误报的检查，几次之后就没人看了。

判据必须是**路径分量级**的包含关系，不是字符串前缀。
本模块用 `pathlib.PurePath.is_relative_to()`，它按分量比较。

> 这条不是假想。写这个文件时第一版就是 `startswith`，
> 自测立刻报出几十条「越界」—— 全是 BigA 自己的文件。

判据
----
==========  ==================================================
I-1         BigA 的进程不得以**写模式**打开 `~/.openclaw/` 下的文件
I-2         邻居的 gateway 进程不受影响（pid 不变）
R-2         nvm 的 bin 目录里没有 openclaw
端口        BigA 与邻居的端口不重叠
==========  ==================================================

**只读。** 本脚本不写任何文件，也不调用任何 openclaw CLI ——
包括邻居的（红线 R-1 的反方向：我们同样不该去碰它）。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

HOME = pathlib.Path.home()
NEIGHBOUR = HOME / ".openclaw"
BIGA = HOME / ".openclaw-biga"
NVM_BIN = HOME / ".nvm/versions/node/v24.21.0/bin"

#: BigA 用的端口。邻居用 18789，间距 1000。
BIGA_PORTS = {19789, 19791}
BIGA_PORT_RANGE = range(19800, 19900)


#: 三态。🔴 `UNKNOWN` 是红线 R-3 的核心：**算不出来必须说算不出来。**
#:
#: 这里原本只有布尔两态，而代码里已经写着「⚠️ 没给 --before，这一项不构成证据」
#: 却紧接着 `res.add(True, ...)` —— 作者知道那不算数，但**数据结构里没有第三态
#: 可以用来表达**，于是「知道」没能变成「报出来」。外部评审 F18。
#:
#: ⚠️ 它取代的旧脚本 `phase1_acceptance.py` 用的是三态 PASS/FAIL/PENDING，
#:    这个文件在这一点上曾经是**退步**。
PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"

_MARK = {PASS: "✅", FAIL: "❌", UNKNOWN: "🔶"}


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, verdict: str, name: str, detail: str = "") -> None:
        assert verdict in _MARK, verdict
        self.rows.append((verdict, name, detail))

    def ok(self, name: str, detail: str = "") -> None:
        self.add(PASS, name, detail)

    def fail(self, name: str, detail: str = "") -> None:
        self.add(FAIL, name, detail)

    def unknown(self, name: str, detail: str = "") -> None:
        """判不了。**不计入通过**，退出码非零。"""
        self.add(UNKNOWN, name, detail)

    def count(self, verdict: str) -> int:
        return sum(1 for v, _, _ in self.rows if v == verdict)

    @property
    def failed(self) -> int:
        return self.count(FAIL)

    @property
    def unknowns(self) -> int:
        return self.count(UNKNOWN)

    def render(self) -> str:
        out = []
        for verdict, name, detail in self.rows:
            out.append(f"{_MARK[verdict]} {name}")
            if detail:
                out.extend(f"     {ln}" for ln in detail.splitlines())
        out.append("")
        if self.failed:
            out.append(f"══ {self.failed} 项未通过"
                       + (f"，{self.unknowns} 项判不了 ══" if self.unknowns else " ══"))
        elif self.unknowns:
            # 🔴 不说「全绿」。判不了就是判不了 —— 这一行是 R-3 在输出上的落点。
            out.append(f"══ {self.unknowns} 项判不了 —— **本次未构成隔离证据** ══")
        else:
            out.append("══ 隔离自检全绿 ══")
        return "\n".join(out)


def _under(path: str, root: pathlib.Path) -> bool:
    """`path` 是否在 `root` 之下 —— **按路径分量比较，不是字符串前缀**。

    见模块 docstring 的「前缀陷阱」。
    """
    try:
        return pathlib.PurePath(path).is_relative_to(root)
    except (ValueError, TypeError):
        return False


#: BigA **运行时**的可执行文件。cmdline 里出现它，才说明这是一个真的
#: BigA 进程，而不是一个恰好 `cd` 到仓库里的终端。
BIGA_RUNTIME = BIGA / "runtime"


def _biga_pids() -> list[tuple[int, str, bool]]:
    """找出属于 BigA 的进程 → `(pid, cmd, 是不是真运行时)`。

    🔴 为什么要分这两类（外部评审 F2）
    ----------------------------------
    原来的判据是「cwd 或 cmdline 落在 BigA 根下」，于是开发者的 VSCode
    server、bash、编辑器会话**全部**被算成「BigA 进程」。实测：真网关只有
    2 个，而这个判据数出 12~14 个。

    后果是这项检查**几乎永远为真** —— 它实际验证的是「我这个终端会话没有
    泄漏 fd 到邻居」，与真实网关是否在跑、是否安全无关。真网关早已崩溃时，
    它照样报「检查了 N 个进程，✅ 通过」。

    ⇒ **扫描范围仍然要宽**（多扫不会漏报），但**能不能构成证据，看有没有
       扫到真运行时**。两件事必须分开，这正是原来那版混为一谈的地方。
    """
    out = []
    for d in pathlib.Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        pid = int(d.name)
        try:
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", "replace").strip()
            cwd = os.readlink(d / "cwd")
        except (OSError, PermissionError):
            continue
        if _under(cwd, BIGA) or str(BIGA) in cmd:
            out.append((pid, cmd[:110] or f"<pid {pid}>", str(BIGA_RUNTIME) in cmd))
    return out


def check_i1(res: Result) -> None:
    """I-1：BigA 的进程不得以写模式打开邻居目录下的文件。"""
    pids = _biga_pids()
    runtime = [(p, c) for p, c, is_rt in pids if is_rt]
    offenders: list[str] = []
    checked = 0
    for pid, cmd, _is_rt in pids:
        fdd = pathlib.Path(f"/proc/{pid}/fd")
        try:
            fds = list(fdd.iterdir())
        except (OSError, PermissionError):
            continue
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            checked += 1
            # 🔴 关键：BigA 自己的路径也以 ~/.openclaw 开头，必须先排除
            if not _under(target, NEIGHBOUR) or _under(target, BIGA):
                continue
            try:
                info = pathlib.Path(f"/proc/{pid}/fdinfo/{fd.name}").read_text()
            except OSError:
                continue
            m = re.search(r"^flags:\s*(\d+)", info, re.M)
            if not m:
                continue
            # O_WRONLY=1, O_RDWR=2（八进制 flags 的低两位）
            if int(m.group(1), 8) & 0o3:
                offenders.append(f"pid {pid} ({cmd}) → {target}")

    scope = (f"（{len(pids)} 个进程，其中真运行时 {len(runtime)} 个"
             f" / {checked} 个 fd）")
    if offenders:
        res.fail(f"I-1 · BigA 写模式打开了邻居文件{scope}", "\n".join(offenders))
    elif runtime:
        res.ok(f"I-1 · BigA 无写模式打开邻居文件{scope}",
               f"邻居目录 {NEIGHBOUR}\n"
               + "\n".join(f"运行时 pid {p}：{c}" for p, c in runtime))
    else:
        # 🔴 找到了一堆进程，但一个真运行时都没有 ⇒ 什么都没验证到。
        #    这与「一个进程都没找到」是同一件事，而原来只防住了后者。
        res.unknown(f"I-1 · 判不了 —— 没有 BigA 运行时在跑{scope}",
                    "扫到的都是恰好 cwd 在仓库下的会话（终端 / 编辑器 / 本脚本自己）。\n"
                    "它们不碰邻居是自然的，证明不了「BigA 运行时是安全的」。\n"
                    f"⇒ 先把 gateway 起起来（`{BIGA}/bin/biga gateway --port 19789`），再跑本检查。")


def check_r2(res: Result) -> None:
    """R-2：nvm 的 bin 目录里绝不能有 openclaw。"""
    bad = NVM_BIN / "openclaw"
    if bad.exists():
        res.fail("R-2 · nvm bin 里出现了 openclaw",
                 f"{bad} 存在 —— 邻居的 systemd 会静默改用 BigA 的 binary")
    elif not NVM_BIN.is_dir():
        # 目录都不在，「里面没有 openclaw」是平凡真
        res.unknown("R-2 · 判不了 —— nvm bin 目录不存在", str(NVM_BIN))
    else:
        res.ok("R-2 · nvm bin 里没有 openclaw", str(NVM_BIN))


def _listening_ports() -> set[int]:
    """当前在 LISTEN 的端口。读 /proc/net/tcp，不起任何连接。"""
    listening: set[int] = set()
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = pathlib.Path(f).read_text().splitlines()[1:]
        except OSError:
            continue
        for ln in lines:
            parts = ln.split()
            if len(parts) < 4 or parts[3] != "0A":     # 0A = LISTEN
                continue
            try:
                listening.add(int(parts[1].split(":")[1], 16))
            except (IndexError, ValueError):
                continue
    return listening


def check_ports(res: Result) -> None:
    """端口不重叠。"""
    listening = _listening_ports()
    ours = {p for p in listening if p in BIGA_PORTS or p in BIGA_PORT_RANGE}
    theirs = {p for p in listening if 18000 <= p < 19000}
    clash = ours & theirs
    detail = (f"BigA 在听 {sorted(ours) or '（无）'}；"
              f"18xxx 段在听 {sorted(theirs) or '（无）'}")
    if clash:
        res.fail("端口 · BigA 与邻居重叠", detail + f"\n🔴 撞了：{sorted(clash)}")
    elif ours and theirs:
        res.ok("端口 · BigA 与邻居不重叠", detail)
    else:
        # 🔴 外部评审 F2：这一档原来根本不存在 —— 两边都没在听时
        #    `clash` 是空集，`not clash` 为真，于是稳定报 ✅。
        #    「没人在监听」不是「不冲突」，是**还没到能判断的时候**。
        #    紧挨着的 I-1 至少防住了「0 个进程」，端口检查连这个都没防。
        res.unknown("端口 · 判不了 —— 至少一边没在监听", detail + "\n"
                    "空集不冲突是平凡成立的，证明不了端口分配是对的。\n"
                    "⇒ 两边 gateway 都起着的时候再跑。")


def check_i2(res: Result, before: str | None) -> None:
    """I-2：邻居的状态库没有被我们改动。

    ⚠️ **这条只有配合 `--before` 才有意义。** 单看 mtime 证明不了任何事 ——
    邻居自己也在跑，它的库本来就会变。判据是「**在 BigA 做了一件事的前后**，
    它没有变」。
    """
    db = NEIGHBOUR / "state" / "openclaw.sqlite"
    if not db.exists():
        res.unknown("I-2 · 判不了 —— 邻居状态库不存在",
                    f"{db}\n没有可比对的对象，跳过不等于通过。")
        return
    now = str(db.stat().st_mtime_ns)
    if before is None:
        # 这行注释原来就写着「不构成证据」，而下一行是 add(True) —— 见 F18。
        res.unknown("I-2 · 判不了 —— 没给 --before",
                    f"当前 mtime_ns={now}\n"
                    f"单看 mtime 证明不了任何事：邻居自己也在写它。\n"
                    f"   正确用法：先记下这个值，做完 BigA 的操作再用 --before 比对")
        return
    if now == before:
        res.ok("I-2 · 邻居状态库在 BigA 操作前后未变", f"before={before}\nafter ={now}")
    else:
        res.fail("I-2 · 邻居状态库变了",
                 f"before={before}\nafter ={now}\n"
                 "🔴 要么是我们写的，要么是邻居自己在跑 —— 必须查清楚是哪一种")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="BigA 隔离自检（只读）")
    ap.add_argument("--before", help="之前记录的邻居状态库 mtime_ns，用于 I-2 比对")
    ap.add_argument("--print-mtime", action="store_true",
                    help="只打印邻居状态库的 mtime_ns 然后退出（给 --before 用）")
    a = ap.parse_args(argv)

    db = NEIGHBOUR / "state" / "openclaw.sqlite"
    if a.print_mtime:
        print(db.stat().st_mtime_ns if db.exists() else "")
        return 0

    res = Result()
    check_i1(res)
    check_i2(res, a.before)
    check_r2(res)
    check_ports(res)
    print(res.render())
    # 🔴 R-3：`UNKNOWN` ≠ `PASS` ⇒ 判不了也必须是非零退出码，
    #    否则「跑完没报错」这个最常用的读法会把它读成通过。
    #    2 与 1 分开，是为了让调用方能区分「坏了」和「没验到」。
    if res.failed:
        return 1
    return 2 if res.unknowns else 0


if __name__ == "__main__":
    raise SystemExit(main())
