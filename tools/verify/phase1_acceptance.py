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

from _contract import (CN_TZ, STAGE1_AGENTS, STAGE2_AGENTS,  # noqa: E402
                       parse_run_marker)

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


#: 🔴 「这一行是不是一次**真 spawn**」—— 肯定式判据，不枚举 task_kind。
#:
#: 2026-09-23 评审实证：`task_kind` 在运行时里是**自由文本开放列**
#: （`taskKind: normalizeOptionalString(params.taskKind)`，无枚举无校验），
#: 已发货的取值就不止三个（`exec` / `automation_run` /
#: `context_engine_turn_maintenance` / `<label>_generation`）。
#: 更要命的是 `agent_id` 的缺省解析会回退到**发起者自己的会话**
#: （`resolveTaskAgentId`：explicitAgentId → childSessionKey → ownerKey →
#: requesterSessionKey），于是**任何在 agent X 会话里建出来的 task 行，
#: 缺省就带 `agent_id = X`**。
#:
#: ⇒ 原来的「排除 `task_kind='exec'`」是**排除列表**：实测能被绕过 ——
#:    造六行 `task_kind='image_generation'`、agent_id 是 specialist 自己、
#:    决策号写在它自己可控的文本里，核验报「两份记录都齐」，而真实 spawn 零次。
#:    那正是 F3 要抓的形状。
#:
#: 改成肯定式：一次真 spawn 必然同时满足三件事 ——
#:   ① 有子会话，且子会话的 agent 段与 `agent_id` **相等**
#:      （agent 自己建的行：child 指向它自己的会话，段名碰巧也等于自己
#:       —— 所以这条单独不够，要配 ②）
#:   ② `run_id` 是**裸 UUID**：真 spawn 没有命名空间前缀，而工具/定时/后台
#:      执行都带（`exec:…` / `cron:…` / `tool:…:…`）。这一条挡住的正是
#:      「agent 在自己会话里调工具」那一整类。
#: 实测全表：NULL/bare-uuid 496 行、exec/`exec:` 340 行、automation_run/`cron:` 61 行
#: —— 三种已知 kind 被 run_id 命名空间完整分开；对 BIGA-20260922-001 新旧判据
#: 都是 12 行，零回归。
_REAL_SPAWN_SQL = (
    " AND child_session_key IS NOT NULL AND agent_id IS NOT NULL"
    " AND child_session_key LIKE 'agent:'||agent_id||':subagent:%'"
    " AND run_id NOT LIKE '%:%'"
)


def _dedupe_by_run_id(rows: list[dict]) -> list[dict]:
    """同一次 spawn 在两张表里各有一行 ⇒ 按 `run_id` 去重，保留先到的那条。

    🔴 `subagent_runs ⊂ task_runs`（实测 67/67，0 个独有）。取数时先读
    `task_runs`（全集、有显式 `agent_id` 列），`subagent_runs` 只补老库。
    不去重的后果不是「多算几条」那么轻 —— `orphan_spawns()` 会把同一次 spawn
    印两遍，而它报的是**钱**（回合二实测 09-21：54 个 → 真实 29 个，虚高 1.9 倍）。

    `run_id` 为空的行不参与去重（宁可重复，也不把两条不同的记录合成一条）。
    """
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        rid = r.get("run_id")
        if rid and rid in seen:
            continue
        if rid:
            seen.add(rid)
        out.append(r)
    return out


