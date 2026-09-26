"""P3-6/P3-7 decision-data boundary.

Specialists do not select network providers.  The orchestrator freezes every required
Data Platform dataset into one EvidenceSet before Stage 1, and specialists read the
exact frozen DatasetSnapshot through this module.

Manual/ad-hoc runs may still call the live helpers here, but the provider choice remains
inside the data layer rather than inside a specialist skill.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from easyup_biga.domain import now_cn
from easyup_biga.persistence import (
    latest_trading_day,
    link_evidence_set_dataset,
    load_dataset_partition,
    load_dataset_snapshot,
    load_evidence_set,
    load_raw_artifact,
)
from easyup_biga.providers import (
    BOARD_PCT_LIMIT,
    INDEX_PCT_LIMIT,
    IndexDaily,
    as_of_for_trade_date,
    as_of_for_undated_snapshot,
    fetch_index_daily as _fetch_index_daily,
    implausible_bars,
    market_is_open,
    session_in_progress,
)
from easyup_biga.providers.eastmoney import (
    Board,
    BoardResult,
    BreadthResult,
    PoolResult,
    fetch_boards as _fetch_boards,
    fetch_breadth as _fetch_breadth,
    fetch_pool as _fetch_pool,
)
from easyup_biga.providers.http import SourceError
from easyup_biga.providers.cls_news import (
    STALE_SEC as CLS_STALE_SEC,
    fetch_feed as _cls_fetch_feed,
)
from easyup_biga.providers.news_item import NewsFeed, NewsItem
from easyup_biga.providers.sina_news import (
    STALE_SEC,
    fetch_feed as _fetch_feed,
)

#: 各源的盘中静默阈值。**按 provider 查，不是一个全局常量。**
#:
#: 🔴 两个源的节奏差一个量级（同窗口实测：新浪 34 条/小时、财联社 8.6 条/小时），
#:    共用一个阈值等于宣称它们一样。财联社那份是 `None` ——
#:    手上只有非交易日样本，**还没测出来**（R-3：算不出来就说算不出来）。
STALE_SEC_BY_PROVIDER: dict[str, int | None] = {
    "sina_news": STALE_SEC,
    "cls_news": CLS_STALE_SEC,
}
from easyup_biga.providers.tencent import (
    IndexQuote,
    fetch_index_quote as _fetch_index_quote,
)

from .contracts import DatasetLink, DatasetStatus
from .failover import ProviderExecutionAttempt, execute_with_fallback
from .file_store import FileStore
from .datasets import emotion_close
from easyup_biga.providers.sina_boards import (
    fetch_boards as _sina_fetch_boards,
)
from easyup_biga.providers.sina_limit_pool import (
    fetch_all_pools as _sina_all_pools,
)
from easyup_biga.providers.sina_breadth import (
    fetch_breadth as _sina_fetch_breadth,
)
from .publication import DatasetRowPublisher, PublishResult
from .provider_registry import source_prefix_of
from .registry import get_dataset
from .snapshot_resolver import resolve_evidence_set_snapshot

__all__ = [
    "DecisionDataClient",
    "DecisionDataFreezeResult",
    "FrozenDataset",
    "SourceError",
    "BOARD_PCT_LIMIT",
    "INDEX_PCT_LIMIT",
    "STALE_SEC",
    "STALE_SEC_BY_PROVIDER",
    "IndexDaily",
    "IndexQuote",
    "BoardResult",
    "BreadthResult",
    "PoolResult",
    "NewsFeed",
    "as_of_for_trade_date",
    # 🔴 不带日期的端点（涨跌家数 / 板块榜）算 as_of 的**唯一**判据。
    #    market 与 sector 共用 —— 各写一份就是 L-3，而那份差异
    #    的表现是「两个 agent 报了不同的交易日」，不报错。
    "as_of_for_undated_snapshot",
    "latest_trading_day",
    # 🔴 消费 skill 要靠它把 Evidence 的 source 写成**实际供数方**。
    #    ⚠️ 走 `_data` 这层薄壳导出，而不是让 skill 直接 import
    #      `data.provider_registry` —— specialist 不许碰 provider 模块
    #      （P3-6 的边界，由 AST 判据钉着）。
    "source_prefix_of",
    "implausible_bars",
    "market_is_open",
    "session_in_progress",
    "fetch_index_daily",
    "fetch_index_quote",
    "fetch_breadth",
    "fetch_boards",
    "fetch_pool",
    "fetch_feed",
]

DIRECT_DATASETS = frozenset({
    "cn.index.realtime_quote",
    "cn.market.breadth",
    "cn.sector.board_snapshot",
    "cn.market.limit_pool",
    "cn.news.flash",
})


@dataclass(frozen=True, slots=True)
class FrozenDataset:
    dataset_id: str
    snapshot_id: str
    raw_hash: str | None
    rows: tuple[dict[str, Any], ...]
    #: 🔴 **实际供数方**（不是 dataset 的 primary）。
    #:
    #: 没有它，消费 skill 只能把 source 写死成主源的字面量
    #: （`sector-calc` 里就写着 `f"em:clist/{kind}"`）。于是降级过的那天，
    #: **Evidence 的 source 会说谎** —— 数据来自新浪，溯源指着东财。
    #: 那比缺字段更糟：缺字段会进 `missing[]`，说谎不会。
    provider_id: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionDataFreezeResult:
    evidence_set_id: str
    frozen: Mapping[str, str]
    errors: Mapping[str, str]

    @property
    def complete(self) -> bool:
        return not self.errors


class _MappingEmotionCollector:
    """Adapter used to publish P3-5 emotion_close from the same frozen limit pools."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = dict(payload)

    def collect(self, trade_date: str) -> Mapping[str, Any]:
        _ = trade_date
        return self.payload


