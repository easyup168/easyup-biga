#!/usr/bin/env python3
"""install_notify_timer.py —— 装 `notify-worker-biga.timer`，定期跑 `bin/biga-notify`。

对齐 `docs/external/2026-09-23-biga-minimal-feishu-design.md` §6/§13：
cron/systemd 该有一个入口定期把 `notification_outbox` 里积压的通知推给飞书。
`notify_worker.py`（批 G-I）早就能干这件事，缺的只是「谁、多久调一次它」——
这个缺口在本仓库 TODO 里挂了很久（"notify_worker.py 仍然没有调度方"）。

🔴 不经过 `bin/biga`（R-1 例外，理由写清楚）
------------------------------------------------
`bin/biga` 只转发真实的 `openclaw` 子命令（见 `bin/biga` 自己的注释）。装一个
纯 BigA 自己的 systemd 定时器，跟 OpenClaw 的 profile/gateway 完全无关——不存在
一个"openclaw notify-timer install"子命令可以转发给。R-1 要防的是"漏打
--profile、读写错状态目录"，而这里既不读也不写 OpenClaw 的任何状态，
直接用 `systemctl --user` 是唯一路径,不是绕过 R-1。

🔴 R-2：单元名硬编码，不走 profile 推导
----------------------------------------
网关那个单元（`openclaw-gateway-biga.service`）的名字是 OpenClaw 按 `--profile`
**推导**出来的，`OPENCLAW_SYSTEMD_UNIT` 这个 env 能覆盖那个推导 ⇒ `apply_config.py`
的 `check_r2()` 专门防这个漏洞。这里完全是另一种情况——单元名是这个脚本自己
**写死**的常量（`UNIT_SERVICE`/`UNIT_TIMER`），没有推导、没有能覆盖它的 env 变量，
风险面天然更小。但仍然显式断言一次后缀（`tools/verify/isolation.py::
check_namespaces()` 的判据是"引用 BigA 路径的单元必须以 `-biga.service` 结尾"，
这里装前自己先查一遍，不等它在下一次隔离自检时才发现）。

用法
----
    python3 deploy/openclaw/install_notify_timer.py            # dry-run：只打印会做什么
    python3 deploy/openclaw/install_notify_timer.py --apply    # 真装 + enable --now
    python3 deploy/openclaw/install_notify_timer.py --apply --uninstall  # 卸载
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
UNIT_SERVICE = "notify-worker-biga.service"
UNIT_TIMER = "notify-worker-biga.timer"


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
#: `feishu_deliverer.Runner`/`apply_config._run_biga` 同一套依赖注入做法——
#: 测试注入一个记录调用、不真跑的假实现，就能断言「装的是不是对的命令」，
#: 不必真的碰这台机器的 systemd 用户实例。
Runner = Callable[[list], subprocess.CompletedProcess]


def _real_run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def install(*, dry_run: bool = True, runner: Runner = _real_run,
            deploy_root: pathlib.Path = _DEPLOY_ROOT,
            systemd_dir: pathlib.Path = _SYSTEMD_USER_DIR) -> int:
    """把两个单元文件拷进 `systemd_dir`，`daemon-reload` + `enable --now` 定时器。

    🔴 只拷文件、reload、enable——不改这两个文件本身的内容。真要改调度间隔，
    改 `deploy/openclaw/notify-worker-biga.timer` 源文件，重新跑一次本脚本。

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
