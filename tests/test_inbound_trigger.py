"""批 G-II 入站触发：幂等占号 + 异步受理 + 编排脱离 main 进程树。

探针清单（设计文档 §6 批 G-II 的 P1 / P2 / P3，离线可判的部分）：

  P1  幂等：同一个飞书 event id（trigger_id）重投两次 ⇒ 只触发一次决策/一次拉起，
      第二次被识别为重复 —— 在两层各钉一遍：store（reserve_decision_for_trigger）、
      adapter（accept_trigger）。
  P2  编排脱离 main 进程树：出卡入口可以经过 main（LLM 允许），但**编排绝不在 main 的
      进程树里跑**。`detached_biga_card_launcher` 必须用 `systemd-run --user` 把出卡拉成
      脱离进程树的瞬态单元（⇒ entry_guard 判 HUMAN），或在回退路径显式清掉
      `OPENCLAW_SERVICE_*`（entry_guard 判 AGENT 的依据）—— 这才是挡住 2026-09-21
      「会话自己拼 sessions_spawn 递归出卡」的那道。另钉：触发路径（inbound.py）里
      不出现 sessions_spawn/LLM/编排依赖。
  P3  异步：accept_trigger 立刻返回 ACK（只调 launcher，不等 170~200s 的出卡）；
      人工 CLI 的同步入口不被这条路改坏（bin/biga-card 无 env 仍走 origin=cli）。

P4/P5 在 test_apply_config.py（R-2 / tools.deny）；P6 是 live（真飞书 /card → 卡 →
投递），不在离线判据里。

🔴 这些是**新守卫的探针**：每条都能靠「把被守的东西弄坏 ⇒ 报红」验证（红灯记录见
   CHANGELOG 批 G-II 探针一节）。
"""

from __future__ import annotations

import pathlib
import re
import sys
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))
sys.path.insert(0, str(REPO / "skills" / "card" / "scripts"))

from _contract import new_run_context  # noqa: E402
from _store import (  # noqa: E402
    connect,
    find_run_by_trigger,
    init_schema,
    open_run,
    reserve_decision_for_trigger,
    reserve_decision_id,
)

import inbound  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    return p


