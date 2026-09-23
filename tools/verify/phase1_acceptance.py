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
import os
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "skills"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _contract import CN_TZ, STAGE1_AGENTS, STAGE2_AGENTS  # noqa: E402

import isolation  # noqa: E402  —— I-1 的唯一判据，见下方 F19 的注释

# 退出码的唯一定义 —— 见 tools/verify/_verdict.py 的 docstring
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _verdict as _v  # noqa: E402

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


def _runtime_spawn_records(decision_id: str) -> list[dict] | None:
    """读 OpenClaw 运行时自己记的子会话表，**只取属于这次决策的**。

    返回 None 表示读不到（状态库不存在 / 表结构变了），
    调用方必须据此报 PENDING 而不是 PASS。

    🔴 为什么按决策号过滤（复查发现的洞）
    ------------------------------------
    第一版取「最近 50 条」然后只问「这个 agent 名字出现过吗」——
    **完全没有跟决策号绑定**。实测复现：

        伪造决策号 BIGA-20260921-901，用合法 API 写 6 行 agent_runs
        → spawn 核验：6 个 agent 两份独立记录都齐   ✅ 通过

    因为机器当天确实跑过别的真实决策，伪造的号**蹭上了别人的记录**。
    而 `bin/biga-card` 正常使用本来就会反复运行 ⇒
    这个条件在真实机器上**几乎总是成立**，不是刁钻场景。

    ⚠️ 最讽刺的是：决策号**本来就明文写在 `payload_json` 里**（Supervisor
    的 spawn 指令带着它），只是没被拿来做绑定。
    ⇒ 这一版用它过滤，同时去掉 LIMIT —— 按号取就不该有条数上限，
      否则一天跑得多了，早先的决策会悄悄查不到。

    🔴 为什么读**两张**表（2026-09-23）
    ----------------------------------
    运行时把一次 spawn 记在哪张表**不是恒定的**，而这套核验原来只读
    `subagent_runs` 一张。实测对照：

        BIGA-20260922-001（至今唯一一张编排器真实产出的卡）
          subagent_runs                      0 行
          task_runs（排除 exec）             12 行

    于是 `spawn_check` 对那张真卡报「判不了」(exit 2)——**一张真卡，一次真
    spawn，核验却给不出结论**。而同一条 Adapter 代码路径在 2026-09-23 重新
    spawn 时，`subagent_runs` 又确实进了行（另一个会话的实测）。
    ⇒ 两张表都可能是那次 spawn 的落点，只读一张就会在另一张那侧变成盲区。

    ⚠️ 读两张**不削弱判据**：两张表都是运行时自己写的，BigA 的业务代码
    碰不到任何一张 —— 「被验证方写不到的地方才算证据」这条前提不变。
    变的只是「去哪儿找那份证据」。
    """
    import sqlite3  # store-exempt: 读的是 OpenClaw 运行时状态库，不是 BigA 事实层；
                    # _store 的单一入口规则是为了「将来切 PG 只改一个文件」，
                    # 而这个库永远不会跟着 BigA 迁移。

    # ⚠️ 环境变量只为**测试**存在。
    #    没有它，唯一重要的那条测试（「别人的记录不算我的」）写不出来 ——
    #    只能去 fake `_runtime_spawn_records` 本身，而那正好把
    #    「按决策号过滤」这段逻辑**整个绕过去**，等于不测它。
    #    第一版就是这么写的测试，所以复查发现的洞它一条都没拦住。
    db = pathlib.Path(os.environ.get("BIGA_RUNTIME_DB")
                      or pathlib.Path.home() / ".openclaw-biga/state/openclaw.sqlite")
    if not db.exists():
        return None
    try:
        # store-exempt: 同上 —— 外部运行时状态库，只读
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        out: list[dict] = []
        # 🔴 每张表**各自**容错：少一张不等于读不到。
        #    第一版把两条查询写在同一个 try 里 —— 运行时库里没有 `task_runs`
        #    （或反过来）就整个 return None ⇒ 核验退化成「判不了」，而
        #    「判不了」是会被忽略的。只有**两张都取不到**才是真的读不到。
        got_any = False

        # ① subagent_runs —— agent 名字藏在 child_session_key 的第二段里
        try:
            for r in conn.execute(
                    "SELECT run_id, child_session_key, created_at "
                    "FROM subagent_runs WHERE payload_json LIKE ? "
                    "ORDER BY created_at DESC", (f"%{decision_id}%",)):
                d = dict(r)
                seg = (d.get("child_session_key") or "").split(":")[1:2]
                d["agent"] = seg[0] if seg else None
                d["source"] = "subagent_runs"
                out.append(d)
            got_any = True
        except sqlite3.Error:
            pass

        # ② task_runs —— agent 名字有独立的 agent_id 列，不用从 session key 里抠
        #
        # 🔴 排除 task_kind='exec'：那是 **Specialist 自己在会话里跑 shell**
        #    （`run_id` 形如 `exec:<名字>`、requester 是它自己的子会话），
        #    不是「被 spawn 起来」。不排掉的话，一个只跑过 exec、从未被 spawn
        #    的 agent 会被判成「spawn 过」—— 正是这套核验要抓的那种伪造。
        try:
            for r in conn.execute(
                    "SELECT run_id, agent_id, child_session_key, created_at "
                    "FROM task_runs WHERE task LIKE ? "
                    "  AND (task_kind IS NULL OR task_kind <> 'exec') "
                    "ORDER BY created_at DESC", (f"%{decision_id}%",)):
                d = dict(r)
                d["agent"] = d.get("agent_id")
                d["source"] = "task_runs"
                out.append(d)
            got_any = True
        except sqlite3.Error:
            pass

        # R-3：两张都读不到 ⇒ 说「判不了」，不说「零条记录」。
        # 这两者在调用方那里是**完全不同的结论**（后者会被判成伪造）。
        return out if got_any else None
    except sqlite3.Error:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def orphan_spawns(day: str) -> list[tuple[str, str, str]] | None:
    """当天**没有归属**的 specialist spawn → `[(时刻, agent, 原因), …]`。

    🔴 它抓的是「钱花了，结果进不了任何卡」。

    实测（2026-09-21 19:31:52，飞书触发那次）：

        emotion     无决策号
        technical   无决策号
        market      无决策号
        sector      BIGA-20260921-000   ← 临时号，`save_verdict` 会直接拒

    四个 spawn 白跑，约 $0.4 / 2.5 分钟。根因是 Supervisor 在
    **占号之前**就 spawn 了 Stage 1（L-11「身份晚于证据」）——
    而 schema v4 那整套机制正是为了防它。

    ⚠️ 与 `spawn_proof()` 的分工：那个按决策号查「这张卡的 agent 真跑了吗」，
    **查不到孤儿** —— 孤儿的特征恰恰是没有号可查。两个都要有。

    返回 `None` = 读不到运行时库（R-3：判不了，不是没问题）。
    """
    import re
    import sqlite3  # store-exempt: 外部运行时状态库，只读

    db = pathlib.Path(os.environ.get("BIGA_RUNTIME_DB")
                      or pathlib.Path.home() / ".openclaw-biga/state/openclaw.sqlite")
    if not db.exists():
        return None
    pat = re.compile(r"BIGA-\d{8}-(\d{3})")
    lo = int(datetime.strptime(day, "%Y%m%d").replace(tzinfo=CN_TZ).timestamp() * 1000)
    hi = lo + 86_400_000
    out: list[tuple[str, str, str]] = []
    try:
        # store-exempt: 同上 —— 外部运行时状态库，只读
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT child_session_key, created_at, payload_json FROM subagent_runs"
            " WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
            (lo, hi)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass

    known = set(STAGE1_AGENTS) | set(STAGE2_AGENTS)
    for r in rows:
        parts = (r["child_session_key"] or "").split(":")
        agent = parts[1] if len(parts) > 1 else ""
        if agent not in known:
            continue                      # 不是 specialist，不在本检查范围
        seqs = pat.findall(r["payload_json"] or "")
        when = datetime.fromtimestamp(r["created_at"] / 1000, CN_TZ).strftime("%H:%M:%S")
        if not seqs:
            out.append((when, agent, "无决策号"))
        elif all(s == "000" for s in seqs):
            out.append((when, agent, "只带临时号 -000（落库会被拒）"))
    return out


