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

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

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


def _run(*args, env_extra=None, timeout=30, lock=None):
    # 🔴 锁路径必须指到测试自己的地盘。默认那把在**仓库根**，与这台机器上
    #    每 2 分钟跑一次的 notify-worker-biga.timer 是同一把 ——
    #    生产恰好并发时测试就红、重跑又绿，看起来像 flaky，
    #    实际是**测试与生产抢同一个资源**（违反 P1-3「默认 pytest hermetic」）。
    env = {**os.environ, **(env_extra or {})}
    env.setdefault("BIGA_NOTIFY_LOCK", str(lock or (REPO / ".biga-notify.test.lock")))
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


class TestSingleInstanceLock:
    """P2-4：并发两个 worker 只有一个发出通知，另一个因拿不到锁直接退出。

    sabotage 验证：把 bin/biga-notify 里的 flock 块注释掉，
    两个进程都会跑到 deliver，send_count 变成 2。
    """

    def test_并发两个worker只发送一次(self, tmp_path):
        p = tmp_path / "t.db"
        init_schema(p)
        with connect(p) as conn:
            _insert_notification(conn, event_type="card_completed",
                                 aggregate="BIGA-C-001", payload={"decision_id": "BIGA-C-001"})

        # 慢桩：sleep 1.5s，保证两个进程真的会重叠
        counter_file = tmp_path / "send_count"
        counter_file.write_text("0")
        slow_stub = tmp_path / "slow-biga"
        slow_stub.write_text(
            "#!/usr/bin/env bash\n"
            "set -e\n"
            f"n=$(cat '{counter_file}')\n"
            f"echo $((n+1)) > '{counter_file}'\n"
            "sleep 1.5\n"
            "echo '{\"payload\":{\"ok\":true}}'\n",
            encoding="utf-8")
        slow_stub.chmod(0o755)

        env = {**os.environ,
               "BIGA_DB_PATH": str(p),
               "BIGA": str(slow_stub),
               # 🔴 这一把锁**必须是本测试专属的**：被测的正是「两个 worker 只发
               #    一次」，而默认锁在仓库根、与生产定时器共用 ⇒ 生产并发时
               #    这条会红（2026-09-26 实测撞到）。见 `_run()` 的说明。
               "BIGA_NOTIFY_LOCK": str(tmp_path / "notify.lock"),
               "BIGA_FEISHU_OWNER_ID": "ou_test"}
        import threading
        procs = []
        errs = []

        def launch():
            r = subprocess.run(
                ["bash", str(REPO / "bin" / "biga-notify")],
                cwd=REPO, env=env, capture_output=True, text=True, timeout=10)
            procs.append(r)
            if "Traceback" in r.stderr:
                errs.append(r.stderr)

        t1 = threading.Thread(target=launch)
        t2 = threading.Thread(target=launch)
        t1.start()
        t2.start()
        t1.join(timeout=12)
        t2.join(timeout=12)

        assert not errs, f"进程 stderr 里出现了 Traceback：{errs}"
        assert all(r.returncode == 0 for r in procs), [r.stderr for r in procs]
        send_count = int(counter_file.read_text().strip())
        assert send_count == 1, (
            f"两个并发 worker 发了 {send_count} 次，flock 没有拦住重复投递"
        )
