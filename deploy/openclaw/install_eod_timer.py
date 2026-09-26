#!/usr/bin/env python3
"""install_eod_timer.py —— 装 `eod-daily-bars-biga.timer`，每个交易日收盘后跑
`bin/biga-data run-eod-bundle`。

外部 P3-4 实现包里带了一份 systemd 单元，**三处都不对**，一处比一处安静：

1. `WorkingDirectory=%h/easyup-biga` —— 路径根本不存在，装上起不来（最响）
2. `ExecStart=… python -m …` —— 本机只有 `python3`，没有 `python`
3. 🔴 单元名是 `biga-eod-daily-bars`（`biga-` **前缀**）而不是 `-biga` 后缀 ——
   `isolation.py::check_namespaces()` 的判据是「引用 BigA 路径的单元名以
   `-biga.service/.timer` 结尾」。前缀式的名字它**根本不会把它算成 BigA 的单元**，
   于是那条守卫对它一声不吭。**比报红更糟**：报红会被修，静默漏检不会。

⇒ 这三处都没合，单元与本脚本按仓库既有形制重写。

本文件与 `install_notify_timer.py` / `install_reap_timer.py` 几乎同构 ——
那两个的模块头已经说明这种对称是**有意的**：三个都是「BigA 自己的 systemd
定时器，跟 OpenClaw 的 profile/gateway 无关」，适用同一条 R-1 例外理由
（不经过 `bin/biga`）与同一条 R-2 判据（单元名带 `-biga` 后缀）。

⚠️ 定时器**不判交易日**，周末/节假日照跑。理由见 `.timer` 文件里的注释：
把交易日判断塞进 timer 等于日历口径多一份实现（L-3），而 systemd 的
`OnCalendar` 根本表达不了 A 股的调休。跑空的那天由 `run-eod-bundle`
自己 fail-closed（取不到当日 universe / 取不到行情 ⇒ 不发布快照）。

用法
----
    python3 deploy/openclaw/install_eod_timer.py            # dry-run：只打印会做什么
    python3 deploy/openclaw/install_eod_timer.py --apply    # 真装 + enable --now
    python3 deploy/openclaw/install_eod_timer.py --apply --uninstall  # 卸载
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
from typing import Callable

__all__ = ["UNIT_SERVICE", "UNIT_TIMER", "R2Violation", "Runner",
           "check_r2", "install", "uninstall"]

_HERE = pathlib.Path(__file__).resolve()
_DEPLOY_ROOT = _HERE.parent
_SYSTEMD_USER_DIR = pathlib.Path.home() / ".config" / "systemd" / "user"

#: 与 `tools/verify/isolation.py::BIGA_UNIT_SUFFIX` 同一判据（引用 BigA 路径的
#: 单元必须以它结尾）——这里独立断言一次，装的时候就查，不等隔离自检才发现。
UNIT_SERVICE = "eod-daily-bars-biga.service"
UNIT_TIMER = "eod-daily-bars-biga.timer"


class R2Violation(RuntimeError):
    """要装一个不带 `-biga` 后缀的单元 —— 会被 `isolation.py` 判成"顶掉了别人的"。"""


def check_r2(service_name: str = UNIT_SERVICE, timer_name: str = UNIT_TIMER) -> None:
    """两个单元名都必须带 `-biga` 后缀（fail closed，装之前查）。"""
    bad = [n for n in (service_name, timer_name)
           if not (n.endswith("-biga.service") or n.endswith("-biga.timer"))]
    if bad:
        raise R2Violation(
            f"{bad} 不带 `-biga` 后缀——isolation.py::check_namespaces() 只认\n"
            "  「引用 BigA 路径的单元名以 -biga.service/.timer 结尾」这一条判据，\n"
            "  名字不对会被判成覆写了同机已有实例的单元。")


#: 一次 `systemctl` 调用：`(argv) -> CompletedProcess`。抽成类型别名，与
#: `feishu_deliverer.Runner`/`apply_config._run_biga`/`install_notify_timer.Runner`
#: 同一套依赖注入做法——测试注入一个记录调用、不真跑的假实现，就能断言「装的
#: 是不是对的命令」，不必真的碰这台机器的 systemd 用户实例。
Runner = Callable[[list], subprocess.CompletedProcess]


def _real_run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def install(*, dry_run: bool = True, runner: Runner = _real_run,
            deploy_root: pathlib.Path = _DEPLOY_ROOT,
            systemd_dir: pathlib.Path = _SYSTEMD_USER_DIR) -> int:
    """把两个单元文件拷进 `systemd_dir`，`daemon-reload` + `enable --now` 定时器。

    🔴 只拷文件、reload、enable——不改这两个文件本身的内容。真要改调度间隔，
    改 `deploy/openclaw/eod-daily-bars-biga.timer` 源文件，重新跑一次本脚本。

    Args:
        runner: 真跑 `subprocess.run`；测试传一个只记录调用的假实现（离线可测，
            不碰这台机器真实的 systemd 用户实例）。
        deploy_root / systemd_dir: 源文件目录 / 目标目录，测试指向 tmp_path。
    """
    check_r2()
    if not dry_run:
        systemd_dir.mkdir(parents=True, exist_ok=True)
    for name in (UNIT_SERVICE, UNIT_TIMER):
        src, dst = deploy_root / name, systemd_dir / name
        print(f"▸ {'（dry-run）' if dry_run else ''}cp {src} {dst}")
        if not dry_run:
            shutil.copyfile(src, dst)

    for cmd in (["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", UNIT_TIMER]):
        print(f"▸ {'（dry-run，不真跑）' if dry_run else ''}{' '.join(cmd)}")
        if dry_run:
            continue
        r = runner(cmd)
        if r.returncode != 0:
            print(r.stderr, file=sys.stderr)
            return r.returncode
    if dry_run:
        print("\n✅ dry-run 完成 —— 未写任何东西。真装加 --apply。")
    return 0


def uninstall(*, dry_run: bool = True, runner: Runner = _real_run,
              systemd_dir: pathlib.Path = _SYSTEMD_USER_DIR) -> int:
    """反向操作：stop + disable 定时器，删掉两个单元文件，`daemon-reload`。"""
    check_r2()
    cmd = ["systemctl", "--user", "disable", "--now", UNIT_TIMER]
    print(f"▸ {'（dry-run，不真跑）' if dry_run else ''}{' '.join(cmd)}")
    rc = 0
    if not dry_run:
        # disable 一个本来就没装过的单元会非零退出——卸载路径不因此当失败处理。
        runner(cmd)
    for name in (UNIT_SERVICE, UNIT_TIMER):
        dst = systemd_dir / name
        print(f"▸ {'（dry-run）' if dry_run else ''}rm -f {dst}")
        if not dry_run:
            dst.unlink(missing_ok=True)
    cmd = ["systemctl", "--user", "daemon-reload"]
    print(f"▸ {'（dry-run，不真跑）' if dry_run else ''}{' '.join(cmd)}")
    if not dry_run:
        rc = runner(cmd).returncode
    if dry_run:
        print("\n✅ dry-run 完成 —— 未删任何东西。真卸载加 --apply。")
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="真装（默认 dry-run）")
    ap.add_argument("--uninstall", action="store_true", help="卸载而不是装")
    a = ap.parse_args(argv)
    fn = uninstall if a.uninstall else install
    return fn(dry_run=not a.apply)


if __name__ == "__main__":
    raise SystemExit(main())
