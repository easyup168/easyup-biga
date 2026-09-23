"""`bin/biga-notify` —— 对齐 minimal-feishu-design 的固定投递入口（真跑子进程）。

它是 `notify_worker.py` 的一层薄壳：唯一的理由是把默认渠道从**安全但什么都不发**
的 `stdout` 桩，换成"这条命令的调用者显然是想真的发"的 `feishu`——批 G-II P6 live
真跑时就撞过一次「忘记 `--deliverer feishu`，通知一直攒在 outbox 里」的事故形状。

真跑 bash 子进程（与 `test_entry_guard.py` 测 `bin/biga-card` 同一手法），
不 mock 被测脚本本身——只在最外层用一个假 `bin/biga` stub 替身，断言
「默认会不会试图经它发飞书」而不需要真实凭据。
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))

from _store import init_schema  # noqa: E402
from _store.db import _insert_notification, connect  # noqa: E402


@pytest.fixture()
def db_with_pending(tmp_path):
    """一个已迁移、且有一条待投通知的库。"""
    p = tmp_path / "t.db"
    init_schema(p)
    with connect(p) as conn:
        _insert_notification(conn, event_type="card_completed",
                             aggregate="BIGA-TEST-001", payload={"decision_id": "BIGA-TEST-001"})
    return p


@pytest.fixture()
def fake_biga(tmp_path):
    """替身 `bin/biga`：只记「被调用过」，不真跑任何东西（不联网、不认证）。"""
    marker = tmp_path / "biga-was-called"
    stub = tmp_path / "fake-biga"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"echo called >> '{marker}'\n"
        "echo '{\"payload\":{\"ok\":true}}'\n",
        encoding="utf-8")
    stub.chmod(0o755)
    return stub, marker


def _run(*args, env_extra=None, timeout=30):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(["bash", str(REPO / "bin" / "biga-notify"), *args],
                          cwd=REPO, env=env, capture_output=True,
                          text=True, timeout=timeout)


class TestDefaultsToFeishu:
    def test_不传参数时默认真投递_会经bin_biga尝试发(self, db_with_pending, fake_biga):
        stub, marker = fake_biga
        r = _run(env_extra={"BIGA_DB_PATH": str(db_with_pending),
                            "BIGA": str(stub),
                            "BIGA_FEISHU_OWNER_ID": "ou_test"})
        assert marker.exists(), (
            "默认调用一条都没经过 bin/biga——说明没有真的默认成 feishu。\n"
            f"stdout={r.stdout!r} stderr={r.stderr!r}")

    def test_显式stdout时不经过bin_biga(self, db_with_pending, fake_biga):
        stub, marker = fake_biga
        r = _run("--deliverer", "stdout",
                 env_extra={"BIGA_DB_PATH": str(db_with_pending), "BIGA": str(stub)})
        assert r.returncode == 0, r.stderr
        assert not marker.exists(), "显式要 stdout 桩，不该碰 bin/biga"
        assert "投出 1" in r.stderr


class TestPassthrough:
    def test_limit参数会传给notify_worker(self, tmp_path):
        p = tmp_path / "empty.db"
        init_schema(p)
        r = _run("--deliverer", "stdout", "--limit", "3",
                 env_extra={"BIGA_DB_PATH": str(p)})
        assert r.returncode == 0, r.stderr
        assert "待投 0" in r.stderr

    def test_未知参数原样报错_不吞掉(self, tmp_path):
        p = tmp_path / "empty.db"
        init_schema(p)
        r = _run("--not-a-real-flag", env_extra={"BIGA_DB_PATH": str(p)})
        assert r.returncode != 0
