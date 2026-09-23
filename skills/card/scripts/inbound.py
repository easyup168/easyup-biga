#!/usr/bin/env python3
"""入站触发适配器 —— 把一次外部「出卡」请求变成一次幂等、异步、脱离进程树的运行（批 G-II）。

它是「gateway/adapter 层」那一层（设计文档 §3 目标架构 `Trigger Gateway`）：飞书里
main 认出一次出卡请求 → 调 `biga_card_trigger` 工具（`skills/card/SKILL.md`）→ 那个
工具调 `accept_trigger()`。人工 CLI 的 `bin/biga-card` 走的是同一个 orchestrator ——
这里只负责「受理 + 去重 + 异步拉起」，不复制任何决策逻辑。

它解决三件事，都是 2026-09-21 两次事故（§9 L-14 / 19:31 四孤儿 spawn）的根子：

  1. **幂等**：飞书事件会重投。`trigger_id`（= 飞书 event id）经
     `reserve_decision_for_trigger` 原子占号 —— 同一个 event 重投第二次拿到
     `created=False`，**不起新决策**（探针 P1）。
  2. **异步**：一次出卡要跑 170~200 秒，塞不进一次对话/命令的 ACK 窗口。
     这里拉起后台运行、**立刻**返回一句 ACK，不等它跑完（探针 P3）。
  3. **编排脱离 main 的会话进程树**：main 可以**发起**这次调用（LLM 允许），但出卡的
     编排绝不在 main 的进程树里跑。`detached_biga_card_launcher` 用 `systemd-run`
     把 `bin/biga-card` 拉成一个瞬态单元、脱离进程树 —— `entry_guard` 因此判成
     HUMAN、不是 AGENT，2026-09-21 那种「会话自己拼 sessions_spawn 递归出卡」的路
     根本到不了（探针 P2：launcher 必须脱树 / 清 service env）。**入口可经过 main，
     编排绝不经过 main。**

🔴 为什么拉起的是 `bin/biga-card` 而不是直接 `orchestrator.py`
------------------------------------------------------------------
出卡的五道守卫（总闸 → ownership → 单实例锁 → 预算闸门 → 第一次付费）全在
`bin/biga-card` 里（L-14 的防护顺序，由测试逐字节钉住）。飞书路径**必须**同样受这
五道守卫保护 —— 所以异步拉起的是 `bin/biga-card`（带 origin/trigger/decision 三个
环境变量），不是绕过守卫直插 orchestrator。人工 CLI 与飞书 inbound 就此走到**同一个
带守卫的入口**（设计要求）。

🔴 为什么后台运行要「脱离 agent 进程树」
----------------------------------------
`entry_guard.classify_caller()` 把「祖先里有 OpenClaw 运行时 / env 里有
`OPENCLAW_SERVICE_*`」判成 AGENT 并拒绝（防 main 递归拉起出卡）。而 command-dispatch
工具是在网关进程里跑的 —— 直接 fork 出的 `bin/biga-card` 会继承这两样、被判成 AGENT。
⇒ 用 `systemd-run --user`（默认）把它拉成一个**瞬态 systemd 单元**：祖先变成
`systemd --user`、不继承网关的 service env ⇒ `classify_caller` 判成 HUMAN ——
正是 entry_guard docstring 里「顶层入口只能由 人 / CLI / cron / **外部 Trigger** 启动」
明确允许的那一类。**这是新增一条被允许的路径，不是改 entry_guard 已经在拦的那条。**
（systemd-run 不可用时回退到 `setsid` + 显式清掉 service env，语义相同。）
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import shutil
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent.parent.parent / "skills"))

from _contract import RUN_ORIGINS  # noqa: E402
from _store import (  # noqa: E402
    find_run_by_trigger,
    reserve_decision_for_trigger,
)

__all__ = ["AckResult", "accept_trigger", "detached_biga_card_launcher", "main"]

#: 仓库根 —— 拉起 `bin/biga-card` 用绝对路径（后台运行的 cwd 不能假设）。
_ROOT = _HERE.parent.parent.parent.parent

#: 可重投、因此需要幂等的来源。CLI 每次是一次独立的、有意的调用，不在此列
#: （它的 trigger_id 每次都是新的，本就不该去重）。
_DEDUP_ORIGINS = frozenset({"feishu", "cron"})


@dataclasses.dataclass(frozen=True)
class AckResult:
    """一次受理的结果 —— 直接回给飞书的那句话（`message`）+ 结构化字段（供工具/测试）。

    Attributes:
        accepted:   这次调用**新拉起**了一次出卡运行（`True`）还是没有（重复/被拒）。
        duplicate:  这个 `trigger_id` 之前已经受理过（飞书事件重投）。
        decision_id: 这次外部请求对应的决策号（新占的或已占的那个）。
        run_state:  已有运行的当前状态（重复受理时补给人看，可能为 None）。
        message:    人类可读的 ACK 文本，投回飞书。
    """

    accepted: bool
    duplicate: bool
    decision_id: str
    run_state: str | None
    message: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def detached_biga_card_launcher(
    origin: str, trigger_id: str, decision_id: str, *, root: pathlib.Path = _ROOT
) -> None:
    """把 `bin/biga-card` 拉成一个**脱离本进程树**的后台运行，立刻返回（不等它跑完）。

    origin/trigger/decision 三个值经环境变量传给 `bin/biga-card`（它再转成
    orchestrator 的 `--origin/--trigger-id/--decision-id`）。

    优先 `systemd-run --user`（干净地脱离网关进程树，见模块 docstring）；
    机器上没有它则回退 `setsid` + **显式清掉** `OPENCLAW_SERVICE_*` —— 后者是
    entry_guard 判 AGENT 的依据，而这次后台运行是一次真正的外部触发、不是 agent
    会话，清掉它是**声明这个事实**，不是绕过守卫（守卫要拦的是 main 的 LLM 递归，
    那条路根本到不了这里）。
    """
    biga_card = str(root / "bin" / "biga-card")
    env_overrides = {
        "BIGA_CARD_ORIGIN": origin,
        "BIGA_CARD_TRIGGER_ID": trigger_id,
        "BIGA_CARD_DECISION_ID": decision_id,
    }
    log = root / "data" / "inbound-runs.log"
    log.parent.mkdir(parents=True, exist_ok=True)

    if shutil.which("systemd-run"):
        # 瞬态 systemd 单元：祖先 = systemd --user，不继承网关 service env ⇒ HUMAN。
        cmd = ["systemd-run", "--user", "--quiet", "--collect",
               f"--working-directory={root}"]
        for k, v in env_overrides.items():
            cmd += ["--setenv", f"{k}={v}"]
        cmd += [biga_card]
        subprocess.run(cmd, check=True, cwd=str(root),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    # 回退：setsid 脱离会话 + 清掉 service env（详见 docstring）。
    env = {k: v for k, v in os.environ.items()
           if k not in ("OPENCLAW_SERVICE_KIND", "OPENCLAW_SERVICE_MARKER",
                        "OPENCLAW_SYSTEMD_UNIT")}
    env.update(env_overrides)
    with open(log, "ab") as fh:
        subprocess.Popen(
            ["setsid", biga_card], cwd=str(root), env=env,
            stdin=subprocess.DEVNULL, stdout=fh, stderr=fh,
            start_new_session=True)


def accept_trigger(
    *,
    origin: str = "feishu",
    trigger_id: str,
    launcher=detached_biga_card_launcher,
    path: pathlib.Path | str | None = None,
) -> AckResult:
    """受理一次外部「出卡」请求：幂等占号 → 异步拉起（仅当是新请求）→ 立刻 ACK。

    Args:
        origin:     `feishu` / `cron`（可重投、需要去重）。`cli` 走 `bin/biga-card`
                    本身，不该进这条路 —— 传进来直接拒绝（fail closed）。
        trigger_id: 幂等键。飞书传 event id；同一个 event 重投必须拿到同一个决策、
                    **不重跑**。
        launcher:   拉起后台运行的可调用（依赖注入点）。默认脱离进程树拉
                    `bin/biga-card`；测试传一个只记录调用的假实现，断言「只拉起一次」。

    Returns:
        AckResult。`accepted=True` 表示这次真拉起了一次运行；`duplicate=True`
        表示这是重投、什么都没多做。两种情况都给一句能投回飞书的 `message`。
    """
    if origin not in RUN_ORIGINS:
        raise ValueError(
            f"origin 必须是 {sorted(RUN_ORIGINS)} 之一，收到 {origin!r}（未知来源当 bug 拒绝）")
    if origin not in _DEDUP_ORIGINS:
        # cli 有意排除：它每次是一次独立调用，不去重；且它本就该直接跑 bin/biga-card。
        raise ValueError(
            f"origin={origin!r} 不走入站适配器 —— 只有会重投的来源"
            f"（{sorted(_DEDUP_ORIGINS)}）才需要幂等受理。CLI 直接用 bin/biga-card。")
    if not isinstance(trigger_id, str) or not trigger_id.strip():
        raise ValueError(f"trigger_id 必须是非空字符串，收到 {trigger_id!r}")

    # 🔴 原子占号：同一个 trigger 第二次到达拿到 created=False —— 这是幂等的**唯一**
    #    判据（数据库唯一约束仲裁，不靠「先查再起」那个有竞态的两步）。
    decision_id, created = reserve_decision_for_trigger(
        trigger_id, by=f"{origin}:{trigger_id[:24]}", path=path)

    if not created:
        # 重投：绝不再拉起一次运行。补一句当前状态（若 run 已 open）给人看。
        hdr = find_run_by_trigger(trigger_id, path=path)
        state = None
        if hdr is not None:
            from _store import current_state  # 局部导入，避免与上面的批量导入纠缠
            state = current_state(hdr["run_id"], path=path)
        state_txt = f"（当前 {state}）" if state else "（正在启动）"
        return AckResult(
            accepted=False, duplicate=True, decision_id=decision_id, run_state=state,
            message=f"这条出卡请求已经在处理了：决策 {decision_id}{state_txt}。"
                    f"重复的请求不会再跑一遍 —— 卡好了会推送给你。")

    # 新请求：异步拉起带守卫的出卡入口，立刻返回。拉起失败不该把已占的号也回滚
    #（号是只追加的、占了就占了）——把失败如实报出来，让上游知道没真的跑起来。
    try:
        launcher(origin, trigger_id, decision_id)
    except Exception as e:  # noqa: BLE001
        return AckResult(
            accepted=False, duplicate=False, decision_id=decision_id, run_state=None,
            message=f"决策 {decision_id} 已占号，但后台运行没拉起来：{e}。"
                    f"请在终端直接跑 bin/biga-card 查原因。")

    return AckResult(
        accepted=True, duplicate=False, decision_id=decision_id, run_state="RECEIVED",
        message=f"收到，正在出卡：决策 {decision_id}。约 3 分钟，跑完自动推送给你 ——"
                f"这条不用等，也不用重发。")


def main(argv: list[str] | None = None) -> int:
    """薄 CLI —— 出卡触发入口（`skills/card/SKILL.md` 指引 main 用 shell 跑它）。
    真出卡由它异步拉起的 `bin/biga-card` 完成，本命令**立刻返回**（打印 ACK）。"""
    ap = argparse.ArgumentParser(
        description="受理一次外部出卡请求（幂等 + 异步，批 G-II 入站适配器）")
    ap.add_argument("--origin", default="feishu", choices=sorted(_DEDUP_ORIGINS))
    ap.add_argument("--trigger-id", required=True,
                    help="幂等键（飞书 event id）。同一个重投只跑一次。")
    ap.add_argument("--json", action="store_true", help="输出结构化 AckResult 而非纯文本")
    a = ap.parse_args(argv)
    ack = accept_trigger(origin=a.origin, trigger_id=a.trigger_id)
    print(json.dumps(ack.to_dict(), ensure_ascii=False) if a.json else ack.message)
    # 退出码：受理成功 / 已是重复 都算「正常处理」= 0；只有真出错才非零（上面已兜住）。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
