#!/usr/bin/env python3
"""risk-check —— 从 Stage 1 的**冻结证据**里算出风险事实，输出 FactBundle。

🔴 批 E-III：Facts/Assessment 拆分的最后一棒。risk 从产合体 `AgentVerdict` 迁到产
`FactBundle`（只事实、无 stance），形状与前五个一致。risk 的 stance 是 `VETO_STANCE`
（"否决"）—— 制衡层唯一能拦住 BUY 的信号，由 Risk Agent 事后 `amend_verdict.py --stance`
追加一个 `AgentAssessment`，经 `load_verdict` 多态压回 `AgentVerdict.stance`、被
`DecisionCard` 读到并拦截（穿透链见教程第 30 章）。
⚠️ risk 读**上游**五个 verdict 仍用 `load_verdict()`（多态，对新旧形状都返回
`AgentVerdict`）——risk 是它们的消费方，那一半跟这次迁移无关。

🔴 它不采数据
--------------
本脚本**没有任何数据源依赖**（不 import `_sources`，由测试钉死）。
输入只有 Stage 1 各 Specialist 的 `verdict_id`。

理由是制衡层的意义所在：risk 要审的是**别人看到的那份证据**。
它自己重采一遍，就会出现「risk 看到的市场」与「market 看到的市场」不是同一个 ——
那时候它审的是自己的幻觉，不是这次决策的依据。

    Stage 1 证据冻结 ⇒ Stage 2 只读不采 ⇒ 回放时两边看到的完全一样

🔴 它不给结论
--------------
本脚本只回答「哪些**写死的阈值**被触发了」「上游证据有多完整/多新鲜/是否自相矛盾」。

「这算不算该否决」是 Risk Agent 的判断（铁律 4）。
阈值本身写在 `THRESHOLDS` 里并带版本号 —— 改了阈值要升 `CALC_VERSION`，
否则历史结论无法清点。

用法::

    python3 risk_check.py --verdict-ids 21,22
    python3 risk_check.py --verdict-ids 21,22 --render
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from typing import Any

_HERE = pathlib.Path(__file__).resolve()
_REPO = _HERE.parent.parent.parent.parent
sys.path.insert(0, str(_REPO / "skills"))

from _contract import (  # noqa: E402
    OriginRef,
    verdict_origins,
    ADHOC_TASK_SEQ,
    CROSS_CHECK_PAIRS,
    STAGE1_AGENTS,
    AgentVerdict,  # 上游判定（load_verdict 多态返回）仍是 AgentVerdict —— risk 是消费方
    Evidence,
    FactBundle,
    MissingItem,
    new_task_id,
    now_cn,
)
from _store import init_schema, load_verdict, save_fact_bundle  # noqa: E402

AGENT = "risk"
CALC_VERSION = "risk-check/1"

#: 写死的风险阈值。`(字段, 比较, 阈值, 代码, 人话)`
#:
#: 🔴 这些数字**未经本项目验证**，与情绪分同样属于「业内常见口径」。
#:    它们的区分力要到测量阶段做过检验才算数。
#:    现在的作用只是把「哪些线被碰到了」变成可复查的事实，不是风险评分。
THRESHOLDS: tuple[tuple[str, str, float, str, str], ...] = (
    ("broken_rate",    ">", 0.50, "risk.emotion.broken_rate_high", "炸板率超过 50%，买盘承接明显不足"),
    ("volume_ratio",   ">", 2.00, "risk.market.volume_spike",      "量能是 20 日均量的 2 倍以上，异常放量"),
    ("volume_ratio",   "<", 0.50, "risk.market.volume_dry",        "量能不足 20 日均量的一半，极度缩量"),
    ("advance_ratio",  "<", 0.20, "risk.market.breadth_weak",      "上涨家数占比不足 20%，普跌"),
    ("max_streak",     ">", 6.99, "risk.emotion.streak_extreme",   "最高板 7 板以上，情绪处在高位"),
    ("limit_down_count", ">", 29.9, "risk.emotion.limit_down_many", "跌停家数超过 30，恐慌迹象"),
)

#: 已知互斥的 stance 组合 —— 上游互相打架时，下游不该假装看不见。
CONFLICT_PAIRS: tuple[tuple[str, str, str, str], ...] = (
    ("market", "放量上涨", "emotion", "恐慌"),
    ("market", "放量上涨", "emotion", "冰点"),
    ("market", "缩量上涨", "emotion", "亢奋"),
    ("market", "放量下跌", "emotion", "亢奋"),
)

#: A 股收盘时刻。用来判断「这批证据描述的那个交易时段还开着吗」。
#:
#: 🔴 为什么不直接数「超过 N 秒的证据有几条」
#:    第一版是那么写的，结果非交易日跑出来「25 条证据全部陈旧」——
#:    因为 as_of 停在上一个交易日收盘，距今几十小时。
#:    **一个在周末必然全红的指标，等于没有指标**（与 R-3 同源）。
#:    改成只报两个事实：最旧证据的年龄 + 那个交易时段是否还开着。
#:    「几十小时算不算陈旧」由 Risk Agent 结合 `session_live` 判断。
_CLOSE_HHMM = (15, 0)


def _cmp(value: Any, op: str, bound: float) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return v > bound if op == ">" else v < bound


def build_fact_bundle(*, verdict_ids: list[int], store: bool, task_id: str) -> FactBundle:
    t_start = time.monotonic()
    retrieved = now_cn()
    missing: list[MissingItem] = []
    warnings: list[str] = []

    upstream: list[AgentVerdict] = []
    #: 真的读出来了的那些 verdict_id —— 元数据类派生值的来源（裁定 16）。
    loaded_ids: list[int] = []
    for vid in verdict_ids:
        v = load_verdict(vid)
        if v is None:
            missing.append(MissingItem(
                f"上游判定 #{vid} —— 在 agent_verdicts 里不存在，无法审阅",
                "risk.upstream.verdict_not_found"))
            continue
        upstream.append(v)
        loaded_ids.append(vid)

    # 🔴 Stage 边界的身份守卫（外部评审 P1-2）。
    #
    # risk 原本只检查覆盖率、交易日、陈旧度、阈值、stance 冲突、跨源校验 ——
    # **唯独没有检查上游判定属不属于这次决策**。
    #
    # 于是可以出现：五个 Specialist 全在、trade_date 一致、coverage_ratio=1.0，
    # 而它们分别来自三个不同的决策。risk 会在这个错误组合上做出判断。
    #
    # ⚠️ 合成阶段那道闸门拦得住最终的卡，但拦不住这件事：
    #    **错误的 risk 判定已经生成、而且可能已经落库。**
    #    制衡层在污染的输入上得出的结论，事后拒绝那张卡也撤销不了。
    #
    # 处理方式沿用本项目的一贯口径：不崩溃、不静默，
    # **报成缺失并把结论压到 UNKNOWN** —— 出一张标着「不知道」的卡，
    # 比不出卡强得多，也比出一张看起来正常的卡强得多。
    #
    # 🔴 但第一版是「半硬」的（外部深度评审）
    # --------------------------------------
    # 它检测到 foreign 之后**照样把全套指标算完**，最后才在末尾把
    # `verdict` 压成 UNKNOWN。于是落库的那条判定长这样：
    #
    #     verdict: UNKNOWN
    #     result:  coverage_ratio=1.0, trade_date_consistent=True,
    #              max_source_lag_sec=…, max_evidence_age_sec=…, tripped_thresholds=[…]
    #     confidence: 0.9          ← 由 len(result) 算出来的
    #     warnings:  [阈值触发…]    ← 从别的决策的证据里算出来的
    #
    # 每一个数字都是**把三次决策的证据混在一起**算出来的，而它们
    # 看起来和正常判定逐字段同形。任何不去读 `verdict` 字段的消费方
    # ——回放脚本、台账、将来的模型分层——都会照常用它们。
    #
    # > `verdict` 说「不知道」，`result` 说得头头是道。
    # > 只要有一个消费方读后者不读前者，fail-closed 就漏了。
    #
    # ⇒ 改成**硬的**：立刻返回，`result` 里只留「归属本身」这一个事实。
    #   归属是关于**这次混淆**的事实，不是关于市场的事实，所以它可以留。
    foreign = sorted({v.task_id for v in upstream} - {task_id})
    if foreign:
        names = "、".join(
            f"{v.agent}({v.task_id})" for v in upstream if v.task_id != task_id)
        missing.append(MissingItem(
            f"风险判断的证据归属 —— 本次决策是 {task_id}，但上游判定来自 "
            f"{foreign}：{names}。不同决策的证据不能合在一起审 —— "
            "它们采自不同时刻，甚至可能是不同的市场状态",
            "risk.upstream.foreign_decision"))
        # ⚠️ 只放归属，不放任何**从混合证据算出来的**数。
        #    归属是关于「谁串进来了」的事实，risk 就在 `retrieved` 这一刻
        #    亲眼看到它 —— 所以它有正当的 as_of，不是凭空的数。
        #
        # 🔴 第一版把这两个字段直接塞进 `result`、`evidence=[]`，
        #    契约层当场拒绝构造：
        #      ValueError: result 字段无证据支撑 …（铁律 3）
        #    那次报错是对的。绕过它的办法（改成 `result={}`）会把排查
        #    需要的信息一起扔掉 ⇒ 正确做法是**老实给证据**。
        attribution = {
            "foreign_task_ids": foreign,
            "upstream_attribution": {v.agent: v.task_id for v in upstream},
        }
        return FactBundle(
            task_id=task_id, agent=AGENT, status="failed", verdict="UNKNOWN",
            result=attribution,
            # 🔴 `confidence` 不能沿用 `len(result)/_EXPECTED_FIELDS` ——
            #    那个公式衡量的是「算出来多少字段」，在这里会把
            #    「我拒绝判断」算成一个不低的置信度。
            data_completeness=0.0,
            evidence=[Evidence(
                field=k, source="derived:risk-check", value=val,
                as_of=retrieved, retrieved_at=retrieved,
                calc_version=CALC_VERSION,
                label="上游判定的决策号归属（不是市场事实）")
                for k, val in attribution.items()],
            warnings=warnings, missing=missing,
            elapsed_ms=int((time.monotonic() - t_start) * 1000))

    result: dict[str, Any] = {}
    evidence: list[Evidence] = []

    if not upstream:
        missing.append(MissingItem(
            "全部风险判据 —— 没有拿到任何上游判定，无从审起",
            "risk.upstream.none"))
        return FactBundle(
            task_id=task_id, agent=AGENT, status="failed", verdict="UNKNOWN",
            result={}, data_completeness=0.0, evidence=[], warnings=warnings,
            missing=missing, elapsed_ms=int((time.monotonic() - t_start) * 1000))

    # 🔴 risk 的结论不可能比它最旧的输入更新鲜。
    as_of = min(e.as_of for v in upstream for e in v.evidence) \
        if any(v.evidence for v in upstream) else retrieved

    #: 上游全部证据摊平一份 —— risk 的派生值引用的是**别人的**证据，
    #: 所以 `input_ids_for` 查的是这个池子，不是 risk 自己的 `evidence`。
    upstream_ev = [e for v in upstream for e in v.evidence]

    def add(field: str, value: Any, label: str, *,
            kind: str | None, origins: tuple = ()) -> None:
        """产出一条 risk 证据。

        `kind` 无默认值（裁定 16）。`origins` 收**已构造好的** `OriginRef` ——
        与各 Specialist 的 `inputs=(字段名,)` 不同：risk 引用的是别人的东西
        （上游证据、上游 verdict），按字段名查得先指明「在谁的池子里查」。

        ⚠️ risk 的证据没有 `raw_hash`（它不直接碰数据源）⇒ 凡是 `kind="derived"`
        的都**必须**给出 input_ids，否则契约当场拒绝（批 3 的铁律）。
        """
        result[field] = value
        evidence.append(Evidence(
            field=field, source="derived:risk-check", value=value,
            as_of=as_of, retrieved_at=retrieved,
            calc_version=CALC_VERSION, label=label,
            kind=kind, derived_from=origins))

    def _ids_of(*fields: str) -> tuple:
        """上游**支撑这些字段**的证据（值类派生的输入，裁定 16）。"""
        out = []
        for f in fields:
            out.extend(OriginRef("evidence", e.evidence_id)
                       for e in upstream_ev if e.field == f)
        return tuple(dict.fromkeys(out))

    def _derived_if(ids: tuple[str, ...]) -> str | None:
        """有输入才叫 derived。

        🔴 一个输入都没有时，这个值**不是从上游算出来的** —— 声明 derived 会把
        「没东西可比」说成「比过了」。返回 None（尚未归类）让它如实显示。

        ⚠️ 这不是按字符串形状猜类别（L-13），是按**这次运行真的有没有输入**判 ——
        同一个字段在有上游时是 derived、没上游时未归类，两者描述的确实是两件事。
        """
        return "derived" if ids else None

    def _all_upstream_ids() -> tuple:
        """上游**全部**证据（全量类派生的输入：取 max / 比 raw_hash 那几个）。"""
        return tuple(dict.fromkeys(OriginRef("evidence", e.evidence_id)
                                   for e in upstream_ev))

    present = sorted(v.agent for v in upstream)
    # 🔴 下面四条是**元数据类**（裁定 16）：依据是「哪些上游 verdict 到场了」及其
    #    元数据，不是任何一条证据的**值**。所以引的是 verdict 本身，不是证据 ——
    #    拿「上游全部证据 id」充数说的是「我看了这些数」，与「这些 verdict 存不存在」
    #    是两回事（批 4 之前 Evidence 表达不了这个，故曾留白）。
    _vo = verdict_origins(loaded_ids)
    add("upstream_agents", present, "到场的上游 Agent",
        kind=_derived_if(_vo), origins=_vo)
    add("coverage_ratio",
        round(len([a for a in present if a in STAGE1_AGENTS]) / len(STAGE1_AGENTS), 4),
        f"Stage 1 覆盖率(应到 {len(STAGE1_AGENTS)})", kind=_derived_if(_vo), origins=_vo)

    absent = [a for a in STAGE1_AGENTS if a not in present]
    if absent:
        missing.append(MissingItem(
            f"风险面不完整 —— Stage 1 缺席：{'、'.join(absent)}，"
            f"这些领域的风险本次没有被看过",
            "risk.upstream.coverage_incomplete"))

    add("upstream_missing_count", sum(len(v.missing) for v in upstream),
        "上游缺失项总数", kind=_derived_if(_vo), origins=_vo)

    # --- 上游自报的交易日是否一致 ---
    dates = {v.agent: v.result.get("trade_date") for v in upstream
             if v.result.get("trade_date")}
    consistent = len(set(dates.values())) <= 1
    _td = _ids_of("trade_date")
    add("trade_date_consistent", consistent, "上游交易日是否一致",
        kind=_derived_if(_td), origins=_td)
    if dates:
        add("upstream_trade_date",
            next(iter(set(dates.values()))) if consistent else sorted(set(dates.values())),
            "上游自报的交易日", kind=_derived_if(_td), origins=_td)
    if not consistent:
        missing.append(MissingItem(
            f"风险判断的时间基准 —— 上游报告了不同的交易日 {dates}，"
            f"不能当作同一天的事实一起审",
            "risk.upstream.trade_date_inconsistent"))

    # --- 证据新鲜度 ---
    # 🔴 批 R / 评审 E-19：这里以前只报一个数，叫 `max_staleness_sec`，标签写
    #    「最旧证据的年龄(秒)」—— 而它算的是**取数滞后**（取回时刻 − 数据时刻），
    #    根本不是年龄。一份三天前冻的快照，只要当初抓取花了 2 秒，它就报 2。
    #    名字把口径说错了，于是用的人也就用错了。
    #
    #    现在两个数都报，各自叫对名字：
    #      · max_source_lag_sec   —— 数据产生到我们取到它，隔了多久
    #      · max_evidence_age_sec —— 到**本次风险判断这一刻**为止，最旧证据多老
    #    后者用 `age_at(retrieved)` 而不是 `age_sec`：基准是 risk 自己这次运行的
    #    时刻，且这个时刻已随 risk 的证据落库 ⇒ 回放读同一份 verdict 得到同一个数。
    lags = [e.source_lag_sec for v in upstream for e in v.evidence]
    if lags:
        add("max_source_lag_sec", max(lags), "取数滞后：取回时刻−数据时刻，最大值(秒)",
            kind="derived", origins=_all_upstream_ids())
        add("max_evidence_age_sec",
            max(e.age_at(retrieved) for v in upstream for e in v.evidence),
            "最旧证据的年龄(秒)", kind="derived", origins=_all_upstream_ids())
    today = retrieved.strftime("%Y%m%d")
    live = (consistent and dates and next(iter(set(dates.values()))) == today
            and (retrieved.hour, retrieved.minute) < _CLOSE_HHMM)
    # 由上游交易日 + risk 自己这次运行的时刻算出 ⇒ 输入是那几条 trade_date 证据。
    add("session_live", bool(live), "证据所属交易时段是否仍在进行",
        kind=_derived_if(_td), origins=_td)

    # --- 写死阈值 ---
    tripped: list[str] = []
    for field, op, bound, code, why in THRESHOLDS:
        for v in upstream:
            if field in v.result and _cmp(v.result[field], op, bound):
                tripped.append(code)
                warnings.append(f"[{code}] {why}（{v.agent}.{field}={v.result[field]}）")
                break
    # 阈值比对的输入是被比的那些字段的证据 —— 阈值本身是常量，随 CALC_VERSION 版本化。
    #
    # 🔴 上游一个可比字段都没有时**不产出这个字段**（批 5）。
    #    以前无论如何都报 `tripped_thresholds=[]`，而空列表在卡面上读起来是
    #    「比过了，没有触发」—— 实际是「没东西可比」。同一个 `[]` 表示两件相反的事：
    #    一个是「已核对，安全」，一个是「没核对」。这正是 R-3 要防的形状，
    #    而且方向最坏：它把**未知**显示成**安全**。
    #
    #    判据用 `_th`（输入证据）而不是「字段在不在 result 里」：没有输入就是没比过，
    #    两者在这里是同一件事，用前者顺带保证了产出的那条一定说得出出处。
    _th = _ids_of(*{f for f, *_ in THRESHOLDS})
    if _th:
        add("tripped_thresholds", tripped, "被触发的风险阈值",
            kind="derived", origins=_th)
    else:
        missing.append(MissingItem(
            "风险阈值 —— 上游一个可比字段都没有（炸板率/量能比/涨跌家数占比…全缺），"
            "无法判断有没有触发。**空列表不等于没触发**",
            "risk.thresholds.nothing_to_check"))

    # --- 被声明的重复事实：两个 agent 从同一个源取同一个值 ---
    # 🔴 裁定 15 的受控例外：允许重复，**前提是有人核对**。
    #
    # 判据从「值相等」改成「出自同一份冻结数据」（批 D-II）。批 D-II 之后
    # market 与 technical 读的是**同一份**冻结快照（SnapshotCoordinator 冻结、
    # 二者都带 --evidence-set-id 读），sh_close 与 close 由同一份数据算出，
    # **值必然相等** —— 再比值就退化成恒真死配置（L-7，设计文档 §6 批 D 点名要防）。
    #
    # 改成核对两条 Evidence 的 raw_hash（= 冻结集登记的 content_sha256，整份 raw
    # 的指纹）是否相同：
    #   · 都读了同一份冻结快照 ⇒ 两个 raw_hash 都是那份的指纹 ⇒ 相同 ⇒ 不报。
    #   · 某个 Specialist 没传 --evidence-set-id 悄悄退回独立抓取 ⇒ 它的 raw_hash
    #     出自另一份数据（根数不同 / 抓取时刻不同）⇒ 不同 ⇒ 报红。
    # 它仍然会红（探针 P2 钉住），只是守的东西从「数值凑巧对上」变成「真的共享了
    # 同一份数据」—— 共享之后这条检查才不是恒真，而是「谁没读冻结快照」的探照灯。
    ev_by_agent = {v.agent: {e.field: e for e in v.evidence} for v in upstream}
    xconf: list[str] = []
    for a, fa, b, fb, label in CROSS_CHECK_PAIRS:
        ea = ev_by_agent.get(a, {}).get(fa)
        eb = ev_by_agent.get(b, {}).get(fb)
        if ea is None or eb is None:
            continue  # 某一方没产出这条证据 —— 归覆盖率/缺失项管，不在这里判
        # 🔴 批 E-I：优先比 evidence_set_id —— 结构验证「真的读了同一个冻结集」，
        #    比 raw_hash 硬。D-II 留的账：两次独立抓取碰巧逐字节相同时 raw_hash 会
        #    碰巧相等、漏报「悄悄退回独立抓取」；evidence_set_id 不同就是不同，不看内容。
        #    ⚠️ 两条都有 evidence_set_id 才用它；只要有一条没有（老 Specialist 还没填
        #    这个字段），退回 raw_hash 比较 —— 不能因为一方字段缺失就让整条检查失效。
        if ea.evidence_set_id is not None and eb.evidence_set_id is not None:
            if ea.evidence_set_id != eb.evidence_set_id:
                xconf.append(
                    f"{label}: {a}.{fa} 与 {b}.{fb} 出自不同冻结集"
                    f"（evidence_set_id {ea.evidence_set_id} ≠ {eb.evidence_set_id}）")
            continue
        ha, hb = ea.raw_hash, eb.raw_hash
        if ha is None or hb is None:
            # sh_close/close 都有 raw 来源，两个溯源字段都为空本身就是异常：无从核实
            # 是否同源 ⇒ 按不一致处理（fail-closed，不给「查不了就放过」）。
            xconf.append(f"{label}: {a}.{fa} 或 {b}.{fb} 既无 evidence_set_id 又无 raw_hash，无法核实是否同源")
        elif ha != hb:
            xconf.append(f"{label}: {a}.{fa} 与 {b}.{fb} 出自不同数据"
                         f"（raw_hash {ha[:12]}… ≠ {hb[:12]}…）")
    _allu = _all_upstream_ids()
    add("cross_check_conflict", xconf, "跨源校验：两者是否读同一份冻结数据",
        kind=_derived_if(_allu), origins=_allu)
    if xconf:
        missing.append(MissingItem(
            "风险判断的事实基准 —— " + "；".join(xconf)
            + " —— 两个 Agent 本该读同一份冻结快照，raw_hash 却不同，"
            "说明至少一方退回了独立抓取，它们看到的不是同一份数据",
            "risk.upstream.cross_check_conflict"))

    # --- 上游 stance 互斥 ---
    stances = {v.agent: v.stance for v in upstream if v.stance}
    conflicts = [f"{a}={x} 与 {b}={y}"
                 for a, x, b, y in CONFLICT_PAIRS
                 if stances.get(a) == x and stances.get(b) == y]
    add("stance_conflict", conflicts, "上游判断互相矛盾之处",
        kind=_derived_if(_vo), origins=_vo)
    if conflicts:
        warnings.append("上游判断互斥：" + "；".join(conflicts)
                        + " —— 至少有一方是错的，不要各取所需")

    # 🔴 没有 stance 的上游 = 它没给判断。risk 审的是判断，不是只审数字。
    silent = [v.agent for v in upstream if not v.stance and v.verdict != "UNKNOWN"]
    if silent:
        missing.append(MissingItem(
            f"上游判断 —— {'、'.join(silent)} 没有给出方向判断，无法审阅其结论",
            "risk.upstream.stance_absent"))

    # ⚠️ 这里原本还有一个 `if foreign:` 分支，把结论压成 UNKNOWN。
    #    现在 foreign 在上面就返回了 —— 走到这里 `foreign` 必然是空的。
    #    留一条断言而不是留那个分支：**死代码会让人以为防护还在这儿**，
    #    下次改这段的人会绕着它走，而真正的防护在两百行之前。
    assert not foreign, "foreign 应当已在上游归属检查处返回"

    core = {"coverage_ratio", "tripped_thresholds", "trade_date_consistent"}
    if not missing:
        status, level = "completed", "PASS"
    elif core <= set(result):
        status, level = "partial", "WARNING"
    else:
        status, level = "partial", "UNKNOWN"

    # 🔴 批 E-III：产 FactBundle（只事实、无 stance）。stance（否决/放行/…）由 Risk Agent
    #    事后 amend_verdict.py --stance 追加一个 AgentAssessment，不重打这份事实。
    v = FactBundle(
        task_id=task_id, agent=AGENT, status=status, verdict=level,
        result=result, data_completeness=round(len(result) / _EXPECTED_FIELDS, 2) if result else 0.0,
        evidence=evidence, warnings=warnings, missing=missing,
        elapsed_ms=int((time.monotonic() - t_start) * 1000))
    return v


#: 数据齐备时应当产出的字段数（同 market/emotion，故意不截断，由测试钉死）。
#: ⚠️ 手工维护、且**故意不截断到 1.0** —— 加字段忘了改这里，`data_completeness`
#:    会算出 >1，被契约当场拒掉（批 R 加 `max_evidence_age_sec` 时就是这么发现的：
#:    11/10 = 1.1 → ValueError）。刺耳但正确：截断会把「口径变了」悄悄抹平。
_EXPECTED_FIELDS = 11


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="从 Stage 1 冻结证据算风险事实 → FactBundle JSON（批 E-III）")
    ap.add_argument("--verdict-ids", required=True,
                    help="Stage 1 各 Specialist 的 verdict_id，逗号或空格分隔")
    ap.add_argument("--task-id", help="BIGA-YYYYMMDD-NNN，缺省自动生成")
    ap.add_argument("--run-id", default=None,
                    help="本次编排执行尝试的 run_id（RunContext.run_id），由 Supervisor "
                         "传下来落进 agent_verdicts.run_id。只 capture 不校验，缺省 None")
    ap.add_argument("--no-store", action="store_true", help="不落判定原件")
    ap.add_argument("--render", action="store_true", help="附带人类可读摘要")
    args = ap.parse_args(argv)

    ids = []
    for raw in args.verdict_ids.replace(",", " ").split():
        if not raw.isdigit():
            print(f"--verdict-ids 只接受数字 id，收到 {raw!r}。"
                  f"这个 id 由各 Specialist 的 skill 在 stderr 上打印：verdict_ref=NN",
                  file=sys.stderr)
            return 2
        ids.append(int(raw))

    store = not args.no_store
    if store:
        init_schema()

    fb = build_fact_bundle(verdict_ids=ids, store=store,
                           task_id=args.task_id or new_task_id(ADHOC_TASK_SEQ))
    # 🔴 批 E-III：事实原件（FactBundle，不含 stance）直接落库；stance 由 Risk Agent 事后追加。
    # 🔴 批 F：这个决策的 risk 事实由编排器在 spawn 之前就算好落库了 —— 你（risk）不该再
    #    自己跑一遍。真跑了会撞上「一个 (task_id, agent) 至多一份 fact」的唯一索引，
    #    save_fact_bundle 抛一个指路的 ValueError。这里接住它、干净退出（码 2），不让它
    #    变成一坨 traceback（dev-workflow §8：报错要指路）。
    try:
        ref = save_fact_bundle(fb, run_id=args.run_id) if store else None
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    print(json.dumps(fb.to_dict(), ensure_ascii=False, indent=2))
    if ref is not None:
        print(f"verdict_ref={ref}", file=sys.stderr)

    if args.render:
        print("\n" + "─" * 60, file=sys.stderr)
        print(f"{AGENT}  {fb.status}/{fb.verdict}  耗时 {fb.elapsed_ms}ms"
              + (f"  verdict_ref={ref}" if ref else "  (未落库)"), file=sys.stderr)
        for e in fb.evidence:
            print(f"  {e.display_label:<26} = {e.value}", file=sys.stderr)
        for w in fb.warnings:
            print(f"  ⚠ {w}", file=sys.stderr)
        for m in fb.missing:
            print(f"  ⚠ 缺失 [{m.code}] {m}", file=sys.stderr)

    return {"PASS": 0, "WARNING": 2}.get(fb.verdict, 3)


if __name__ == "__main__":
    raise SystemExit(main())
