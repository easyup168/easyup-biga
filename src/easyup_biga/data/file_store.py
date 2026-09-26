"""Filesystem data plane for Phase 3.

SQLite remains the control plane.  Large raw payloads and historical/decision
partitions live on disk.  Writes are same-directory temp -> fsync/read-back ->
atomic replace so metadata is never pointed at a half-written file.
"""
from __future__ import annotations

import gzip
import hashlib
import importlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping

if TYPE_CHECKING:  # makes the runtime dependency visible to packaging/static scanners
    import duckdb  # noqa: F401


class FileStoreError(RuntimeError):
    pass


def _duckdb():
    try:
        return importlib.import_module("duckdb")  # store-exempt: declared DuckDB runtime, never sqlite3
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on local install
        raise RuntimeError(
            "Phase 3 Parquet/DuckDB operations require runtime dependency 'duckdb'. "
            "Install the project dependencies before running data jobs."
        ) from exc


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_component(value: str) -> str:
    text = str(value)
    if not text or text in {".", ".."} or "/" in text or "\\" in text:
        raise ValueError(f"unsafe path component: {value!r}")
    return text


def _atomic_bytes(path: Path, data: bytes) -> tuple[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        readback = tmp.read_bytes()
        if readback != data:
            raise FileStoreError(f"read-back mismatch before publishing {path}")
        os.replace(tmp, path)
        # fsync the directory entry on POSIX where supported.
        try:
            dfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    finally:
        if tmp.exists():
            tmp.unlink()
    return _sha256_bytes(data), len(data)


def _sql_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_path(path: Path | str) -> str:
    return str(path).replace("'", "''")


def _scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # Dataset normalizers should already flatten nested values.  This fallback keeps
    # Parquet rows deterministic instead of relying on driver-specific object coercion.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _duck_type(values: list[Any]) -> str:
    non_null = [v for v in values if v is not None]
    if not non_null:
        return "VARCHAR"
    if all(isinstance(v, bool) for v in non_null):
        return "BOOLEAN"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in non_null):
        return "BIGINT"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null):
        return "DOUBLE"
    return "VARCHAR"


