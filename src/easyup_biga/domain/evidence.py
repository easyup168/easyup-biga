"""Evidence —— 一条可追溯的事实。

契约层是 Evidence / AgentVerdict / DecisionCard 的**唯一实现**。
任何 agent 或脚本不得自建第二套（由 tests/test_contract_single_impl.py 钉死）。

设计要点
--------
* `as_of` 是**数据本身的时间**，不是取回时间。混淆这两者会让盘中数据与收盘数据
  被当作同一时刻的事实汇总，这是上游文档 §9 点名要防的。
* `field` 指向它支撑的 `AgentVerdict.result` 的哪个键。没有它，「每个 result 字段
  必须能追到至少一条 Evidence」这条铁律就只能靠人看，无法用代码钉死。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ._freeze import deep_freeze, thaw

__all__ = ["Evidence", "CN_TZ", "now_cn"]

# A 股市场时区。证据的时间一律带 tzinfo —— naive datetime 在跨日聚合时会静默错位。
CN_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

#: 允许 `retrieved_at` 超前真实时钟多少秒。
#: 只为机器间的时钟抖动留口子 —— 不是为了容忍错误的日期推断。
_FUTURE_TOLERANCE_SEC = 120


def now_cn() -> datetime:
    """当前时刻（东八区，带 tzinfo）。"""
    return datetime.now(CN_TZ)


@dataclass(frozen=True)
class Evidence:
    """一条事实 + 它的来源与时间。

    Attributes:
        field: 它支撑 `AgentVerdict.result` 里的哪个键。
        source: 事实的出处。约定格式 ``<域>:<定位>``，例如
            ``biga.db:raw_market_snapshot`` / ``em:api/clist`` / ``cls:12345``。
        value: 事实本身。必须是可 JSON 序列化的标量或简单结构。
        as_of: 🔴 **数据本身的时间**（这份数据描述的是哪一刻的市场）。
        retrieved_at: 取回这份数据的时间。
        calc_version: 口径版本。换算法时据此清点受影响的历史结论。
        label: 给人看的字段名，用于渲染 Decision Card。缺省时回退到 `field`。
        raw_hash: 🔴 **这条证据出自哪一份原始响应**（`raw_market_snapshot.content_sha256`）。
            没有它，「这个结论基于哪份数据」只能靠时间戳猜。
            派生字段（`source` 以 ``derived:`` 开头）没有单一来源，允许为空。
            ⚠️ 这一列的**计算依据在 schema v13（批 I）之后变了**：新行的
            `content_sha256` 基于数据源**原始响应文本**算（`_store.raw_text_sha256`），
            v13 之前的行基于解析后重排的对象算（`payload_sha256`）。raw 层只追加、旧行
            不回填，所以两种口径并存 —— **别拿跨 v13 的两个 `content_sha256` 直接比**
            （都「看起来正常」，却来自不同口径）。
        evidence_set_id: 🔴 **这条证据出自哪一次冻结**（`evidence_sets.evidence_set_id`）。
            批 E-I 新增（可选，参照 `raw_hash` 的先例）。读冻结快照的 Specialist
            填它，于是 risk 的 CROSS_CHECK 能**直接比它**判断两个 Agent 是否真的
            共享了同一份数据 —— 比 `raw_hash` 碰巧相同更硬（批 D-II 留的账）。
            没接冻结快照的 Specialist（news/emotion 不读日线）没有这个值，留空。
    """

    field: str
    source: str
    value: Any
    as_of: datetime
    retrieved_at: datetime
    calc_version: str | None = None
    label: str | None = None
    raw_hash: str | None = None
    evidence_set_id: str | None = None

    def __post_init__(self) -> None:
        # 🔴 先冻结再校验（批 R，评审 E-17）：这样「被校验的那份内容」与
        #    「将被落库的那份内容」是同一份。反过来（先校验后冻结）中间留着
        #    一个窗口，而 `value` 里 14.7% 是 dict/list —— 调用方手上那个
        #    原始对象改一下，已经过检的证据就变了，且不会再触发任何校验。
        object.__setattr__(self, "value", deep_freeze(self.value))
        for name in ("field", "source"):
            v = getattr(self, name)
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"Evidence.{name} 必须是非空字符串，收到 {v!r}")
        for name in ("as_of", "retrieved_at"):
            v = getattr(self, name)
            if not isinstance(v, datetime):
                raise TypeError(f"Evidence.{name} 必须是 datetime，收到 {type(v).__name__}")
            if v.tzinfo is None:
                # naive datetime 是时区错位的头号来源，直接拒绝而不是猜一个时区。
                raise ValueError(f"Evidence.{name} 必须带时区（tzinfo），收到 naive datetime")
        if self.as_of > self.retrieved_at:
            raise ValueError(
                f"Evidence.as_of ({self.as_of.isoformat()}) 晚于 "
                f"retrieved_at ({self.retrieved_at.isoformat()}) —— 数据不可能早于自身被取回"
            )
        # 🔴 外部评审 F15：唯一的时间校验是「两者都带 tzinfo」和
        #    「as_of <= retrieved_at」，**从不与真实当前时刻比较**。
        #    于是一对系统性错位、但彼此只差 60 秒的时间戳：
        #
        #        source_lag_sec = 60       # 1 分钟，看起来非常新鲜
        #        而 as_of 实际比现在晚了 3 天
        #
        #    `source_lag_sec` 是个差值 —— 差值对**共模误差免疫**。
        #    只要上游的 as_of 和 retrieved_at 由同一段错误逻辑派生，
        #    它就会把日期错误完整伪装成「数据是新鲜的」。
        #
        #    ⇒ 补一条锚在真实时钟上的检查。未来时刻**永远**不合法。
        #      只查未来、不查过去：历史证据本来就该是过去的。
        horizon = now_cn() + timedelta(seconds=_FUTURE_TOLERANCE_SEC)
        if self.retrieved_at > horizon:
            raise ValueError(
                f"Evidence.retrieved_at ({self.retrieved_at.isoformat()}) 在未来 —— "
                f"当前是 {now_cn().isoformat()}。\n"
                f"  时间戳整体错位时 source_lag_sec 仍会显得很新鲜（它是差值，"
                f"对共模误差免疫），所以这条必须锚在真实时钟上。\n"
                f"  容差 {_FUTURE_TOLERANCE_SEC}s，只为机器间的时钟抖动留口子。"
            )

    @property
    def source_lag_sec(self) -> int:
        """**取数滞后**：取回时刻 − 数据时刻，单位秒。

        🔴 改名自 `staleness_sec`（批 R，评审 E-19）。旧名字读起来像「数据有多旧」，
        于是它**真的被当成年龄用了** —— `risk_check` 把它加进 result 时的标签
        写的是「最旧证据的年龄(秒)」，而它根本不是年龄。

        它衡量的是「从数据产生到我们取到它，隔了多久」。一份三天前冻结的快照，
        只要当初抓取只花了 2 秒，这个数就是 **2** —— 看起来无比新鲜。

        ⚠️ **这是个差值，对共模误差免疫**（F15）—— as_of 和 retrieved_at 由
        同一段错误逻辑一起算错时，它不会有任何异常表现。
        要问「有多旧」请用 `age_at(评估时刻)`。
        """
        return int((self.retrieved_at - self.as_of).total_seconds())

    def age_at(self, evaluated_at: datetime) -> int:
        """这条证据描述的时刻，距 `evaluated_at` 多久（秒）。

        🔴 **必须显式传入评估时刻**（批 R，评审 E-19）。读一份历史证据时，
        「它有多旧」只在「相对于哪一刻」下才有意义：拿今天去减一个月前的决策，
        得到的是一个每天都在变大、且与当时的判断毫无关系的数。
        要求调用方把基准时刻说出来，就没法不小心用错基准。
        """
        if not isinstance(evaluated_at, datetime):
            raise TypeError(f"age_at 需要 datetime 基准时刻，收到 {type(evaluated_at).__name__}")
        if evaluated_at.tzinfo is None:
            raise ValueError("age_at 的基准时刻必须带时区 —— naive datetime 是时区错位的头号来源")
        return int((evaluated_at - self.as_of).total_seconds())

    @property
    def age_sec(self) -> int:
        """这条证据描述的时刻，距**此刻**多久。

        与 `source_lag_sec` 的区别正是 F15 的要害：
        前者锚在真实时钟上，后者只是两个可能同时错掉的数之差。

        ⚠️ 锚在 `now_cn()` 上 ⇒ **每次调用结果都不同**。任何要落库、上卡、
        或被回放比对的数字都不能用它，用 `age_at(一个记录下来的时刻)`。
        """
        return self.age_at(now_cn())

    @property
    def display_label(self) -> str:
        return self.label or self.field

    # --- 序列化：回放的地基，必须能原样往返 ---

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "source": self.source,
            # 解冻：`value` 冻结后可能是 mappingproxy/tuple，json.dumps 不认前者。
            "value": thaw(self.value),
            "as_of": self.as_of.isoformat(),
            "retrieved_at": self.retrieved_at.isoformat(),
            "calc_version": self.calc_version,
            "label": self.label,
            "raw_hash": self.raw_hash,
            "evidence_set_id": self.evidence_set_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Evidence":
        return cls(
            field=d["field"],
            source=d["source"],
            value=d["value"],
            as_of=datetime.fromisoformat(d["as_of"]),
            retrieved_at=datetime.fromisoformat(d["retrieved_at"]),
            calc_version=d.get("calc_version"),
            label=d.get("label"),
            raw_hash=d.get("raw_hash"),
            evidence_set_id=d.get("evidence_set_id"),
        )
