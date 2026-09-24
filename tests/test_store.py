"""数据层行为测试。

重点不在 CRUD 跑不跑得通，而在三件「坏掉时不会报错」的事：
  1. 只追加的表真的改不动（触发器，不是约定）
  2. 派生列不可能与 card_json 不一致（因为调用方无法单独指定它们）
  3. 回放不覆盖原始记录
"""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

from _contract import (
    STANCE_VOCAB,
    AgentVerdict,
    DecisionCard,
    Evidence,
    new_task_id,
    now_cn,
)
from _store import (
    SCHEMA_VERSION,
    AppendOnlyViolation,
    connect,
    init_schema,
    list_agent_runs,
    load_card_by_record_id,
    load_online_card,
    load_raw_snapshot,
    load_verdicts,
    next_decision_id,
    record_agent_run,
    record_verdict_run,
    reserve_decision_id,
    save_card,
    save_raw_snapshot,
    save_trading_calendar,
)

from _provenance import open_test_run, provenance_for  # noqa: E402

#: 批 O：卡构造器是纯函数、拿不到 fixture，而在线卡的血缘必须指向**真落库**的行
#: （见 tests/_provenance.py）。`db` fixture 把库路径放这儿，构造器读它。
_DB: list = []


TID = new_task_id(1, day="20260919")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    # 批 N：在线卡要带 run_id，而它必须追得到 decision_runs（TestRunIdNamespace）。
    open_test_run(p)
    _DB[:] = [p]
    return p


def make_verdict(**kw) -> AgentVerdict:
    t = now_cn()
    # contract-exempt: 构造真 dataclass 的 kwargs
    base = dict(
        task_id=TID, agent="emotion", status="completed", verdict="PASS",
        result={"limit_up": 42}, data_completeness=0.8,
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
    # 🔴 只给 1 个 agent，另外 5 个天然缺席——F-8 之后 roster 判据按计数
    #    比较（missing 条数须不少于缺席 agent 数），5 条占位才够
    #    （本文件不测 roster）。
    # contract-exempt: 同上
    base = dict(
        decision_id=did, status="WAIT", headline="核心矛盾一句话",
        verdicts=[make_verdict(task_id=did)], synthesis="",
        model_ref="anthropic/claude-sonnet-5", elapsed_ms=41000,
        missing=[f"占位缺失项{i}——本文件不测 roster" for i in range(5)],
    )
    base.update(kw)
    # 批 N/O：在线卡必须说得清属于哪次执行、哪份切片、哪些原件（见 tests/_provenance.py）。
    #   ⚠️ 放在 base.update 之后：调用方可能改了 decision_id / verdicts，
    #      血缘要跟着那个**最终**的值走，不是默认值。
    if _DB and "input_verdict_refs" not in kw:
        rid, esid, refs = provenance_for(
            _DB[0], base["decision_id"], [v.agent for v in base["verdicts"]])
        base.setdefault("run_id", rid)
        base.setdefault("evidence_set_id", esid)
        base["input_verdict_refs"] = refs
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
            ("fact_trading_calendar", "UPDATE fact_trading_calendar SET is_open=0"),
        ],
    )
    def test_UPDATE被数据库拒绝(self, db, table, sql):
        save_card(make_card(), path=db)
        record_agent_run(task_id=TID, agent="emotion", status="completed",
                         started_at="t0", finished_at="t1", elapsed_ms=1, path=db)
        save_raw_snapshot(source="em:api", as_of="a", retrieved_at="b",
                          payload={"k": 1}, raw_text=json.dumps({"k": 1}), path=db)
        save_trading_calendar(source="szse:calendar/2026-09", as_of="a",
                              retrieved_at="b", days=[("20260901", True)], path=db)
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(db) as c:
                c.execute(sql)

    def test_DELETE被数据库拒绝(self, db):
        save_raw_snapshot(source="em:api", as_of="a", retrieved_at="b",
                          payload={"k": 1}, raw_text=json.dumps({"k": 1}), path=db)
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(db) as c:
                c.execute("DELETE FROM raw_market_snapshot")

    def test_迁移后agent_runs仍拒绝UPDATE和DELETE(self, db):
        """🔴 批 J-II：v9 的 `RENAME COLUMN run_id TO ledger_id` 之后，只追加触发器
        必须仍然真的拦得住写。

        SQLite 的 `RENAME COLUMN` 会自动改写引用该列的触发器体 —— 名字还挂在
        `sqlite_master` 里，但触发器体可能被改坏，而这**不会报错**。所以判据必须是
        「真跑一次 UPDATE 和一次 DELETE 看拒不拒」，不是「触发器名字还在不在」。
        （`db` fixture 走完整迁移到 v9，所以这里的 agent_runs 是改名之后的。）
        """
        record_agent_run(task_id=TID, agent="market", status="completed",
                         started_at="a", finished_at="b", elapsed_ms=1,
                         runtime_run_id="rt-x", path=db)
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(db) as c:
                c.execute("UPDATE agent_runs SET verdict='PASS'")
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(db) as c:
                c.execute("DELETE FROM agent_runs")

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