@dataclass(frozen=True, slots=True)
class StagedParquet:
    """一个写好但**还没进 lake** 的 Parquet 分区。

    `commit()` 之前它对扫盘查询不可见；`abort()` 丢弃它，不留痕迹。
    两者都是幂等的 —— 提交后再 abort 不会删掉已发布的分区。
    """

    staging: Path
    target: Path
    sha256: str
    row_count: int

    def commit(self) -> Path:
        """原子地把它放进 lake。目标已存在 ⇒ 抛，绝不覆盖已发布的字节。"""
        if not self.staging.exists():
            if self.target.exists():
                return self.target          # 已经提交过，幂等
            raise FileStoreError(f"staged Parquet is gone, cannot commit: {self.staging}")
        self.target.parent.mkdir(parents=True, exist_ok=True)
        # 同设备硬链接：**不替换**地原子发布 ⇒ 即使并发也守得住不可变性。
        try:
            os.link(self.staging, self.target)
        except FileExistsError as exc:
            raise FileStoreError(
                f"immutable Parquet target already exists: {self.target}") from exc
        self.staging.unlink()
        try:
            dfd = os.open(self.target.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
        return self.target

    def abort(self) -> None:
        """丢弃暂存文件。已经提交过就什么都不做。"""
        if self.staging.exists():
            self.staging.unlink()


class FileStore:
    def __init__(self, root: Path | str = "data") -> None:
        self.root = Path(root)

    def write_raw_text(
        self,
        dataset_id: str,
        provider_id: str,
        date_key: str,
        artifact_id: str,
        body: str,
        ext: str = "json.gz",
    ) -> tuple[str, str, int]:
        dataset = _safe_component(dataset_id.replace(".", "_"))
        provider = _safe_component(provider_id)
        date = _safe_component(date_key)
        artifact = _safe_component(artifact_id)
        if ext not in {"json", "json.gz", "csv.gz", "bin"}:
            raise ValueError(f"unsupported raw extension: {ext}")
        path = self.root / "raw" / dataset / f"provider={provider}" / f"date={date}" / f"{artifact}.{ext}"
        raw = body.encode("utf-8")
        stored = gzip.compress(raw, mtime=0) if ext.endswith(".gz") else raw
        sha, size = _atomic_bytes(path, stored)
        return str(path), sha, size

    def parquet_path(
        self,
        dataset_id: str,
        partition_key: Mapping[str, str],
        *,
        schema_version: int = 1,
        data_version: int = 1,
    ) -> Path:
        if int(schema_version) < 1 or int(data_version) < 1:
            raise ValueError("schema_version and data_version must be positive")
        suffix = "/".join(
            f"{_safe_component(k)}={_safe_component(v)}"
            for k, v in sorted(partition_key.items())
        )
        if not suffix:
            raise ValueError("partition_key must not be empty")
        return (
            self.root
            / "lake"
            / _safe_component(dataset_id.replace(".", "_"))
            / f"schema_version={int(schema_version)}"
            / suffix
            / f"data_version={int(data_version):06d}"
            / "part-00000.parquet"
        )

    def write_parquet_rows(
        self,
        dataset_id: str,
        partition_key: Mapping[str, str],
        rows: Iterable[Mapping[str, Any]],
        schema_version: int = 1,
        data_version: int = 1,
    ) -> tuple[str, str, int]:
        """写一个不可变 Parquet 分区并**立刻**落进 lake。

        ⚠️ 生产发布路径**不走这个**，走 `stage_parquet_rows()` —— 见那边的说明。
        这里保留是因为「我只想写个分区」在测试与一次性脚本里是合理诉求，
        而让它们去凑一个两阶段协议只会让人绕开协议。
        """
        staged = self.stage_parquet_rows(
            dataset_id, partition_key, rows,
            schema_version=schema_version, data_version=data_version)
        staged.commit()
        return str(staged.target), staged.sha256, staged.row_count

    def stage_parquet_rows(
        self,
        dataset_id: str,
        partition_key: Mapping[str, str],
        rows: Iterable[Mapping[str, Any]],
        schema_version: int = 1,
        data_version: int = 1,
    ) -> "StagedParquet":
        """把分区写进暂存区，**先不放进 lake**；返回可提交/可丢弃的句柄。

        🔴 为什么要两阶段
        -----------------
        发布是「写文件」+「进账本」两件事，中间总有一个窗口。问题是**哪一半
        先做**，因为两种失败的可见性天差地别：

        - **先文件、后账本**：崩在中间 ⇒ lake 里多一个控制面不认的分区。
          `query_eod_as_of` 走控制面看不见它，而 `query_eod_between` 是**扫盘**的，
          会读到它 —— 同一个交易日两条查询给出两套数，**都不报错**。哑的。
        - **先账本、后文件**：崩在中间 ⇒ 账本行指向一个不存在的文件。
          回放与完整性审计都会重算哈希，**当场抛**。响的。

        ⇒ 取后者。暂存区在 `lake/` **之外**，所以扫盘查询绝对看不到未提交的分区。

        实际接法是把 `commit()` 交给 `DatasetSnapshotService.publish()` 的
        `materialize` 回调 —— 于是文件落进 lake 这一步发生在分区行之后、
        快照之前。提交失败 ⇒ run 走 FAILED ⇒ **不出快照** ⇒ 下游看不见。
        """
        items = [{str(k): _scalar(v) for k, v in dict(row).items()} for row in rows]
        if not items:
            raise ValueError("cannot publish an empty Parquet partition")
        keys = list(items[0])
        if not keys or any(set(item) != set(keys) for item in items):
            raise ValueError("all Parquet rows must have the same non-empty column set")

        path = self.parquet_path(
            dataset_id,
            partition_key,
            schema_version=schema_version,
            data_version=data_version,
        )
        # A data-version path is immutable.  Sequential retry/revision logic must never
        # replace bytes that an existing DatasetPartition already references.
        if path.exists():
            raise FileStoreError(f"immutable Parquet target already exists: {path}")
        # 🔴 暂存目录在 `lake/` **之外** —— 扫盘查询的 glob 以 `lake/` 开头，
        #    所以未提交的分区它绝对看不到。放在 lake 里再靠文件名前缀躲，
        #    是拿「glob 恰好不匹配」当不变量，那种约定迟早被一次改 glob 破掉。
        staging_dir = self.root / "_staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="part-", suffix=".parquet.staged", dir=staging_dir)
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            db = _duckdb()
            con = db.connect(database=":memory:")
            try:
                columns = []
                for key in keys:
                    values = [item.get(key) for item in items]
                    columns.append(f"{_sql_ident(key)} {_duck_type(values)}")
                con.execute("CREATE TABLE payload (" + ",".join(columns) + ")")
                placeholders = ",".join("?" for _ in keys)
                con.executemany(
                    f"INSERT INTO payload VALUES ({placeholders})",
                    [[item.get(key) for key in keys] for item in items],
                )
                con.execute(
                    f"COPY payload TO '{_sql_path(tmp)}' (FORMAT PARQUET, COMPRESSION ZSTD)"
                )
                got = con.execute(
                    f"SELECT count(*) FROM read_parquet('{_sql_path(tmp)}')"
                ).fetchone()[0]
                if int(got) != len(items):
                    raise FileStoreError(
                        f"Parquet read-back row count {got} != {len(items)}"
                    )
            finally:
                con.close()
            data = tmp.read_bytes()
            if not data:
                raise FileStoreError("DuckDB produced an empty Parquet file")
            sha = _sha256_bytes(data)
            with tmp.open("rb") as fh:
                os.fsync(fh.fileno())
        except Exception:
            if tmp.exists():
                tmp.unlink()
            raise
        return StagedParquet(staging=tmp, target=path, sha256=sha, row_count=len(items))

    def read_parquet_rows(self, uri: str) -> list[dict[str, Any]]:
        db = _duckdb()
        con = db.connect(database=":memory:")
        try:
            cur = con.execute(f"SELECT * FROM read_parquet('{_sql_path(uri)}')")
            columns = [item[0] for item in cur.description]
            return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()

    def verify_file_hash(self, uri: str, expected_sha256: str) -> None:
        """重算文件哈希并与预期对齐。对不上、或文件没了，都抛 `FileStoreError`。

        🔴 「文件不存在」必须和「内容被改」走**同一个异常类型**。
        否则调用方 `except FileStoreError` 捕到的是「改了」，而删掉整个文件
        会漏成一个 `FileNotFoundError` 往上冒 —— 最彻底的那种篡改反而绕过了
        为篡改准备的那条分支。
        """
        path = Path(uri)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise FileStoreError(f"file is unreadable: {path} ({exc})") from exc
        actual = _sha256_bytes(data)
        if actual != expected_sha256:
            raise FileStoreError(
                f"file hash mismatch: {path} expected={expected_sha256} actual={actual}"
            )
