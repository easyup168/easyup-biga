"""生产库围栏 —— 测试进程不许打开 `data/biga.db`。

为什么有这个文件
----------------
`tools/verify/probe.sh` 就是为这件事写的：把手工探针跑在一次性库上，
因为 `_store` 是追加式的、写错的行**删不掉**。

它存在之后，同一件事**又发生了一次** —— 2026-09-23 两行假证据
（`task_id=BIGA-20260302-001`、`field=x`、`source=derived:x`）进了真库，
来自一次没设 `BIGA_DB_PATH` 的探针。第二次是外部复核在清点派生证据时发现的：
六类分布相加差 2 条。

> 「记得用 probe.sh」是靠记性的约定，而记性正是追加式存储惩罚的东西。
> 同一形状第二次发生 ⇒ 把判据从文档搬进进程里。

与 `conftest.py` 开头那条禁网围栏是同一个动作：**声称的规矩必须有东西执行它**。

🔴 本文件是那条围栏自己的探针 —— 围栏没被验证过会红，就只是另一句散文。
"""

from __future__ import annotations

import pathlib
import sqlite3  # store-exempt: 本文件测的就是「裸 sqlite3 也被拦住」——
#                 走 _store 只能证明那一条路被拦，证明不了别的路
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
PROD = (REPO / "data" / "biga.db").resolve()


class Test围栏真的会红:
    def test_直接开生产库被拦(self):
        with pytest.raises(Exception) as ei:
            # store-exempt: 围栏探针，必须用裸调用
            sqlite3.connect(str(PROD))
        assert "生产库" in str(ei.value)

    def test_只读URI也被拦(self):
        """只读也拦：那让测试依赖这台机器上恰好有什么数据 —— 与禁网同一个理由。"""
        with pytest.raises(Exception) as ei:
            # store-exempt: 围栏探针，必须用裸调用
            sqlite3.connect(f"file:{PROD}?mode=ro", uri=True)
        assert "生产库" in str(ei.value)

    def test_Path对象形式也被拦(self):
        with pytest.raises(Exception) as ei:
            # store-exempt: 围栏探针，必须用裸调用
            sqlite3.connect(PROD)
        assert "生产库" in str(ei.value)

    def test_走_store的connect也被拦(self):
        """🔴 判据要打在**业务真正走的那条路**上，不能只拦裸 sqlite3 ——
        「守卫查的地方和它声称守的地方不是同一处」是本仓库最常见的失效形状。"""
        sys.path.insert(0, str(REPO / "skills"))
        from _store import connect
        with pytest.raises(Exception) as ei:
            with connect(PROD) as c:
                c.execute("SELECT 1")
        assert "生产库" in str(ei.value)

    def test_相对路径也被拦(self, monkeypatch):
        """探针当初用的就是相对路径 `data/biga.db`，解析之后才等于生产库。"""
        monkeypatch.chdir(REPO)
        with pytest.raises(Exception) as ei:
            # store-exempt: 围栏探针，必须用裸调用
            sqlite3.connect("data/biga.db")
        assert "生产库" in str(ei.value)


class Test围栏不误伤:
    def test_tmp库照常可用(self, tmp_path):
        # store-exempt: 围栏探针，必须用裸调用
        c = sqlite3.connect(str(tmp_path / "x.db"))
        c.execute("SELECT 1")
        c.close()

    def test_内存库照常可用(self):
        # store-exempt: 围栏探针，必须用裸调用
        c = sqlite3.connect(":memory:")
        c.execute("SELECT 1")
        c.close()

    def test_同名但不同目录的库不被误拦(self, tmp_path):
        """判据是**解析后的绝对路径**，不是文件名 —— 全仓几十处夹具都叫
        `tmp_path / "biga.db"`，按文件名拦会把它们全部误杀。"""
        # store-exempt: 围栏探针，必须用裸调用
        c = sqlite3.connect(str(tmp_path / "biga.db"))
        c.execute("SELECT 1")
        c.close()