#: 归属「编排执行尝试」这个命名空间的列名。批 N 加 `orchestration_run_id` ——
#: 它装的就是 `decision_runs.run_id`，只是因为 `agent_runs` 里已经有一个
#: `runtime_run_id`（OpenClaw 的 spawn id）才没有直接叫 run_id。
#: 🔴 `runtime_run_id` **故意不在这里**：它是**别人的** id，追不到 decision_runs
#:    是它的正常状态，纳进来这道守卫会恒红。
_RUN_ID_COLUMNS = ("run_id", "orchestration_run_id")


def _run_id_namespace_violations(conn):
    """批 J-II 的守卫本体，抽成函数以便自证它两条子句都会红。

    返回 `(type_bad, value_bad, checked)`：
      · `type_bad`  名为 `run_id` 却不是 TEXT 的列
      · `value_bad` 名为 `run_id`、有非 NULL 值却追不到 `decision_runs.run_id` 的
      · `checked`   实际扫到、带 `run_id` 列的表（证明不是一张都没扫到的平凡通过）

    🔴 判据**可派生**：从 `sqlite_master` 现有的表推出来，不是手写清单
    （`test_roster_matches_config` 的教训：清单只加固当时想到的那几列）。
    语义不同的第四个同名几乎必然过不了这两关 —— `agent_runs.run_id` 当年是
    INTEGER，第一关就红。
    """
    canonical = {r[0] for r in conn.execute("SELECT run_id FROM decision_runs")}
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    type_bad, value_bad, checked = [], [], set()
    for t in tables:
        cols = [c for c in conn.execute(f"PRAGMA table_info({t})")
                if c[1] in _RUN_ID_COLUMNS]
        if not cols:
            continue
        checked.add(t)
        for c in cols:
            if (c[2] or "").upper() != "TEXT":
                type_bad.append(f"{t}.{c[1]} 声明为 {c[2]!r}，应为 TEXT")
        for c in cols:
            for (val,) in conn.execute(
                    f"SELECT {c[1]} FROM {t} WHERE {c[1]} IS NOT NULL"):
                if val not in canonical:
                    value_bad.append(f"{t}.{c[1]}={val!r} 追不到 decision_runs")
    return type_bad, value_bad, checked


