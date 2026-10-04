#!/usr/bin/env python3
"""打印当前 OpenClaw Version / Tool Policy Hash / Agent Config Hash —— H 节 Baseline 冻结用。

为什么是 hash，不是原文抄进文档
--------------------------------
`openclaw.json` 的 `agents` 子树里全是真实的 `$HOME` 绝对路径
（`agentDir`/`workspace`）。这个仓库是 Public 的，公开仓库纪律不允许把
真实家目录路径抄进文档——但「配置有没有漂移」这个判据本身要留住。
⇒ 只记 hash，不记原文；hash 本身不可逆，公开无风险。

为什么先替换 $HOME 再算 hash
----------------------------
换一台机器（不同用户名）跑同一份逻辑等价的配置，`$HOME` 展开出来的
绝对路径会不同，hash 会跟着变——但配置本身没变。`tools/verify/isolation.py`
在记录 systemd unit 路径时已经踩过同一个坑，用 `%h/.openclaw-biga` 这种
可移植写法解决；这里同样把真实 `$HOME` 替换成 `~` 再算，hash 就只反映
配置的**逻辑结构**，不反映**跑在哪台机器上**。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

HOME = pathlib.Path.home()
BIGA = HOME / ".openclaw-biga"
CONFIG_PATH = BIGA / "openclaw.json"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BIGA_CLI = REPO_ROOT / "bin" / "biga"


def portable(obj, *, home: str):
    """递归地把 home 替换成 ~，使结果不依赖具体用户名。"""
    if isinstance(obj, dict):
        return {k: portable(v, home=home) for k, v in obj.items()}
    if isinstance(obj, list):
        return [portable(v, home=home) for v in obj]
    if isinstance(obj, str):
        return obj.replace(home, "~")
    return obj


def canonical_hash(subtree, *, home: str) -> str:
    """规范 JSON（排序键、无多余空白）后取 sha256 —— 同一逻辑配置永远同一个 hash。"""
    blob = json.dumps(
        portable(subtree, home=home), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def openclaw_version(biga_cli: pathlib.Path) -> str:
    result = subprocess.run(
        [str(biga_cli), "--version"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def main() -> int:
    if not CONFIG_PATH.exists():
        print(f"🔴 找不到 {CONFIG_PATH} —— 这台机器上还没装 BigA profile", file=sys.stderr)
        return 1
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    home = str(HOME)

    print(f"OpenClaw Version   : {openclaw_version(BIGA_CLI)}")
    print(f"Tool Policy Hash   : sha256:{canonical_hash(config['tools'], home=home)}")
    print(f"Agent Config Hash  : sha256:{canonical_hash(config['agents'], home=home)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
