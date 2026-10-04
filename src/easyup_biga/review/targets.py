"""P4-3 target resolution over point-in-time market data."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from easyup_biga.data import analytics
from easyup_biga.data.contracts import DatasetStatus

from .anchors import AnchorResolution, HorizonSchedule
from .registry import TargetDefinition, TargetPolicyDefinition, get_target_policy

__all__ = [
    "TargetBar",
    "TargetResolution",
    "TargetResolver",
]


@dataclass(frozen=True, slots=True)
class TargetBar:
    target_id: str
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    source_snapshot_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("target OHLC must be positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("invalid target OHLC")
        object.__setattr__(self, "source_snapshot_ids", tuple(dict.fromkeys(self.source_snapshot_ids)))


@dataclass(frozen=True, slots=True)
class TargetResolution:
    target_policy_id: str
    anchor_trade_date: str
    knowledge_cutoff: str
    bars: Mapping[str, tuple[TargetBar, ...]]
    snapshot_states: Mapping[str, Mapping[str, Any]]
    deferred_targets: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "bars",
            MappingProxyType({key: tuple(value) for key, value in self.bars.items()}),
        )
        object.__setattr__(
            self,
            "snapshot_states",
            MappingProxyType({key: MappingProxyType(dict(value)) for key, value in self.snapshot_states.items()}),
        )
        object.__setattr__(self, "deferred_targets", MappingProxyType(dict(self.deferred_targets)))

    def bars_for(self, target_id: str) -> tuple[TargetBar, ...]:
        return self.bars.get(target_id, ())


class TargetResolver:
    """Resolve Phase-4 primary market targets without bypassing the control plane.

    P4-3/P4-4 activates the two direct index components and the 50/50 blend.  The registered
    ``market.equity_ew.v1`` robustness endpoint is intentionally deferred to P4-7, where its
    tradability/adjustment-factor semantics are tested together with the robustness suite.
    """

    def __init__(
        self,
        *,
        snapshot_state_loader: Callable[..., dict[str, dict[str, Any]]] | None = None,
        index_row_loader: Callable[..., list[dict[str, Any]]] | None = None,
    ) -> None:
        self._snapshot_state_loader = snapshot_state_loader or analytics.resolve_dataset_snapshots_as_of
        self._index_row_loader = index_row_loader or analytics.query_index_eod_as_of

    def resolve(
        self,
        anchor: AnchorResolution,
        schedule: HorizonSchedule,
        *,
        db_path=None,
        data_root="data",
        target_policy_id: str = "market.index_targets.v1",
    ) -> TargetResolution:
        policy: TargetPolicyDefinition = get_target_policy(target_policy_id)
        end_date = max(schedule.horizon_trade_dates.values(), default=anchor.anchor_trade_date)
        states = self._snapshot_state_loader(
            "cn.index.eod_bars",
            db_path=db_path,
            knowledge_cutoff=schedule.knowledge_cutoff,
            start_date=anchor.anchor_trade_date,
            end_date=end_date,
        )
        complete_days = {
            day
            for day, state in states.items()
            if state.get("status") == DatasetStatus.COMPLETE.value
        }
        rows = self._index_row_loader(
            db_path=db_path,
            knowledge_cutoff=schedule.knowledge_cutoff,
            start_date=anchor.anchor_trade_date,
            end_date=end_date,
            instrument_ids=["index.sh000001", "index.sz399106"],
        )
        # Only rows whose exact snapshot is the latest control-plane revision are usable.
        by_day_iid: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            day = str(row.get("trade_date") or "")
            state = states.get(day)
            if day not in complete_days or state is None:
                continue
            if str(row.get("_snapshot_id") or "") != str(state.get("snapshot_id") or ""):
                continue
            by_day_iid[(day, str(row.get("instrument_id") or ""))] = row

        bars: dict[str, list[TargetBar]] = {
            "index.sh000001": [],
            "index.sz399106": [],
            "market.index_blend.v1": [],
        }
        # 🔴 blend 的每根 bar 都以 **anchor 日的 open** 归一化 ⇒ 拿不到那两行就一根都产不出。
        #    外部实现在 `_blend` 里对这种情况 `raise ValueError`，而它**可达**：
        #    anchor 日是 QUARANTINED、后面某天是 COMPLETE 时就会走到。
        #    那一炸发生在 `build_outcome()` 判隔离**之前** ⇒ 一个本该被如实记成
        #    QUARANTINED 的场景变成了未捕获异常。设计文档 §14.1 要的是
        #    「UNSCORABLE + reason」，不是崩。
        #    ⇒ 这里直接不产 blend，让下游的成熟度/隔离判定照常工作。
        sh_anchor = by_day_iid.get((anchor.anchor_trade_date, "index.sh000001"))
        sz_anchor = by_day_iid.get((anchor.anchor_trade_date, "index.sz399106"))
        blend_available = (
            sh_anchor is not None
            and sz_anchor is not None
            and float(sh_anchor["open"]) > 0
            and float(sz_anchor["open"]) > 0
        )
        for day in schedule.open_trade_dates:
            if day > end_date or day not in complete_days:
                continue
            sh = by_day_iid.get((day, "index.sh000001"))
            sz = by_day_iid.get((day, "index.sz399106"))
            if sh is None or sz is None:
                continue
            snapshot_id = str(states[day]["snapshot_id"])
            bars["index.sh000001"].append(self._direct("index.sh000001", sh, snapshot_id))
            bars["index.sz399106"].append(self._direct("index.sz399106", sz, snapshot_id))
            if blend_available:
                bars["market.index_blend.v1"].append(
                    self._blend(day, sh, sz, snapshot_id, anchor, by_day_iid)
                )

        deferred: dict[str, str] = {}
        for target in policy.targets:
            if target.role == "ROBUSTNESS_ENDPOINT":
                deferred[target.target_id] = "P4-7_ROBUSTNESS_ENDPOINT"
        return TargetResolution(
            target_policy_id=policy.policy_id,
            anchor_trade_date=anchor.anchor_trade_date,
            knowledge_cutoff=schedule.knowledge_cutoff,
            bars={key: tuple(value) for key, value in bars.items()},
            snapshot_states=states,
            deferred_targets=deferred,
        )

    @staticmethod
    def _direct(target_id: str, row: Mapping[str, Any], snapshot_id: str) -> TargetBar:
        return TargetBar(
            target_id=target_id,
            trade_date=str(row["trade_date"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            source_snapshot_ids=(snapshot_id,),
        )

    @staticmethod
    def _blend(
        day: str,
        sh: Mapping[str, Any],
        sz: Mapping[str, Any],
        snapshot_id: str,
        anchor: AnchorResolution,
        by_day_iid: Mapping[tuple[str, str], Mapping[str, Any]],
    ) -> TargetBar:
        sh0 = by_day_iid.get((anchor.anchor_trade_date, "index.sh000001"))
        sz0 = by_day_iid.get((anchor.anchor_trade_date, "index.sz399106"))
        if sh0 is None or sz0 is None:
            raise ValueError("blend target requires both anchor-day index rows")
        sh_base = float(sh0["open"])
        sz_base = float(sz0["open"])
        if sh_base <= 0 or sz_base <= 0:
            raise ValueError("blend anchor opens must be positive")

        def avg(field: str) -> float:
            return (float(sh[field]) / sh_base + float(sz[field]) / sz_base) / 2.0

        return TargetBar(
            target_id="market.index_blend.v1",
            trade_date=day,
            open=avg("open"),
            high=avg("high"),
            low=avg("low"),
            close=avg("close"),
            source_snapshot_ids=(snapshot_id,),
        )