def _merge_text_by_run_id(rows: list[dict]) -> list[dict]:
    """按 `run_id` 合并，**文本取并集**，保留先到那条的其余字段。

    🔴 与 `_dedupe_by_run_id()` 的区别，以及为什么两个调用点不能共用一个
    -------------------------------------------------------------------
    两张表的文本字段**不是同一份文档**：

        task_runs.task              「[Subagent Context] You are running as…」渲染后的提示词
        subagent_runs.payload_json  「{"runId":…,"taskRunId":…}」spawn 载荷 JSON

    实测：临时号 `-000` 在 `task_runs.task` 里出现 **0 次**，在
    `subagent_runs.payload_json` 里出现 **3 次**。

    `orphan_spawns()` 是**按天取 → 去重 → 再分类**。直接丢掉重复行，就连同那行
    携带的证据一起丢了 —— 去重永远留 `task_runs`（先读），于是
    「只带临时号 -000」这个分支在真实数据上**再也不会亮**：09-21 那批
    实测 28 条全被归成「无决策号」，而其中有一条本该是「临时号」。
    数量对、钱对，但读的人被静默送去了错误的诊断方向：
    「无决策号」= 压根没带号；「只带临时号」= **占号晚于 spawn**，
    也就是 L-11「身份晚于证据」，schema v4 那整套机制专门要防的东西。

    ⚠️ `_runtime_spawn_records()` 用 `_dedupe_by_run_id()` 是**安全的**：
    它两条查询都**先按决策号过滤、再去重**，只在某一张表命中的行不会被丢。
    同一段逻辑在一个调用点正确、在另一个调用点有损 —— 差别不在代码，
    在**调用点的输入处于什么状态**。列消费方清单查不出这一类，
    得问「这段代码在每个调用点拿到的是什么」。
    """
    merged: dict[str, dict] = {}
    out: list[dict] = []
    for r in rows:
        rid = r.get("run_id")
        if not rid:                       # 无 run_id 不参与合并
            out.append(r)
            continue
        if rid in merged:
            merged[rid]["text"] = f'{merged[rid]["text"]}\n{r.get("text") or ""}'
        else:
            merged[rid] = dict(r)
            out.append(merged[rid])
    return out


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

    🔴 为什么读**两张**表（2026-09-23，措辞经回合二评审纠正）
    ----------------------------------------------------------
    ⚠️ **不是「落点会变」，是 `subagent_runs ⊂ task_runs`** —— `task_runs` 记全部，
    `subagent_runs` 只记其中一部分。实测：`subagent_runs` 去重后 67 个 `run_id`，
    **67 个全都出现在 `task_runs` 里，0 个是它独有的**。

    第一版的说法是「运行时把一次 spawn 记在哪张表不是恒定的，两张都可能是落点」——
    那句话本身就是**重复计数的根**：按它写代码就会把两张表的结果直接拼起来，
    而同一次 spawn 在两张表里各有一行。回合二实测：`budget_report.py 20260921`
    打出「孤儿 spawn 54 个」，其中 25 条是逐字重复，真实 29 个，钱虚高约 1.9 倍 ——
    而那是个**报钱数的告警**，最容易让人下次直接忽略。
    ⇒ 归一化时按 `run_id` 去重，且**只在取数处做一次**。

    原来只读 `subagent_runs` 确实是盲区，但成因是「只看了子集」，不是「看错了表」。
    实测对照：

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
        # 🔴 每张表各自容错，且**「表不存在」与「读失败」必须分开**。
        #    第一版两条查询写在同一个 try 里，少一张表就整个 return None；
        #    第二版改成 per-table `except: pass`，但把两种情况混成了一种 ——
        #    2026-09-23 评审实测：上游把 `task` 列改名（查询炸）之后，真卡的
        #    结论不是「判不了」，而是 **FAIL「这个号从未被 spawn 过」**——
        #    一次读失败变成对真卡的指控。这比退化成 UNKNOWN 坏一档。
        #    ⇒ 表不在 = 这个来源没有记录（正常，老库就没有 task_runs）；
        #      表在但读炸 = 算不出来，整条报「判不了」（R-3）。
        #    取数口径照 `_store/runtime.py::read_task_runs()`（先查 sqlite_master）。
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        got_any = False

        # 🔴 三种情况必须分开，混任意两种都会把结论带偏：
        #    a) 两张表**都不在** ⇒ 这压根不是我们认识的那个库 ⇒ 判不了（R-3）
        #    b) 在的那张读炸了   ⇒ 算不出来 ⇒ 判不了
        #    c) 只有一张在       ⇒ 正常，用它（老库本来就没有 task_runs）
        #    评审实测：把 (b) 当成 (c) 处理时，真卡的结论不是「判不了」而是
        #    **FAIL「这个号从未被 spawn 过」**——一次读失败变成对真卡的指控。
        present = tables & {"subagent_runs", "task_runs"}
        if not present:
            return None               # (a)

        def _pull(table: str, sql: str, to_agent) -> bool:
            """返回「这个来源可用吗」。不可用 ⇒ 调用方据此报判不了。"""
            if table not in present:
                return True           # (c) 表不在不算失败，只是没有记录
            try:
                for r in conn.execute(sql, (f"%{decision_id}%",)):
                    d = dict(r)
                    d["agent"] = to_agent(d)
                    d["source"] = table
                    # 🔴 两张表的文本列名不同（`task` / `payload_json`），归一化
                    #    **只在这里做一次** —— 消费端各解析一遍就是第二套口径（L-3）。
                    d["biga_run_id"] = parse_run_marker(
                        d.get("task") or d.get("payload_json"))
                    out.append(d)
                return True
            except sqlite3.Error:
                return False          # 表在却读不了 ⇒ 这就是「算不出来」

        # ① task_runs —— 全集，且 agent 名字有独立的 agent_id 列（比从 session key
        #    里抠可靠）。先读它，`subagent_runs` 只用来补老库缺的部分。
        ok2 = _pull(
            "task_runs",
            # 🔴 B1：把 `task`（任务原文）一并取回 —— 里面带着 BigA 写进去的
            #    `BIGA-RUN-ID: <run_id>` 标记。没有它，运行时侧就只剩一个
            #    孤立的 run_id，证不了「这次 spawn 属于**哪一次** BigA 编排」。
            "SELECT run_id, agent_id, child_session_key, created_at, task FROM task_runs"
            " WHERE task LIKE ?" + _REAL_SPAWN_SQL + " ORDER BY created_at DESC",
            lambda d: d.get("agent_id"))

        # ② subagent_runs —— 子集。老库里没有 task_runs 时它是唯一来源。
        ok1 = _pull(
            "subagent_runs",
            "SELECT run_id, child_session_key, created_at, payload_json FROM subagent_runs"
            " WHERE payload_json LIKE ? ORDER BY created_at DESC",
            lambda d: (seg[0] if (seg := (d.get("child_session_key") or "")
                                  .split(":")[1:2]) else None))

        got_any = ok1 and ok2
        out[:] = _dedupe_by_run_id(out)

        # R-3：任何一个来源「表在却读不了」⇒ 说「判不了」，不说「零条记录」。
        # 这两者在调用方那里是**完全不同的结论**：后者会被判成**伪造**，
        # 也就是把一次读失败变成对一张真卡的指控（评审实测过这条路径）。
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

    ✅ 2026-09-23 复核：上面这个招牌例子**现在仍然跑得出来**（一度跑不出来 ——
    去重把携带临时号的那份文本丢了，见 `_merge_text_by_run_id()`）。实跑
    `orphan_spawns("20260921")` 里 19:31:52 那批：market/technical/emotion
    「无决策号」、sector「只带临时号 -000」，与上面逐条对上。

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
    rows: list[dict] = []
    try:
        # store-exempt: 同上 —— 外部运行时状态库，只读
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        # 🔴 与 `_runtime_spawn_records()` 同源：两张表都读，判据同一份
        #    （`_REAL_SPAWN_SQL`）。
        #
        #    2026-09-23 评审抓到的就是这里：那一版只读 `subagent_runs`。
        #    实测三天对照 ——
        #        20260921  subagent_runs 51 行 | task_runs 真 spawn 100 行
        #        20260922  subagent_runs  0 行 | task_runs 真 spawn  27 行
        #        20260923  subagent_runs  1 行 | task_runs 真 spawn   1 行
        #    09-22 那天 `budget_report.py` 打出「✅ 无孤儿 spawn」——
        #    **那个 ✅ 是读一张空表读出来的**，而当天有 27 次真 spawn。
        #    而且它返回的是 `[]`（干净）不是 `None`（判不了），连 R-3 都没保护到。
        #
        #    ⚠️ 这不是那一批引入的，但那一批的全部论证就是「运行时记在哪张表
        #    不是恒定的」—— 证完之后只修了两个消费方里的一个，就是 L-3：
        #    同一个事实两套口径，且其中一套已被自己证明是错的。
        if "subagent_runs" not in tables and "task_runs" not in tables:
            return None
        if "task_runs" in tables:
            for r in conn.execute(
                    "SELECT run_id, agent_id, child_session_key, created_at, task"
                    " FROM task_runs WHERE created_at >= ? AND created_at < ?"
                    + _REAL_SPAWN_SQL, (lo, hi)):
                d = dict(r)
                d["agent"] = d.get("agent_id") or ""
                d["text"] = d.get("task") or ""
                rows.append(d)
        if "subagent_runs" in tables:
            for r in conn.execute(
                    "SELECT run_id, child_session_key, created_at, payload_json"
                    " FROM subagent_runs WHERE created_at >= ? AND created_at < ?",
                    (lo, hi)):
                d = dict(r)
                seg = (d.get("child_session_key") or "").split(":")[1:2]
                d["agent"] = seg[0] if seg else ""
                d["text"] = d.get("payload_json") or ""
                rows.append(d)
        rows = _merge_text_by_run_id(rows)
        rows.sort(key=lambda d: d["created_at"])
    except sqlite3.Error:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass

    known = set(STAGE1_AGENTS) | set(STAGE2_AGENTS)
    for r in rows:
        agent = r["agent"]
        if agent not in known:
            continue                      # 不是 specialist，不在本检查范围
        seqs = pat.findall(r["text"])
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
    #: 这次的记录分别来自哪张运行时表，`{表名: 条数}`。
    #: 🔴 它是 `_runtime_spawn_records()` 里 `source` 字段的**消费方**（L-1）——
    #:    没有它，那个字段写了没人读；有了它，报错才说得出「刚才到底读到了哪张表」。
    #:    两张表同时存在而只有一张有记录，正是 2026-09-23 那次盲区的形状。
    by_source: dict[str, int] = field(default_factory=dict)
    #: `agent -> 判不了的原因`。**第三态**，只有 `spawn_proof_for_run()` 会填（P1-2）。
    #:
    #: 🔴 为什么必须有第三态：`per_agent` 是个二元组，「账本有行 + 没证明」只能塌进
    #:    `(True, False)`，而消费方把它一律读成**伪造**。但「这行是伪造的」和
    #:    「这次 spawn 是真的，只是没把 runtime_run_id 捞回来」是两个完全不同的根因，
    #:    后者被报成前者会把排查直接引到错误方向（`persist()` 明确允许
    #:    「被 spawn 了但值为 None」这种行，见批 F）。R-3：算不出来要说算不出来。
    #:
    #: 在 `unproven` 里的 agent **不计入 forged** —— 消费方必须先查它。
    unproven: dict[str, str] = field(default_factory=dict)
    #: `agent -> (status, reason)`，status ∈ {ABSENT, PASS, FAIL, UNKNOWN}。
    #: 只有 `spawn_proof_for_run()` 会填（P1-2 行级严格核验）。
    #: `per_agent` / `unproven` 都从它派生 —— 一份判据，两种旧视图。
    status: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: FAIL 的 `agent -> reason`。同样从 `status` 派生。
    failed: dict[str, str] = field(default_factory=dict)
    #: 这次核验落在哪个决策上。`--run-id` 模式下由 `decision_runs` 反查得到 ——
    #: 🔴 没有它，Run 模式的报错会打出「None 在运行时记录里一条都没有」：
    #:    一句指向空的话，而报错要指路（评审 2026092502 §9）。
    decision_id: str | None = None
    #: `readable=False` 时的原因，给人看。`readable=True` 时为 None。
    #: 🔴 「判不了」有好几种成因，要查的地方完全不同：库不在 / 库读不了 /
    #:    这个 run_id 压根不存在。塌成同一句「判不了」＝ 让人从头猜一遍。
    unreadable_reason: str | None = None


