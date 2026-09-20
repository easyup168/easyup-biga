"""判定原件的落库与引用 —— 「结构化数据不经过 LLM」这条的守卫。

背景（2026-09-20，`BIGA-20260920-002` 实测）
---------------------------------------------
契约原本要求 Specialist「把 skill 的 JSON 原样带上」、Supervisor 再抄进 heredoc。
两层复述的结果：

- skill 实际输出 15 条 evidence，**每条都有 `retrieved_at`**
- Specialist 转述后：`as_of` 34 条，`retrieved_at` **0 条**
- 落库 Card 上 25 条 evidence 的 `retrieved_at` 全是 `20:44:34`
  —— 那是 Supervisor 敲命令的时刻，真实采集时刻是 `20:42:48`

也就是说「事实可追溯」在最后一公里被复述打穿了。
本文件守的就是这条路不再被走回去。
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills"))

from _contract import CN_TZ, AgentVerdict, Evidence  # noqa: E402
from _store import (  # noqa: E402
    AppendOnlyViolation,
    connect,
    init_schema,
    load_verdict,
    load_verdict_meta,
    save_verdict,
)

AMEND = REPO / "skills" / "decision-card" / "scripts" / "amend_verdict.py"
SYNTH = REPO / "skills" / "decision-card" / "scripts" / "synthesize.py"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "t.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(path=p)
    return p


def _verdict(agent="market", missing=None, verdict="PASS", status="completed"):
    from datetime import datetime
    as_of = datetime(2026, 9, 18, 15, 0, tzinfo=CN_TZ)
    got = datetime(2026, 9, 20, 20, 42, 48, tzinfo=CN_TZ)   # 真实采集时刻
    ev = [Evidence(field="sh_close", source="sina:kline/sh000001", value=3911.87,
                   as_of=as_of, retrieved_at=got, calc_version="market-calc/1",
                   label="上证指数点位")]
    return AgentVerdict(
        task_id="BIGA-20260918-001", agent=agent, status=status, verdict=verdict,
        result={"sh_close": 3911.87}, confidence=1.0, evidence=ev,
        warnings=[], missing=list(missing or []), elapsed_ms=1234)


class TestRoundTrip:
    def test_存取后每个字段逐字节不变(self, db):
        v = _verdict()
        got = load_verdict(save_verdict(v, path=db), path=db)
        assert got.to_dict() == v.to_dict()

    def test_retrieved_at不被换成当前时刻(self, db):
        """🔴 这条是整件事的起点。

        复述路径下它会变成「敲命令的时刻」；走原件必须原样保留采集时刻。
        """
        v = _verdict()
        got = load_verdict(save_verdict(v, path=db), path=db)
        assert [e.retrieved_at for e in got.evidence] == \
               [e.retrieved_at for e in v.evidence]
        assert got.evidence[0].retrieved_at.strftime("%H:%M:%S") == "20:42:48"

    def test_不存在的id返回None(self, db):
        assert load_verdict(9999, path=db) is None

    def test_只接受契约对象(self, db):
        with pytest.raises(TypeError):
            save_verdict({"agent": "market"}, path=db)


class TestAppendOnly:
    def test_不许UPDATE(self, db):
        vid = save_verdict(_verdict(), path=db)
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(db) as c:
                c.execute("UPDATE agent_verdicts SET verdict_json='{}' "
                          "WHERE verdict_id=?", (vid,))

    def test_不许DELETE(self, db):
        vid = save_verdict(_verdict(), path=db)
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(db) as c:
                c.execute("DELETE FROM agent_verdicts WHERE verdict_id=?", (vid,))

    def test_修订必须写理由(self, db):
        vid = save_verdict(_verdict(), path=db)
        with pytest.raises(ValueError, match="amend_reason"):
            save_verdict(_verdict(missing=["x"], verdict="WARNING",
                                  status="partial"), amends=vid, path=db)


class TestAmendCLI:
    def _run(self, *args, db=None):
        env = {**dict(__import__("os").environ), "BIGA_DB_PATH": str(db)}
        return subprocess.run([sys.executable, str(AMEND), *args],
                              capture_output=True, text=True, env=env)

    def test_追加缺失项写新行原件不动(self, db):
        vid = save_verdict(_verdict(), path=db)
        r = self._run("--ref", str(vid), "--add-missing", "趋势判不了",
                      "--verdict", "WARNING", db=db)
        assert r.returncode == 0, r.stderr
        new = int(r.stderr.split("verdict_ref=")[1].split()[0])

        original, amended = load_verdict(vid, path=db), load_verdict(new, path=db)
        assert original.missing == [] and original.verdict == "PASS"
        assert amended.missing == ["趋势判不了"] and amended.verdict == "WARNING"
        assert amended.status == "partial"
        # 证据一条不少，且仍是采集时刻
        assert len(amended.evidence) == len(original.evidence)
        assert amended.evidence[0].retrieved_at == original.evidence[0].retrieved_at
        assert load_verdict_meta(new, path=db)["amends"] == vid

    def test_加缺失项不给verdict时给出指路报错(self, db):
        vid = save_verdict(_verdict(), path=db)
        r = self._run("--ref", str(vid), "--add-missing", "x", db=db)
        assert r.returncode == 2
        assert "WARNING" in r.stderr and "UNKNOWN" in r.stderr, \
            "报错必须说清下一步怎么做，否则 agent 要花几轮去猜"

    def test_什么都不改时拒绝(self, db):
        vid = save_verdict(_verdict(), path=db)
        assert self._run("--ref", str(vid), db=db).returncode == 2

    def test_ref不存在时报错说清原因(self, db):
        r = self._run("--ref", "999", "--add-missing", "x",
                      "--verdict", "WARNING", db=db)
        assert r.returncode == 2 and "--no-store" in r.stderr


class TestSynthesizeByIds:
    def _run(self, *args, db=None):
        env = {**dict(__import__("os").environ), "BIGA_DB_PATH": str(db)}
        return subprocess.run([sys.executable, str(SYNTH), *args],
                              capture_output=True, text=True, env=env)

    def test_按id合成(self, db):
        a = save_verdict(_verdict("market"), path=db)
        b = save_verdict(_verdict("emotion"), path=db)
        r = self._run("--verdict-ids", f"{a},{b}", "--status", "WAIT",
                      "--headline", "h", "--model-ref", "m", "--no-store", db=db)
        assert r.returncode == 0, r.stderr
        assert "market" in r.stdout and "emotion" in r.stdout

    def test_两种来源互斥(self, db):
        r = self._run("--verdict-ids", "1", "--verdicts", "x.json",
                      "--status", "WAIT", "--headline", "h",
                      "--model-ref", "m", "--no-store", db=db)
        assert r.returncode != 0

    def test_id不存在时报错指路(self, db):
        r = self._run("--verdict-ids", "9999", "--status", "WAIT",
                      "--headline", "h", "--model-ref", "m", "--no-store", db=db)
        assert r.returncode != 0 and "--no-store" in r.stderr


class TestContractsTeachTheSafePath:
    """🔴 契约不许再教那条会丢字段的路。

    守卫写了没用，如果 `AGENTS.md` 还在示范贴 JSON —— agent 照着文档做。
    """

    @pytest.mark.parametrize("path", [
        REPO / "AGENTS.md",
        REPO / "agents" / "emotion" / "AGENTS.md",
        REPO / "agents" / "market" / "AGENTS.md",
    ])
    def test_契约里不出现贴JSON的写法(self, path):
        text = path.read_text(encoding="utf-8")
        assert "--verdicts " not in text and "--verdicts\n" not in text, \
            f"{path.name} 仍在示范 --verdicts（贴 JSON）—— 应改用 --verdict-ids"
        assert "verdict_ref" in text, \
            f"{path.name} 没有告诉 agent 去哪里拿 verdict_ref"
