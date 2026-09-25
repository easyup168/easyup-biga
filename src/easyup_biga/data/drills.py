"""P3-15 · 验收演练的判据。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：读**真实留下的痕迹**（provider 尝试记录、run 终态、修订链、文件字节），
  回答「这项演练到底过没过」
- **不覆盖**：制造那些痕迹。除了 `raw_tamper_drill`（它必须自己制造一次篡改，
  因为没人会在生产里真的改坏一个文件），其余都只观察

🔴 这些函数不会「造」出 PASS
---------------------------
每一个的 `passed` 都来自库里/盘上已经存在的东西。调用方把结果记进
`acceptance.py` 的账本 —— 账本因此记的是观察，不是意图。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from easyup_biga.persistence import connect, data_run_state, partition_key_json

from .contracts import DataRunStatus, ProviderAttemptStatus, ProviderRole
from .file_store import FileStore, FileStoreError
from .lineage_audit import audit_revision_chain
from .replay import offline_replay_bundle

#: 「这次尝试失败了」的全部取值。
#: 🔴 不用 `status.startswith("FAILED")` —— 按字符串形状分类正是 L-13：
#:    哪天多一个 `FAILED_*` 值或某个值改名，判据会静默跟着变，而不是报错。
_FAILED_ATTEMPT_STATUSES = frozenset({
    ProviderAttemptStatus.FAILED_RETRYABLE.value,
    ProviderAttemptStatus.FAILED_PERMANENT.value,
})

#: 演练需要 ≥2 个修订才算证明了「修订链真的能走第二轮」。
REVISION_DRILL_MIN_VERSION = 2


@dataclass(frozen=True, slots=True)
class DrillResult:
    passed: bool
    name: str
    detail: dict[str, Any]


def provider_fallback_drill(data_run_id: str, *, path=None) -> DrillResult:
    """只有「PRIMARY 失败过 **且** FALLBACK 在同一次 run 里成功」才算过。"""
    with connect(path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT provider_id,provider_role,attempt_no,status,error_code "
            "FROM provider_attempts WHERE data_run_id=? ORDER BY attempt_no,attempt_id",
            (data_run_id,),
        ).fetchall()
    attempts = [dict(row) for row in rows]
    primary_failed = any(
        row["provider_role"] == ProviderRole.PRIMARY.value
        and str(row["status"]) in _FAILED_ATTEMPT_STATUSES
        for row in attempts
    )
    fallback_succeeded = any(
        row["provider_role"] == ProviderRole.FALLBACK.value
        and str(row["status"]) == ProviderAttemptStatus.SUCCEEDED.value
        for row in attempts
    )
    return DrillResult(
        primary_failed and fallback_succeeded,
        "PRIMARY_FALLBACK",
        {"data_run_id": data_run_id, "attempts": attempts},
    )


def quarantine_drill(data_run_id: str, *, path=None) -> DrillResult:
    """隔离演练：这次 run 的终态真的落在 QUARANTINED 上。"""
    state = data_run_state(data_run_id, path=path)
    return DrillResult(
        state == DataRunStatus.QUARANTINED.value,
        "QUARANTINED",
        {"data_run_id": data_run_id, "terminal_state": state},
    )


def revision_v2_drill(
    dataset_id: str, partition_key: dict[str, str], *, path=None
) -> DrillResult:
    """同一分区出过第二个修订，且整条链仍然合法。"""
    audit = audit_revision_chain(dataset_id, partition_key, path=path)
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT MAX(data_version) AS max_version,COUNT(*) AS n FROM dataset_snapshots "
            "WHERE dataset_id=? AND partition_key_json=?",
            (dataset_id, partition_key_json(partition_key)),
        ).fetchone()
    max_version = int(row["max_version"] or 0)
    count = int(row["n"] or 0)
    return DrillResult(
        audit.ok and max_version >= REVISION_DRILL_MIN_VERSION and count >= 2,
        "REVISION_V2",
        {
            "dataset_id": dataset_id,
            "partition_key": partition_key,
            "snapshot_count": count,
            "max_data_version": max_version,
            "audit_errors": list(audit.errors),
        },
    )


def raw_tamper_drill(*, work_root: Path | str) -> DrillResult:
    """真的写一个 raw 文件、真的改坏它，证明哈希校验 fail closed。

    ⚠️ 这条是六项里唯一**主动制造**证据的 —— 因为「改坏一个生产文件」
    不可能在生产里自然发生，而不做又等于这道校验从没被验证过。
    所以它只在 `work_root` 下动手，不碰真实数据面。
    """
    store = FileStore(Path(work_root))
    uri, sha, _size = store.write_raw_text(
        "drill.raw", "drill-provider", "20260926", "tamper-proof", '{"ok":true}'
    )
    file = Path(uri)
    file.write_bytes(file.read_bytes() + b"tamper")
    detected = False
    error = None
    try:
        store.verify_file_hash(uri, sha)
    except FileStoreError as exc:
        detected = True
        error = str(exc)
    return DrillResult(detected, "RAW_TAMPER", {"uri": uri, "detected_error": error})


def source_zip_replay_drill(
    evidence_set_id: str, *, path=None, data_root: str = "data"
) -> DrillResult:
    """在恢复出来的那棵树上，证明回放是离线且精确的。

    ⚠️ 解压 / 拷库 / 拷数据面那几步是**操作**，不在这里；
    这个函数是操作做完之后的那道判据。
    """
    try:
        bundle = offline_replay_bundle(evidence_set_id, path=path, data_root=data_root)
    except Exception as exc:
        return DrillResult(False, "SOURCE_ZIP_REPLAY", {"error": str(exc)})
    return DrillResult(
        True,
        "SOURCE_ZIP_REPLAY",
        {
            "evidence_set_id": evidence_set_id,
            "manifest_version": bundle.manifest_version,
            "dataset_snapshot_ids": {
                key: value.snapshot_id for key, value in sorted(bundle.datasets.items())
            },
            "legacy_raw_snapshot_ids": list(bundle.legacy_raw_snapshot_ids),
        },
    )


def duckdb_runtime_drill() -> DrillResult:
    """DuckDB 在**这台机器上**真的能跑 —— 不是 pyproject 里声明了就算。"""
    try:
        import duckdb

        con = duckdb.connect(database=":memory:")
        try:
            got = con.execute("SELECT 40 + 2").fetchone()[0]
        finally:
            con.close()
        return DrillResult(int(got) == 42, "DUCKDB_RUNTIME",
                           {"version": duckdb.__version__, "probe": got})
    except Exception as exc:
        return DrillResult(False, "DUCKDB_RUNTIME", {"error": f"{type(exc).__name__}: {exc}"})
