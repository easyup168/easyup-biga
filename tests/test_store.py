"""数据层行为测试。

重点不在 CRUD 跑不跑得通，而在三件「坏掉时不会报错」的事：
  1. 只追加的表真的改不动（触发器，不是约定）
  2. 派生列不可能与 card_json 不一致（因为调用方无法单独指定它们）
  3. 回放不覆盖原始记录
"""

from __future__ import annotations

import pathlib
import sys
from datetime import timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

from _contract import AgentVerdict, DecisionCard, Evidence, new_task_id, now_cn
from _store import (
    SCHEMA_VERSION,
    AppendOnlyViolation,
    connect,
    init_schema,
    list_agent_runs,
    load_card,
    load_raw_snapshot,
    load_verdicts,
    next_decision_id,
    record_agent_run,
    record_verdict_run,
    reserve_decision_id,
    save_card,
    save_raw_snapshot,
)

TID = new_task_id(1, day="20260919")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    return p


def make_verdict(**kw) -> AgentVerdict:
    t = now_cn()
    # contract-exempt: 构造真 dataclass 的 kwargs
    base = dict(
        task_id=TID, agent="emotion", status="completed", verdict="PASS",
        result={"limit_up": 42}, confidence=0.8,
        evidence=[Evidence(field="limit_up", source="biga.db:raw_market_snapshot",
                           value=42, as_of=t - timedelta(seconds=300),
                           retrieved_at=t, label="涨停家数")],
        elapsed_ms=8400,
    )
    base.update(kw)
    return AgentVerdict(**base)


def make_card(**kw) -> DecisionCard:
    """默认造出**合法**的卡。

    🔴 改 `decision_id` 时，verdict 的 `task_id` 必须跟着改 ——
    否则造出来的就是「卡 A 装着 B 的判定」，而契约层现在会拒绝它
    （外部评审 P1-1）。

    在加那条约束之前，这个 helper 会默默造出非法卡，
    于是两条测试一直在用不合法的样本跑 —— 它们通过，只是因为没人拦。
    """
    did = kw.get("decision_id", TID)
    # contract-exempt: 同上
    base = dict(
        decision_id=did, status="WAIT", headline="核心矛盾一句话",
        verdicts=[make_verdict(task_id=did)], synthesis="",
        model_ref="anthropic/claude-sonnet-5", elapsed_ms=41000,
    )
    base.update(kw)
    return DecisionCard(**base)


class TestSchema:
    def test_init幂等(self, db):
        # 不硬编码版本号：写死数字会让每次迁移都变成「机械改数字」，
        # 而不是「确认迁移做对了」。
        assert init_schema(db) == init_schema(db) == SCHEMA_VERSION

    def test_三张表都在(self, db):
        with connect(db, readonly=True) as c:
            names = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"decision_records", "agent_runs", "raw_market_snapshot"} <= names

    def test_token列已在v2删除(self, db):
        """它们从建表起就没有生产方；留着假装有，比没有更糟。"""
        with connect(db, readonly=True) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(agent_runs)")}
        assert "tokens_in" not in cols and "tokens_out" not in cols

    def test_WAL已开启(self, db):
        with connect(db) as c:
            assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