def fetch_index_daily(symbol: str, *, bars: int = 30):
    """Ad-hoc daily-bar helper. Network ownership stays in the data layer."""
    return _fetch_index_daily(symbol, bars=bars)


def fetch_index_quote(codes):
    """Ad-hoc live helper. Provider ownership stays in the data layer."""
    return _fetch_index_quote(codes)


def fetch_breadth():
    return _fetch_breadth()


def fetch_boards(kind: str):
    return _fetch_boards(kind)


def fetch_pool(pool: str, date: str, *, page_size: int = 500):
    return _fetch_pool(pool, date, page_size=page_size)


def fetch_feed(*, pages: int = 1):
    return _fetch_feed(pages=pages)


def _sina_pools_in_order(trade_date: str, names) -> list:
    """新浪自算三池，按 `names` 的顺序返回。

    🔴 **一次抓取**。写成 `[_sina_all_pools(d)[n] for n in names]` 会
    每次迭代都重新拉一遍全市场（~5 MB × 3 / 24 秒），而且三个池在时间上
    还会不一致 —— 盘中尤其明显。我写第一版时就是那样，这行注释是它的墓志铭。
    """
    pools = _sina_all_pools(trade_date)
    return [pools[n] for n in names]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _raw_hash(raw_text: str) -> str:
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def _trade_date_from_evidence_set(evidence_set_id: str, *, path=None) -> str:
    es = load_evidence_set(evidence_set_id, path=path)
    if es is None:
        raise SourceError(f"evidence_set_id={evidence_set_id!r} 不存在")
    symbols = (es.get("manifest") or {}).get("symbols") or {}
    dates = {str(v.get("trade_date") or "") for v in symbols.values() if v.get("trade_date")}
    if len(dates) != 1:
        raise SourceError(
            f"EvidenceSet {evidence_set_id} 无法确定唯一交易日：{sorted(dates)}")
    return next(iter(dates))