class TestRunIdNamespace:
    """🔴 批 J-II：防止「第四个语义不同的 `run_id`」再长出来。

    §4 实测过三个互不相同的东西共用 `run_id` 这个名字（编排执行尝试 / 账本行号 /
    运行时 spawn id）。这一批收敛成一个：在 BigA 自己的库里，`run_id` 只指编排的
    一次执行尝试（`decision_runs.run_id`）。守卫见 `_run_id_namespace_violations`。
    """

    def test_当前schema里run_id列全部合规(self, db):
        with connect(db, readonly=True) as c:
            type_bad, value_bad, checked = _run_id_namespace_violations(c)
        assert type_bad == [], type_bad
        assert value_bad == [], value_bad
        # 🔴 非平凡：必须真的扫到了已知的两处 run_id 列，否则「全绿」可能是没扫到。
        # 批 N：decision_records / agent_runs 也进来了（新增的那三列）。
        assert {"decision_runs", "run_events",
                "decision_records", "agent_runs"} <= checked, (
            f"守卫没扫到已知的 run_id 列，覆盖坏了：checked={sorted(checked)}")

    def test_类型子句会红_加一个run_id_INTEGER列(self, db):
        """P5 的常驻版：造一张带 `run_id INTEGER` 的表，断言类型子句抓到它。

        （另有一次对**真实 schema** 的手工探针，见 CHANGELOG —— 这里用临时表证明
        守卫函数本身不是平凡通过，不改动已发布的迁移。）
        """
        with connect(db) as c:
            c.execute("CREATE TABLE _probe_int (run_id INTEGER, x TEXT)")
            type_bad, _, checked = _run_id_namespace_violations(c)
        assert "_probe_int" in checked
        assert any("_probe_int" in m for m in type_bad), (
            f"新加的 run_id INTEGER 列没被守卫抓到：{type_bad}")

    def test_值子句会红_run_id追不到decision_runs(self, db):
        """TEXT 但语义不对（值不是任何真实执行尝试）也要被抓到 —— 这一关正是
        「类型对了但换了个含义」的兜底，`agent_runs.run_id` 当年靠第一关就红，
        将来若有人用 TEXT 装第四个同名，靠的是这一关。"""
        with connect(db) as c:
            c.execute("CREATE TABLE _probe_txt (run_id TEXT)")
            c.execute("INSERT INTO _probe_txt VALUES ('not-a-real-run-id')")
            type_bad, value_bad, _ = _run_id_namespace_violations(c)
        assert type_bad == []                      # 类型没问题
        assert any("_probe_txt" in m for m in value_bad), (
            f"追不到 decision_runs 的 run_id 值没被抓到：{value_bad}")


    def test_orchestration_run_id也受同一套判据(self, db):
        """批 N：新列换了个名字就绕过守卫的话，J-II 收敛掉的歧义会从旁边长回来。

        探针：造一张带 `orchestration_run_id INTEGER` 的表，断言类型子句抓到它 ——
        证明这个名字真的在判据里，不是「加了列但守卫没看」。
        """
        with connect(db) as c:
            c.execute("CREATE TABLE _probe_orch (orchestration_run_id INTEGER)")
            type_bad, _, checked = _run_id_namespace_violations(c)
        assert "_probe_orch" in checked
        assert any("_probe_orch" in m for m in type_bad), type_bad

    def test_runtime_run_id不受这套判据(self, db):
        """反向：`runtime_run_id` 是 OpenClaw 的 id，追不到 decision_runs 是常态。

        把它纳进判据会让守卫恒红 —— 这条钉住「故意排除」不是「忘了加」。
        """
        with connect(db) as c:
            c.execute("INSERT INTO agent_runs (task_id, agent, status, missing_count,"
                      " elapsed_ms, started_at, finished_at, runtime_run_id)"
                      " VALUES ('BIGA-20260919-001','market','completed',0,1,"
                      "'t','t','openclaw-run-not-ours')")
            _, value_bad, _ = _run_id_namespace_violations(c)
        assert value_bad == [], (
            "runtime_run_id 被误纳进「编排执行尝试」命名空间 —— 它是别人的 id")