@dataclass
class SpawnProof:
    """spawn 核验的三态原料。`per_agent[agent] = (在 agent_runs 里, 被 spawn 过)`。"""

    readable: bool
    rows: int
    per_agent: dict[str, tuple[bool, bool]]


def spawn_proof(decision_id: str) -> SpawnProof:
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

    返回 `SpawnProof`。🔴 **`readable` 与「有没有记录」必须分开**：

    ============================  ================================================
    `readable=False`              读不到运行时库 ⇒ UNKNOWN（R-3：算不出来要说）
    `readable=True, rows=0`       库能读、这个号一条记录都没有
                                  —— 若 `agent_runs` 里却有行，那就是**伪造**
    ============================  ================================================

    第一版把这两种混为一谈，于是伪造被报成「判不了」——
    而「判不了」是会被忽略的，「伪造」不会。

    🔴 批 J-II —— 结构化 join 取代文本匹配（有 runtime_run_id 时）
    -----------------------------------------------------------
    `agent_runs.runtime_run_id` 非空时（在线路径落库的新行），直接拿它与运行时
    `subagent_runs.run_id` 做 join，比原先按 `child_session_key` 段名匹配硬：
    段名匹配会被「蹭上别人记录」骗过 —— 机器上别的决策真跑过同一个 agent，它的
    `child_session_key` 段名照样命中。为 NULL（所有历史行）时退回段名判据。
    形状照抄 `risk_check.py::CROSS_CHECK_PAIRS`（有 `evidence_set_id` 用它、
    缺失退回 `raw_hash`）—— 逐字同形，不发明第二种兼容写法。
    """
    from _contract import STAGE1_AGENTS, STAGE2_AGENTS
    from _store import list_agent_runs

    runs = list_agent_runs(decision_id=decision_id)
    ours = {r["agent"] for r in runs}
    # agent → 该 agent 落库时记下的 runtime_run_id 集合（去 NULL）。
    # ⚠️ 用 .get：历史行没有这一列，测试的 mock 行也只有 "agent" 键 —— 缺键即
    #    NULL，走退回分支（这正是 P4「历史行三态不变」成立的原因）。
    rr_by_agent: dict[str, set[str]] = {}
    for r in runs:
        rid = r.get("runtime_run_id")
        if rid:
            rr_by_agent.setdefault(r["agent"], set()).add(rid)

    spawns = _runtime_spawn_records(decision_id)
    if spawns is None:
        return SpawnProof(readable=False, rows=0, per_agent={})
    # 结构化 join 的右表：这次决策在运行时侧真实存在的 spawn id。它取自**已按决策号
    # 过滤**的 spawns，所以「id 存在」同时也蕴含「这条记录属于本次决策」——比对全表
    # 存在性更硬：伪造者就算抄一个别处的真 id，那条记录的 payload 也不含本决策号。
    runtime_ids = {r.get("run_id") for r in spawns if r.get("run_id")}

    out = {}
    # 判据取自契约里的 stage 名单，**不是手写的一个名字** ——
    # 名单会随 agent 增加而自己长大，手写的那个不会。
    for agent in list(STAGE1_AGENTS) + list(STAGE2_AGENTS):
        if agent == "discipline":          # 裁定 13：故意不建
            continue
        rr = rr_by_agent.get(agent)
        if rr:
            # 🔴 强绑定：runtime_run_id 与运行时 subagent_runs.run_id 结构化 join。
            spawned = any(rid in runtime_ids for rid in rr)
        else:
            # 退回弱绑定（历史行无 runtime_run_id）：比**归一化后的 agent 名**。
            # 🔴 两张表的 agent 名来源不同（`subagent_runs` 从 `child_session_key`
            #    的第二段抠、`task_runs` 有现成的 `agent_id` 列），归一化在
            #    `_runtime_spawn_records()` 里做完 —— 这里只比 `rec["agent"]`，
            #    不在消费端各解析一遍（那就是第二套口径，L-3）。
            #    仍然是**整段相等**，不是子串包含（子串会让 `news` 命中
            #    `newsflash`，而这类误判从来不会报错）。
            spawned = any(r.get("agent") == agent for r in spawns)
        out[agent] = (agent in ours, spawned)
    return SpawnProof(readable=True, rows=len(spawns), per_agent=out)


def check_1_spawned(decision_id: str | None) -> Check:
    """Supervisor 真的 spawn 了**每一个** specialist —— 两份独立记录都要有。"""
    c = Check("1", "Supervisor 确实 spawn 了各 Specialist（两份独立记录都要有）")
    if not decision_id:
        return c.pending("未提供 --decision-id") or c

    proof = spawn_proof(decision_id)
    if not proof.readable:
        return c.pending(
            "读不到运行时的 subagent_runs，无法证明任何一个是 Supervisor spawn 的"
        ) or c

    ours = [a for a, (o, _) in proof.per_agent.items() if o]
    forged = [a for a, (o, sp) in proof.per_agent.items() if o and not sp]
    absent = [a for a, (o, _) in proof.per_agent.items() if not o]
    if proof.rows == 0 and ours:
        return c.fail(
            f"{decision_id} 在运行时 subagent_runs 里一条记录都没有，"
            f"而 agent_runs 里有 {sorted(ours)} —— 这个号从未被 spawn 过") or c
    if forged:
        return c.fail(
            f"这些 agent 在 agent_runs 里有行，但运行时 subagent_runs 里没有："
            f"{sorted(forged)} —— 那些行是被直接写入的，"
            "**不是 Supervisor spawn 出来的**") or c
    if absent:
        return c.pending(f"本次决策没有这些 agent 的记录：{sorted(absent)}") or c
    c.ok(f"{len(proof.per_agent)} 个 agent 两份记录都齐：{sorted(proof.per_agent)}")
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
    from _store import load_online_card

    card = load_online_card(decision_id)
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

    from _store import load_online_card
    from _store.runtime import read_turns

    card = load_online_card(decision_id)
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
            # 用法错误。不是「判不了」—— 参数怎么给由调用方决定，能立刻改对。
            return _v.FAIL
        args.baseline.write_text(
            json.dumps(neighbour_state(), ensure_ascii=False, indent=2))
        print(f"邻居基线已写入 {args.baseline}")
        return _v.PASS

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

    # 🔴 三态。上面那句注释（「PENDING 不算 PASS，这是第一条红线」）
    #    原来配的是 `return 0 if … else 1` —— **PENDING 和 FAIL 退成同一个码**。
    #    渲染层分得清、退出码分不清，等于没分（外部深度评审同形状第 4 处）。
    if tally["FAIL"]:
        return _v.FAIL
    if tally["PENDING"]:
        return _v.UNKNOWN
    return _v.PASS


if __name__ == "__main__":
    raise SystemExit(main())
