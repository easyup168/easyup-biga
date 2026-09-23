#!/usr/bin/env python3
"""apply_config.py —— 把 deploy/openclaw/ 的配置即代码落到 live profile（批 G-II）。

它把三份声明式配置（`agents.yaml` / `tool-policy.yaml` / `profile.template.json`）
渲染成一个**增量 patch**，经 `bin/biga config patch` 落到 `~/.openclaw-biga` 的
live `openclaw.json`；装服务经 `bin/biga daemon install`。

🔴 撞红线 R-2，机关已在、这里只是走它
--------------------------------------
1. **一切经 `bin/biga`，绝不裸 `openclaw`。** wrapper 强制 `--profile biga` ——
   少一次就写到同机另一套实例的状态目录去了（R-1）。本脚本里没有任何一处直接调
   `openclaw`，也不直接写 `openclaw.json`（那会绕过配置日志/指纹）。
2. **systemd 单元名必须带 `-biga`。** `bin/biga daemon install`（--profile biga）
   自己推导成 `openclaw-gateway-biga.service`；本脚本装服务前**拒绝**任何非 `-biga`
   单元名（`OPENCLAW_SYSTEMD_UNIT` 这个 env 覆盖是唯一能绕过推导的口子，堵上它），
   装后由 `tools/verify/isolation.py::check_namespaces()` 核对（探针 P4）。

🔴 只增量合并自己管的键，绝不覆盖 live 值
------------------------------------------
`config patch` 递归合并：对象并、数组/标量替换、null 删。本脚本的 patch 只含
`agents.entries.<pipeline>.tools.deny` / `commands.text`。它**不碰**飞书 appSecret、
网关鉴权 token、owner 白名单这几个 live-only 的凭据与可识别 id
（patch 里没提到的键原样保留）。⇒ 仓库里一个凭据都不落。

出卡触发不注册任何 MCP server：BigA 的技能一律「SKILL.md + shell 跑脚本」
（main 认出出卡请求 → 跑 skills/card/scripts/inbound.py），与全仓形态一致。

🔴 patch 显式清掉本脚本曾经写过的那个 MCP server（`mcp.servers.biga-card-trigger:
null`），不是只在渲染里"不再提它"——`config patch` 的合并语义是「patch 没提到的键
原样保留」，早期 apply 过旧版 patch 的 live 配置里那个键**不会自己消失**，会一直
指向一个已经从仓库删掉的脚本路径（真复现过：live 上那个键留了一版指向已删文件的
`card_trigger_mcp.py`，且对应的 stdio 子进程还在跑）。只有显式 null 才是「删」，
省略只是「不管」。

🔴 agent 名单从 `_contract` 派生，不手写
----------------------------------------
出卡 roster 的权威源是 `_contract`（裁定 15 / dev-workflow 第五问）。`agents.yaml`
只声明**策略**（非交互流水线禁 ask_user），名单由 `built_pipeline_agents()` 从
`STAGE1_AGENTS + STAGE2_AGENTS + SYNTHESIZER_AGENT` 派生、再按「真有 AGENTS.md 才算
建成」过滤（discipline 在 STAGE2 名单里但没建，裁定 13 —— 过滤掉，不给它写配置）。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

import yaml

_HERE = pathlib.Path(__file__).resolve()
_DEPLOY_ROOT = _HERE.parent
_REPO_ROOT = _DEPLOY_ROOT.parent.parent  # deploy/openclaw/ → repo root

__all__ = [
    "R2Violation", "BIGA_UNIT_SUFFIX", "EXPECTED_UNIT",
    "built_pipeline_agents", "render_patch", "check_r2", "main",
]

#: BigA 网关单元名必须带这个后缀（--profile biga 推导得到）。与 isolation.py 同一常量。
BIGA_UNIT_SUFFIX = "-biga.service"
EXPECTED_UNIT = "openclaw-gateway-biga.service"

#: 一切经它，绝不裸 openclaw（R-1/R-2）。可被 env 覆盖只为测试。
_BIGA = os.environ.get("BIGA", str(pathlib.Path.home() / ".openclaw-biga/bin/biga"))


class R2Violation(RuntimeError):
    """要写一个非 -biga 的 systemd 单元名 —— 会静默顶掉同机另一套实例的生产单元。"""


def built_pipeline_agents(repo_root: pathlib.Path = _REPO_ROOT) -> list[str]:
    """出卡流水线里**已建成**的、被 spawn 的非交互 agent —— 从 `_contract` 派生。

    候选 = STAGE1 + STAGE2 + synthesizer；过滤到真有 `agents/<name>/AGENTS.md` 的那些
    （discipline 在 STAGE2 名单里但故意没建，裁定 13 —— 这里自然被过滤掉）。
    main **不在候选里**（它是 supervisor，不在任何 stage 名单）⇒ 绝不会被禁 ask_user。
    """
    sys.path.insert(0, str(repo_root / "skills"))
    from _contract import STAGE1_AGENTS, STAGE2_AGENTS, SYNTHESIZER_AGENT
    candidates = [*STAGE1_AGENTS, *STAGE2_AGENTS, SYNTHESIZER_AGENT]
    # dict.fromkeys 去重且保序（stage 名单本就互斥，稳妥起见）。
    return [a for a in dict.fromkeys(candidates)
            if (repo_root / "agents" / a / "AGENTS.md").exists()]


def render_patch(
    deploy_root: pathlib.Path = _DEPLOY_ROOT,
    repo_root: pathlib.Path = _REPO_ROOT,
) -> dict:
    """渲染要 patch 进 live 配置的**增量**（纯函数，离线可测）。

    只含本脚本管的键：非交互流水线 agent 的 `tools.deny` + `commands.text` + 显式
    清掉曾经写过的那个 MCP server 注册。**不含**任何凭据/可识别 id（那些 live-only，
    patch 不提及 ⇒ 原样保留）。出卡触发是纯 skill（main 用 shell 跑 inbound.py），
    不再注册任何 MCP server —— `mcp.servers.biga-card-trigger` 显式 null（删），
    不是靠"不再提它"让它自然消失（省略 ≠ 删，见模块 docstring）。
    """
    policy = yaml.safe_load((deploy_root / "agents.yaml").read_text("utf-8"))
    toolpol = yaml.safe_load((deploy_root / "tool-policy.yaml").read_text("utf-8"))

    deny = list(policy["policy"]["non_interactive_pipeline"]["deny"])
    agents = built_pipeline_agents(repo_root)
    entries = {a: {"tools": {"deny": deny}} for a in agents}
    # 🔴 main 绝不进 patch —— 不写它 = 它的 ask_user 原样保留（探针 P5）。
    assert "main" not in entries, "main 不该出现在 tools.deny patch 里"

    return {
        "agents": {"entries": entries},
        "commands": {"text": bool(toolpol["commands"]["text"])},
        # 只删本脚本自己曾经写过的那一个键，不动 mcp.servers 下可能存在的别的条目。
        "mcp": {"servers": {"biga-card-trigger": None}},
    }


def check_r2(environ: dict | None = None) -> str:
    """R-2 关卡：返回将要写的单元名；要写非 -biga 名字就抛 `R2Violation`（fail closed）。

    唯一能绕过 `--profile biga` 推导的是 `OPENCLAW_SYSTEMD_UNIT` env 覆盖 ——
    它一旦从别处漏进 shell（比如复制了生产单元的 env），`daemon install` 就会写到
    同机另一套实例的生产单元上。这里把它挡在装服务之前。
    """
    env = os.environ if environ is None else environ
    leak = (env.get("OPENCLAW_SYSTEMD_UNIT") or "").strip()
    if leak and not (leak.endswith(BIGA_UNIT_SUFFIX) or leak.endswith("-biga")):
        raise R2Violation(
            f"OPENCLAW_SYSTEMD_UNIT={leak!r} 不带 -biga 后缀 —— 拒绝装服务。\n"
            f"  它会覆盖 --profile biga 推导出的 {EXPECTED_UNIT}，写到同机另一套实例的\n"
            f"  生产单元上（静默顶掉，服务照常起、只是指向了另一套）。\n"
            f"  先 `unset OPENCLAW_SYSTEMD_UNIT` 再装。")
    return leak or EXPECTED_UNIT


def _run_biga(args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess:
    """经 bin/biga 跑一条子命令（强制 --profile biga）。绝不裸 openclaw。"""
    cmd = [_BIGA, *args]
    return subprocess.run(cmd, input=input_text, text=True, capture_output=True)


def _apply_patch(patch: dict, *, dry_run: bool) -> int:
    payload = json.dumps(patch, ensure_ascii=False, indent=2)
    args = ["config", "patch", "--stdin"] + (["--dry-run"] if dry_run else [])
    print(f"▸ {'（dry-run）' if dry_run else ''}bin/biga config patch --stdin")
    print(payload)
    r = _run_biga(args, input_text=payload)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    return r.returncode


def _install_daemon(*, dry_run: bool) -> int:
    unit = check_r2()  # R-2：非 -biga 名字在这里就被拒
    print(f"▸ R-2 OK：将写单元 {unit}")
    if dry_run:
        print("  （dry-run，不真装。真装：--install-daemon --apply）")
        return 0
    r = _run_biga(["daemon", "install"])
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    if r.returncode != 0:
        return r.returncode
    # 装后用 isolation.py 核对单元名带 -biga、没顶掉别人（探针 P4 的正向核对）。
    iso = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "tools/verify/isolation.py")],
        text=True, capture_output=True)
    sys.stdout.write(iso.stdout)
    print(f"▸ isolation.py 退出码 {iso.returncode}（0=全绿，1=判不了，2=有 FAIL）")
    return 0 if iso.returncode in (0, 1) else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="把 deploy/openclaw/ 配置即代码落到 live profile（经 bin/biga，R-2 安全）")
    ap.add_argument("--apply", action="store_true",
                    help="真写（默认 dry-run，只渲染 + 校验、不落库）")
    ap.add_argument("--install-daemon", action="store_true",
                    help="装/更新 systemd 服务（走 bin/biga daemon，R-2 守卫）")
    a = ap.parse_args(argv)
    dry = not a.apply

    patch = render_patch()
    rc = _apply_patch(patch, dry_run=dry)
    if rc != 0:
        print(f"🔴 config patch 失败（rc={rc}）", file=sys.stderr)
        return rc
    if a.install_daemon:
        rc = _install_daemon(dry_run=dry)
    if dry:
        print("\n✅ dry-run 完成 —— 未写任何东西。真落地加 --apply。")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
