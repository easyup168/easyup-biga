"""P2-1：Migration 原子化 —— 故障注入验证。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：`_apply_migration` 失败后 user_version 不变（可重试）、
  失败后相同 version 可用正确 SQL 补跑、DDL 语法错误下无部分建表残留
- **不覆盖**：完整 schema 的版本升级链（那是 test_store.py 的责任）

探针清单：
  M1  坏 SQL 触发异常
  M2  异常后 user_version 未被更新（版本号 = 0）
  M3  异常后以相同 version 重跑正确 SQL 可以成功（真正的"可重试"）
  M4  坏 SQL 后目标表未被建出来（无部分 DDL 残留）

sabotage 说明：
  把 `_apply_migration` 里的 `PRAGMA user_version = {version}` 移到 `BEGIN IMMEDIATE` 之前，
  M2 就会变红（user_version 会被更新，但 DDL 回滚了）。
"""

from __future__ import annotations

import sqlite3  # store-exempt: _apply_migration 接受裸 Connection，此测试必须直接构造它

import pytest

from easyup_biga.persistence.db import _apply_migration  # type: ignore[attr-defined]


@pytest.fixture()
def fresh_conn(tmp_path):
    db = tmp_path / "m.db"
    conn = sqlite3.connect(str(db))  # store-exempt: 直接测 _apply_migration 内部接口
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


class TestMigrationAtomicity:
    def test_M1_坏SQL触发异常(self, fresh_conn):
        with pytest.raises(Exception):
            _apply_migration(fresh_conn, version=1,
                             sql="CREATE INDEX bad_idx ON nonexistent_table(col)")

    def test_M2_异常后user_version未更新(self, fresh_conn):
        assert _user_version(fresh_conn) == 0
        with pytest.raises(Exception):
            _apply_migration(fresh_conn, version=1,
                             sql="CREATE INDEX bad_idx ON nonexistent_table(col)")
        assert _user_version(fresh_conn) == 0, "失败的 migration 不能把 user_version 推进"

    def test_M3_失败后相同版本可用正确SQL重试(self, fresh_conn):
        with pytest.raises(Exception):
            _apply_migration(fresh_conn, version=1,
                             sql="CREATE INDEX bad_idx ON nonexistent_table(col);")
        # 相同 version=1，这次给正确 SQL（注意末尾分号）
        _apply_migration(fresh_conn, version=1,
                         sql="CREATE TABLE migration_ok (id INTEGER PRIMARY KEY);")
        assert _user_version(fresh_conn) == 1
        assert _table_exists(fresh_conn, "migration_ok")

    def test_M4_坏SQL后目标表未被建出(self, fresh_conn):
        # SQL 包含两条：第一条建表成功，第二条引用不存在的表（导致脚本整体失败）
        bad_sql = (
            "CREATE TABLE partial_table (id INTEGER PRIMARY KEY);\n"
            "CREATE INDEX bad_idx ON nonexistent_table(col);\n"
        )
        with pytest.raises(Exception):
            _apply_migration(fresh_conn, version=1, sql=bad_sql)
        # DDL 应被整体回滚：partial_table 不应该存在
        assert not _table_exists(fresh_conn, "partial_table"), (
            "失败的 migration 不能留下半建好的表 —— BEGIN IMMEDIATE 必须保证原子性"
        )
