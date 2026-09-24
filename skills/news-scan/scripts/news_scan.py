#!/usr/bin/env python3
"""快讯扫描 → FactBundle。`news` Agent 的唯一算子。

🔴 批 E-II：从产合体 `AgentVerdict` 迁到产 `FactBundle`（只事实、无 stance），形状与
E-I 迁 emotion 完全一致。news 的 stance（利好/利空/平静…）由 News Agent 事后
`amend_verdict.py --stance` 追加一个 `AgentAssessment`（不重打事实与原文）。
消费方零改动（`load_verdict` 多态）。

🔴 这是第一个「skill 算不出结论」的 Specialist
================================================
前五个 Agent 的形状都是：skill 把数字算完，agent 复述并从词表里挑一个 stance。
news 不一样 —— **「这条消息是利好还是利空」没有算式**。

于是两条铁律在这里指向同一个方向：

* 铁律 3「Agent 不做算术」 ⇒ 条数、时间、覆盖度由这里算
* 铁律 4「skill 不做判断性归类」 ⇒ 消息的**含义**留给 agent

所以本 skill 的产出是：**事实 + 原文**。它不打情绪分，也不分利好利空。

回放怎么办
----------
前五个 Agent 的回放是确定的：同样的输入，Python 必然给出同样的数。
news 的判断由模型做 ⇒ 同样的输入可能给出不同的 stance。

这不是缺陷，但必须让它**可测量**：

1. 原文（含 `id`）随证据冻结进 `agent_verdicts`
2. 回放时喂的是**同一批 id 与同一段文本**（`content_sha256` 可校验）
3. ⇒ 输入相同而结论不同 ⇒ 那是模型的不确定性，**是一个能被统计的量**

没有第 1、2 步的话，结论不同时无法区分「模型飘了」和「新闻变了」。

🔴 news 不得产出数字
====================
实测盘中 100 条快讯里有 21 条是机器行情播报：

    「深证成指涨1.00%，现报13777.470点；上证指数涨0.44%，现报3929.027点」

这些是 `market` / `sector` 已经权威产出的事实（裁定 15）。
本 skill 给它们打 `is_quote` 标记但**不过滤**（过滤是判断性归类，
而且误伤一条真消息在下游完全看不出来），并由 agent 契约明令：
**不许从快讯文本里重新提取任何行情数字。**
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import time
from datetime import timedelta
from typing import Any

_HERE = pathlib.Path(__file__).resolve()
_REPO = _HERE.parent.parent.parent.parent
sys.path.insert(0, str(_REPO / "skills"))

from _contract import (  # noqa: E402
    input_ids_for,
    resolve_provenance,
    ADHOC_TASK_SEQ,
    Evidence,
    FactBundle,
    MissingItem,
    new_task_id,
    now_cn,
)
from _sources import (  # noqa: E402
    SourceError,
    market_is_open,
)
from _sources.sina_news import STALE_SEC, fetch_feed  # noqa: E402
from _store import (  # noqa: E402
    init_schema,
    raw_text_sha256,
    save_fact_bundle,
    save_raw_snapshot,
)

AGENT = "news"
CALC_VERSION = "news-scan/1"

#: 回看窗口与条数上限 —— **两个数是一起定的，不是各拍一个。**
#:
#: 🔴 先问「什么时候**不该**红」（开发流程第 7 条）。
#:
#: 第一版写的是窗口 180 分钟、上限 40 条。实跑出来：
#: 窗口内 250 条，只展示 40 条，`items_truncated=210`，**而 verdict 是 PASS**。
#: agent 只看到 16% 的消息却说「数据完整」—— 那正是 R-3 要防的静默 fail-open。
#:
#: 重新按实测分布定额（盘中，取自 500 条样本）：
#:
#: | 窗口 | 条数 | 字符 | ≈token |
#: |---|---|---|---|
#: | 30 分钟 | 43 | 5.4k | 3.4k |
#: | **60 分钟** | **93** | **9.4k** | **5.9k** |
#: | 120 分钟 | 179 | 23k | 14k |
#: | 180 分钟 | 245 | 36k | 22k |
#:
#: ⇒ 窗口 60 分钟、上限 120 条：盘中典型 93 条 < 120，**正常不截断**。
#: 于是「截断」真发生时就是一个有信息量的信号（消息量突然翻倍），
#: 而不是每次都在的常驻噪音。
#:
#: 代价是只能就最近一小时发言。这是**诚实的小范围**，
#: 好过「三小时」这个读了 16% 就敢下的大结论。
WINDOW_MIN = 60
MAX_ITEMS = 120
#: 取几页原始数据。1 页 100 条；盘中 300 条 ≈ 2.8 小时，
#: 远超 60 分钟窗口 ⇒ `news.window.incomplete` 正常也不会红。
FETCH_PAGES = 3

_EXPECTED_FIELDS = 11


def build_fact_bundle(
    *, break_source: set[str], store: bool, task_id: str,
    window_min: int = WINDOW_MIN, max_items: int = MAX_ITEMS,
) -> FactBundle:
    t_start = time.monotonic()
    result: dict[str, Any] = {}
    evidence: list[Evidence] = []
    warnings: list[str] = []
    missing: list[MissingItem] = []
    retrieved = now_cn()
    # 取到快讯之前，能诚实声明的 as_of 就只有「此刻」。
    as_of = retrieved

    # 🔴 news 没有「交易日」这个概念 —— 7x24 快讯是连续流。
    #    它能诚实声明的只有：**最新一条是哪天的**。
    #    这让它和 emotion 站在同一边（实时源说「此刻」），
    #    与日线类 Agent（说「上一个已完成交易日」）不同。
    #    那个分裂已记在 phase-2-specialists.md §3.11，不在这里现编一个折中。
    live = market_is_open(retrieved)

    # 🔴 外部评审 P2-1：news 已经在落 raw snapshot，但证据**不带 raw_hash**
    #    ⇒ 拿着一条 `item_count=48` 查不回「它出自哪一份报文」。
    #
    #    与 market / sector 的做法对齐：非 `derived:` 的证据指回 raw 层。
    #    派生字段返回 None —— **不硬凑一个哈希**，凑出来的溯源比没有更糟，
    #    它会让人以为查得到。
    raw_hashes: dict[str, str] = {}

    def add(field: str, value: Any, label: str, source: str, *,
            kind: str | None, inputs: tuple[str, ...] = ()) -> None:
        """`kind` 无默认值（裁定 16）。`kind=None` = 尚未归类，调用点写明原因。"""
        result[field] = value
        evidence.append(Evidence(
            field=field, value=value, source=source, label=label,
            as_of=as_of, retrieved_at=retrieved, calc_version=CALC_VERSION,
            raw_hash=resolve_provenance(source, raw_hashes),
            kind=kind,
            input_evidence_ids=input_ids_for(evidence, inputs, of=field)))

    # 🔴 字段名不能叫 `session_live` —— `risk` 已经有一个同名字段，
    #    但那个问的是「上游数据所属的交易日过完了没」，
    #    而这个问的是「**此刻**市场在不在交易」。周六 10:00 两者相反。
    #
    #    同名不同事实，和「同一事实两个名字」（market.sh_close vs
    #    technical.close）是镜像问题，同样糟：
    #    前者让人以为是一个数，后者让守卫看不见重复。
    #    单一生产方守卫抓到了这一条 —— 它比的是名字，这次正好对上。
    # kind 暂缺：由交易日历 fact + 时钟算出，输入既不是原始响应也不是
    # 已有证据。批 4 连同 tradetime 那一类一起定（裁定 16 的第 4 类）。
    add("market_open", live, "此刻是否连续竞价时段", "derived:tradetime", kind=None)
    # 🔴 裁定 16 的 parameter：回看窗口是**我们自己的设定**，不是关于市场的事实。
    # 与 risk 的 THRESHOLDS 同类，只是那边根本不进 evidence、这边要上卡给人看。
    add("window_min", window_min, "回看窗口(分钟)", "derived:config", kind="parameter")

    src = "sina:7x24/zhibo152"
    feed = None
    if "feed" in break_source:
        missing.append(MissingItem(
            "全部快讯 —— 数据源被人为中断（--break-source feed）",
            "news.feed.source_broken"))
    else:
        try:
            feed = fetch_feed(pages=FETCH_PAGES)
        except SourceError as e:
            missing.append(MissingItem(
                f"全部快讯 —— 数据源不可用: {e}", "news.feed.unavailable"))

    if feed is not None:
        # 🔴 7x24 是**连续事件流**，它没有「收盘」这个概念。
        #
        # 原来是把最新一条的日期喂给 `as_of_for_trade_date()` —— 那个函数是
        # 给**日线**用的：交易日过完了就返回当天 15:00。
        #
        # 于是（外部评审 P1-3，已实测量化）：
        #
        #   盘中 14:35   as_of=14:35   staleness   0 分钟   ← 碰巧对
        #   收盘后 20:00 as_of=15:00   staleness 300 分钟   ← 🔴 假陈旧
        #   深夜 23:50   as_of=15:00   staleness 530 分钟   ← 🔴
        #
        # 而真实情况是最新一条通常只有几分钟前。这会污染
        # `staleness_sec`、risk 的 `max_evidence_age_sec`、新鲜度判断、
        # 以及日后所有的数据质量统计。
        #
        # ⚠️ 它**盘中是对的** —— 这正是它躲过一整天测试的原因：
        #    当天所有实测都发生在 15:00 之前。
        #
        # ⇒ 对连续事件流，唯一诚实的 as_of 是**最新一条的真实时刻**。
        as_of = feed.items[0].at
        newest_day = as_of.strftime("%Y%m%d")
        # 🔴 哈希**无条件算**，不只在 store 时算 —— 否则 `--no-store` 跑出来的
        #    证据没有 raw_hash，而那正是人工核对时最常用的一条路径。
        #    批 I：基于原始响应文本算，与 save_raw_snapshot 的 content_sha256 同口径。
        raw_hashes[src] = raw_text_sha256(feed.raw_text)
        # 落 raw 放在 as_of 算出来之后 —— 原样落盘的那份也要标对时刻。
        if store:
            save_raw_snapshot(source=src, as_of=as_of.isoformat(),
                              retrieved_at=retrieved.isoformat(),
                              payload=feed.raw, raw_text=feed.raw_text)
        # source 改用真实表键（原来写的 `sina:7x24` 是泛化标签，解析不到指纹）。
        add("trade_date", newest_day, "最新一条所属日期", f"derived:{src}", kind="derived")

        cutoff = retrieved - timedelta(minutes=window_min)
        inwin = [i for i in feed.items if i.at >= cutoff]

        # 🔴 窗口有没有被覆盖全。
        #    取回来的最旧一条**仍然晚于**窗口起点 ⇒ 这段时间我们没看过。
        #    不报的话，agent 会理直气壮地说「近三小时没有重大消息」——
        #    而它其实只看了一小时。
        oldest = feed.items[-1].at
        complete = oldest <= cutoff
        if not complete:
            gap = int((oldest - cutoff).total_seconds() / 60)
            missing.append(MissingItem(
                f"窗口前 {gap} 分钟的快讯 —— 取回 {len(feed.items)} 条只覆盖到 "
                f"{oldest:%H:%M}，窗口起点是 {cutoff:%H:%M}，"
                "这段时间没有被看过，不能说「没有重大消息」",
                "news.window.incomplete"))

        add("item_count", len(inwin), "窗口内条数", src, kind="derived")
        add("quote_count", sum(1 for i in inwin if i.is_quote),
            "其中机器行情播报", src, kind="derived")

        if inwin:
            newest = inwin[0].at
            stale = int((retrieved - newest).total_seconds())
            add("newest_at", newest.isoformat(), "最新一条的时刻", src, kind="derived")
            # ⚠️ 这个 `staleness_sec` 与 `Evidence.source_lag_sec`（批 R 前叫
            #    staleness_sec）**不是一回事**：这里是「最新一条快讯距现在多久」，
            #    量的是**新闻源静不静**；那个量的是**取数滞后**。同名不同义容易
            #    被顺手「统一」掉 —— 别改，两者各自的标签才是口径。
            add("staleness_sec", stale, "距最新一条(秒)", src, kind="derived")

            # 🔴 静默守卫**只在盘中启用**。
            #    非盘中静默是常态（夜间实测间隔可达 1830s，周末只会更长），
            #    在那里设线只会得到又一个「周末必红」的灯。
            #    盘中实测最大间隔 235s，600s 留了 2.5 倍余量。
            if live and stale > STALE_SEC:
                missing.append(MissingItem(
                    f"最近 {stale // 60} 分钟的快讯 —— 连续竞价时段内静默超过 "
                    f"{STALE_SEC // 60} 分钟（实测盘中最大间隔仅 4 分钟）。"
                    "可能是源出了问题，**也可能今天是节假日** —— "
                    "本系统尚无交易日历。两种情况都不能当成「没消息」",
                    "news.feed.stale"))

            tags = collections.Counter(t for i in inwin for t in i.tags)
            add("tag_counts", dict(tags.most_common()), "来源自带分类分布", src, kind="derived")

            shown = inwin[:max_items]
            # 🔴 不是 parameter：它是「窗口内条数」被上限截断后的结果 —— 有真实数据输入。
            # 原先 source 标成 derived:config 掩盖了这一点（批 2 已指出标错）。
            add("items_shown", len(shown), "交给 agent 读的条数", "derived:config",
                kind="derived", inputs=("item_count",))
            add("items_truncated", max(0, len(inwin) - len(shown)),
                "因上限未展示的条数", "derived:config", kind="derived", inputs=("item_count",))
            # 🔴 原文随证据冻结 —— 回放要靠它证明「喂进去的是同一批文本」。
            add("items", [{"id": i.id, "at": i.at.strftime("%H:%M"),
                           "quote": i.is_quote, "text": i.text}
                          for i in shown], "快讯原文", src, kind="derived")
            # 🔴 截断是**缺失**，不是 warning。
            #    agent 没读过的消息里可能正好有那条重要的，
            #    而它读完展示的部分会理直气壮地说「没有重大消息」。
            #    额度已按实测定到正常不截断 ⇒ 这条红了就是真有事。
            if len(inwin) > len(shown):
                missing.append(MissingItem(
                    f"窗口内另外 {len(inwin) - len(shown)} 条快讯 —— "
                    f"{window_min} 分钟内有 {len(inwin)} 条，超过 {max_items} 条上限，"
                    "未展示的部分没有被看过。消息量异常放大本身也是一个信号",
                    "news.window.truncated"))
        else:
            missing.append(MissingItem(
                f"近 {window_min} 分钟的快讯 —— 窗口内一条都没有。"
                "本源实测凌晨仍有 21 条/小时，空窗口意味着取数或过滤出了问题",
                "news.window.empty"))

    core = {"trade_date", "item_count", "items"}
    if not missing:
        status, level = "completed", "PASS"
    elif core <= set(result):
        status, level = "partial", "WARNING"
    else:
        status, level = "partial", "UNKNOWN"

    # 🔴 批 E-II：产 FactBundle（只事实、无 stance）；stance 由 News Agent 事后追加。
    return FactBundle(
        task_id=task_id, agent=AGENT, status=status, verdict=level,
        result=result,
        data_completeness=round(len(result) / _EXPECTED_FIELDS, 2) if result else 0.0,
        evidence=evidence, warnings=warnings, missing=missing,
        elapsed_ms=int((time.monotonic() - t_start) * 1000))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="快讯扫描 → FactBundle JSON（批 E-II）")
    ap.add_argument("--break-source", action="append", default=[], metavar="NAME",
                    help="演练：人为中断 (feed)")
    ap.add_argument("--window-min", type=int, default=WINDOW_MIN)
    ap.add_argument("--max-items", type=int, default=MAX_ITEMS)
    ap.add_argument("--task-id")
    ap.add_argument("--run-id", default=None,
                    help="本次编排执行尝试的 run_id（RunContext.run_id），由 Supervisor "
                         "传下来落进 agent_verdicts.run_id。只 capture 不校验，缺省 None")
    ap.add_argument("--no-store", action="store_true")
    ap.add_argument("--render", action="store_true")
    args = ap.parse_args(argv)

    store = not args.no_store
    if store:
        init_schema()
    fb = build_fact_bundle(break_source=set(args.break_source), store=store,
                           task_id=args.task_id or new_task_id(ADHOC_TASK_SEQ),
                           window_min=args.window_min, max_items=args.max_items)
    # 🔴 批 E-II：事实原件（FactBundle，不含 stance）直接落库；stance 由 agent 事后追加。
    ref = save_fact_bundle(fb, run_id=args.run_id) if store else None

    print(json.dumps(fb.to_dict(), ensure_ascii=False, indent=2))
    if ref is not None:
        print(f"verdict_ref={ref}", file=sys.stderr)
    if args.render:
        print("\n" + "─" * 60, file=sys.stderr)
        print(f"{AGENT}  {fb.status}/{fb.verdict}  耗时 {fb.elapsed_ms}ms"
              + (f"  verdict_ref={ref}" if ref else "  (未落库)"), file=sys.stderr)
        for e in fb.evidence:
            val = e.value
            if e.field == "items":
                val = f"{len(val)} 条（最新：{val[0]['text'][:36]}…）" if val else "无"
            print(f"  {e.display_label:<20} = {val}", file=sys.stderr)
        for w in fb.warnings:
            print(f"  ⚠ {w}", file=sys.stderr)
        for m in fb.missing:
            print(f"  ⚠ 缺失 [{m.code}] {m}", file=sys.stderr)
    return {"PASS": 0, "WARNING": 2}.get(fb.verdict, 3)


if __name__ == "__main__":
    raise SystemExit(main())
