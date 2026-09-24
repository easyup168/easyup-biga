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

from _contract import CN_TZ, AgentVerdict, Evidence, FactBundle  # noqa: E402
from _store import (  # noqa: E402
    AppendOnlyViolation,
    connect,
    init_schema,
    load_outcome,
    load_verdict,
    load_verdict_meta,
    save_fact_bundle,
    save_verdict,
)
from _roster import absent_registrations, DEFAULT_ROSTER  # noqa: E402

AMEND = REPO / "skills" / "decision-card" / "scripts" / "amend_verdict.py"
SYNTH = REPO / "skills" / "decision-card" / "scripts" / "synthesize.py"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "t.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(path=p)
    return p


def _verdict(agent="market", missing=None, verdict="PASS", status="completed",
             stance=None):
    from datetime import datetime
    as_of = datetime(2026, 9, 18, 15, 0, tzinfo=CN_TZ)
    got = datetime(2026, 9, 20, 20, 42, 48, tzinfo=CN_TZ)   # 真实采集时刻
    ev = [Evidence(field="sh_close", source="sina:kline/sh000001", value=3911.87,
                   as_of=as_of, retrieved_at=got, calc_version="market-calc/1",
                   label="上证指数点位")]
    return AgentVerdict(
        task_id="BIGA-20260918-001", agent=agent, status=status, verdict=verdict,
        result={"sh_close": 3911.87}, data_completeness=1.0, evidence=ev,
        warnings=[], missing=list(missing or []), elapsed_ms=1234, stance=stance)


def _fact(agent="market"):
    """一条新形状 FactBundle（批 E-III 后六个 skill 都产它）。"""
    from datetime import datetime
    as_of = datetime(2026, 9, 18, 15, 0, tzinfo=CN_TZ)
    got = datetime(2026, 9, 20, 20, 42, 48, tzinfo=CN_TZ)
    ev = [Evidence(field="sh_close", source="sina:kline/sh000001", value=3911.87,
                   as_of=as_of, retrieved_at=got, calc_version="market-calc/1",
                   label="上证指数点位")]
    return FactBundle(
        task_id="BIGA-20260918-001", agent=agent, status="completed", verdict="PASS",
        result={"sh_close": 3911.87}, data_completeness=1.0, evidence=ev, elapsed_ms=1234)


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


class TestAmendLineage:
    """A8：修订不许跨 agent、跨决策；一条原件最多被修订一次（不许分叉）。"""

    def test_amends指向不存在的原件被拒(self, db):
        with pytest.raises(ValueError, match="不存在"):
            save_verdict(_verdict(), amends=99999, amend_reason="x", path=db)

    def test_跨agent的修订被拒(self, db):
        vid = save_verdict(_verdict(agent="market"), path=db)
        with pytest.raises(ValueError, match="跨 agent 或跨决策"):
            save_verdict(_verdict(agent="emotion"), amends=vid,
                        amend_reason="不该被接受", path=db)

    def test_跨决策的修订被拒(self, db):
        vid = save_verdict(_verdict(), path=db)
        base = _verdict()
        # contract-exempt: 复制一份原件的字段，只改 task_id，模拟「另一次决策」
        other_decision = AgentVerdict(
            task_id="BIGA-20260919-001", agent=base.agent, status=base.status,
            verdict=base.verdict, result=base.result,
            data_completeness=base.data_completeness, evidence=base.evidence,
            stance=base.stance)
        with pytest.raises(ValueError, match="跨 agent 或跨决策"):
            save_verdict(other_decision, amends=vid, amend_reason="不该被接受", path=db)

    def test_同agent同决策的修订被接受(self, db):
        vid = save_verdict(_verdict(), path=db)
        new = save_verdict(_verdict(missing=["x"], verdict="WARNING",
                                    status="partial"),
                           amends=vid, amend_reason="合法修订", path=db)
        assert load_verdict_meta(new, path=db)["amends"] == vid

    def test_同一条原件不许被修订两次(self, db):
        """探针：先合法修订一次，再对同一个 verdict_id 修订第二次，必须被拒。"""
        vid = save_verdict(_verdict(), path=db)
        save_verdict(_verdict(missing=["x"], verdict="WARNING", status="partial"),
                    amends=vid, amend_reason="第一次修订", path=db)
        with pytest.raises(ValueError, match="只能线性，不许分叉"):
            save_verdict(_verdict(missing=["y"], verdict="WARNING", status="partial"),
                        amends=vid, amend_reason="第二次修订，应该被拒", path=db)


