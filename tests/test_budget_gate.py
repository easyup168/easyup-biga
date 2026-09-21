"""出卡预算闸门 —— 接飞书之后新增的攻击面。

在飞书接通之前，误触一次出卡的代价是零：每次都要人手工敲一条命令。
**接通之后，手机上一句话就是 198s / $1.37。** 2026-09-21 当天出了 18 张卡。

🔴 去重挡不住这个：外部设计文档提的「飞书事件去重」防的是
**飞书重发同一个事件**，而人连点两下是**两个不同的事件** —— 去重会放行。

闸门放在 Stage 0 占号这个咽喉点：任何触发路径都绕不过它。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))
sys.path.insert(0, str(REPO / "skills" / "decision-card" / "scripts"))

import budget  # noqa: E402

from _store import db, init_schema  # noqa: E402

NEW_DECISION = REPO / "skills" / "decision-card" / "scripts" / "new_decision.py"


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    p = tmp_path / "b.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    return p


def run(*args, env=None):
    import os
    return subprocess.run([sys.executable, str(NEW_DECISION), *args],
                          capture_output=True, text=True, cwd=REPO,
                          env={**os.environ, **(env or {})})


class TestGate:
    def test_空库放行(self, fresh):
        assert budget.check_budget(path=fresh) == []

    def test_刚占过号就拦(self, fresh):
        db.reserve_decision_id(by="a", path=fresh)
        reasons = budget.check_budget(path=fresh)
        assert any("最小间隔" in r for r in reasons)

    def test_拦的时候要说清是哪个号(self, fresh):
        """🔴 报错要指路 —— 「太快了」没用，要说出是谁占的、什么时候。"""
        db.reserve_decision_id(by="某人", path=fresh)
        joined = " ".join(budget.check_budget(path=fresh))
        assert "某人" in joined and "BIGA-" in joined

    def test_没出卡的运行算在跑(self, fresh):
        db.reserve_decision_id(by="a", path=fresh)
        assert any("没出卡" in r for r in budget.check_budget(path=fresh))

    def test_陈旧占号不永久关死闸门(self, fresh, monkeypatch):
        """🔴 库里有冒烟测试留下的占号，它们**永远不会**出卡。

        不设窗口的话闸门会被它们永久关死 —— 而一个永远拒绝的闸门
        等于逼所有人常用 `--force`，也就等于没有闸门。
        """
        db.reserve_decision_id(by="smoke", path=fresh)
        monkeypatch.setattr(budget, "INFLIGHT_SEC", 0)
        monkeypatch.setattr(budget, "MIN_GAP_SEC", 0)
        assert budget.check_budget(path=fresh) == []

    def test_当日上限(self, fresh, monkeypatch):
        monkeypatch.setattr(budget, "MIN_GAP_SEC", 0)
        monkeypatch.setattr(budget, "INFLIGHT_SEC", 0)
        monkeypatch.setattr(budget, "DAILY_CAP", 3)
        for _ in range(3):
            db.reserve_decision_id(by="a", path=fresh)
        assert any("上限" in r for r in budget.check_budget(path=fresh))

    def test_闸门只读_不消耗配额(self, fresh):
        """🔴 「检查一下能不能跑」本身不该消耗配额 ——
        这是这类闸门最常见的设计错误。"""
        before = len(db.list_decision_ids(path=fresh)) \
            if hasattr(db, "list_decision_ids") else None
        for _ in range(5):
            budget.check_budget(path=fresh)
        with db.connect(fresh, readonly=True) as c:
            n = c.execute("SELECT count(*) FROM decision_ids").fetchone()[0]
        assert n == 0, f"检查了 5 次，库里多了 {n} 个号"
        assert before in (None, 0)


class TestCli:
    """判据落在**命令行行为**上 —— 那才是飞书与 cron 真正会走的路。"""

    def test_第一次放行第二次被拦(self, tmp_path):
        env = {"BIGA_DB_PATH": str(tmp_path / "c.db")}
        a = run("--by", "one", env=env)
        assert a.returncode == 0 and a.stdout.strip().startswith("BIGA-")
        b = run("--by", "two", env=env)
        assert b.returncode == 3, b.stdout
        assert "预算闸门" in b.stderr

    def test_force能越过(self, tmp_path):
        env = {"BIGA_DB_PATH": str(tmp_path / "d.db")}
        run("--by", "one", env=env)
        c = run("--by", "two", "--force", env=env)
        assert c.returncode == 0 and c.stdout.strip().startswith("BIGA-")

    def test_被拦时不占号(self, tmp_path):
        """🔴 关键：拒绝路径**不能**消耗一个编号 ——
        否则连点十下就把当日配额用完了，而一张卡都没出。"""
        env = {"BIGA_DB_PATH": str(tmp_path / "e.db")}
        run("--by", "one", env=env)
        for _ in range(3):
            run("--by", "spam", env=env)
        sys.path.insert(0, str(REPO / "skills"))
        with db.connect(tmp_path / "e.db", readonly=True) as c:
            n = c.execute("SELECT count(*) FROM decision_ids").fetchone()[0]
        assert n == 1, f"被拦了 3 次却占掉了 {n} 个号"


class TestErrorPointsSomewhereReal:
    def test_报错指的那条命令真的存在(self):
        """F7 的教训：报错里点名的文件必须真的存在。"""
        msg = budget.explain(["测试"])
        import re
        for rel in re.findall(r"(tools/verify/\S+?\.py)", msg):
            assert (REPO / rel).exists(), f"报错指向不存在的 {rel}"

    def test_报错说了怎么越过(self):
        msg = budget.explain(["测试"])
        assert "--force" in msg, "只说「不行」的闸门会被绕过，不会被修好"
