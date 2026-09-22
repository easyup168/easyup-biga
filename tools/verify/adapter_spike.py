#!/usr/bin/env python3
"""确定性编排批 C-I · 把 spike 留下的三个未知数测掉（对着**真实运行时**跑）。

设计文档 §7 只验证了 1 次 spawn。这里验的是批 C 会依赖的用法：

  parallel         P1  五路真实 fan-out 共用一个 groupId，断言运行区间**相交**
  grant-longevity  P2  grant TTL≥780s，真实起一次 + 等够时长 + 再验 grant 仍活
  failure          P3  故意让 spawn 失败，断言 Adapter 给的是结构化状态不是裸异常

🔴 它驱动的是**真实的 `_runtime.OpenClawRuntimeAdapter`**，不是另写一套 MCP 调用 ——
   否则测的是这个脚本，不是产品（F3/L-13：假的东西造得太靠上，测的就是自己的假货）。

🔴 **这个工具会花钱**（真实 spawn，走共享 anthropic 凭据）。不是 pytest 能跑的东西
   （pytest 里 conftest 禁网、且不该花钱）。它的价值在于：运行时升级后重跑一遍，
   §7 的三条假设哪条破了，当场就知道。

用法::

    python3 tools/verify/adapter_spike.py parallel
    python3 tools/verify/adapter_spike.py failure
    python3 tools/verify/adapter_spike.py grant-longevity   # ~13 分钟（多数时间在等）
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "skills"))

from _runtime import (  # noqa: E402
    Grant,
    MCPClient,
    OpenClawRuntimeAdapter,
    SpawnStartError,
    SpawnStatus,
)

STAGE1 = ("market", "sector", "news", "technical", "emotion")
#: 给 Specialist 的最小指令 —— 我们要的是「五个真的并行跑起来」这件事，
#: 不是完整采集结果，所以用最短的真实任务压低成本（仍是真实 agent 会话）。
_MIN_TASK = "只回复两个字：收到。不要运行任何 skill，不要采集数据。"


def _session(tag: str) -> str:
    return f"agent:main:orchestrator-spike-{tag}-{int(time.time())}"


def parallel() -> int:
    """P1：五路 fan-out，断言运行区间相交（不是排队）。"""
    print("== P1 · 五路并行 fan-out ==")
    gid = f"spike-p1-{int(time.time())}"
    with OpenClawRuntimeAdapter.attach(_session("p1"), ttl_ms=300_000) as ad:
        handles = [ad.start(a, "BIGA-20260101-000", _MIN_TASK, group_id=gid,
                            run_timeout_sec=120) for a in STAGE1]
        print(f"  起了 {len(handles)} 个，groupId={gid}")
        for h in handles:
            print(f"    {h.agent:10} runId={h.run_id[:8]}")

        # 轮询：每个 tick 记下哪些 handle 处于 RUNNING。区间相交 = 某个 tick 里
        # 同时 ≥2 个在 RUNNING。
        max_concurrent = 0
        running_seen: dict[str, bool] = {}
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            now_running = [h for h in handles if ad.status(h) == SpawnStatus.RUNNING]
            max_concurrent = max(max_concurrent, len(now_running))
            for h in now_running:
                running_seen[h.run_id] = True
            done = [h for h in handles if ad.status(h) in (
                SpawnStatus.SUCCEEDED, SpawnStatus.FAILED, SpawnStatus.CANCELLED)]
            if len(done) == len(handles):
                break
            time.sleep(2)

        results = ad.wait(handles, timeout_sec=120)
    ok_status = sum(1 for r in results if r.status == SpawnStatus.SUCCEEDED)
    total_in = sum((r.usage or {}).get("input", 0) for r in results)
    total_out = sum((r.usage or {}).get("output", 0) for r in results)
    print(f"  峰值并发 RUNNING = {max_concurrent} / {len(handles)}")
    print(f"  期间进入过 RUNNING 的 = {len(running_seen)} / {len(handles)}")
    print(f"  成功 {ok_status}/{len(handles)}；usage 合计 in={total_in} out={total_out}")
    overlap = max_concurrent >= 2
    print(f"  {'✅' if overlap else '🔴'} 区间相交（并行）：{overlap}"
          f" —— 峰值同时 RUNNING {max_concurrent} 个"
          + ("" if overlap else "，看起来是排队执行（串行）"))
    return 0 if overlap else 1


def grant_longevity() -> int:
    """P2：grant TTL≥780s，真实起一次 → 等够时长 → 再验 grant 仍活。"""
    deadline_sec = 780
    ttl_ms = (deadline_sec + 60) * 1000
    print(f"== P2 · grant 长跑（TTL={ttl_ms}ms，等 {deadline_sec}s 后再验）==")
    gid = f"spike-p2-{int(time.time())}"
    with Grant.mint(_session("p2"), ttl_ms=ttl_ms) as grant:
        print(f"  grant expiresAt={grant.expires_at}  configPath={grant.config_path}")
        assert grant.config_path.exists(), "刚铸的 .mcp.json 不在？"
        client = MCPClient(grant.url, grant.token)
        client.initialize()
        ad = OpenClawRuntimeAdapter(client)
        # t=0：真实起一次，证明 grant 一开始就能用
        h = ad.start("emotion", "BIGA-20260101-000", _MIN_TASK, group_id=gid,
                     run_timeout_sec=120)
        [r0] = ad.wait([h], timeout_sec=120)
        print(f"  t=0 起一次：status={r0.status}  usage={r0.usage}")
        # 等够 deadline（纯日历时间，不产生新的模型调用）
        t0 = time.monotonic()
        print(f"  等 {deadline_sec}s（{time.strftime('%H:%M:%S')} 起）...")
        time.sleep(deadline_sec)
        waited = time.monotonic() - t0
        # t≈780s：再用同一个 grant 做一次真实 MCP 操作，验它还活着
        try:
            client2 = MCPClient(grant.url, grant.token)
            client2.initialize()
            tools = client2.list_tools()
            alive = any(t.get("name") == "sessions_spawn" for t in tools)
        except Exception as e:  # noqa: BLE001
            alive = False
            print(f"  t={waited:.0f}s 复用 grant 失败：{e}")
        print(f"  等了 {waited:.0f}s；grant 仍可用：{alive}")
        cfg_before_close = grant.config_path.exists()
    cfg_after_close = grant.config_path.exists()
    print(f"  .mcp.json：退出前存在={cfg_before_close}，退出后存在={cfg_after_close}")
    ok = alive and cfg_before_close and not cfg_after_close and waited >= deadline_sec
    print(f"  {'✅' if ok else '🔴'} grant 撑过 {deadline_sec}s 且 .mcp.json 被清理：{ok}")
    return 0 if ok else 1


def cancel_correlation() -> int:
    """验 `cancel(handle)` 杀的是**对的那一个** —— runId→taskId 的对应靠不靠谱。

    起两个，取消第一个，断言：第一个进 CANCELLED，第二个没有被连坐。
    """
    import json
    print("== cancel · runId→taskId 对应验证 ==")
    gid = f"spike-cancel-{int(time.time())}"
    with OpenClawRuntimeAdapter.attach(_session("cancel"), ttl_ms=300_000) as ad:
        long_task = "采集今日A股情绪数据并汇报（这是取消测试，慢慢来）。"
        h0 = ad.start("market", "BIGA-20260101-000", long_task, group_id=gid, run_timeout_sec=180)
        h1 = ad.start("emotion", "BIGA-20260101-000", long_task, group_id=gid, run_timeout_sec=180)
        print(f"  h0={h0.agent} runId={h0.run_id[:8]}  h1={h1.agent} runId={h1.run_id[:8]}")
        time.sleep(4)
        # 诊断：完整 dump 一次 list，看 active[]/tasks[] 有没有可对应的字段
        listing = ad._c.call_tool("subagents", {"action": "list", "recentMinutes": 60})
        print("  active[] runIds:", [a.get("runId", "?")[:8] for a in listing.get("active", [])])
        print("  tasks[]  taskIds:", [t.get("taskId", "?")[:8] for t in listing.get("tasks", [])])
        print("  tasks[0] full:", json.dumps(listing.get("tasks", [{}])[0], ensure_ascii=False))
        ad.cancel(h0)
        time.sleep(3)
        s0, s1 = ad.status(h0), ad.status(h1)
        print(f"  取消 h0 之后：h0={s0}  h1={s1}")
        ad.cancel(h1)  # 收尾，别留一个真跑的
    right_one_died = s0 == SpawnStatus.CANCELLED and s1 != SpawnStatus.CANCELLED
    print(f"  {'✅' if right_one_died else '🔴'} 取消杀的是对的那一个：{right_one_died}")
    return 0 if right_one_died else 1


def failure() -> int:
    """P3：故意让 spawn 失败，断言 Adapter 给的是结构化状态不是裸异常。"""
    print("== P3 · spawn 失败的结构化错误面 ==")
    gid = f"spike-p3-{int(time.time())}"
    with OpenClawRuntimeAdapter.attach(_session("p3"), ttl_ms=120_000) as ad:
        # 不存在的 agent 名 —— 运行时应结构化拒绝，Adapter 翻成 SpawnStartError
        try:
            ad.start("no-such-agent-xyz", "BIGA-20260101-000", _MIN_TASK, group_id=gid)
            print("  🔴 起一个不存在的 agent 竟然成功了？")
            return 1
        except SpawnStartError as e:
            print(f"  ✅ 不存在的 agent → SpawnStartError（结构化）：{str(e)[:120]}")
        except Exception as e:  # noqa: BLE001
            print(f"  🔴 抛的是裸异常 {type(e).__name__}，不是结构化的 SpawnStartError：{e}")
            return 1
    print("  ✅ 失败被翻译成可判断的状态，没让裸异常从 Adapter 里漏出去")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="批 C-I 的 spike 补验（会花钱，走真实运行时）")
    ap.add_argument("check", choices=["parallel", "grant-longevity", "failure",
                                      "cancel", "all"])
    a = ap.parse_args()
    if a.check == "all":
        rc = 0
        for fn in (parallel, failure, grant_longevity):
            rc |= fn()
            print()
        return rc
    return {"parallel": parallel, "grant-longevity": grant_longevity,
            "failure": failure, "cancel": cancel_correlation}[a.check]()


if __name__ == "__main__":
    raise SystemExit(main())