class _RecordingLauncher:
    """假的后台拉起器：只记录调用，不真起进程（离线 P1/P3）。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, origin: str, trigger_id: str, decision_id: str) -> None:
        self.calls.append((origin, trigger_id, decision_id))


# ══════════════════════════════════════════════════════════════════════════
# schema v14 + store 层：一个 trigger 至多一个决策（原子）
# ══════════════════════════════════════════════════════════════════════════
class TestStoreIdempotency:

    def test_v14_加了trigger_id列与唯一索引(self, db):
        with connect(db, readonly=True) as c:
            cols = [r[1] for r in c.execute("PRAGMA table_info(decision_ids)")]
            idx = [r[1] for r in c.execute("PRAGMA index_list(decision_ids)")]
        assert "trigger_id" in cols
        assert "ux_decision_ids_trigger" in idx

    def test_同一trigger重复占号返回同一决策_不新建(self, db):
        did1, created1 = reserve_decision_for_trigger("evt-A", by="feishu", path=db)
        did2, created2 = reserve_decision_for_trigger("evt-A", by="feishu", path=db)
        assert created1 is True and created2 is False
        assert did1 == did2, "重投必须复用同一个决策号，不能再占一个"

    def test_不同trigger占不同号(self, db):
        did1, _ = reserve_decision_for_trigger("evt-A", path=db)
        did2, _ = reserve_decision_for_trigger("evt-B", path=db)
        assert did1 != did2

    def test_唯一索引是原子仲裁_裸插重复trigger被拒(self, db):
        """并发两次同 trigger 的第二次，靠 UNIQUE 撞掉 —— 不是靠「先查再插」。

        用 `_store.connect`（不裸 `import sqlite3` —— test_no_raw_sqlite 钉死这条）直接
        插一行重复 trigger 的号，断言数据库层当场拒绝（IntegrityError 经 connect 原样
        抛出）。这是「唯一约束是唯一可靠的并发仲裁」在 schema 层的落点。
        """
        reserve_decision_for_trigger("evt-A", path=db)
        with pytest.raises(Exception) as ei:  # noqa: PT011  —— 见下断言，锁死在 UNIQUE 上
            with connect(db) as c:
                c.execute(
                    "INSERT INTO decision_ids (decision_id, reserved_at, reserved_by,"
                    " trigger_id) VALUES ('BIGA-20260101-999','t','x','evt-A')")
        assert "unique" in str(ei.value).lower() or "trigger_id" in str(ei.value)

    def test_CLI占号不带trigger_id_不受影响(self, db):
        did = reserve_decision_id(by="cli", path=db)
        assert re.match(r"BIGA-\d{8}-\d{3}", did)

    def test_空trigger_id被拒(self, db):
        with pytest.raises(ValueError, match="trigger_id"):
            reserve_decision_for_trigger("   ", path=db)

    def test_find_run_by_trigger_open前None_open后返回头(self, db):
        did, _ = reserve_decision_for_trigger("evt-A", path=db)
        assert find_run_by_trigger("evt-A", path=db) is None
        ctx = new_run_context(origin="feishu", non_interactive=True,
                              trigger_id="evt-A", decision_id=did)
        open_run(ctx, path=db)
        hdr = find_run_by_trigger("evt-A", path=db)
        assert hdr is not None and hdr["decision_id"] == did and hdr["origin"] == "feishu"


# ══════════════════════════════════════════════════════════════════════════
# adapter accept_trigger：P1 幂等 + P3 异步
# ══════════════════════════════════════════════════════════════════════════
class TestAcceptTrigger:

    def test_P1_同trigger只拉起一次_第二次是重复(self, db):
        L = _RecordingLauncher()
        a1 = inbound.accept_trigger(origin="feishu", trigger_id="evt-A", launcher=L, path=db)
        a2 = inbound.accept_trigger(origin="feishu", trigger_id="evt-A", launcher=L, path=db)
        assert a1.accepted and not a1.duplicate
        assert a2.duplicate and not a2.accepted
        assert a1.decision_id == a2.decision_id
        assert len(L.calls) == 1, "重投绝不能第二次拉起出卡"

    def test_P3_受理快速返回_不阻塞在出卡上(self, db):
        L = _RecordingLauncher()
        t0 = time.monotonic()
        ack = inbound.accept_trigger(origin="feishu", trigger_id="evt-A", launcher=L, path=db)
        assert (time.monotonic() - t0) < 1.0, "受理必须立刻返回（异步），不等 170~200s"
        assert ack.accepted and L.calls, "受理后应已把出卡拉到后台"

    def test_不同trigger各拉起一次(self, db):
        L = _RecordingLauncher()
        inbound.accept_trigger(origin="feishu", trigger_id="evt-A", launcher=L, path=db)
        inbound.accept_trigger(origin="feishu", trigger_id="evt-B", launcher=L, path=db)
        assert len(L.calls) == 2

    def test_cli来源被拒_不走入站适配器(self, db):
        with pytest.raises(ValueError, match="cli|入站"):
            inbound.accept_trigger(origin="cli", trigger_id="x",
                                   launcher=_RecordingLauncher(), path=db)

    def test_拉起失败如实上报_不静默算成功(self, db):
        def boom(*a):
            raise RuntimeError("systemd-run 不在")
        ack = inbound.accept_trigger(origin="feishu", trigger_id="evt-A",
                                     launcher=boom, path=db)
        assert not ack.accepted and not ack.duplicate
        assert "没拉起来" in ack.message

    def test_launcher拿到origin_trigger_decision三样(self, db):
        L = _RecordingLauncher()
        ack = inbound.accept_trigger(origin="feishu", trigger_id="evt-A", launcher=L, path=db)
        assert L.calls == [("feishu", "evt-A", ack.decision_id)]


# ══════════════════════════════════════════════════════════════════════════
# P2：编排脱离 main 进程树（这是 L-14 递归的真正止血点）
# ══════════════════════════════════════════════════════════════════════════
class TestOrchestrationDetached:

    def test_P2_有systemd_run时用它脱离进程树(self, tmp_path, monkeypatch):
        """systemd-run --user ⇒ 祖先变 systemd --user、不继承网关 service env ⇒ HUMAN。

        这是「编排不在 main 进程树里跑」的首选实现。把它换成一个直接 fork（不脱树）
        就会让出卡继承网关祖先、被 entry_guard 判成 AGENT —— 那正是要防的递归入口。
        """
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["kw"] = kw
            class R:  # noqa: D401 - 假 CompletedProcess
                returncode = 0
            return R()

        monkeypatch.setattr(inbound.shutil, "which", lambda name: "/usr/bin/systemd-run")
        monkeypatch.setattr(inbound.subprocess, "run", fake_run)

        inbound.detached_biga_card_launcher("feishu", "evt-A", "BIGA-1", root=tmp_path)

        cmd = captured["cmd"]
        assert cmd[0] == "systemd-run"
        assert "--user" in cmd, "必须 --user 拉成用户级瞬态单元（脱离网关进程树）"
        assert cmd[-1] == str(tmp_path / "bin" / "biga-card")
        joined = " ".join(cmd)
        for k in ("BIGA_CARD_ORIGIN=feishu", "BIGA_CARD_TRIGGER_ID=evt-A",
                  "BIGA_CARD_DECISION_ID=BIGA-1"):
            assert k in joined, f"origin/trigger/decision 要经 --setenv 传给 biga-card：缺 {k}"

    def test_P2_回退路径显式清掉service_env(self, tmp_path, monkeypatch):
        """没有 systemd-run 时回退 setsid，但必须**清掉** OPENCLAW_SERVICE_* ——

        那三个 env 是 entry_guard 判 AGENT 的依据。不清 = 出卡继承网关的 service 身份、
        被判 AGENT、被拒（fail-closed），或更糟：绕过守卫。清掉是「声明这是一次外部
        触发、不是 agent 会话」。删掉这段清理，本测试就会抓到 service env 漏进子进程。
        """
        captured = {}

        class FakePopen:
            def __init__(self, cmd, **kw):
                captured["cmd"] = cmd
                captured["env"] = kw.get("env", {})

        monkeypatch.setattr(inbound.shutil, "which", lambda name: None)  # 无 systemd-run
        monkeypatch.setattr(inbound.subprocess, "Popen", FakePopen)
        # 模拟网关进程里带着 service 身份
        monkeypatch.setenv("OPENCLAW_SERVICE_KIND", "gateway")
        monkeypatch.setenv("OPENCLAW_SERVICE_MARKER", "x")
        monkeypatch.setenv("OPENCLAW_SYSTEMD_UNIT", "openclaw-gateway-biga.service")

        inbound.detached_biga_card_launcher("feishu", "evt-A", "BIGA-1", root=tmp_path)

        env = captured["env"]
        assert captured["cmd"][0] == "setsid"
        for leaked in ("OPENCLAW_SERVICE_KIND", "OPENCLAW_SERVICE_MARKER",
                       "OPENCLAW_SYSTEMD_UNIT"):
            assert leaked not in env, f"{leaked} 漏进了脱树子进程 —— entry_guard 会判它 AGENT"
        # origin/trigger/decision 仍要传到
        assert env["BIGA_CARD_ORIGIN"] == "feishu"
        assert env["BIGA_CARD_TRIGGER_ID"] == "evt-A"
        assert env["BIGA_CARD_DECISION_ID"] == "BIGA-1"

    def test_P2_触发路径不import_llm或编排(self):
        """出卡入口经过 main 没问题，但触发路径的**代码**里不该依赖 LLM/编排 ——

        编排在程序里、脱树跑；inbound.py 只做「占号 + 脱树拉起 + ACK」。查**真实
        import**（不是 substring —— docstring 里为解释「防的是什么」会提到 spawn 等词，
        那不算依赖）：inbound.py 不该 import 任何 LLM SDK 或编排器。
        """
        import ast
        src = (REPO / "skills" / "card" / "scripts" / "inbound.py").read_text("utf-8")
        imported: set[str] = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                imported.update(n.name.split(".")[0] for n in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("openai", "anthropic", "orchestrator"):
            assert forbidden not in imported, \
                f"inbound.py import 了 {forbidden!r} —— 触发路径不该依赖 LLM/编排器"


# ══════════════════════════════════════════════════════════════════════════
# card 技能：model-invocable + 指引 main「跑脚本、别编排」（P2 的落点之一）
# ══════════════════════════════════════════════════════════════════════════
class TestCardSkill:

    def _skill(self) -> str:
        return (REPO / "skills" / "card" / "SKILL.md").read_text("utf-8")

    def test_card技能是model_invocable_没被禁(self):
        """本版允许 main 的 LLM 发起 ⇒ 技能必须能被 model 选到（不能 disable）。"""
        fm = self._skill().split("---")[1]
        assert "disable-model-invocation" not in fm, \
            "本版靠 main 认出出卡请求来发起，不能禁 model 调用"
        assert re.search(r"user-invocable:\s*true", fm)

    def test_card技能指向inbound并明令不编排(self):
        body = self._skill()
        assert "skills/card/scripts/inbound.py" in body, "技能必须文档化跑 inbound.py"
        assert "--trigger-id" in body
        # 明令 main 不要自己编排/spawn/等
        assert "spawn" in body and ("不要 spawn" in body or "不 spawn" in body or "别" in body)
        assert "不要等" in body or "不要等它跑完" in body