class TestAppendOnly:
    """L-8：状态被原地 UPDATE 之后，「当时看到的是什么」永久不可重建。"""

    @pytest.mark.parametrize(
        "table,sql",
        [
            ("decision_records", "UPDATE decision_records SET status='BUY'"),
            ("agent_runs", "UPDATE agent_runs SET verdict='PASS'"),
            ("raw_market_snapshot", "UPDATE raw_market_snapshot SET source='x'"),
        ],
    )
    def test_UPDATE被数据库拒绝(self, db, table, sql):
        save_card(make_card(), path=db)
        record_agent_run(task_id=TID, agent="emotion", status="completed",
                         started_at="t0", finished_at="t1", elapsed_ms=1, path=db)
        save_raw_snapshot(source="em:api", as_of="a", retrieved_at="b",
                          payload={"k": 1}, path=db)
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(db) as c:
                c.execute(sql)

    def test_DELETE被数据库拒绝(self, db):
        save_raw_snapshot(source="em:api", as_of="a", retrieved_at="b",
                          payload={"k": 1}, path=db)
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(db) as c:
                c.execute("DELETE FROM raw_market_snapshot")

    def test_每张表都有只追加触发器(self, db):
        """🔴 判据取自**数据库里实际有哪些表**，不是手写清单。

        v4 加 `decision_ids` 时漏了触发器，上面那份 parametrize 清单
        当然也不会提到它 —— 手写清单只覆盖「你想到过的」，
        而漏掉的恰恰是没想到的那张。外部评审 F1 就是这么找到的。

        ⇒ 反过来：**新表默认就该受保护，例外必须在 EXEMPT 里自己举手。**
        """
        # 版本号走 `PRAGMA user_version`，不占表 ⇒ 目前只有 SQLite 自己的表豁免
        EXEMPT = {"sqlite_sequence"}
        with connect(db, readonly=True) as c:
            tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")} - EXEMPT
            guarded = {r[0].rsplit("_no_", 1)[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert tables, "一张表都没扫到，这个测试等于没测"
        assert tables <= guarded, f"这些表可被改写：{sorted(tables - guarded)}"

    def test_决策编号发出去不能收回(self, db):
        """F1：号被 DELETE 之后会被重新分配，两次运行共用一个身份。

        为什么这比「少了个触发器」严重：FIX-01 / FIX-02 校验的都是
        「这些判定的 task_id 是不是同一个」。号回收之后两次运行**真实自洽**，
        两道闸门一致放行 —— 正好是 v4 要防的那种混卡。
        """
        first = reserve_decision_id(by="run-A", path=db)
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(db) as c:
                c.execute("DELETE FROM decision_ids WHERE decision_id=?", (first,))
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(db) as c:
                c.execute("UPDATE decision_ids SET reserved_by='伪造'")
        assert reserve_decision_id(by="run-B", path=db) != first

    def test_只读连接拒绝写入(self, db):
        with pytest.raises(Exception):
            with connect(db, readonly=True) as c:
                c.execute("INSERT INTO agent_runs "
                          "(task_id,agent,status,elapsed_ms,started_at,finished_at) "
                          "VALUES ('x','y','z',0,'a','b')")


class TestDecisionRecords:
    def test_存取往返(self, db):
        card = make_card()
        save_card(card, path=db)
        got = load_card(card.decision_id, path=db)
        assert got is not None
        assert got.to_dict() == card.to_dict()

    def test_派生列由卡对象生成(self, db):
        card = make_card(status="AVOID", headline="高位风险")
        save_card(card, path=db)
        with connect(db, readonly=True) as c:
            row = c.execute("SELECT status, headline, missing_count "
                            "FROM decision_records").fetchone()
        assert row["status"] == "AVOID"
        assert row["headline"] == "高位风险"
        assert row["missing_count"] == 0

    def test_只接受契约对象(self, db):
        with pytest.raises(TypeError, match="DecisionCard"):
            save_card({"decision_id": TID}, path=db)  # type: ignore[arg-type]

    def test_同一decision_id不许存两条在线记录(self, db):
        save_card(make_card(), path=db)
        with pytest.raises(Exception):
            save_card(make_card(), path=db)

    def test_回放追加新行且不覆盖原始(self, db):
        original = make_card(status="WAIT")
        rid = save_card(original, path=db)

        replay = make_card(status="AVOID", model_ref="anthropic/claude-opus-5")
        save_card(replay, replay_of=rid, path=db)

        # 在线那条仍然是原始结论
        assert load_card(TID, path=db).status == "WAIT"
        assert load_card(TID, record_id=rid, path=db).model_ref == "anthropic/claude-sonnet-5"
        with connect(db, readonly=True) as c:
            assert c.execute("SELECT COUNT(*) FROM decision_records").fetchone()[0] == 2

    def test_load_verdicts取回冻结证据(self, db):
        save_card(make_card(), path=db)
        vs = load_verdicts(TID, path=db)
        assert len(vs) == 1 and vs[0].agent == "emotion"
        assert vs[0].evidence[0].as_of.tzinfo is not None

    def test_不存在返回None(self, db):
        assert load_card("BIGA-20260101-999", path=db) is None


class TestDecisionIdAllocation:
    """回归：`synthesize.py` 原本硬编码 `new_task_id(1)`，当天第二次决策必撞主键。

    症状不是崩溃退出 —— Supervisor 会自己去查库推序号然后重试，
    每轮多花一百多秒。**只看「Card 出来了没有」永远发现不了。**
    """

    def test_首次分配001(self, db):
        assert next_decision_id(day="20260919").endswith("-001")

    def test_自动跳过已占用的序号(self, db):
        save_card(make_card(decision_id="BIGA-20260919-001"), path=db)
        save_card(make_card(decision_id="BIGA-20260919-002"), path=db)
        assert next_decision_id(day="20260919") == "BIGA-20260919-003"

    def test_连续两次合成不撞主键(self, db):
        """当初这条测试存在的话，那个 bug 根本进不了主干。"""
        a = next_decision_id(day="20260919")
        save_card(make_card(decision_id=a), path=db)
        b = next_decision_id(day="20260919")
        assert a != b
        save_card(make_card(decision_id=b), path=db)   # 不该抛错

    def test_回放不占用新序号(self, db):
        rid = save_card(make_card(decision_id="BIGA-20260919-001"), path=db)
        save_card(make_card(decision_id="BIGA-20260919-001", status="AVOID"),
                  replay_of=rid, path=db)
        # 回放记录用的是同一个 decision_id，不该把 002 也算成已占用
        assert next_decision_id(day="20260919") == "BIGA-20260919-002"

    def test_撞号时的报错要能自解释(self, db):
        """原来抛的是裸 sqlite3.IntegrityError，调用方只能去猜（实测它猜了很久）。"""
        save_card(make_card(decision_id="BIGA-20260919-001"), path=db)
        with pytest.raises(ValueError, match="next_decision_id"):
            save_card(make_card(decision_id="BIGA-20260919-001"), path=db)

    def test_按天隔离(self, db):
        save_card(make_card(decision_id="BIGA-20260919-001"), path=db)
        assert next_decision_id(day="20260920") == "BIGA-20260920-001"


class TestAgentRuns:
    def test_记一次执行(self, db):
        rid = record_agent_run(
            task_id=TID, agent="emotion", status="completed", verdict="PASS",
            model="anthropic/claude-sonnet-5", elapsed_ms=8400,
            started_at="2026-09-19T10:05:00+08:00",
            finished_at="2026-09-19T10:05:08+08:00", decision_id=TID, path=db)
        assert rid > 0
        rows = list_agent_runs(decision_id=TID, path=db)
        assert len(rows) == 1 and rows[0]["agent"] == "emotion"

    def test_从verdict直接记账(self, db):
        v = make_verdict(status="partial", verdict="WARNING", missing=["最高板"])
        record_verdict_run(v, started_at="a", finished_at="b", decision_id=TID, path=db)
        row = list_agent_runs(agent="emotion", path=db)[0]
        assert row["verdict"] == "WARNING"
        assert row["missing_count"] == 1
        assert row["elapsed_ms"] == v.elapsed_ms

    def test_按agent过滤(self, db):
        for a in ("emotion", "risk", "emotion"):
            record_agent_run(task_id=TID, agent=a, status="completed",
                             started_at="a", finished_at="b", elapsed_ms=1, path=db)
        assert len(list_agent_runs(agent="emotion", path=db)) == 2
        assert len(list_agent_runs(path=db)) == 3


class TestRawSnapshot:
    def test_原样落盘并取回(self, db):
        payload = {"limit_up": 42, "rows": [{"code": "600000", "pct": 10.0}]}
        sid = save_raw_snapshot(source="em:api/clist", as_of="2026-09-19T15:00:00+08:00",
                                retrieved_at="2026-09-19T15:00:03+08:00",
                                payload=payload, path=db)
        got = load_raw_snapshot(sid, path=db)
        assert got["payload"] == payload
        assert got["source"] == "em:api/clist"
        assert len(got["content_sha256"]) == 64

    def test_相同内容不去重(self, db):
        """采了两次就是两个事实，都留着 —— 去重会丢掉「这一刻也采到了」这条信息。"""
        kw = dict(source="em:api", as_of="a", retrieved_at="b", payload={"k": 1})
        a = save_raw_snapshot(**kw, path=db)
        b = save_raw_snapshot(**kw, path=db)
        assert a != b
        assert load_raw_snapshot(a, path=db)["content_sha256"] == \
               load_raw_snapshot(b, path=db)["content_sha256"]


# ══ 外部评审 P2-3：agent_runs 不是 spawn 证明 ═══════════════════
#
# 教程第 4 章曾写「`agent_runs` 是 Agent 被调用过的唯一凭证」。
# 第 7 章在端到端实测时推翻了它（「那行是我手工跑 synthesize.py 插进去的」），
# 但第 4 章与 schema.py 的注释都没跟着改 —— 两套口径并存了很久（L-3）。
#
# 真正的 spawn 证明在运行时自己的库里（`subagent_runs`），
# 那是被验证方写不到的地方。


class TestAgentRunsIsLedgerNotProof:
    def test_我们自己的代码就在写它(self):
        """判据是**有非 _store 的业务代码调用它** —— 那就说明它可被自产。"""
        import ast
        from _scan import repo_files
        callers = set()
        for f in repo_files(".py"):
            rel = str(f.relative_to(REPO))
            if rel.startswith(("skills/_store/", "tests/")):
                continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                        and n.func.id in ("record_agent_run", "record_verdict_run"):
                    callers.add(rel)
        assert callers, (
            "没有业务代码写 agent_runs 了？那这条测试的前提变了，"
            "请重新确认它到底能不能当证明")

    def test_源头注释已改正(self):
        """schema 与 db 的注释是权威处 —— 它们说错了，别处再怎么改都会漂回来。"""
        for rel in ("skills/_store/schema.py", "skills/_store/db.py"):
            src = (REPO / rel).read_text(encoding="utf-8")
            assert "subagent_runs" in src, f"{rel} 没有指向真正的 spawn 证明"

    def test_教程第4章挂了修正指针(self):
        """过程文档写完即冻结 ⇒ 不改原文，只追加「⏩ 后续变动」。"""
        t = (REPO / "docs/tutorial/04-store-layer.md").read_text(encoding="utf-8")
        assert "⏩" in t and "subagent_runs" in t


class TestF23MissingDatabase:
    """库不存在是**全新环境的正常状态**，不该是一屏 traceback。

    外部评审 F23。这条本身不严重（失败很响、退出码非零，没人会误读成成功），
    但它落在「第一次 clone 下来跑巡检」这个位置上 ——
    第一印象是一屏 `sqlite3.OperationalError`，既不说路径也不说该做什么。
    """

    def test_只读打开不存在的库给的是人话(self, tmp_path):
        from _store import StoreNotInitialised
        nope = tmp_path / "never" / "created.db"
        with pytest.raises(StoreNotInitialised) as ei:
            with connect(nope, readonly=True):
                pass
        msg = str(ei.value)
        assert str(nope) in msg, "报错必须说出是哪个路径"
        assert "biga-card" in msg, "🔴 报错要指路 —— 只说坏了等于没说"

    def test_建好但零行不算未初始化(self, db):
        """🔴 区分「文件不在」与「schema 建好但零行」——后者是正常状态，
        把它也报成错，就会有人为了消警告去塞假数据。"""
        with connect(db, readonly=True) as c:
            assert c.execute("SELECT count(*) FROM decision_records").fetchone()[0] == 0

    @pytest.mark.parametrize("tool", ["missing_ledger", "latency_report", "readback_check"])
    def test_巡检工具不吐traceback(self, tmp_path, tool):
        """判据是**有没有 traceback**，不是退出码 —— 退出码本来就非零。"""
        import os
        import subprocess

        env = {**os.environ, "BIGA_DB_PATH": str(tmp_path / "nope.db")}
        r = subprocess.run(
            [sys.executable, str(REPO / "tools" / "verify" / f"{tool}.py")],
            capture_output=True, text=True, env=env, cwd=REPO)
        assert "Traceback" not in r.stderr, r.stderr[-500:]
        assert "判不了" in r.stderr
        assert r.returncode == 2, f"判不了统一用退出码 2（与 isolation.py 一致）"
