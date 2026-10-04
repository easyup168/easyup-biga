"""外发通知的事件类型词表 + 分类判据 —— 契约层的唯一一份（批 G-I）。

设计文档 §6 批 G-I。这一批只做**推**（Outbound Only）：Card 完成 / UNKNOWN /
risk 否决 / 运行失败四类事件，与 Card 同事务入队 `notification_outbox`，
worker 异步投递。**不接受任何飞书方向的输入**（批 G-II）。

为什么词表 + 分类在契约层
--------------------------
`event_type` 的取值集与「一张卡算哪一类」的判据都只能有一份实现：
`_store.db.enqueue_notification` 落库前拿它做白名单（非法值 fail-closed 拒绝），
`card_ops.persist` 拿它把卡分类。两处各写一份词表必然漂（`STANCE_VOCAB` /
`RUN_STATES` 独立踩过——见 `run.py` docstring）。⇒ 和 `RunState` / `RUN_ORIGINS`
一样，放契约层，消费方都 import 这一份。

🔴 这不是「skill 做判断性归类」（开发约定 4 禁的那个）：那条禁的是 skill 把
「这算不算强势」这类**市场判断**塞进事实层。这里分的是「这张**已经产出的卡**
该走哪条通知」——一个确定性的、程序拥有的**工作流路由**（设计文档 §1
「Program decides workflow」），与市场判断无关。
"""

from __future__ import annotations

from .card import DecisionCard
from .run import RunState
from .verdict import VETO_STANCE

__all__ = [
    "CARD_COMPLETED",
    "CARD_UNKNOWN",
    "RISK_BLOCK",
    "RUN_FAILED",
    "NOTIFICATION_EVENT_TYPES",
    "CARD_EVENT_TYPES",
    "NOTIFY_FAILURE_STATES",
    "card_event_type",
]

#: 四类事件类型。
CARD_COMPLETED = "card_completed"   #: 正常收尾的卡（数据够、无否决、无 UNKNOWN）。
CARD_UNKNOWN = "card_unknown"       #: 卡产出了，但结论被不确定性主导（UNKNOWN / 缺失项）。
RISK_BLOCK = "risk_block"           #: 制衡层否决（某 verdict 的 stance = VETO_STANCE）。
RUN_FAILED = "run_failed"           #: 运行进入失败终态（FAILED / TIMEOUT / CANCELLED）。

#: 落库白名单（`enqueue_notification` fail-closed 校验它）。未知 event_type 是 bug，
#: 不是合法输入 —— 与 `RUN_ORIGINS` 同一个立场。
NOTIFICATION_EVENT_TYPES: frozenset[str] = frozenset(
    {CARD_COMPLETED, CARD_UNKNOWN, RISK_BLOCK, RUN_FAILED})

#: 从一张卡推出来的三类（`card_event_type` 的值域）—— 不含 run_failed（那来自运行终态，
#: 压根不经过 Card）。
CARD_EVENT_TYPES: frozenset[str] = frozenset(
    {CARD_COMPLETED, CARD_UNKNOWN, RISK_BLOCK})

#: 触发 run_failed 通知的运行终态。
#:
#: 🔴 **不含 INPUT_REQUIRED**（也是终态，但语义是「要人去看提示词」，不是「运行失败」，
#:    对应退出码 5 而非 1/4）——它是另一类事件，不在本批四类里。也不含 COMPLETED。
#:    ⇒ 这里显式列三个，不写成 `TERMINAL_STATES - {COMPLETED, INPUT_REQUIRED}`：
#:    显式列举读起来就是「这三个会推 run_failed」，减法式定义会把「将来往
#:    TERMINAL_STATES 加了个新终态」这件事静默卷进来。
NOTIFY_FAILURE_STATES: frozenset[str] = frozenset(
    {RunState.FAILED, RunState.TIMEOUT, RunState.CANCELLED})


def card_event_type(card: DecisionCard) -> str:
    """把一张**已经产出的** Decision Card 分到三类通知之一。纯函数。

    优先级（互斥，一张卡一类）：

      1. `risk_block` —— 任一 verdict 的 `stance == VETO_STANCE`。
         🔴 判据是 **stance 不是 status**：`status='BLOCK'` 也可能是判官自己权衡出来的
         （没有否决），而 `stance=VETO_STANCE` 是契约里「制衡层否决」的唯一权威信号
         （`DecisionCard.__post_init__` 就是按它强制「否决 ⇒ status 只能 AVOID/BLOCK」）。
         最高优先级：有否决就是最该提醒人的那件事。
      2. `card_unknown` —— 任一 verdict 的数据完整度 `verdict == 'UNKNOWN'`，或卡带着
         缺失项（`card.missing` 非空）。
         🔴 R-3：`UNKNOWN` / 缺失必须被**显式推出去**，不能藏在一个 `card_completed`
         的通知背后让人以为一切正常。判据取「任一/非空」而不是某个比例阈值，是**故意偏向
         报不确定**（fail-loud）—— 与全项目 fail-closed 一致，且阈值是拍脑袋、"任一" 不是。
      3. `card_completed` —— 其余（数据够、无否决、无 UNKNOWN、无缺失）。

    ⚠️ 现状下（Phase 2 出口条件未达成，risk 常报 UNKNOWN、缺失项常在），多数卡会落到
    `card_unknown`，`card_completed` 较少触发 —— 这是**如实反映**当前数据完整度，不是 bug。
    `card_completed` 不是死分支（L-7）：一张真正干净的卡（全 PASS、无缺失、无否决）就会命中它，
    交易日分裂修好后会常态化。将来若要调「多严算 unknown」，改这一个函数即可。
    """
    if not isinstance(card, DecisionCard):
        raise TypeError(
            f"card_event_type 只接受 _contract.DecisionCard，收到 {type(card).__name__}")
    if any(v.stance == VETO_STANCE for v in card.verdicts):
        return RISK_BLOCK
    if any(v.verdict == "UNKNOWN" for v in card.verdicts) or card.missing:
        return CARD_UNKNOWN
    return CARD_COMPLETED