class TestDecisionRecords:
    def test_存取往返(self, db):
        card = make_card()
        save_card(card, path=db)
        got = load_online_card(card.decision_id, path=db)
        assert got is not None
        assert got.to_dict() == card.to_dict()

    def test_派生列由卡对象生成(self, db):
        # 满 roster + missing=[]：要验证 missing_count 的派生值真的是 0，
        # 不能靠「占位 missing」绕过——那样 missing_count 就不是 0 了。
        full_roster = [make_verdict(agent=a, task_id=TID) for a in sorted(STANCE_VOCAB)]
        card = make_card(status="AVOID", headline="高位风险",
                         verdicts=full_roster, missing=[])
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
        assert load_online_card(TID, path=db).status == "WAIT"
        assert load_card_by_record_id(rid, path=db).model_ref == "anthropic/claude-sonnet-5"
        with connect(db, readonly=True) as c:
            assert c.execute("SELECT COUNT(*) FROM decision_records").fetchone()[0] == 2

    def test_load_verdicts取回冻结证据(self, db):
        save_card(make_card(), path=db)
        vs = load_verdicts(TID, path=db)
        assert len(vs) == 1 and vs[0].agent == "emotion"
        assert vs[0].evidence[0].as_of.tzinfo is not None

    def test_不存在返回None(self, db):
        assert load_online_card("BIGA-20260101-999", path=db) is None


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

    def test_runtime_run_id落库并读回(self, db):
        """🔴 批 J-II：spawn id 落进 agent_runs.runtime_run_id，能原样取回。

        这是 J2-3 结构化 join 的全部原料 —— 落不进去，spawn 核验就没有硬绑定
        可用，只能永远走文本匹配的退回分支（静默退化，看起来一切正常）。
        """
        record_agent_run(task_id=TID, agent="market", status="completed",
                         started_at="a", finished_at="b", elapsed_ms=1,
                         runtime_run_id="rt-abc123", path=db)
        row = list_agent_runs(agent="market", path=db)[0]
        assert row["runtime_run_id"] == "rt-abc123"

    def test_runtime_run_id默认为空(self, db):
        """不传就是 NULL —— 历史行与回放路径都靠它如实表达「不知道 spawn id」。"""
        record_agent_run(task_id=TID, agent="market", status="completed",
                         started_at="a", finished_at="b", elapsed_ms=1, path=db)
        assert list_agent_runs(agent="market", path=db)[0]["runtime_run_id"] is None

    def test_从verdict记账也能带runtime_run_id(self, db):
        v = make_verdict()
        record_verdict_run(v, started_at="a", finished_at="b", decision_id=TID,
                           runtime_run_id="rt-fromverdict", path=db)
        assert list_agent_runs(agent="emotion", path=db)[0]["runtime_run_id"] \
            == "rt-fromverdict"


class TestRawSnapshot:
    def test_原样落盘并取回(self, db):
        payload = {"limit_up": 42, "rows": [{"code": "600000", "pct": 10.0}]}
        sid = save_raw_snapshot(source="em:api/clist", as_of="2026-09-19T15:00:00+08:00",
                                retrieved_at="2026-09-19T15:00:03+08:00",
                                payload=payload, raw_text=json.dumps(payload), path=db)
        got = load_raw_snapshot(sid, path=db)
        assert got["payload"] == payload
        assert got["source"] == "em:api/clist"
        assert len(got["content_sha256"]) == 64

    def test_相同内容不去重(self, db):
        """采了两次就是两个事实，都留着 —— 去重会丢掉「这一刻也采到了」这条信息。"""
        kw = dict(source="em:api", as_of="a", retrieved_at="b", payload={"k": 1},
                  raw_text=json.dumps({"k": 1}))
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
            # 🔴 批 H-I：store 的真实实现已迁至 src/easyup_biga/persistence/，而
            #    db.py 内部 record_verdict_run → record_agent_run 是**store 自调**，
            #    不算「业务代码写它」。排除新旧两处（旧路径现为薄壳、无调用）——
            #    只排旧路径会把 store 自调当成业务调用，守卫的语义就漂了。
            if rel.startswith(("skills/_store/", "src/easyup_biga/persistence/", "tests/")):
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
        # 🔴 批 H-I：真实源已迁至 src/easyup_biga/persistence/；旧路径现为薄壳，
        #    薄壳里没有这条注释，读旧路径这条断言会误红。
        for rel in ("src/easyup_biga/persistence/schema.py",
                    "src/easyup_biga/persistence/db.py"):
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