#: 一个 agent 在**一次 run** 里的账本行 → `(status, reason)`。
#: 🔴 逐字移植自评审 `reference/strict_spawn_proof.py::verify_agent_rows`。
#:
#: **不许先把多行压成集合再判**，这正是上一版的漏口：
#:
#: ==========================================  ====================================
#: online 行没有 runtime_run_id，
#: 旁边一条 legacy 行有，且 join 得上           上一版 `any(...)` ⇒ **PASS**
#: 两条 online 行，一条真一条伪造              上一版 `any(...)` ⇒ **PASS**
#: ==========================================  ====================================
#:
#: 两次都是「成功 join 的那个 id 根本不来自被核验的那一行」。
#: ⇒ 判据必须落在**行**上：先确定「哪一行才是这次在线执行」，再看那一行的 id。
#:
#: ⚠️ **与 reference 的唯一偏离**：无行时 reference 返回 `FAIL/no_agent_run`，
#: 这里返回 `ABSENT`。理由是本项目里「某个 agent 这次没跑」是**合法**状态
#: （裁定 13 的 discipline 根本不建；risk 在两种确定性早退里不被 spawn），
#: 判成 FAIL 会让每一张 roster 不满的卡都红。评审正文 §5.4 写的也是
#: 「没有行 → ABSENT」，是 reference 代码与正文不一致，这里跟正文。
def verify_agent_rows(rows: list[dict], *,
                      runtime_by_id: dict[str, dict],
                      expected_agent: str,
                      expected_biga_run_id: str) -> tuple[str, str]:
    """`(status, reason)`，status ∈ {ABSENT, PASS, FAIL, UNKNOWN}。

    🔴 判据是**三元组**，不是「这个 id 在运行时库里出现过」：

        (BigA orchestration_run_id, agent, OpenClaw runtime_run_id)

    上一版只比 `ledger.runtime_run_id in runtime_ids`，于是两条都通不过：

    ======================================  ==========================
    market 的账本行填了 news 那次 spawn 的 id  上一版 ⇒ **PASS**
    Run B 的账本行填了 Run A 那次 spawn 的 id  上一版 ⇒ **PASS**
    ======================================  ==========================

    前者是「一次真 spawn 给另一个 agent 背书」，后者是「同 decision 的另一次
    执行给这次背书」—— 两者都不是「这次 run 真的启动了这个 agent」。
    ⇒ 逐条移植评审 `reference/runtime_proof_triple.py::verify_exact_runtime_identity`。
    """
    if not rows:
        return "ABSENT", "no_agent_run"

    online = [r for r in rows if r.get("provenance_mode") == "online"]
    legacy = [r for r in rows if r.get("provenance_mode") == "legacy"]
    other = [r for r in rows
             if r.get("provenance_mode") not in {"online", "legacy"}]

    if other:
        # v19 之前落的行（provenance_mode 为 NULL）走这里：证不了，也不冤枉。
        return "UNKNOWN", "unsupported_provenance_mode"
    if online and legacy:
        return "UNKNOWN", "mixed_provenance"
    if not online:
        return "UNKNOWN", "legacy_only"
    if len(online) != 1:
        # 🔴 FAIL 不是 UNKNOWN：同一次 run 同一个 agent 出现两条在线执行记录，
        #    本身就是不该存在的状态（v21 的唯一索引拦它），不是「查不到」。
        return "FAIL", "duplicate_online_rows"

    row = online[0]
    if row.get("agent") != expected_agent:
        return "FAIL", "ledger_agent_mismatch"

    runtime_run_id = row.get("runtime_run_id")
    if not runtime_run_id:
        return "UNKNOWN", "missing_runtime_run_id"

    runtime = runtime_by_id.get(runtime_run_id)
    if runtime is None:
        return "FAIL", "runtime_record_not_found"

    # ── 以下三条是 B1 新增的，上一版一条都没查 ──────────────────────────
    if runtime.get("agent") is None:
        return "UNKNOWN", "runtime_agent_unavailable"
    if runtime.get("agent") != expected_agent:
        return "FAIL", "runtime_agent_mismatch"
    if runtime.get("biga_run_id") is None:
        # 任务文本里没有 BIGA-RUN-ID 标记 —— v22 之前 spawn 的都这样。
        # 🔴 UNKNOWN 不是 FAIL：把「没有标记」读成「对不上」会把一批历史真 run
        #    判成伪造。
        return "UNKNOWN", "runtime_biga_run_unavailable"
    if runtime.get("biga_run_id") != expected_biga_run_id:
        return "FAIL", "runtime_biga_run_mismatch"

    return "PASS", "exact_run_agent_runtime_match"


