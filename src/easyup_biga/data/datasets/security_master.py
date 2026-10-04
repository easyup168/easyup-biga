"""`cn.security_master` 的 Dataset 适配器 —— 归一、质量裁定、落 fact、发布快照。

取自外部 P3-3 实现包并适配到本仓库的注册表 API。

🔴 Point-in-time 的诚实边界（外部实现这一点做对了，原样保留）
--------------------------------------------------------------
这个 provider 给的是**当前在册**名单，没有完整退市历史。
⇒ 只宣称「从 BigA 第一次成功同步那天起」的 point-in-time，
  **不把今天的名单回填成一份历史快照**。

那正是 R-3 的正解：算不出来就说算不出来，而不是拿一个看起来合理的值顶上。

⚠️ 上游 provider **尚未在本机探活成功**（见
`src/easyup_biga/providers/eastmoney_security_master.py` 的模块头）。
在复验通过之前这条链跑不通 —— 那是 fail-closed，不是缺陷。
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Callable, Iterable, Mapping, Sequence

# ⚠️ 这两个 import 块**变短了**，那不是清理，是证据：
#    `DatasetPartition` / `DatasetSnapshot` / `QualityReport` /
#    `save_dataset_partition` / `save_dataset_snapshot` / `save_quality_report` /
#    `save_raw_artifact` 这一整套，正是本文件原先自己走一遍的那条账本流程。
#    现在它们只出现在 `data/snapshots.py` 里 —— 全仓**只有一处**。
from easyup_biga.data.contracts import (
    DataIssue,
    DataJobRun,
    DatasetStatus,
    ProviderAttempt,
    ProviderAttemptStatus,
    ProviderRole,
    RawArtifact,
    new_data_run_id,
    new_partition_id,
    new_raw_artifact_id,
)
from easyup_biga.domain import now_cn
from easyup_biga.providers.instrument_segments import a_share_segment
from easyup_biga.providers.security_listing import (
    SecurityListing,
    SecurityMasterFetchResult,
)
from easyup_biga.persistence import (
    find_dataset_snapshot,
    load_security_master_records,
    open_data_run,
    raw_text_sha256,
    record_provider_attempt,
    save_raw_snapshot,
    save_security_master_records,
    transition_data_run,
)
from easyup_biga.providers.eastmoney_security_master import fetch_security_master
from easyup_biga.providers.http import SourceError
from easyup_biga.providers.sina_security_master import (
    fetch_security_master as sina_fetch_security_master,
)

from ..failover import ProviderExecutionAttempt, execute_with_fallback
from ..snapshots import DatasetSnapshotService, SnapshotPublishRequest

DATASET_ID = "cn.security_master"
JOB_ID = "security-master-sync"
#: 🔴 **适配器级** provider_id（与 `providers/` 下的模块同名），不是 `"em"`。
#:    `"em"` 是 raw 层 `source` 的**站点前缀**，两者不是一回事 ——
#:    `provider_for_source()` 负责在它们之间换算。写 `"eastmoney"` 更糟：
#:    那会是同一个源的第三套名字。
PROVIDER_ID = "eastmoney_security_master"
#: 备用源。⚠️ 口径与主源**不等价**（没有上市日）—— 见适配器模块头。
FALLBACK_PROVIDER_ID = "sina_security_master"


class Exchange(StrEnum):
    SSE = "SSE"
    SZSE = "SZSE"
    BSE = "BSE"


class SecurityBoard(StrEnum):
    SSE_MAIN = "SSE_MAIN"
    STAR = "STAR"
    SZSE_MAIN = "SZSE_MAIN"
    CHINEXT = "CHINEXT"
    BSE = "BSE"


class SecurityType(StrEnum):
    STOCK = "STOCK"


class SecurityStatus(StrEnum):
    LISTED = "LISTED"
    DELISTED = "DELISTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class SecurityMasterRecord:
    instrument_id: str
    symbol: str
    exchange: Exchange
    name: str
    security_type: SecurityType
    board: SecurityBoard
    list_date: str | None
    delist_date: str | None
    status: SecurityStatus
    available_at: str
    retrieved_at: str
    provider_id: str
    raw_artifact_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "symbol": self.symbol,
            "exchange": self.exchange.value,
            "name": self.name,
            "security_type": self.security_type.value,
            "board": self.board.value,
            "list_date": self.list_date,
            "delist_date": self.delist_date,
            "status": self.status.value,
            "available_at": self.available_at,
            "retrieved_at": self.retrieved_at,
            "provider_id": self.provider_id,
            "raw_artifact_id": self.raw_artifact_id,
        }


@dataclass(frozen=True, slots=True)
class SecurityMasterQualityResult:
    status: DatasetStatus
    metrics: Mapping[str, Any]
    issues: tuple[DataIssue, ...]


@dataclass(frozen=True, slots=True)
class SecurityMasterSyncResult:
    status: DatasetStatus
    as_of_date: str
    data_run_id: str
    snapshot_id: str | None
    partition_id: str | None
    quality_report_id: str | None
    row_count: int
    reused: bool = False


FetchSecurityMaster = Callable[[], SecurityMasterFetchResult]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _validate_as_of_date(value: str) -> str:
    if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
        raise ValueError(f"as_of_date must be YYYYMMDD, got {value!r}")
    date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")
    return value


def _symbol(value: Any) -> str:
    if value in (None, "", "-"):
        raise ValueError("security code is missing")
    text = str(value).strip()
    if not text.isdigit() or len(text) > 6:
        raise ValueError(f"invalid A-share security code: {value!r}")
    return text.zfill(6)


def _exchange_and_board(symbol: str, provider_market: Any) -> tuple[Exchange, SecurityBoard]:
    """🔴 判据来自**唯一**那份号段表（`providers/instrument_segments.py`）。

    这里曾经自己写一遍，而同样的判断在日线那边还有两份 ——
    2026-09-26 一起收口。逼出这次收口的是通达信盘后包：它按市场分文件，
    同一份数据里 **840 个代码在两个市场都存在**（`000001` 既是上证指数
    也是平安银行）。

    ⚠️ 认不出**仍然 fail closed**（抛 `ValueError`），不返回默认值 ——
    这是 universe 的入口，混进基金/债券不会报错，只会让全市场筛选多出
    一堆不该有的标的。
    """
    segment = a_share_segment(symbol, market=provider_market)
    if segment is None:
        market = str(provider_market).strip() if provider_market is not None else ""
        raise ValueError(
            f"unsupported stock-code prefix: symbol={symbol}, provider_market={market!r}"
        )
    return Exchange(segment.exchange), SecurityBoard(segment.board)


def _list_date(value: Any) -> str | None:
    if value in (None, "", "-", 0, "0"):
        return None
    text = str(value).strip().replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"invalid list_date: {value!r}")
    parsed = date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")
    return parsed.isoformat()


def normalize_security_master_rows(
    rows: Iterable[SecurityListing],
    *,
    provider_id: str,
    raw_artifact_id: str,
    retrieved_at: str,
) -> tuple[SecurityMasterRecord, ...]:
    """把 **provider 中立行** 归一成稳定的 BigA 证券身份。

    🔴 入参从「东财的字段名 dict」改成了 `SecurityListing`（2026-09-26）。
    原来这里直接读 `f12` / `f14` / `f13` / `f26` —— 只有一个源时看不出问题，
    **加第二个源的那一刻就咬人**：要么让新 provider 把字段伪装成 `f12`
    （读的人会以为它是东财），要么在这里加一个「这是哪家」的分支
    （同一个判断两份实现）。两条都错 ⇒ 翻译交给各自的适配器。
    """
    # Validate timestamp once.  ISO text is also used for lexical cutoff queries.
    datetime.fromisoformat(retrieved_at)
    records: list[SecurityMasterRecord] = []
    # 🔴 不认识的前缀**攒起来一次报全**，不是撞上第一个就死。
    #    死在第一个的代价不是「慢」：全市场名单一年可能新增几个代码段，
    #    一次只暴露一个 ⇒ 要重跑 N 次、每次等一分多钟才能知道全貌。
    #    而 fail-closed 本身不放松 —— 有一个不认识就整体不发布。
    unsupported: list[str] = []
    for row in rows:
        symbol = _symbol(row.symbol)
        name = str(row.name or "").strip()
        if not name or name == "-":
            raise ValueError(f"security {symbol} has no name")
        try:
            exchange, board = _exchange_and_board(symbol, row.market_hint)
        except ValueError:
            unsupported.append(symbol)
            continue
        suffix = {
            Exchange.SSE: "SH",
            Exchange.SZSE: "SZ",
            Exchange.BSE: "BJ",
        }[exchange]
        records.append(
            SecurityMasterRecord(
                instrument_id=f"{symbol}.{suffix}",
                symbol=symbol,
                exchange=exchange,
                name=name,
                security_type=SecurityType.STOCK,
                board=board,
                list_date=_list_date(row.list_date),
                delist_date=None,
                status=SecurityStatus.LISTED,
                available_at=retrieved_at,
                retrieved_at=retrieved_at,
                provider_id=provider_id,
                raw_artifact_id=raw_artifact_id,
            )
        )
    if unsupported:
        raise ValueError(
            f"unsupported stock-code prefixes ({len(unsupported)} 只)："
            f"{sorted(unsupported)[:20]}"
            + ("…" if len(unsupported) > 20 else "")
            + " —— 代码前缀是身份规则的唯一依据，不认识就**整体不发布**，"
              "免得基金/债券被静默混进股票 universe。"
              "确认是新代码段后加进 `_exchange_and_board`。")
    return tuple(sorted(records, key=lambda item: item.instrument_id))


class SecurityMasterQualityPolicy:
    policy_id = "cn-security-master-v1"

    def __init__(
        self,
        *,
        minimum_rows: int = 1_000,
        required_exchanges: frozenset[Exchange] | None = None,
    ) -> None:
        if minimum_rows < 1:
            raise ValueError("minimum_rows must be positive")
        self.minimum_rows = minimum_rows
        self.required_exchanges = (
            frozenset(Exchange) if required_exchanges is None else required_exchanges
        )

    def evaluate(
        self,
        records: Sequence[SecurityMasterRecord],
        *,
        declared_total: int,
        as_of_date: str,
    ) -> SecurityMasterQualityResult:
        ids = [item.instrument_id for item in records]
        duplicate_count = len(ids) - len(set(ids))
        exchange_counts = {
            exchange.value: sum(1 for item in records if item.exchange == exchange)
            for exchange in Exchange
        }
        missing_exchanges = sorted(
            exchange.value
            for exchange in self.required_exchanges
            if exchange_counts[exchange.value] == 0
        )
        missing_list_dates = sum(1 for item in records if item.list_date is None)
        future_list_dates = sum(
            1
            for item in records
            if item.list_date is not None
            and item.list_date.replace("-", "") > as_of_date
        )

        issues: list[DataIssue] = []
        if len(records) < self.minimum_rows:
            issues.append(
                DataIssue(
                    code="data.quality.row_count_below_threshold",
                    severity="CRITICAL",
                    detail=(
                        f"security master rows={len(records)}, "
                        f"minimum={self.minimum_rows}"
                    ),
                )
            )
        if declared_total != len(records):
            issues.append(
                DataIssue(
                    code="data.quality.declared_total_mismatch",
                    severity="CRITICAL",
                    detail=f"declared={declared_total}, normalized={len(records)}",
                )
            )
        if duplicate_count:
            issues.append(
                DataIssue(
                    code="data.quality.duplicate_key",
                    severity="CRITICAL",
                    detail=f"duplicate instrument_id rows={duplicate_count}",
                )
            )
        if missing_exchanges:
            issues.append(
                DataIssue(
                    code="data.quality.required_group_missing",
                    severity="CRITICAL",
                    detail=f"missing exchanges: {missing_exchanges}",
                )
            )
        if future_list_dates:
            issues.append(
                DataIssue(
                    code="data.quality.future_effective_date",
                    severity="CRITICAL",
                    detail=f"future list_date rows={future_list_dates}",
                )
            )

        status = DatasetStatus.QUARANTINED if issues else DatasetStatus.COMPLETE
        metrics = {
            "declared_total": declared_total,
            "normalized_count": len(records),
            "duplicate_count": duplicate_count,
            "exchange_counts": exchange_counts,
            "missing_list_date_count": missing_list_dates,
            "future_list_date_count": future_list_dates,
            "historical_backfill_complete": False,
        }
        return SecurityMasterQualityResult(
            status=status,
            metrics=metrics,
            issues=tuple(issues),
        )


class SecurityMasterService:
    """Fetch, normalize, validate and publish one Security Master snapshot."""

    def __init__(
        self,
        *,
        path=None,
        fetcher: FetchSecurityMaster | None = None,
        quality_policy: SecurityMasterQualityPolicy | None = None,
    ) -> None:
        self._path = path
        #: 🔴 默认取数走**降级链**，不是写死主源。
        #:    2026-09-26 起 `cn.security_master` 有了真跑通过的 FALLBACK ——
        #:    而一条「注册表里声明了、代码里写死单源」的降级路径比没有更糟
        #:    （第 67 章日历那条踩过一次）。
        self._fetch = fetcher or self._fetch_with_fallback
        self._live_current_only = fetcher is None
        #: 本次真正供数的那家（注册表 id）。注入 fetcher 时退回主源 id ——
        #: 测试桩没有 provider 身份可言。
        self._served_by = PROVIDER_ID
        self._attempts: tuple[ProviderExecutionAttempt, ...] = ()
        self._quality = quality_policy or SecurityMasterQualityPolicy()

    def _fetch_with_fallback(self) -> SecurityMasterFetchResult:
        """按注册表顺序取数，并记下**真正供数的那家**。

        🔴 溯源必须写实际供数方，不能写 dataset 的 primary ——
        「降级过的那天」会因此在库里**看起来像正常的一天**：
        数据来自备用源，`provider_id` 却指着主源。
        （`execute_with_fallback` 的 docstring 里写着同一句话。）
        """
        attempts: list[ProviderExecutionAttempt] = []
        result = execute_with_fallback(
            DATASET_ID,
            {
                PROVIDER_ID: fetch_security_master,
                FALLBACK_PROVIDER_ID: sina_fetch_security_master,
            },
            on_attempt=attempts.append,
        )
        self._served_by = result.provider_id
        self._attempts = tuple(attempts)
        return result.value

    def sync(
        self,
        *,
        as_of_date: str | None = None,
        trigger_id: str | None = None,
        data_version: int | None = None,
    ) -> SecurityMasterSyncResult:
        if data_version is not None and data_version < 1:
            raise ValueError("data_version must be >= 1")
        current_date = now_cn().strftime("%Y%m%d")
        requested_date = _validate_as_of_date(as_of_date or current_date)
        if self._live_current_only and requested_date != current_date:
            raise ValueError(
                "the live Security Master provider exposes only the current universe; "
                "historical as_of_date would create false point-in-time data"
            )
        partition_key = {"as_of_date": requested_date}
        existing = find_dataset_snapshot(
            DATASET_ID,
            partition_key,
            status=DatasetStatus.COMPLETE,
            path=self._path,
        )
        latest_version = int(existing["data_version"]) if existing is not None else 0
        if existing is not None and (
            data_version is None or data_version == latest_version
        ):
            records = load_security_master_records(
                str(existing["snapshot_id"]),
                path=self._path,
            )
            return SecurityMasterSyncResult(
                status=DatasetStatus.COMPLETE,
                as_of_date=requested_date,
                data_run_id="",
                snapshot_id=str(existing["snapshot_id"]),
                partition_id=str(existing["manifest"]["partition_ids"][0]),
                quality_report_id=str(existing["quality_report_id"]),
                row_count=len(records),
                reused=True,
            )

        target_version = data_version or 1
        if existing is None and target_version != 1:
            raise ValueError("the first Security Master version must be 1")
        supersedes_snapshot_id = str(existing["snapshot_id"]) if existing else None
        supersedes_partition_id = None
        if existing is not None:
            prior_partitions = existing["manifest"].get("partition_ids") or []
            if len(prior_partitions) != 1:
                raise ValueError("existing Security Master snapshot has invalid partition lineage")
            supersedes_partition_id = str(prior_partitions[0])

        # ── 取数：provider 交互与失败留痕，是本数据集**自己**的事 ──────────
        fetch_started = time.monotonic()
        fetch_started_at = now_cn().isoformat()
        try:
            fetched = self._fetch()
        except SourceError as exc:
            # 🔴 取数就失败 ⇒ 没有可发布的东西，但**必须留下一次 run**：
            #    「源挂了」和「今天没跑」在库里长得一模一样的话，排查时
            #    最先要区分的就是这两件事。
            self._record_fetch_failure(
                partition_key=partition_key, target_version=target_version,
                trigger_id=trigger_id, requested_date=requested_date,
                started_at=fetch_started_at, elapsed_ms=int(
                    (time.monotonic() - fetch_started) * 1_000),
                error=exc)
            raise

        retrieved_at = fetched.retrieved_at
        if self._live_current_only and retrieved_at[:10].replace("-", "") != requested_date:
            raise ValueError(
                "live Security Master retrieval date differs from partition date")

        legacy_raw_id = save_raw_snapshot(
            source=fetched.source, as_of=retrieved_at, retrieved_at=retrieved_at,
            payload=fetched.raw, raw_text=fetched.raw_text, path=self._path)
        artifact = RawArtifact(
            artifact_id=new_raw_artifact_id(),
            dataset_id=DATASET_ID,
            provider_id=self._served_by,
            request_fingerprint=_sha256({
                "dataset_id": DATASET_ID, "provider_id": self._served_by,
                "adapter_version": fetched.adapter_version,
                "as_of_date": requested_date,
            }),
            body_uri=f"biga+sqlite://raw_market_snapshot/{legacy_raw_id}",
            body_sha256=raw_text_sha256(fetched.raw_text),
            size_bytes=len(fetched.raw_text.encode("utf-8")),
            retrieved_at=retrieved_at, content_type="application/json",
            compression=None, as_of=retrieved_at, available_at=retrieved_at,
        )

        records = normalize_security_master_rows(
            fetched.rows, provider_id=self._served_by,
            raw_artifact_id=artifact.artifact_id, retrieved_at=retrieved_at)
        quality_result = self._quality.evaluate(
            records, declared_total=fetched.total, as_of_date=requested_date)

        # ── 发布：账本流程走**唯一那一处** ────────────────────────────────
        # 🔴 这里原本是本仓库第三处逐项重复的 `DataRun → RawArtifact →
        #    Partition → Quality → Snapshot`。三份实现意味着给状态机加一个格子
        #    要改三处，而**漏改是静默的**（一个数据集用新规则、另一个用旧的，
        #    两边都不报错）。⇒ 收敛到 `DatasetSnapshotService`。
        #
        #    它需要的唯一新能力是 `materialize`：本数据集的物理行落在
        #    `fact_security_master` 里，而那张表要等分区 id 才写得进去。
        partition_id = new_partition_id()
        normalized_hash = _sha256([item.to_dict() for item in records])
        result = DatasetSnapshotService(path=self._path).publish(
            SnapshotPublishRequest(
                dataset_id=DATASET_ID,
                job_id=JOB_ID,
                partition_key=partition_key,
                trigger_id=trigger_id or f"{JOB_ID}:{requested_date}:{uuid.uuid4().hex}",
                # 🔴 写**实际供数方**，不是 dataset 的 primary。
                #    写 primary 会让降级过的那天看起来像正常的一天。
                provider_id=self._served_by,
                raw_artifacts=(artifact,),
                # 真实取数链（含主源失败）⇒ `provider_attempts` 里能查到
                # 「主源挂了、备用源接上」这件事真的发生过。
                failover_attempts=tuple(
                    (a.provider_id, a.role.value, a.succeeded, a.error)
                    for a in self._attempts),
                storage_format="sqlite_fact",
                storage_uri=f"biga+sqlite://fact_security_master/{partition_id}",
                content_sha256=normalized_hash,
                row_count=len(records),
                as_of=retrieved_at,
                knowledge_cutoff=retrieved_at,
                quality_metrics=quality_result.metrics,
                quality_issues=quality_result.issues,
                data_version=target_version,
                quality_status=quality_result.status,
                supersedes_partition_id=supersedes_partition_id,
                supersedes_snapshot_id=supersedes_snapshot_id,
                partition_id=partition_id,
            ),
            materialize=lambda pid: save_security_master_records(
                pid, records, path=self._path),
        )
        return SecurityMasterSyncResult(
            status=quality_result.status,
            as_of_date=requested_date,
            data_run_id=result.data_run_id,
            snapshot_id=result.snapshot_id or None,
            partition_id=result.partition_id or None,
            quality_report_id=result.quality_report_id,
            row_count=len(records),
        )

    def _record_fetch_failure(
        self, *, partition_key, target_version, trigger_id, requested_date,
        started_at, elapsed_ms, error,
    ) -> None:
        """取数失败也要留一次可查的 run。

        🔴 实现在 `DatasetSnapshotService.record_fetch_failure()` —— **只有一份**。
        这里曾经自己写过一遍（本文件的账本收口那一轮留下的），而 2026-09-26
        真机跑 P3-6 时发现 `decision_client` 也需要同一件事 ⇒ 两个调用方就是
        收敛的信号，别等第三个。
        """
        DatasetSnapshotService(path=self._path).record_fetch_failure(
            dataset_id=DATASET_ID, job_id=JOB_ID, partition_key=partition_key,
            trigger_id=trigger_id or f"{JOB_ID}:{requested_date}:{uuid.uuid4().hex}",
            provider_id=PROVIDER_ID, started_at=started_at,
            elapsed_ms=elapsed_ms, error=error, data_version=target_version,
        )
