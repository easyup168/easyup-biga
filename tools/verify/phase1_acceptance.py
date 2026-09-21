#!/usr/bin/env python3
"""Phase 1 验收自检 —— 8 条标准逐条机器核对。

为什么写成脚本而不是清单：
文档里的关键事实会漂移（写着「8 条全过」，而其中两条其实从没跑过）。
凡是能由程序判定的，就不要手抄。

🔴 本脚本自身遵守 UNKNOWN ≠ PASS：
   没法判定的项报 `PENDING`，**绝不因为「看起来没问题」就算 PASS**。
   退出码只在**全部 PASS** 时为 0。

用法::

    python3 tools/verify/phase1_acceptance.py                  # 只做静态检查
    python3 tools/verify/phase1_acceptance.py --decision-id BIGA-20260919-001
    python3 tools/verify/phase1_acceptance.py --baseline base.json --save-baseline
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Literal

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "skills"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import isolation  # noqa: E402  —— I-1 的唯一判据，见下方 F19 的注释

Verdict = Literal["PASS", "FAIL", "PENDING"]

_MARK = {"PASS": "✅", "FAIL": "❌", "PENDING": "⏳"}


@dataclass
class Check:
    n: str
    title: str
    verdict: Verdict = "PENDING"
    detail: str = ""
    notes: list[str] = field(default_factory=list)

    def ok(self, detail: str = "") -> None:
        self.verdict, self.detail = "PASS", detail

    def fail(self, detail: str) -> None:
        self.verdict, self.detail = "FAIL", detail

    def pending(self, detail: str) -> None:
        self.verdict, self.detail = "PENDING", detail


# ──────────────────────────────────────────────────────────── 需要跑过一次的项


def _runtime_spawn_records() -> list[dict] | None:
    """读 OpenClaw 运行时自己记的子会话表 —— **BigA 无法伪造的那一份证据**。

    返回 None 表示读不到（状态库不存在 / 表结构变了），
    调用方必须据此报 PENDING 而不是 PASS。
    """
    import sqlite3  # store-exempt: 读的是 OpenClaw 运行时状态库，不是 BigA 事实层；
                    # _store 的单一入口规则是为了「将来切 PG 只改一个文件」，
                    # 而这个库永远不会跟着 BigA 迁移。

    db = pathlib.Path.home() / ".openclaw-biga/state/openclaw.sqlite"
    if not db.exists():
        return None
    try:
        # store-exempt: 同上 —— 外部运行时状态库，只读
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT run_id, child_session_key, controller_session_key, "
            "       requester_session_key, created_at, payload_json "
            "FROM subagent_runs ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def spawn_proof(decision_id: str) -> dict[str, tuple[bool, bool]]:
    """`agent -> (在 agent_runs 里, 在运行时 subagent_runs 里)`。

    🔴 两份记录的性质完全不同，这正是本函数存在的全部理由：

    ======================  ==============================================
    `agent_runs`            **BigA 自己写的**。手工跑一遍 skill 也会写进去
    `subagent_runs`         **OpenClaw 运行时写的**。BigA 业务代码碰不到它
    ======================  ==============================================

    只有第二份能区分「Supervisor 真的 spawn 了」与「有人手工跑了脚本」。

    外部评审 F3：这套核验原来**只认 `"emotion"` 一个字面量**，
    Phase 2 新增的五个 specialist 完全没有对应版本 ——
    也就是说 Phase 2 产出的每一张 Card，「Specialist 真的被调用过」
    这条最硬的约束**事实上没有任何机器在管**。

    ⚠️ 返回 `{}` 表示读不到运行时状态库 ⇒ 调用方必须报 PENDING / UNKNOWN，
       **不是 PASS**（红线 R-3）。
    """
    from _contract import STAGE1_AGENTS, STAGE2_AGENTS
    from _store import list_agent_runs

    ours = {r["agent"] for r in list_agent_runs(decision_id=decision_id)}
    spawns = _runtime_spawn_records()
    if spawns is None:
        return {}

    out = {}
    # 判据取自契约里的 stage 名单，**不是手写的一个名字** ——
    # 名单会随 agent 增加而自己长大，手写的那个不会。
    for agent in list(STAGE1_AGENTS) + list(STAGE2_AGENTS):
        if agent == "discipline":          # 裁定 13：故意不建
            continue
        spawned = any(
            agent in (r.get("payload_json") or "")
            or agent in (r.get("child_session_key") or "")
            for r in spawns)
        out[agent] = (agent in ours, spawned)
    return out


def check_1_spawned(decision_id: str | None) -> Check:
    """Supervisor 真的 spawn 了**每一个** specialist —— 两份独立记录都要有。"""
    c = Check("1", "Supervisor 确实 spawn 了各 Specialist（两份独立记录都要有）")
    if not decision_id:
        return c.pending("未提供 --decision-id") or c

    proof = spawn_proof(decision_id)
    if not proof:
        return c.pending(
            "读不到运行时的 subagent_runs，无法证明任何一个是 Supervisor spawn 的"
        ) or c

    forged = [a for a, (ours, spawned) in proof.items() if ours and not spawned]
    absent = [a for a, (ours, _) in proof.items() if not ours]
    if forged:
        return c.fail(
            f"这些 agent 在 agent_runs 里有行，但运行时 subagent_runs 里没有："
            f"{sorted(forged)} —— 那些行是被直接写入的，"
            "**不是 Supervisor spawn 出来的**") or c
    if absent:
        return c.pending(f"本次决策没有这些 agent 的记录：{sorted(absent)}") or c
    c.ok(f"{len(proof)} 个 agent 两份记录都齐：{sorted(proof)}")
    return c


def check_2_verdict(decision_id: str | None) -> Check:
    c = Check("2", "emotion 返回合法 AgentVerdict，含 ≥1 条带 as_of 的 Evidence")
    if not decision_id:
        return c.pending("未提供 --decision-id") or c
    from _store import load_verdicts

    vs = [v for v in load_verdicts(decision_id) if v.agent == "emotion"]
    if not vs:
        return c.fail("Card 里没有 emotion 的 Verdict") or c
    ev = [e for e in vs[0].evidence if e.as_of and e.as_of.tzinfo]
    if ev:
        c.ok(f"{len(ev)} 条带时区 as_of 的证据，最旧 {vs[0].max_staleness_sec}s")
    else:
        c.fail("没有任何一条带 tzinfo 的 as_of")
    return c


def check_3_card_sections(decision_id: str | None) -> Check:
    c = Check("3", "Decision Card 含 状态 / 证据 / 缺失项 三段")
    if not decision_id:
        return c.pending("未提供 --decision-id") or c
    from _store import load_card

    card = load_card(decision_id)
    if card is None:
        return c.fail(f"decision_records 里没有 {decision_id}") or c
    text = card.render()
    want = {"状态：": "状态", "证据": "证据", "缺失项": "缺失项"}
    miss = [label for token, label in want.items() if token not in text]
    if miss:
        c.fail(f"渲染结果缺少段落: {miss}")
    else:
        c.ok(f"三段齐全（缺失项 {len(card.missing)} 条）")
    return c


def check_4_replay(decision_id: str | None) -> Check:
    c = Check("4", "落库 1 行且 replay 重跑出一致结论")
    if not decision_id:
        return c.pending("未提供 --decision-id") or c
    from _store import connect

    with connect(readonly=True) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM decision_records "
            "WHERE decision_id=? AND replay_of IS NULL", (decision_id,)
        ).fetchone()[0]
    if n != 1:
        return c.fail(f"在线记录 {n} 行（应为 1）") or c

    r = subprocess.run(
        [sys.executable, str(_REPO / "skills/decision-card/scripts/replay.py"),
         decision_id, "--check"],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode == 0:
        c.ok("replay --check 一致")
    else:
        c.fail(f"replay --check 退出码 {r.returncode}: {r.stdout.strip()[:120]}")
    return c


def check_5_fail_closed(run_live: bool) -> Check:
    c = Check("5", "打断数据源 → UNKNOWN + missing 非空（不是 PASS）")
    if not run_live:
        return c.pending("需要 --live（会联网）") or c
    r = subprocess.run(
        [sys.executable, str(_REPO / "skills/emotion-calc/scripts/emotion_calc.py"),
         "--break-source", "limit_up", "--no-store"],
        capture_output=True, text=True, timeout=180,
    )
    try:
        v = json.loads(r.stdout)
    except json.JSONDecodeError:
        return c.fail(f"输出不是 JSON: {r.stderr.strip()[:120]}") or c
    if v["verdict"] == "PASS":
        c.fail("数据源被打断却给出 PASS —— 静默 fail-open")
    elif v["verdict"] == "UNKNOWN" and v["missing"]:
        c.ok(f"verdict=UNKNOWN，missing {len(v['missing'])} 条")
    else:
        c.fail(f"verdict={v['verdict']} missing={len(v['missing'])}")
    return c


def check_8_latency(decision_id: str | None, budget_ms: int = 90_000) -> Check:
    """🔴 读**真实墙钟**，不读 Supervisor 自报的数。

    早先这条查的是 `card.elapsed_ms` —— 那是 Supervisor 自己填进来的数字，
    等于让被考核方自己报成绩。实测它（122.8s）比真实墙钟（215.2s）小得多。

    真相源是 OpenClaw 运行时的 trajectory：每一轮 LLM 的起止时刻。
    """
    c = Check("8", f"单次端到端 < {budget_ms // 1000}s（真实墙钟）")
    if not decision_id:
        return c.pending("未提供 --decision-id") or c
    from datetime import datetime, timedelta

    from _store import load_card
    from _store.runtime import read_turns

    card = load_card(decision_id)
    if card is None:
        return c.fail("Card 不存在") or c
    if card.elapsed_ms <= 0:
        return c.pending(
            "Card 的 elapsed_ms 为 0（Supervisor 没传 --elapsed-ms），"
            "无法精确定界这次决策的时间窗") or c

    probe = read_turns()
    if probe.unavailable:
        return c.pending("运行时数据不完整：" + "；".join(probe.unavailable)) or c

    end = datetime.fromisoformat(card.generated_at)
    start = end - timedelta(milliseconds=card.elapsed_ms)
    # 区间重叠：合成轮的结束时刻必然晚于 Card 的生成时刻
    win = [t for t in probe.turns
           if t.ended_at.astimezone(end.tzinfo) >= start
           and t.started_at.astimezone(end.tzinfo) <= end]
    if not win:
        return c.pending("窗口内没有 LLM 轮次，无法测量") or c

    wall = int((max(t.ended_at for t in win)
                - min(t.started_at for t in win)).total_seconds() * 1000)
    cost = sum(t.cost_usd for t in win)
    detail = (f"{wall / 1000:.1f}s（{len(win)} 轮，${cost:.4f}）"
              f"；Supervisor 自报 {card.elapsed_ms / 1000:.1f}s")
    if wall < budget_ms:
        c.ok(detail)
    else:
        c.fail(f"{detail} —— 超预算 {wall / budget_ms:.1f}×")
        c.notes.append("分解见 tools/verify/latency_report.py")
    return c


# ─────────────────────────────────────────────────────────────── 静态可查的项


def _pid_on_port(port: int) -> str | None:
    r = subprocess.run(["ss", "-lntpH"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if f":{port} " in line:
            m = re.search(r"pid=(\d+)", line)
            if m:
                return m.group(1)
    return None


def _proc_start(pid: str) -> str:
    r = subprocess.run(["ps", "-o", "lstart=", "-p", pid],
                       capture_output=True, text=True)
    return r.stdout.strip()


#: 🔴 这些观测量必须逐位相同。选取判据：**如果邻居被影响过，它一定会变。**
_STABLE_KEYS = ("gateway_pid", "gateway_started", "gateway_listening",
                "config_mtime_ns", "nvm_default")

#: ⚠️ 仅供参考，**不参与相等性比较**。
#:    邻居是一套活着的系统，它自己会不停写自己的状态库 ——
#:    实测它在 BigA 完全静止时也会变。
#:    拿它做长期基线会让这条检查恒红，然后就没人看了。
#:    它只在**短窗口**（跑一条 BigA 命令的前后几秒）里有判别力。
_INFO_KEYS = ("state_mtime_ns",)


def neighbour_state() -> dict:
    """采集邻居的可观测量。

    记**启动时间**而非只记 pid —— 被重启过 pid 可能不变，而 lstart 一定变。
    """
    pid = _pid_on_port(18789)
    home = pathlib.Path.home()

    def mtime(p: pathlib.Path) -> str | None:
        return f"{p.stat().st_mtime_ns}" if p.exists() else None

    return {
        "gateway_pid": pid,
        "gateway_started": _proc_start(pid) if pid else None,
        "gateway_listening": pid is not None,
        "state_mtime_ns": mtime(home / ".openclaw/state/openclaw.sqlite"),
        "config_mtime_ns": mtime(home / ".openclaw/openclaw.json"),
        "nvm_default": (home / ".nvm/alias/default").read_text().strip()
        if (home / ".nvm/alias/default").exists() else None,
        "nvm_openclaw": sorted(
            str(p) for p in (home / ".nvm/versions/node").glob("*/bin/openclaw")
        ),
    }


# 🔴 I-1 的判据**不在这个文件里** —— 外部评审 F19。
#
# 这里原本有一份独立实现（`biga_fds_into_neighbour()`，判据是 cmdline 含
# "openclaw-biga"）。同一分钟内与 `isolation.py` 的判据现场对照：
#
#     isolation.py 口径        14 个进程 / 412 个 fd
#     这个文件的口径            4 个进程
#
# 两边都报 0 命中、都是 ✅，但**检查对象的集合完全不同**。
# 如果 I-1 真的被违反、而违反它的进程只满足其中一种判据，
# 两份实现会给出相反的结论，使用者根本不知道该信哪一份。
#
# 这正是 `architecture.md` §9 L-3 点名的失败模式（同一判据多份实现，
# 错法全是静默的）—— 而它发生在这个项目最看重的那条不变式自己的验证代码里。
#
# ⇒ 删掉这一份，统一走 `isolation.py`。**判据只留一处。**

def check_6_neighbour(baseline: dict | None) -> Check:
    c = Check("6", "邻居 gateway pid / 启动时间 / 监听 / config / nvm default 未变")
    now = neighbour_state()
    if baseline is None:
        return c.pending(
            "无基线可比（用 --save-baseline 先记一份）。当前："
            f"pid={now['gateway_pid']} started={now['gateway_started']}"
        ) or c
    diffs = [f"{k}: 基线 {baseline.get(k)!r} → 现在 {now[k]!r}"
             for k in _STABLE_KEYS if baseline.get(k) != now[k]]
    if diffs:
        c.fail("; ".join(diffs))
    else:
        c.ok(f"{len(_STABLE_KEYS)} 项一致（pid {now['gateway_pid']}，"
             f"启动于 {now['gateway_started']}）")
    for k in _INFO_KEYS:
        if baseline.get(k) != now[k]:
            c.notes.append(
                f"（参考）{k} 有变化 —— 邻居是活着的系统，会自己写自己的状态库。"
                "这一项不参与判定，I-1 由第 6b 条直接验证"
            )
    return c


def check_6b_no_write_handles() -> Check:
    """不变式 I-1 —— **判据由 `isolation.py` 提供，这里只做三态转译。**"""
    c = Check("6b", "没有任何 BigA 进程以写模式打开邻居目录下的文件（不变式 I-1）")
    res = isolation.Result()
    isolation.check_i1(res)
    verdict, name, detail = res.rows[0]
    if verdict == isolation.FAIL:
        c.fail(f"🔴 {name}\n{detail}")
    elif verdict == isolation.UNKNOWN:
        # 三态在这里能原样传下去 —— 旧脚本本来就有 PENDING，
        # 反倒是 isolation.py 一度把它丢了（F18）。
        c.pending(f"{name}\n{detail}")
    else:
        c.ok(name)
    return c


def check_7_nvm() -> Check:
    c = Check("7", "nvm bin 里只有邻居那个 node 版本带 openclaw（红线 R-2）")
    found = neighbour_state()["nvm_openclaw"]
    biga_node = str(pathlib.Path.home() / ".nvm/versions/node/v24.21.0/bin/openclaw")
    if biga_node in found:
        c.fail(f"🔴 BigA 的 node 下出现了 openclaw: {biga_node}")
    elif len(found) == 1:
        c.ok(f"仅 {found[0]}")
    elif not found:
        c.fail("nvm 里一个 openclaw 都没有 —— 邻居的 PATH 注入会落空")
    else:
        c.fail(f"存在多个: {found}")
    return c


# ──────────────────────────────────────────────────────────────────────── main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 1 验收自检")
    ap.add_argument("--decision-id", help="要核对的 decision_id")
    ap.add_argument("--live", action="store_true", help="允许联网（第 5 项需要）")
    ap.add_argument("--baseline", type=pathlib.Path,
                    help="邻居基线 JSON（第 6 项需要）")
    ap.add_argument("--save-baseline", action="store_true",
                    help="把当前邻居状态写进 --baseline 指定的文件后退出")
    args = ap.parse_args(argv)

    if args.save_baseline:
        if not args.baseline:
            print("--save-baseline 需要同时给 --baseline <路径>", file=sys.stderr)
            return 1
        args.baseline.write_text(
            json.dumps(neighbour_state(), ensure_ascii=False, indent=2))
        print(f"邻居基线已写入 {args.baseline}")
        return 0

    baseline = None
    if args.baseline and args.baseline.exists():
        baseline = json.loads(args.baseline.read_text())

    checks = [
        check_1_spawned(args.decision_id),
        check_2_verdict(args.decision_id),
        check_3_card_sections(args.decision_id),
        check_4_replay(args.decision_id),
        check_5_fail_closed(args.live),
        check_6_neighbour(baseline),
        check_6b_no_write_handles(),
        check_7_nvm(),
        check_8_latency(args.decision_id),
    ]

    print("═" * 78)
    print("Phase 1 验收自检")
    print("═" * 78)
    for c in checks:
        print(f"{_MARK[c.verdict]} {c.n}. {c.title}")
        if c.detail:
            print(f"      {c.detail}")
        for note in c.notes:
            print(f"      ℹ {note}")
    print("─" * 78)

    tally = {v: sum(1 for c in checks if c.verdict == v)
             for v in ("PASS", "FAIL", "PENDING")}
    print(f"PASS {tally['PASS']}  ·  FAIL {tally['FAIL']}  ·  PENDING {tally['PENDING']}")

    if tally["FAIL"]:
        print("\n❌ Phase 1 未通过。")
    elif tally["PENDING"]:
        # 🔴 PENDING 不算 PASS。这条是本项目的第一条红线。
        print("\n⏳ 尚未判定完毕 —— PENDING 不等于通过。")
    else:
        print("\n✅ Phase 1 八条全部通过。")

    return 0 if tally == {"PASS": len(checks), "FAIL": 0, "PENDING": 0} else 1


if __name__ == "__main__":
    raise SystemExit(main())