#: 原因码 → 给人看的一句话。**只有这一份**（L-3）：
#: `spawn_check.py` 与 `check_1_spawned()` 都从这里取，不各写一遍措辞。
#: 🔴 每一条都要说清「所以该去查什么」——报错要指路，不能只说判不了。
_UNPROVEN_HINT: dict[str, str] = {
    "runtime_biga_run_unavailable":
        "运行时任务文本里没有 BIGA-RUN-ID 标记——v22 之前 spawn 的都这样。证不了，但也不算伪造",
    "runtime_agent_unavailable":
        "运行时记录里读不出 agent（表结构变了？）—— 证不了",
    "legacy_run_without_spawn_plan":
        "这个 run 早于 schema v22，没冻过「期望 spawn 名单」——只能按今天的 Registry 猜，而那对改过 agent 名册的历史 run 会猜错，所以一律降级为判不了",
    "unsupported_provenance_mode":
        "账本行的 provenance_mode 不是 online/legacy（多半是 v19 之前落的历史行）"
        "—— 证不了也不算伪造",
    "legacy_only":
        "这次 run 里这个 agent 只有 legacy 行 —— 弱证据，不作数（§8.4："
        "段名匹配只对 decision 级历史入口开放）",
    "mixed_provenance":
        "同一次 run 同一个 agent 既有 online 行又有 legacy 行 —— "
        "分不清哪一行代表这次执行。**legacy 行的 runtime_run_id 不许替 online 行作证**；"
        "查是谁写了第二行",
    "missing_runtime_run_id":
        "在线行但没记下 runtime_run_id —— spawn 多半真的发生过，"
        "是编排器没把 SpawnResult.handle.runtime_run_id 收回来；"
        "查 orchestrator 的 runtime_run_ids 映射",
}

