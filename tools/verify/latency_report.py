#!/usr/bin/env python3
"""延迟与成本分解 —— 回答「时间和钱到底花在哪」。

架构 §10.2 写的是「**不预先优化，但要保证测得出来**」。
这个脚本就是那个「测得出来」。

🔴 为什么需要它：`agent_runs.elapsed_ms` 存的是 **skill** 的耗时（几秒），
   而真正的大头是 **agent 的 LLM 轮次**（几十秒）。
   只看前者做延迟分析，会把优化做到错的地方去。

🔴 两个口径不是一回事，本脚本分开报：
   · **等卡** —— 人从提问到看见 Card 等了多久、花了多少。验收 #8 判的是这个。
   · **决策总成本** —— 还要加上卡落库之后 runtime 唤醒 Supervisor 的「尾巴」轮次。
     算账、报价、评估模型开销时看的是这个。

用法::

    latency_report.py                       # 最近一次决策
    latency_report.py --decision-id BIGA-20260919-003
    latency_report.py --all                 # 全部轮次，按时间排
    latency_report.py --budget-ms 90000     # 自定预算
    latency_report.py --parallel-check      # Stage 1 没并行就退非零

🔴 关于 `--parallel-check`
   判据是**两个 specialist 的时间区间是否相交**，不是「这次跑得快不快」。
   串行的表现是：每步成功、Card 照常产出、日志全绿，**只是慢了一倍**。
   而「慢了一倍」会被网络波动掩盖 —— 区间相交才是结构性证据。
   只有一个 specialist 时结论是「判不了」，**不是「通过」**（R-3 对验证工具本身同样适用）。
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import datetime, timedelta

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "skills"))

from _contract import CN_TZ, STAGE1_AGENTS, STAGE2_AGENTS  # noqa: E402
from _store import StoreNotInitialised, connect  # noqa: E402
from _store.runtime import read_task_runs, read_turns  # noqa: E402

# 🔴 `read_turns()` 返回的时间已经是北京时间（在 `_store.runtime` 的读取边界转好）。
#    这里不再逐处 `.astimezone(CN_TZ)` —— 那种「人人都要记得」的义务迟早漏一个，
#    而漏掉的表现是时间差 8 小时却仍是个合法时刻，不报错。

# 🔴 180s 的推导见 architecture.md §10.1「预算重推」。
#
# 它**不是**把 90s 调大来让红灯变绿。90s 是在一个 agent 都还没有的时候，
# 把四个阶段各拍一个区间再相加得到的 —— 一个愿望，不是一个预测。
#
# 四次盘中实测下来，即使每一项都取最好值：
#     Stage 1 最好 64.2 + risk 最好 19.9 + main 最好 73.9 = 158s
# 而 Stage 1 的最慢项是**第三方接口延迟**，不在我们控制内。
#
# 这条线该红的时候：超过 180s 说明某个组成异常了，不是「今天慢一点」。
# 优化后三次实测都在 158–173s。
DEFAULT_BUDGET_MS = 180_000

# 卡后「尾巴」轮次的串联间隔上限：超过这个空档就认为是另一次运行，不再往后收。
TAIL_GAP_S = 300

# Supervisor 的 agentId —— 并行检查要把它排除在外（它当然不与自己并行）。
SUPERVISOR = "main"

# 从卡向前串联轮次时允许的最大空档。超过它就认为前面那些属于另一次运行。
LEAD_GAP_S = 120



def _decision_window(decision_id: str | None) -> tuple[str, datetime, int] | None:
    """返回 (decision_id, 结束时刻, elapsed_ms)。

    🔴 `elapsed_ms` 是精确定界的关键：窗口 = [generated_at − elapsed_ms, generated_at]。
    Supervisor 没传 `--elapsed-ms` 时它是 0，此时**只能退回粗略窗口**，
    而那种情况必须明确告诉使用者，不能让人以为数字是精确的。
    """
    sql = ("SELECT decision_id, generated_at, elapsed_ms FROM decision_records "
           "WHERE replay_of IS NULL")
    args: tuple = ()
    if decision_id:
        sql += " AND decision_id=?"
        args = (decision_id,)
    sql += " ORDER BY record_id DESC LIMIT 1"
    with connect(readonly=True) as c:
        row = c.execute(sql, args).fetchone()
    if not row:
        return None
    return row["decision_id"], datetime.fromisoformat(row["generated_at"]), int(row["elapsed_ms"] or 0)


def _previous_card_time(decision_id: str) -> datetime | None:
    """上一张在线卡的落库时刻 —— 向前串联的**硬边界**。

    🔴 光靠「间隔小于 N 秒就继续往前吃」会把相邻的两次运行并成一个窗口。
    实测：两次决策相隔 67s，窗口被并起来后报出 238.7s「超预算 2.7×」，
    而真实值是 71.1s（达标）。**一个会虚报 3 倍的指标比没有指标更糟。**
    """
    with connect(readonly=True) as c:
        row = c.execute(
            "SELECT generated_at FROM decision_records "
            "WHERE replay_of IS NULL AND decision_id < ? "
            "ORDER BY decision_id DESC LIMIT 1", (decision_id,)).fetchone()
    return datetime.fromisoformat(row["generated_at"]) if row else None


def _derive_start(end: datetime, turns, floor: datetime | None = None) -> datetime | None:
    """从 Card 落库时刻**向前串联**轮次，推出这次决策真正的起点。

    🔴 为什么不用 Card 自报的 `elapsed_ms`
       那要求 Supervisor 自己做时间减法，而契约禁止它做算术 ——
       实测它会直接省略，于是 `elapsed_ms=0`。
       让被考核方报成绩，Phase 1 已经吃过一次亏（自报 122.8s，真实 215.2s）。

    做法：找到覆盖 `end` 的那一轮（Supervisor 是在自己那一轮**中途**落卡的），
    然后一路向前吃掉间隔小于 `LEAD_GAP_S` 的轮次。
    """
    ordered = sorted(turns, key=lambda t: t.started_at)
    if floor is not None:
        # 上一张卡之前的轮次属于上一次决策，绝不并进来。
        ordered = [t for t in ordered if t.started_at > floor]
    covering = [t for t in ordered
                if t.started_at <= end <= t.ended_at]
    if not covering:
        covering = [t for t in ordered if t.ended_at <= end]
        if not covering:
            return None
        covering = covering[-1:]
    cursor = covering[-1].started_at
    changed = True
    while changed:
        changed = False
        for t in ordered:
            ts, te = t.started_at, t.ended_at
            if ts < cursor and (cursor - te).total_seconds() <= LEAD_GAP_S:
                cursor, changed = ts, True
    return cursor


def _parallel_report(turns) -> bool | None:
    """Stage 1 是否真并行。返回 True/False/None（判不了）。

    🔴 判据是区间相交，不是总耗时变短。
    """
    spans: dict[str, tuple[datetime, datetime]] = {}
    stage2: dict[str, tuple[datetime, datetime]] = {}
    for t in turns:
        if t.agent_id == SUPERVISOR:
            continue
        if t.agent_id in STAGE2_AGENTS:
            a, b = stage2.get(t.agent_id, (t.started_at, t.ended_at))
            stage2[t.agent_id] = (min(a, t.started_at), max(b, t.ended_at))
            continue
        if t.agent_id not in STAGE1_AGENTS:
            continue
        s0, e0 = t.started_at, t.ended_at
        if t.agent_id in spans:
            a, b = spans[t.agent_id]
            spans[t.agent_id] = (min(a, s0), max(b, e0))
        else:
            spans[t.agent_id] = (s0, e0)

    print("Stage 1 并行检查")
    if stage2 and spans:
        # 🔴 Stage 2 与 Stage 1 重叠 = 它读的是**还没冻结**的证据。
        #    这不是性能问题，是正确性问题：制衡层审的必须是定稿，
        #    否则「当时为什么放行」在回放里根本重建不出来。
        s1_end = max(e for _, e in spans.values())
        early = {a: s for a, (s, _) in stage2.items() if s < s1_end}
        for a, s0 in early.items():
            print(f"  🔴 {a}（Stage 2）在 {s0:%H:%M:%S} 就开始了，而 Stage 1 到 "
                  f"{s1_end:%H:%M:%S} 才结束 —— 它读到的证据尚未冻结")
        if not early:
            for a, (s0, e0) in sorted(stage2.items(), key=lambda kv: kv[1][0]):
                print(f"  {a:<11}{s0:%H:%M:%S} – {e0:%H:%M:%S}   "
                      f"{(e0 - s0).total_seconds():5.1f}s   （Stage 2，应在 Stage 1 之后）")

    if len(spans) < 2:
        who = list(spans) or ["（无）"]
        print(f"  窗口内只有 {len(spans)} 个 specialist：{', '.join(who)}")
        print("  ⇒ **判不了**（不是「通过」）。并行需要至少两个 specialist 才谈得上。")
        print()
        return None

    for agent, (s0, e0) in sorted(spans.items(), key=lambda kv: kv[1][0]):
        print(f"  {agent:<11}{s0:%H:%M:%S} – {e0:%H:%M:%S}   "
              f"{(e0 - s0).total_seconds():5.1f}s")

    items = sorted(spans.items(), key=lambda kv: kv[1][0])
    worst_gap: tuple[str, str, float] | None = None
    min_overlap = None
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (a, (as_, ae)), (b, (bs, be)) = items[i], items[j]
            ov = (min(ae, be) - max(as_, bs)).total_seconds()
            if min_overlap is None or ov < min_overlap:
                min_overlap = ov
            if ov <= 0 and (worst_gap is None or -ov > worst_gap[2]):
                worst_gap = (a, b, -ov)

    stage1_wall = (max(e for _, e in spans.values())
                   - min(s for s, _ in spans.values())).total_seconds()
    total = sum((e - s).total_seconds() for s, e in spans.values())

    print(f"  {'─' * 44}")
    if min_overlap is not None and min_overlap > 0:
        print(f"  区间两两相交，最小重叠 {min_overlap:.1f}s  ⇒  ✅ **真并行**")
        print(f"  stage1 墙钟 {stage1_wall:.1f}s   个体之和 {total:.1f}s   "
              f"省下 {1 - stage1_wall / max(total, 0.001):.0%}")
        print()
        return True

    a, b, gap = worst_gap or ("?", "?", 0.0)
    print(f"  {a} 与 {b} 的区间**不相交**（间隔 {gap:.1f}s）  ⇒  ❌ **串行**")
    print(f"  stage1 墙钟 {stage1_wall:.1f}s   个体之和 {total:.1f}s")
    print("  ⇒ 两个 spawn 多半分在了两条消息里。总耗时看起来可能还行 ——")
    print("     快慢会被网络波动掩盖，**区间相交才是结构性证据**。")
    print("     修法见仓库根 AGENTS.md「多个 Specialist 必须在同一条消息里一次性发出」。")
    print()
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="延迟与成本分解")
    ap.add_argument("--decision-id")
    ap.add_argument("--all", action="store_true", help="列出全部轮次，不按决策聚焦")
    ap.add_argument("--window-min", type=int, default=10,
                    help="以决策时刻为终点，往前取多少分钟的轮次（默认 10）")
    ap.add_argument("--budget-ms", type=int, default=DEFAULT_BUDGET_MS)
    ap.add_argument("--parallel-check", action="store_true",
                    help="Stage 1 未并行（或判不了）时退出码非零，可用于闸门")
    args = ap.parse_args(argv)

    probe = read_turns()
    # 🔴 数据不可信时先说清楚，再谈数字
    if probe.unavailable:
        print("⚠️  运行时数据不完整，下面的分解**不可全信**：")
        for u in probe.unavailable:
            print(f"    · {u}")
        print()
    if not probe.turns:
        print("没有读到任何 LLM 轮次。")
        return 1

    turns = probe.turns
    title, approx = "全部轮次", False
    if not args.all:
        got = _decision_window(args.decision_id)
        if not got:
            print("找不到决策记录。用 --all 看全部轮次。")
            return 1
        did, end, elapsed = got
        floor = _previous_card_time(did)
        if elapsed > 0:
            start = end - timedelta(milliseconds=elapsed)
            if floor is not None and start < floor:
                # 自报的 elapsed 越过了上一张卡 —— 它把上一次决策也算进来了。
                start = floor
                title = f"{did}（自报窗口过长，已截到上一张卡 {start:%H:%M:%S}–{end:%H:%M:%S}）"
            else:
                title = f"{did}（精确窗口 {start:%H:%M:%S}–{end:%H:%M:%S}）"
        else:
            derived = _derive_start(end, probe.turns, floor=floor)
            if derived is not None:
                start = derived
                title = f"{did}（由轨迹反推 {start:%H:%M:%S}–{end:%H:%M:%S}）"
            else:
                approx = True
                start = end - timedelta(minutes=args.window_min)
                title = f"{did}（⚠️ 粗略窗口，前 {args.window_min} 分钟）"
        # 🔴 按「区间重叠」而不是「结束时刻落在窗口内」：
        #    Supervisor 是在自己那一轮的**中途**调 synthesize.py 落卡的，
        #    所以那一轮的 ended_at 必然晚于 Card 的 generated_at。
        #    用结束时刻过滤会把最关键的合成轮整个漏掉。
        turns = [t for t in turns
                 if t.ended_at >= start
                 and t.started_at <= end]
        if not turns:
            print(f"{title} 内没有轮次。")
            return 1

    print("═" * 78)
    print(f"延迟与成本分解 · {title}")
    print("═" * 78)
    if approx:
        print("⚠️  Card 的 elapsed_ms 是 0，且无法从轨迹反推起点。")
        print("    只能按固定时长往前取窗口。**窗口里可能混进别的运行**，")
        print("    下面的合计数偏大，不可作为达标判据。")
        print()
    # 🔴 缓存读和缓存写**必须分开列**。写缓存按 1.25× 计费、读缓存按 0.1×，
    #    差 12 倍。合成一列「缓存tok」会让「刚重启过、缓存是冷的」看起来像
    #    「这个配置更贵」—— 本项目已经因此差点把一次冷启动误读成配置劣化。
    print(f"{'起始':>8}  {'agent':<9}{'耗时':>8}{'输出tok':>9}{'缓存读':>9}{'缓存写':>9}{'成本$':>9}  停止原因")
    print("─" * 88)
    cold = False
    for t in turns:
        local = t.started_at
        if t.cache_write > t.cache_read * 0.3:
            cold = True
        print(f"{local:%H:%M:%S}  {t.agent_id:<9}{t.duration_ms/1000:7.1f}s"
              f"{t.tokens_out:9d}{t.cache_read:9d}{t.cache_write:9d}"
              f"{t.cost_usd:9.4f}  {t.stop_reason or '—'}")
    print("─" * 88)
    if cold:
        print("⚠️  缓存写占比偏高 —— 这次多半是**冷启动**（刚重启 gateway / 改过配置 /")
        print("    换过提示词）。此时的**成本不可与热缓存的运行横向比较**；")
        print("    耗时受影响小，仍可比。")
        print()

    # 🔴 与调度器的记录对账。两份独立来源对不上时必须说出来 ——
    #    沉默地展示一份残缺却完整模样的分解，比报错危险得多。
    if not args.all:
        task_rows, task_warn = read_task_runs()
        in_win = [r for r in task_rows
                  if r.started_at and r.ended_at
                  and r.ended_at >= start and r.started_at <= end
                  and r.runtime in ("cli", "subagent")]
        seen = {(t.agent_id, t.started_at.strftime("%H:%M:%S"))
                for t in turns}
        missing = [r for r in in_win
                   if (r.agent_id, r.started_at.strftime("%H:%M:%S")) not in seen]
        if task_warn:
            print("⚠️  对账数据不可用：" + "；".join(task_warn))
        elif missing:
            print("⚠️  对账不一致 —— 调度器记录了下面这些执行，但 trajectory 里没有对应轮次：")
            for r in missing:
                print(f"      {r.started_at:%H:%M:%S}–{r.ended_at:%H:%M:%S} "
                      f"{r.runtime} {r.agent_id} ({r.status})")
            print("      ⇒ 下面的合计**偏小**，缺了这些执行的耗时与成本。")
        else:
            print(f"✅ 与调度器记录对账一致（窗口内 {len(in_win)} 条）")
        print()

    # 🔴 卡后还有账：子 agent 全部 settle 之后 runtime 会**再唤醒一次** Supervisor
    #    （runId 带 `announce:requester-settle`，投递失败还会 retry-N）。
    #    那几轮不计入「等卡时间」—— 卡此时已经落库了 —— 但真金白银花掉了。
    #    只报窗口内成本，就会低报一次决策的真实开销。
    tail: list = []
    if not args.all:
        keys = {t.session_key for t in turns}
        cursor = max(t.ended_at for t in turns)
        for t in sorted(probe.turns, key=lambda x: x.started_at):
            if t.session_key not in keys or t.started_at <= cursor:
                continue
            if (t.started_at - cursor).total_seconds() > TAIL_GAP_S:
                break
            tail.append(t)
            cursor = t.ended_at

    wall = int((max(t.ended_at for t in turns)
                - min(t.started_at for t in turns)).total_seconds() * 1000)
    cost = sum(t.cost_usd for t in turns)

    # 🔴 窗口里没有 Supervisor 的轮次 = 这份分解缺了最大的一块。
    #    成因是**测量时机**：Supervisor 在自己那一轮的中途调 synthesize.py 落卡，
    #    卡一出现就跑报告，它那一轮还没写进 trajectory。
    #    实测漏报过一次：报 41.5s「达标」，而缺掉的合成轮是 159.1s。
    #    ⇒ 缺了就必须说，并且**不许判达标** —— 这正是本脚本存在的理由。
    no_supervisor = not args.all and not any(t.agent_id == SUPERVISOR for t in turns)
    if no_supervisor:
        verdict = "⚠️ 判不了（窗口里没有 Supervisor 轮次）"
    else:
        verdict = "✅ 达标" if wall < args.budget_ms else f"❌ 超预算 {wall/args.budget_ms:.1f}×"
    print(f"{'等卡墙钟':>10} {wall/1000:6.1f}s      预算 {args.budget_ms/1000:.0f}s      {verdict}")
    print(f"{'等卡成本':>10} ${cost:.4f}")
    if no_supervisor:
        print()
        print(f"⚠️  窗口内没有 `{SUPERVISOR}` 的任何轮次 —— **这份分解缺了最大的一块**。")
        print("    最常见的原因是测量太早：Supervisor 在自己那一轮的**中途**落卡，")
        print("    卡一出现就跑报告，它那一轮还没写进 trajectory。")
        print("    ⇒ 等十几秒再跑一次。在那之前，上面的墙钟与成本都是**偏小的**。")
    if tail:
        tcost = sum(t.cost_usd for t in tail)
        tms = sum(t.duration_ms for t in tail)
        print()
        print(f"卡后尾巴 {len(tail)} 轮 · {tms/1000:.1f}s · ${tcost:.4f}"
              f"（占决策总成本 {tcost/(cost+tcost):.0%}）")
        for t in tail:
            print(f"      {t.started_at:%H:%M:%S} {t.agent_id:<9}"
                  f"{t.duration_ms/1000:6.1f}s  out={t.tokens_out:<5d} ${t.cost_usd:.4f}")
        print("      ⇒ 卡已落库，不计入等卡时间；但这是同一次决策真实花掉的钱。")
        print(f"{'决策总成本':>10} ${cost + tcost:.4f}")
    print()

    # 🔴 --all 跨越多天，区间相交毫无意义（实测报出过 97502s 的「重叠」）。
    #    一个在错误模式下仍然给结论的检查，比不给结论更危险。
    parallel = None
    if args.all:
        print("Stage 1 并行检查：--all 模式跳过 —— 跨越多次运行，区间相交没有意义。")
        print()
    else:
        parallel = _parallel_report(turns)

    # 按 agent 汇总，指出瓶颈
    by: dict[str, list] = {}
    for t in turns:
        by.setdefault(t.agent_id, []).append(t)
    print("按 agent 汇总（LLM 轮次时间，不含技能内部耗时）")
    rows = sorted(by.items(), key=lambda kv: -sum(x.duration_ms for x in kv[1]))
    for agent, ts in rows:
        ms = sum(x.duration_ms for x in ts)
        print(f"  {agent:<10}{len(ts)} 轮  {ms/1000:6.1f}s  "
              f"占 {ms/max(wall,1):5.0%}   ${sum(x.cost_usd for x in ts):.4f}")
    if rows:
        top = max(turns, key=lambda t: t.duration_ms)
        print()
        print(f"🔴 最慢的单轮：{top.agent_id} {top.duration_ms/1000:.1f}s，"
              f"输出 {top.tokens_out} tok")
        print("   输出 token 高通常意味着思考档位（thinking）占了大头 —— "
              "优化要先动它，而不是先动技能。")

    if args.parallel_check and (parallel is not True or no_supervisor):
        return 4
    return 0


if __name__ == "__main__":
    # 🔴 F23：库不存在是**全新环境的正常状态**，不该是一屏 traceback。
    #    统一在入口转成人话 —— 每个工具各写一遍就又是一份散开的判据。
    #    退出码 2 = 判不了，与 isolation.py 的三态口径一致。
    try:
        raise SystemExit(main())
    except StoreNotInitialised as e:
        print(f"\n🔶 判不了 —— {e}", file=sys.stderr)
        raise SystemExit(2) from None