class TestAmendCLI:
    def _run(self, *args, db=None):
        env = {**dict(__import__("os").environ), "BIGA_DB_PATH": str(db)}
        return subprocess.run([sys.executable, str(AMEND), *args],
                              capture_output=True, text=True, env=env)

    def test_旧合体行的修订路径已退役(self, db):
        # 🔴 批 E-III：历史合体 AgentVerdict（save_verdict 仍能造它）的旧修订路径退役 ——
        #    照抄一条旧命令，CLI 明确报错（rc=2）指路，不是静默改库。
        vid = save_verdict(_verdict(), path=db)
        r = self._run("--ref", str(vid),
                      "--add-missing", "market.trend.no_history", "趋势判不了",
                      "--verdict", "WARNING", db=db)
        assert r.returncode == 2
        assert "退役" in r.stderr and "只读" in r.stderr

    def test_fact行加stance成功_写新行事实不动(self, db):
        # 新形状：给 fact 行加一行 AgentAssessment，事实那行一个字不动
        fid = save_fact_bundle(_fact(), path=db)
        r = self._run("--ref", str(fid), "--stance", "放量上涨", db=db)
        assert r.returncode == 0, r.stderr
        new = int(r.stderr.split("verdict_ref=")[1].split()[0])
        assert new != fid, "判断该落在新的一行"
        oc = load_outcome(new, path=db)
        assert oc.stance == "放量上涨"
        # 事实那行的 evidence 原样在（没被重打），retrieved_at 仍是采集时刻
        assert oc.fact.evidence[0].retrieved_at == _fact().evidence[0].retrieved_at

    def test_fact行加add_missing被拒(self, db):
        fid = save_fact_bundle(_fact(), path=db)
        r = self._run("--ref", str(fid), "--add-missing", "market.x.y", "限制", db=db)
        assert r.returncode == 2
        assert "--stance" in r.stderr

    def test_ref不存在时报错说清原因(self, db):
        r = self._run("--ref", "999", "--add-missing", "market.trend.no_history", "x",
                      "--verdict", "WARNING", db=db)
        assert r.returncode == 2 and "--no-store" in r.stderr


class TestSynthesizeByIds:
    def _run(self, *args, db=None):
        env = {**dict(__import__("os").environ), "BIGA_DB_PATH": str(db)}
        return subprocess.run([sys.executable, str(SYNTH), *args],
                              capture_output=True, text=True, env=env)

    def test_按id合成(self, db):
        a = save_verdict(_verdict("market", stance="放量上涨"), path=db)
        b = save_verdict(_verdict("emotion", stance="修复"), path=db)
        # 🔴 F-8：2 个 agent 到场，另外 4 个天然缺席——roster 判据按计数
        #    比较，4 条 --extra-missing 才够（本文件不测 roster）。
        extra_missing_args = []
        # 🔴 批 P：每个缺席的 agent 各一条**对得上号**的登记（代码里带 agent 名）。
        #    以前是 N 条 supervisor.agent_offline 凑条数——那正是评审 §18 指出的洞。
        for m in absent_registrations(["market", "emotion"]):
            extra_missing_args += ["--extra-missing", m.code, str(m)]
        r = self._run("--verdict-ids", f"{a},{b}", "--status", "WAIT",
                      "--headline", "h", "--model-ref", "m",
                      *extra_missing_args,
                      "--no-store", db=db)
        assert r.returncode == 0, r.stderr
        assert "market" in r.stdout and "emotion" in r.stdout

    def test_没提交stance时拒绝出卡并指路(self, db):
        """🔴 方向判断只写在自然语言里，等于每跑一次丢一次。"""
        a = save_verdict(_verdict("market"), path=db)      # 没有 stance
        r = self._run("--verdict-ids", str(a), "--status", "WAIT",
                      "--headline", "h", "--model-ref", "m", "--no-store", db=db)
        assert r.returncode != 0
        assert "stance" in r.stderr and "amend_verdict.py" in r.stderr, \
            "报错必须说清下一步跑什么命令"

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

    # 🔴 外部评审 F9：这份清单原来是**手写的三条**，而契约有六份。
    #    自 sector/technical/news/risk 建立以来从未更新 ——
    #    清单类守卫不会随组件数量增长而自动同步，这是本项目的头号形状。
    #    ⇒ 改成扫目录：**新建一个 agent 就自动纳入**。
    @pytest.mark.parametrize(
        "path", [REPO / "AGENTS.md"] + sorted((REPO / "agents").glob("*/AGENTS.md")))
    def test_契约里不出现贴JSON的写法(self, path):
        text = path.read_text(encoding="utf-8")
        assert "--verdicts " not in text and "--verdicts\n" not in text, \
            f"{path.name} 仍在示范 --verdicts（贴 JSON）—— 应改用 --verdict-ids"

    @pytest.mark.parametrize(
        "path", sorted(p for p in (REPO / "agents").glob("*/AGENTS.md")
                       if p.parent.name not in __import__("_consistency").SUPPORT_AGENTS))
    def test_契约讲清楚了第一次去哪拿verdict_ref(self, path):
        # SUPPORT_AGENTS（如 synthesizer）不产 verdict_ref —— 它读别人的 verdict、
        # 产出 Card 判断，没有「第一次去哪拿 verdict_ref」这回事，故排除。
        """🔴 判据不是「提到了这个词」，是「讲清楚了第一次去哪拿」。

        F9 的要害在这里：`news/AGENTS.md` 里 "verdict_ref" 本来就出现了 3 次
        （都在 `--ref <你的 verdict_ref>` 这类示例里），
        **纯子串匹配会判它通过** —— 而它恰恰是唯一缺了那句话的。

        真实后果有 trace 为证：两次真实调用里 news 都把 task_id 当成
        `--ref` 传进去，报错后去读 `--help` 和源码，每次多花
        2~3 次工具调用、约 20~30 秒 —— 与契约自己强调的
        「不要为了确认参数去读源码」形成直接讽刺。

        锚点选 `verdict_ref=NN` 这个**字面量**：它是 stderr 的真实输出格式，
        不是散文措辞，改写同义词不会把它绕过去。
        """
        text = path.read_text(encoding="utf-8")
        assert "verdict_ref=NN" in text, (
            f"{path.name} 没告诉 agent **第一次**去哪拿 verdict_ref。\n"
            "  只在 `--ref <你的 verdict_ref>` 里提到它是不够的 ——\n"
            "  那假设读者已经知道它从哪来。照抄其余契约里那句：\n"
            "  「stderr 最后一行是 `verdict_ref=NN`，记下这个数字。」")
