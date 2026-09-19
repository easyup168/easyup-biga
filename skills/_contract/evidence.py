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

__all__ = ["Evidence", "CN_TZ", "now_cn"]

# A 股市场时区。证据的时间一律带 tzinfo —— naive datetime 在跨日聚合时会静默错位。
CN_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


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
    """

    field: str
    source: str
    value: Any
    as_of: datetime
    retrieved_at: datetime
    calc_version: str | None = None
    label: str | None = None

    def __post_init__(self) -> None:
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

    @property
    def staleness_sec(self) -> int:
        """数据有多旧：取回时刻 − 数据时刻，单位秒。"""
        return int((self.retrieved_at - self.as_of).total_seconds())

    @property
    def display_label(self) -> str:
        return self.label or self.field

    # --- 序列化：回放的地基，必须能原样往返 ---

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "source": self.source,
            "value": self.value,
            "as_of": self.as_of.isoformat(),
            "retrieved_at": self.retrieved_at.isoformat(),
            "calc_version": self.calc_version,
            "label": self.label,
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
        )