#: FAIL 的原因码 → 给人看的一句话。与上面分开，因为**排查方向完全不同**：
#: UNKNOWN 去查数据与时机，FAIL 去查「谁写了这行 / 为什么运行时没有它」。
_FAIL_HINT: dict[str, str] = {
    "runtime_biga_run_mismatch":
        "🔴 运行时那条记录属于**另一次 BigA 编排** —— 同 decision 的别的 run 的 spawn 被拿来给这次背书",
    "runtime_agent_mismatch":
        "🔴 运行时那条记录属于**另一个 agent** —— 一次真 spawn 被拿来给别的 agent 背书。查谁填的 runtime_run_id",
    "ledger_agent_mismatch":
        "账本行的 agent 与期望不符（取行时按 agent 分组过，出现它说明数据被改过）",
    "expected_agent_missing":
        "计划里点名要 spawn 这个 agent，但这次 run 的账本里**一行都没有**——它没被启动，或者启动了但账本没记上。查 orchestrator 的 Stage 1 结果",
    "duplicate_online_rows":
        "同一次 run 同一个 agent 有**多条** online 账本行 —— "
        "一条真的 runtime_run_id 会把另一条伪造的盖住，所以整体不作数。"
        "schema v21 的 ux_online_agent_run_once 本该拦住它，出现说明是索引之前的老行",
    "runtime_record_not_found":
        "在线行的 runtime_run_id 在运行时侧查无此记录 —— 这一行是被直接写进库的，"
        "不是 Supervisor spawn 出来的",
}


