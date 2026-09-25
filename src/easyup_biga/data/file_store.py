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
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".part-", suffix=".parquet.tmp", dir=path.parent)
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
            # Same-directory hard-link publishes atomically *without* replacement.
            # FileExistsError therefore preserves immutability even under a race.
            try:
                os.link(tmp, path)
            except FileExistsError as exc:
                raise FileStoreError(
                    f"immutable Parquet target already exists: {path}"
                ) from exc
            tmp.unlink()
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
        return str(path), sha, len(items)

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
        path = Path(uri)
        actual = _sha256_bytes(path.read_bytes())
        if actual != expected_sha256:
            raise FileStoreError(
                f"file hash mismatch: {path} expected={expected_sha256} actual={actual}"
            )
