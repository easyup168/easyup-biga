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
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import datetime, timedelta

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "skills"))

from _contract import CN_TZ  # noqa: E402
from _store import connect  # noqa: E402
from _store.runtime import read_task_runs, read_turns  # noqa: E402

# 90s 的推导见 architecture.md §10.1 —— 不是拍脑袋，也不是原来那个「下界相加」的 60s。
DEFAULT_BUDGET_MS = 90_000

# 卡后「尾巴」轮次的串联间隔上限：超过这个空档就认为是另一次运行，不再往后收。
TAIL_GAP_S = 300



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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="延迟与成本分解")
    ap.add_argument("--decision-id")
    ap.add_argument("--all", action="store_true", help="列出全部轮次，不按决策聚焦")
    ap.add_argument("--window-min", type=int, default=10,
                    help="以决策时刻为终点，往前取多少分钟的轮次（默认 10）")
    ap.add_argument("--budget-ms", type=int, default=DEFAULT_BUDGET_MS)
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
        if elapsed > 0:
            start = end - timedelta(milliseconds=elapsed)
            title = f"{did}（精确窗口 {start:%H:%M:%S}–{end:%H:%M:%S}）"
        else:
            approx = True
            start = end - timedelta(minutes=args.window_min)
            title = f"{did}（⚠️ 粗略窗口，前 {args.window_min} 分钟）"
        # 🔴 按「区间重叠」而不是「结束时刻落在窗口内」：
        #    Supervisor 是在自己那一轮的**中途**调 synthesize.py 落卡的，
        #    所以那一轮的 ended_at 必然晚于 Card 的 generated_at。
        #    用结束时刻过滤会把最关键的合成轮整个漏掉。
        turns = [t for t in turns
                 if t.ended_at.astimezone(CN_TZ) >= start
                 and t.started_at.astimezone(CN_TZ) <= end]
        if not turns:
            print(f"{title} 内没有轮次。")
            return 1

    print("═" * 78)
    print(f"延迟与成本分解 · {title}")
    print("═" * 78)
    if approx:
        print("⚠️  这张 Card 的 elapsed_ms 是 0（Supervisor 没传 --elapsed-ms），")
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
        local = t.started_at.astimezone(CN_TZ)
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
        seen = {(t.agent_id, t.started_at.astimezone(CN_TZ).strftime("%H:%M:%S"))
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
    verdict = "✅ 达标" if wall < args.budget_ms else f"❌ 超预算 {wall/args.budget_ms:.1f}×"
    print(f"{'等卡墙钟':>10} {wall/1000:6.1f}s      预算 {args.budget_ms/1000:.0f}s      {verdict}")
    print(f"{'等卡成本':>10} ${cost:.4f}")
    if tail:
        tcost = sum(t.cost_usd for t in tail)
        tms = sum(t.duration_ms for t in tail)
        print()
        print(f"卡后尾巴 {len(tail)} 轮 · {tms/1000:.1f}s · ${tcost:.4f}"
              f"（占决策总成本 {tcost/(cost+tcost):.0%}）")
        for t in tail:
            print(f"      {t.started_at.astimezone(CN_TZ):%H:%M:%S} {t.agent_id:<9}"
                  f"{t.duration_ms/1000:6.1f}s  out={t.tokens_out:<5d} ${t.cost_usd:.4f}")
        print("      ⇒ 卡已落库，不计入等卡时间；但这是同一次决策真实花掉的钱。")
        print(f"{'决策总成本':>10} ${cost + tcost:.4f}")
    print()

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