class DecisionDataClient:
    """Freeze/read the datasets consumed by the decision kernel."""

    def __init__(self, *, db_path=None, data_root: str = "data") -> None:
        self.db_path = db_path
        self.data_root = data_root
        self.fs = FileStore(data_root)
        self.publisher = DatasetRowPublisher(db_path=db_path, data_root=data_root)

    # ------------------------------------------------------------------ freeze
    def freeze_required(
        self,
        evidence_set_id: str,
        dataset_ids: Iterable[str],
        *,
        trade_date: str | None = None,
    ) -> DecisionDataFreezeResult:
        wanted = tuple(dict.fromkeys(dataset_ids))
        unknown = sorted(set(wanted) - DIRECT_DATASETS - {"cn.index.daily_bars"})
        if unknown:
            raise ValueError(f"decision-data 不支持冻结这些 dataset：{unknown}")
        day = trade_date or _trade_date_from_evidence_set(evidence_set_id, path=self.db_path)
        frozen: dict[str, str] = {}
        errors: dict[str, str] = {}
        for dataset_id in wanted:
            if dataset_id == "cn.index.daily_bars":
                existing = resolve_evidence_set_snapshot(
                    evidence_set_id, dataset_id, path=self.db_path)
                if existing:
                    frozen[dataset_id] = existing.snapshot_id
                continue
            existing = resolve_evidence_set_snapshot(
                evidence_set_id, dataset_id, path=self.db_path)
            if existing:
                frozen[dataset_id] = existing.snapshot_id
                continue
            started_at = now_cn().isoformat()
            started_mono = time.monotonic()
            try:
                result = self._freeze_one(dataset_id, evidence_set_id, day)
                if result.status != DatasetStatus.COMPLETE or not result.snapshot_id:
                    raise SourceError(
                        f"{dataset_id} 冻结结果不是 COMPLETE：{result.status}")
                link_evidence_set_dataset(
                    DatasetLink(evidence_set_id, dataset_id, result.snapshot_id),
                    path=self.db_path,
                )
                frozen[dataset_id] = result.snapshot_id
            except (SourceError, ValueError) as exc:
                # Provider/data-quality failures are expected degradation. Programming,
                # storage and dependency errors must fail loudly instead of being
                # disguised as a missing market feed.
                #
                # 🔴 但「预期内的降级」不等于「不用留痕」。2026-09-26 第一次真机跑
                #    P3-6：三个 eastmoney dataset 冻结失败，卡上的 missing 报对了、
                #    编排器的 run 事件也记了 dataset_errors，而数据平台自己的账本
                #    （data_job_runs / provider_attempts）**一行都没有** ——
                #    `_freeze_one()` 在 publish() 之前就抛了，DataRun 根本没开过。
                #
                #    后果不只是「日志少一行」：`drills.provider_fallback_drill()`
                #    读的就是 provider_attempts ⇒ 失败的取数从不落表，
                #    「真实 Primary→Fallback 演练」对这 5 个 dataset
                #    **结构上取不到证据**（主源失败那一半永远不出现）。
                errors[dataset_id] = f"{type(exc).__name__}: {exc}"
                self._record_freeze_failure(dataset_id, evidence_set_id, day,
                                            started_at, started_mono, exc)
        return DecisionDataFreezeResult(evidence_set_id, frozen, errors)

    def _record_freeze_failure(self, dataset_id, evidence_set_id, trade_date,
                               started_at, started_mono, error) -> None:
        """冻结失败也落一次 DataRun + 失败的 ProviderAttempt。见上面那段说明。

        ⚠️ 留痕本身失败不许盖住原始的取数失败 —— 那会把「源挂了」变成
        「账本写不进去」，排查方向立刻跑偏。
        """
        definition = get_dataset(dataset_id)
        try:
            self.publisher._service.record_fetch_failure(
                dataset_id=dataset_id,
                job_id=f"decision-freeze-{dataset_id.rsplit('.', 1)[-1]}",
                partition_key={"evidence_set_id": evidence_set_id},
                trigger_id=f"decision-freeze:{evidence_set_id}:{dataset_id}",
                provider_id=definition.primary_provider,
                started_at=started_at,
                elapsed_ms=int((time.monotonic() - started_mono) * 1_000),
                error=error,
            )
        except Exception:                                   # noqa: BLE001
            pass

    def _freeze_one(self, dataset_id: str, evidence_set_id: str, trade_date: str) -> PublishResult:
        key = {"evidence_set_id": evidence_set_id}
        if dataset_id == "cn.index.realtime_quote":
            quotes, raw_text = _fetch_index_quote(("sh000001", "sz399106"))
            rows = [
                {
                    "code": q.code,
                    "name": q.name,
                    "last": q.last,
                    "prev_close": q.prev_close,
                    "volume_hand": q.volume_hand,
                    "amount_wan": q.amount_wan,
                    "quoted_at": q.quoted_at,
                    "raw": q.raw,
                }
                for q in quotes.values()
            ]
            as_of = max(q.quoted_dt for q in quotes.values()).isoformat()
            return self.publisher.publish(
                dataset_id=dataset_id, job_id="decision-freeze-realtime-quote",
                provider_id="tencent", partition_key=key, raw_text=raw_text,
                rows=rows, as_of=as_of,
                quality_metrics={"row_count": len(rows), "trade_date": trade_date},
            )
        if dataset_id == "cn.market.breadth":
            # 🔴 走降级链，不是写死主源。2026-09-26 起 `cn.market.breadth`
            #    有了真跑通过的备用源 —— 而主源那天正整组故障，
            #    三个 eastmoney dataset 在真机出卡时同时失败。
            attempts: list[ProviderExecutionAttempt] = []
            outcome = execute_with_fallback(
                dataset_id,
                {"eastmoney": _fetch_breadth, "sina_breadth": _sina_fetch_breadth},
                on_attempt=attempts.append,
            )
            b = outcome.value
            rows = [{
                "advance": b.advance,
                "decline": b.decline,
                "flat": b.flat,
                "per_market_json": _json(b.per_market),
            }]
            return self.publisher.publish(
                dataset_id=dataset_id, job_id="decision-freeze-breadth",
                # 写**实际供数方** —— 写 primary 会让降级过的那天
                # 在库里看起来像正常的一天。
                provider_id=outcome.provider_id, partition_key=key,
                raw_text=b.raw_text or _json(b.raw),
                rows=rows, as_of=now_cn().isoformat(),
                quality_metrics={"row_count": 1, "trade_date": trade_date},
                failover_attempts=tuple(
                    (a.provider_id, a.role.value, a.succeeded, a.error)
                    for a in attempts),
            )
        if dataset_id == "cn.sector.board_snapshot":
            # 🔴 走降级链。同机另一套长期运行的实例把新浪放**主源**、东财放备胎，
            #    理由写着「东财部分环境被拒」—— 我们 2026-09-26 撞上的正是被拒。
            #    本仓库暂时保持东财为主（口径更全：含主力净流入与涨跌家数），
            #    但不再是唯一一条路。
            attempts: list[ProviderExecutionAttempt] = []
            outcome = execute_with_fallback(
                dataset_id,
                {
                    "eastmoney": lambda: [_fetch_boards("industry"),
                                          _fetch_boards("concept")],
                    "sina_boards": lambda: [_sina_fetch_boards("industry"),
                                            _sina_fetch_boards("concept")],
                },
                on_attempt=attempts.append,
            )
            results = outcome.value
            rows = []
            for result in results:
                for b in result.boards:
                    rows.append({
                        "kind": result.kind,
                        "code": b.code,
                        "name": b.name,
                        "pct": b.pct,
                        "main_inflow": b.main_inflow,
                        "advance": b.advance,
                        "decline": b.decline,
                        "leader": b.leader,
                    })
            raw_text = _json({r.kind: r.raw_text or _json(r.raw) for r in results})
            return self.publisher.publish(
                dataset_id=dataset_id, job_id="decision-freeze-sector-boards",
                provider_id=outcome.provider_id, partition_key=key, raw_text=raw_text,
                rows=rows, as_of=now_cn().isoformat(),
                quality_metrics={"row_count": len(rows), "trade_date": trade_date},
                failover_attempts=tuple(
                    (a.provider_id, a.role.value, a.succeeded, a.error)
                    for a in attempts),
            )
        if dataset_id == "cn.market.limit_pool":
            # 🔴 走降级链。2026-09-26 起这个 dataset 终于有了备用源 ——
            #    而且是**换了一家**：东财自己的付费 AI 接口也能给计数，
            #    但它和主源同属一家，厂商级故障会一起挂。
            #    新浪那条是从全市场快照自算的，走的是完全不同的链路。
            _names = ("limit_up", "broken_board", "limit_down")
            attempts: list[ProviderExecutionAttempt] = []
            outcome = execute_with_fallback(
                dataset_id,
                {"eastmoney": lambda: [_fetch_pool(n, trade_date) for n in _names],
                 "sina_limit_pool": lambda: _sina_pools_in_order(trade_date, _names)},
                on_attempt=attempts.append,
            )
            pools = outcome.value
            rows = [{
                "pool": p.pool,
                "requested_date": p.requested_date,
                "qdate": p.qdate,
                "total": p.total,
                "rows_json": _json(p.rows),
                # 🔴 **这个源的明细里真正有哪些字段**，落库带出去。
                #    不落的话，读回来只能靠「rows 空不空」猜 ——
                #    而 `rows=[]` 是合法状态（真的 0 家）。
                "row_fields_json": _json(sorted(p.row_fields)),
            } for p in pools]
            raw_text = _json({p.pool: p.raw_text or _json(p.raw) for p in pools})
            result = self.publisher.publish(
                dataset_id=dataset_id, job_id="decision-freeze-limit-pool",
                provider_id=outcome.provider_id, partition_key=key, raw_text=raw_text,
                rows=rows, as_of=trade_date,
                quality_metrics={"row_count": len(rows), "trade_date": trade_date},
                failover_attempts=tuple(
                    (a.provider_id, a.role.value, a.succeeded, a.error)
                    for a in attempts),
            )
            # P3-5 integration: close emotion is a deterministic daily derivative of
            # the same three pools; publish it through its own immutable dataset.
            by_name = {p.pool: p for p in pools}
            up = by_name["limit_up"]
            broken = by_name["broken_board"]
            down = by_name["limit_down"]
            denom = up.total + broken.total
            # 🔴 `lbc` 缺失时 `or 1` 会把每只票算成首板 ⇒ `max=1`，
            #    而那是一条**不报错的假事实**（"今天最高才 1 板"）。
            #    源没声明这个字段就交 `None` —— 算不出来要说算不出来（R-3）。
            streaks = ([int(row.get("lbc") or 1) for row in up.rows]
                       if "lbc" in up.row_fields else None)
            emotion_close.run(
                _MappingEmotionCollector({
                    "limit_up_count": up.total,
                    "limit_down_count": down.total,
                    "broken_limit_count": broken.total,
                    "broken_limit_rate": (broken.total / denom if denom else None),
                    "max_consecutive_limit": (max(streaks) if streaks else 0)
                                             if streaks is not None else None,
                    "advance_count": None,
                    "decline_count": None,
                }),
                trade_date,
                # 🔴 收盘情绪派生自**同一次冻结的股池** ⇒ 引用股池那条 raw 血缘。
                #    它曾经把自己算出来的计数重新序列化当 raw —— 自证。
                upstream_artifact_ids=result.raw_artifact_ids,
                db_path=self.db_path, data_root=self.data_root,
            )
            return result
        if dataset_id == "cn.news.flash":
            # 🔴 走降级链，不是写死主源。2026-09-26 起财联社是 PRIMARY ——
            #    它带 `level`（源侧重要性档位），而新浪那三个看起来像重要性的
            #    字段实测恒 0。降级到新浪时 `level` 是 `None`，
            #    **不是 0、不是 "C"** —— 消费方必须能区分「不重要」和「没说」。
            attempts: list[ProviderExecutionAttempt] = []
            outcome = execute_with_fallback(
                dataset_id,
                # 财联社一页 50 条 ≈ 6 小时，1 页就盖住 60 分钟窗口；
                # 新浪要 3 页才到同一量级（100 条 ≈ 3 小时）。
                {"cls_news": lambda: _cls_fetch_feed(pages=1),
                 "sina_news": lambda: _fetch_feed(pages=3)},
                on_attempt=attempts.append,
            )
            feed = outcome.value
            rows = [{
                "id": item.id,
                "at": item.at.isoformat(),
                "text": item.text,
                "tags_json": _json(item.tags),
                "is_quote": item.is_quote,
                "level": item.level,
            } for item in feed.items]
            if not rows:
                raise SourceError("cn.news.flash 取回 0 条")
            return self.publisher.publish(
                dataset_id=dataset_id, job_id="decision-freeze-news",
                # 写**实际供数方** —— 写 primary 会让降级过的那次
                # 在库里看起来像正常的一次。
                provider_id=outcome.provider_id, partition_key=key,
                raw_text=feed.raw_text or _json(feed.raw), rows=rows,
                as_of=feed.items[0].at.isoformat(),
                quality_metrics={"row_count": len(rows)},
                failover_attempts=tuple(
                    (a.provider_id, a.role.value, a.succeeded, a.error)
                    for a in attempts),
            )
        raise ValueError(dataset_id)

    # ------------------------------------------------------------------- read
    def _frozen(self, evidence_set_id: str, dataset_id: str) -> FrozenDataset:
        resolved = resolve_evidence_set_snapshot(
            evidence_set_id, dataset_id, path=self.db_path)
        if resolved is None:
            raise SourceError(
                f"EvidenceSet {evidence_set_id} 没有冻结 required dataset {dataset_id}")
        snapshot = load_dataset_snapshot(resolved.snapshot_id, path=self.db_path)
        if snapshot is None:
            raise SourceError(f"DatasetSnapshot 不存在：{resolved.snapshot_id}")
        pids = tuple(str(x) for x in snapshot["manifest"].get("partition_ids") or ())
        if len(pids) != 1:
            raise SourceError(f"{dataset_id} 应恰好一个分区，实际 {len(pids)}")
        part = load_dataset_partition(pids[0], path=self.db_path)
        if part is None:
            raise SourceError(f"DatasetPartition 不存在：{pids[0]}")
        rows = tuple(self.fs.read_parquet_rows(str(part["storage_uri"])))
        raw_hash = None
        raw_id = part.get("raw_artifact_id")
        if raw_id:
            raw = load_raw_artifact(str(raw_id), path=self.db_path)
            if raw:
                # DatasetRowPublisher stores sha256(raw_text) here.  That is the same
                # raw_hash convention used by Phase 2 Evidence.
                raw_hash = str(raw["request_fingerprint"])
        return FrozenDataset(
            dataset_id, resolved.snapshot_id, raw_hash, rows,
            provider_id=(str(part["provider_id"]) if part.get("provider_id") else None))

    def read_index_quote(self, evidence_set_id: str) -> tuple[dict[str, IndexQuote], str | None]:
        frozen = self._frozen(evidence_set_id, "cn.index.realtime_quote")
        out = {}
        for row in frozen.rows:
            q = IndexQuote(
                code=str(row["code"]), name=str(row["name"]), last=float(row["last"]),
                prev_close=float(row["prev_close"]), volume_hand=int(row["volume_hand"]),
                amount_wan=float(row["amount_wan"]), quoted_at=str(row["quoted_at"]),
                raw=str(row["raw"]),
            )
            out[q.code] = q
        return out, frozen.raw_hash

    def read_breadth(
        self, evidence_set_id: str
    ) -> tuple[BreadthResult, str | None, str | None]:
        """返回 `(涨跌家数, raw_hash, **实际供数方**)`。

        ⚠️ 第三项是 2026-09-26 补的。在那之前 `market_calc` 只能把 source 写死成
        `"em:push2delay/ulist.np"` —— 而当天东财 push2 系整组 502、
        实际由 `sina_breadth` 供数，卡上那五条涨跌家数证据**全部指着一个
        没供过数的端点**。

        🔴 这和 `read_boards` 是同一个洞。板块那次（v0.9.9）修了，
        涨跌家数**漏了** —— 因为当时的判据是「板块的 source 对不对」，
        而不是「所有 read_* 有没有交出供数方」。
        判据跟着症状走，就只会修到症状那一个。
        """
        frozen = self._frozen(evidence_set_id, "cn.market.breadth")
        if len(frozen.rows) != 1:
            raise SourceError("cn.market.breadth 冻结分区应恰好一行")
        row = frozen.rows[0]
        result = BreadthResult(
            advance=int(row["advance"]), decline=int(row["decline"]), flat=int(row["flat"]),
            per_market=list(json.loads(str(row["per_market_json"]))), raw={}, raw_text=None,
        )
        return result, frozen.raw_hash, frozen.provider_id

    def read_boards(
        self, evidence_set_id: str, kind: str
    ) -> tuple[BoardResult, str | None, str | None]:
        """返回 `(板块快照, raw_hash, **实际供数方**)`。

        ⚠️ 第三项是 2026-09-26 加的。在那之前消费方只能把 source 写死成
        主源字面量，于是降级过的那天 Evidence 的 source 指着一个没供过数的源。
        """
        frozen = self._frozen(evidence_set_id, "cn.sector.board_snapshot")
        rows = [r for r in frozen.rows if str(r["kind"]) == kind]
        boards = [Board(
            code=str(r["code"]), name=str(r["name"]), pct=float(r["pct"]),
            main_inflow=None if r["main_inflow"] is None else float(r["main_inflow"]),
            advance=None if r["advance"] is None else int(r["advance"]),
            decline=None if r["decline"] is None else int(r["decline"]),
            leader=None if r["leader"] is None else str(r["leader"]),
        ) for r in rows]
        if not boards:
            raise SourceError(f"cn.sector.board_snapshot 没有 kind={kind}")
        return (BoardResult(kind, len(boards), boards, raw={}, raw_text=None),
                frozen.raw_hash, frozen.provider_id)

    def read_pool(
        self, evidence_set_id: str, pool: str
    ) -> tuple[PoolResult, str | None, str | None]:
        """返回 `(股池, raw_hash, **实际供数方**)`。

        ⚠️ 第三项是 2026-09-26 加的，与 `read_boards` / `read_news` /
        `read_breadth` 同一个理由：写死主源字面量会让降级过的那次
        Evidence 指着一个没供过数的源。

        🔴 `row_fields` 也从分区里读回来 —— 备用源只给计数，
        而 `rows=[]` 是**合法状态**（真的 0 家）。靠「空不空」推断
        会让 emotion 算出 `max_streak=1`、`seal_never_broken_rate=1.0`
        这类**不报错的假事实**。
        """
        frozen = self._frozen(evidence_set_id, "cn.market.limit_pool")
        row = next((r for r in frozen.rows if str(r["pool"]) == pool), None)
        if row is None:
            raise SourceError(f"cn.market.limit_pool 没有 pool={pool}")
        # ⚠️ 用 `.get`：schema v1 的分区没有这一列。
        #    读不到时退回**东财的字段集** —— v1 只可能是东财产的。
        raw_fields = row.get("row_fields_json")
        fields = (frozenset(json.loads(str(raw_fields))) if raw_fields
                  else frozenset({"lbc", "zbc"}))
        result = PoolResult(
            pool=pool,
            requested_date=str(row["requested_date"]),
            qdate=None if row["qdate"] is None else str(row["qdate"]),
            total=int(row["total"]),
            rows=list(json.loads(str(row["rows_json"]))), raw={}, raw_text=None,
            row_fields=fields,
        )
        return result, frozen.raw_hash, frozen.provider_id

    def read_news(
        self, evidence_set_id: str
    ) -> tuple[NewsFeed, str | None, str | None]:
        """返回 `(快讯, raw_hash, **实际供数方**)`。

        ⚠️ 第三项是 2026-09-26 加的，与 `read_boards` 同一个理由：
        在那之前消费方只能把 source 写死成主源字面量，于是降级过的那次
        Evidence 的 source 指着一个没供过数的源。

        🔴 这个源多一层：静默阈值也**按 provider 查**
        （`STALE_SEC_BY_PROVIDER`）。不知道是谁供的数，就只能拿新浪的 600
        去考核财联社 —— 而两者速率差 4 倍。
        """
        frozen = self._frozen(evidence_set_id, "cn.news.flash")
        items = tuple(sorted((NewsItem(
            id=int(r["id"]),
            at=datetime.fromisoformat(str(r["at"])),
            text=str(r["text"]),
            tags=tuple(json.loads(str(r["tags_json"]))),
            is_quote=bool(r["is_quote"]),
            # ⚠️ 用 `.get`：schema v1 的分区没有这一列。
            #    回放一张 2026-09-26 之前的老卡时，`r["level"]` 会 KeyError ——
            #    而那是**只在回放旧快照时**才暴露的崩溃。
            level=(lambda v: str(v) if v else None)(r.get("level")),
        ) for r in frozen.rows), key=lambda x: x.at, reverse=True))
        if not items:
            raise SourceError("cn.news.flash 冻结分区为空")
        return (NewsFeed(items=items, raw={}, raw_text=None),
                frozen.raw_hash, frozen.provider_id)