def spawn_proof(decision_id: str) -> SpawnProof:
    """`agent -> (在 agent_runs 里, 在运行时 subagent_runs 里)`。

    ⚠️ **它证明的是「被 spawn 了」，不是标题写的「被 Supervisor spawn 了」** ——
    全程不看 `requester`。今天两者等价，但靠的是**配置**不是判据：七个 specialist
    的 `subagents.allowAgents` 都是 `[]`，运行时的 `resolveSubagentTargetPolicy`
    读的正是发起方自己这份名单 ⇒ specialist 起不了别的 agent（真实库里这种行 0 条）。
    哪天给某个 specialist 开了 `allowAgents`，「A 起了 B」就会被算成
    「Supervisor 起了 B」，**而且不报错**。
    🔴 不要据此加 requester 判据：实测有 4 行真 spawn 的 requester 等于 child
    （自指，`[Subagent Context]` 那类），加了会误杀。改配置的人看到这段就够了。

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
    #: agent → 是否有任何一条**自称在线**的账本行（P1-2 §8.4）。
    online_agents: set[str] = set()
    for r in runs:
        rid = r.get("runtime_run_id")
        if rid:
            rr_by_agent.setdefault(r["agent"], set()).add(rid)
        if r.get("provenance_mode") == "online":
            online_agents.add(r["agent"])

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
        elif agent in online_agents:
            # 🔴 P1-2 §8.4：自称在线却没有 runtime_run_id ⇒ **不退回段名匹配**。
            #    弱名称匹配只对历史行（provenance_mode 非 online）开放 ——
            #    对一条声明了自己是在线执行的行放行段名匹配，等于把这道
            #    强绑定检查的开关交给被检查方自己按。
            #    这里保持二元组（`spawned=False`）：本函数是 decision 级的
            #    legacy 入口，三态由 `spawn_proof_for_run()` 负责。
            spawned = False
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
    by_source: dict[str, int] = {}
    for r in spawns:
        by_source[r.get("source", "?")] = by_source.get(r.get("source", "?"), 0) + 1
    return SpawnProof(readable=True, rows=len(spawns), per_agent=out,
                      by_source=by_source, decision_id=decision_id)


def spawn_proof_for_run(run_id: str) -> SpawnProof:
    """与 spawn_proof() 相同的逻辑，但按 orchestration_run_id 精确过滤（P1-2）。

    spawn_proof(decision_id) 按 decision 聚合 agent_runs——同一 decision 的
    Run A 真正 spawn 了 Market，Run B 没有，但 Run B 会借用 Run A 的账本行而
    通过检查。本函数只看「这次 run 的 agent_runs」，不跨 run 共享。

    对历史行（orchestration_run_id IS NULL）本函数无法核验，此时退回空集
    而非 FAIL，行为与「没有记录」一致（不借用别的 run）。

    🔴 P1-2：三态分类，判据是 `agent_runs.provenance_mode`（schema v19）
    ------------------------------------------------------------------
    照评审参考实现 `reference/spawn_proof_run_scope.py` 的四分支：

    ==================================  ==========================================
    账本里没有这个 agent                absent —— `(False, False)`
    `provenance_mode != 'online'`       **UNKNOWN**（历史行/手工行，证不了也冤不了）
    `provenance_mode == 'online'`
      且 `runtime_run_id` 为空           **UNKNOWN**（真 spawn 了但没捞回 id）
      且 join 不上运行时                 FAIL（伪造）
      且 join 上了                       PASS
    ==================================  ==========================================

    ⚠️ 这一列在本函数出现之前是**写了没人读**的（v19 加的列，只有一条测试读回
    自己刚写的值）—— 正是 L-1 的形状：为一个没有消费方的字段升了一版 schema。
    """
    from _contract import STAGE1_AGENTS, STAGE2_AGENTS
    from _store import list_agent_runs

    # 按 orchestration_run_id 精确取这次 run 的账本行，**按 agent 分组保留原始行**
    # —— 不压成集合，见 verify_agent_rows() 的说明。
    runs = list_agent_runs(orchestration_run_id=run_id)
    rows_by_agent: dict[str, list[dict]] = {}
    for r in runs:
        rows_by_agent.setdefault(r["agent"], []).append(r)

    # 运行时侧的 spawn 记录仍按 decision 取（因为没有 per-run 过滤接口）。
    # 但我们用结构化 join（runtime_run_id ↔ subagent_runs.run_id）判断，
    # 不是按决策号的字符串匹配——所以同 decision 不同 run 的 spawn id 不会混入。
    # 🔴 不吞异常吞成一句「判不了」：三种成因要查的地方完全不同。
    from _store import connect
    try:
        with connect(readonly=True) as conn:
            row_dr = conn.execute(
                "SELECT decision_id FROM decision_runs WHERE run_id=?", (run_id,)
            ).fetchone()
    except Exception as e:  # noqa: BLE001 —— 库不在/读不了/schema 变了都归这儿
        return SpawnProof(readable=False, rows=0, per_agent={},
                          unreadable_reason=f"读不了 BigA 事实库：{type(e).__name__}: {e}")

    if row_dr is None:
        return SpawnProof(
            readable=False, rows=0, per_agent={},
            unreadable_reason=(
                f"run_id={run_id} 在 decision_runs 里不存在 —— "
                "核不了一次没发生过的执行尝试。"
                "确认 run_id 抄对了：bin/biga-card --status <run_id>"))

    decision_id = row_dr["decision_id"]
    if not decision_id:
        # legacy 路径在 RECEIVED 时还不知道决策号（decision_runs.decision_id 可空）。
        # 🔴 不能当成「运行时零记录」往下走 —— 那会让 check 报「从未被 spawn 过」，
        #    对一次真实执行下一个假结论。
        return SpawnProof(
            readable=False, rows=0, per_agent={},
            unreadable_reason=(
                f"run {run_id} 还没占到决策号（decision_runs.decision_id 为空），"
                "运行时记录按决策号过滤，此时核不了"))
    spawns = _runtime_spawn_records(decision_id)
    if spawns is None:
        return SpawnProof(
            readable=False, rows=0, per_agent={},
            unreadable_reason=("读不到运行时的 spawn 记录"
                               "（subagent_runs / task_runs 两张都不在，或在却读不了）"))

    # 🔴 B1：保留**完整记录**，不再压成 id 集合 —— agent 与 biga_run_id 都要比。
    runtime_by_id = {r["run_id"]: r for r in spawns if r.get("run_id")}

    # 🔴 B1/N2：期望名单读**开 run 时冻下的那份**，不读今天的 Registry。
    from _store import expected_spawn_agents as _frozen_plan
    plan = _frozen_plan(run_id)
    plan_warning: str | None = None
    if plan is None:
        # v22 之前开的 run 没冻过计划。**不退回今天的 Registry 报 PASS** ——
        # 那正是 N2 要防的：加/删 agent 之后，用今天的名单核老卡会得出错的期望集。
        plan_warning = "legacy_run_without_spawn_plan"
        plan = [a for a in list(STAGE1_AGENTS) + list(STAGE2_AGENTS)
                if a != "discipline"]

    status: dict[str, tuple[str, str]] = {}
    out: dict[str, tuple[bool, bool]] = {}
    unproven: dict[str, str] = {}
    failed: dict[str, str] = {}
    # 🔴 核验范围 = 计划 ∪ **实际有在线账本行的 agent**。
    #    只按计划走会留一个洞：给一个**不在计划里**的 agent 伪造一行在线记录，
    #    它根本不会被看一眼。实测发现——`risk` 是条件 spawn、不在计划里，
    #    但这次它真的被 spawn 了、也真的有账本行，而核验完全跳过了它。
    #    ⇒ 计划里的**必须**证明得了（缺席即 FAIL）；计划外但有行的**也要**过三元组。
    extra = sorted(set(rows_by_agent) - set(plan))
    for agent in list(plan) + extra:
        st, reason = verify_agent_rows(
            rows_by_agent.get(agent, []),
            runtime_by_id=runtime_by_id,
            expected_agent=agent,
            expected_biga_run_id=run_id)
        # 🔴 计划里点名的 agent **没有账本行 = FAIL**，不是 ABSENT。
        #    上一版把它算成 ABSENT ⇒ 只要有一个 agent 过，`spawn_check` 就退 0 ——
        #    于是那条名为「每个 Specialist 都真被 spawn 了」的命令，实际上
        #    不要求这次期望的每一个 agent 都在（评审 §4.3 的 PoC）。
        if st == "ABSENT":
            st, reason = "FAIL", "expected_agent_missing"
        # 🔴 没冻过计划时的降级：**PASS 和「缺席」两个方向都要降**。
        #    · PASS → UNKNOWN：不知道当时期望谁，就不能说「都齐了」
        #    · expected_agent_missing → UNKNOWN：同理，不知道期望谁，
        #      就不能指控某个 agent 缺席（那个名单是按今天的 Registry 猜的）
        #    ⚠️ 其余 FAIL（agent 对不上 / run 对不上 / 运行时查无此记录）**不降**：
        #      那些是**观察到的**矛盾，与「当时期望谁」无关。
        if plan_warning and (st == "PASS" or reason == "expected_agent_missing"):
            st, reason = "UNKNOWN", plan_warning
        status[agent] = (st, reason)
        # 三个旧视图从同一份判据派生 —— 不各算一遍（L-3）。
        out[agent] = {"ABSENT": (False, False),
                      "PASS": (True, True)}.get(st, (True, False))
        if st == "UNKNOWN":
            unproven[agent] = reason
        elif st == "FAIL":
            failed[agent] = reason

    by_source: dict[str, int] = {}
    for r in spawns:
        by_source[r.get("source", "?")] = by_source.get(r.get("source", "?"), 0) + 1
    return SpawnProof(readable=True, rows=len(spawns), per_agent=out,
                      by_source=by_source, unproven=unproven,
                      status=status, failed=failed, decision_id=decision_id)


def check_1_spawned(decision_id: str | None, run_id: str | None = None,
                    *, allow_legacy: bool = False) -> Check:
    """Supervisor 真的 spawn 了**每一个** specialist —— 两份独立记录都要有。

    `run_id` 提供时（P1-2），按 orchestration_run_id 精确核验这次 run，
    不允许从同 decision 的其他 run 借用账本行。

    🔴 **不给 `run_id` 不会静默降级**（评审 2026092502 §8）。
    decision 级核验会把同一个 decision 其他 run 的 spawn 记录算进来，是**弱证据**；
    调用方漏了一个参数就自动改用弱证据，等于把安全档位交给手滑决定。
    要查历史决策，显式 `--legacy`：那时得到的是「查了，但用的是弱判据」，
    而不是一句看起来一样的「通过」。
    """
    c = Check("1", "Supervisor 确实 spawn 了各 Specialist（两份独立记录都要有）")
    if not decision_id and not run_id:
        return c.pending("未提供 --decision-id / --run-id") or c

    if run_id:
        proof = spawn_proof_for_run(run_id)
    elif allow_legacy:
        proof = spawn_proof(decision_id)
        c.notes.append(
            "⚠️ 走的是 decision 级（弱）核验：同 decision 其他 run 的 spawn 记录"
            "会被算进来。在线验收请给 --run-id。")
    else:
        return c.pending(
            f"只给了 --decision-id（{decision_id}），没给 --run-id —— "
            "不自动降级到 decision 级弱核验。\n"
            "    在线卡取它自己那次执行：\n"
            "      sqlite3 data/biga.db \"SELECT run_id FROM decision_records "
            f"WHERE decision_id='{decision_id}' AND replay_of IS NULL\"\n"
            "    确实要查历史（弱判据）：加 --legacy") or c
    if not proof.readable:
        return c.pending(
            (proof.unreadable_reason
             or "读不到运行时的 spawn 记录（subagent_runs / task_runs 两张都不在，"
                "或在却读不了）")
            + " —— 无法证明任何一个是被 spawn 的"
        ) or c

    ours = [a for a, (o, _) in proof.per_agent.items() if o]
    # 🔴 `unproven` 里的先摘出去：它们是 UNKNOWN，不是伪造（R-3）。
    forged = [a for a, (o, sp) in proof.per_agent.items()
              if o and not sp and a not in proof.unproven]
    absent = [a for a, (o, _) in proof.per_agent.items() if not o]
    if proof.rows == 0 and ours:
        return c.fail(
            f"{decision_id} 在运行时的 spawn 记录里一条都没有"
            f"（读到的来源：{proof.by_source or '两张表都是空的'}），"
            f"而 agent_runs 里有 {sorted(ours)} —— 这个号从未被 spawn 过") or c
    if proof.failed:
        return c.fail(
            f"（读到的来源：{proof.by_source}）"
            + "；".join(f"{a}：{_FAIL_HINT.get(r, r)}"
                        for a, r in sorted(proof.failed.items()))) or c
    if forged:
        return c.fail(
            f"这些 agent 在 agent_runs 里有行，但运行时的 spawn 记录里没有"
            f"（读到的来源：{proof.by_source}）："
            f"{sorted(forged)} —— 那些行是被直接写入的，"
            "**不是 Supervisor spawn 出来的**") or c
    if proof.unproven:
        return c.pending(
            "这些 agent 的 spawn 判不了（**不是伪造**）："
            + "；".join(f"{a}（{_UNPROVEN_HINT.get(r, r)}）"
                        for a, r in sorted(proof.unproven.items()))) or c
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
        c.ok(f"{len(ev)} 条带时区 as_of 的证据，取数滞后最大 {vs[0].max_source_lag_sec}s")
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
    ap.add_argument("--run-id", help="按 orchestration_run_id 精确核验 spawn（P1-2）")
    ap.add_argument("--legacy", action="store_true",
                    help="显式允许 decision 级（弱）spawn 核验 —— 只用于查历史决策。"
                         "在线验收不要用它：decision 级会把同 decision 其他 run 的"
                         "spawn 记录算进来")
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
        check_1_spawned(args.decision_id, run_id=args.run_id,
                        allow_legacy=args.legacy),
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
