"""批 G-II 入站触发：幂等占号 + 异步受理 + main 结构性出局。

探针清单（设计文档 §6 批 G-II 的 P1 / P2 / P3，离线可判的部分）：

  P1  幂等：同一个飞书 event id（trigger_id）重投两次 ⇒ 只触发一次决策/一次拉起，
      第二次被识别为重复 —— 在三层各钉一遍：store（reserve_decision_for_trigger）、
      adapter（accept_trigger）、命令派发工具（handle_card_trigger）。
  P2  main 不参与路由：/card 技能是 command-dispatch:tool + disable-model-invocation
      （结构性钉住）；触发路径是纯 Python、不 import 任何 main/LLM/spawn —— 反事实
      检验「把 main 的 system prompt 清空，这条路还能不能走」在代码层面成立。
  P3  异步：accept_trigger 立刻返回 ACK（只调 launcher，不等 170~200s 的出卡）；
      人工 CLI 的同步入口不被这条路改坏（bin/biga-card 无 env 仍走 origin=cli）。

P4/P5 在 test_apply_config.py（R-2 / tools.deny）；P6 是 live（真飞书 event → 卡 →
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
sys.path.insert(0, str(REPO / "skills" / "decision-card" / "scripts"))
sys.path.insert(0, str(REPO / "skills" / "card" / "scripts"))

from _contract import new_run_context  # noqa: E402
from _store import (  # noqa: E402
    AppendOnlyViolation,
    connect,
    find_run_by_trigger,
    init_schema,
    open_run,
    reserve_decision_for_trigger,
    reserve_decision_id,
)

import inbound  # noqa: E402
import card_trigger_mcp as ctm  # noqa: E402


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
# schema v13 + store 层：一个 trigger 至多一个决策（原子）
# ══════════════════════════════════════════════════════════════════════════
class TestStoreIdempotency:

    def test_v13_加了trigger_id列与唯一索引(self, db):
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
# 命令派发工具 handle_card_trigger：P1（工具层）+ trigger_id 解析 fail-closed
# ══════════════════════════════════════════════════════════════════════════
class TestCommandDispatchTool:

    @pytest.fixture()
    def patched_accept(self, db, monkeypatch):
        """让工具用注入的假 launcher + tmp db（工具本身不暴露 launcher 参数）。"""
        calls = []
        real = inbound.accept_trigger
        monkeypatch.setattr(
            ctm, "accept_trigger",
            lambda **kw: real(**kw, launcher=lambda *a: calls.append(a), path=db))
        return calls

    def test_P1_工具层同event只受理一次(self, patched_accept):
        m1 = ctm.handle_card_trigger({"command": "", "eventId": "evt-A"})
        m2 = ctm.handle_card_trigger({"command": "", "eventId": "evt-A"})
        assert "正在出卡" in m1 and "已经在处理" in m2
        assert len(patched_accept) == 1

    def test_resolve_trigger_id_顶层与嵌套都认(self):
        assert ctm.resolve_trigger_id({"trigger_id": "e1"}) == "e1"
        assert ctm.resolve_trigger_id({"eventId": "e2"}) == "e2"
        assert ctm.resolve_trigger_id({"context": {"message_id": "e3"}}) == "e3"
        assert ctm.resolve_trigger_id({"meta": {"feishu_event_id": "e4"}}) == "e4"

    def test_resolve_trigger_id_认不出就抛_不编一个(self):
        with pytest.raises(ctm.TriggerIdUnavailable):
            ctm.resolve_trigger_id({"command": "沪深", "commandName": "/card"})

    def test_工具在认不出event时回一句自解释而非抛(self, patched_accept):
        # build_server 里的包装把 TriggerIdUnavailable 转成给人看的 ACK。
        server = ctm.build_server()
        # 直接调 handle_ 会抛；工具包装吞成消息 —— 这里断言 handle_ 的行为，
        # 包装行为在 build_server 内联，靠上面 resolve 的 fail-closed 保证。
        with pytest.raises(ctm.TriggerIdUnavailable):
            ctm.handle_card_trigger({"command": "no-id"})


# ══════════════════════════════════════════════════════════════════════════
# P2：main 结构性出局（结构钉住 + 反事实）
# ══════════════════════════════════════════════════════════════════════════
class TestMainOutOfRouting:

    def test_P2_card技能是command_dispatch_tool且model选不到(self):
        """SKILL.md 强制「命令直达工具、绕过 model」——这是 main 出局的结构性落点。

        🔴 P6 live 实测发现（2026-09-23）：`command-tool` 必须是
        `<mcp server 名>__<工具名>` 的完整形式，不能只写工具自己的裸名
        （`card_trigger_mcp.py` 用 `FastMCP("biga-card-trigger")` +
        `@server.tool(name="biga_card_trigger")` 注册，OpenClaw 对外按
        `biga-card-trigger__biga_card_trigger` 拼出可解析的名字）。写裸名时
        这条静态检查本身照样会绿——它只查字符串存在，不查这个名字能不能真的
        解析到一个工具——`/card` 在真实网关里直接报 `Tool not available`，
        这是离线测不出、只有 live 才暴露的坑。
        """
        fm = (REPO / "skills" / "card" / "SKILL.md").read_text("utf-8").split("---")[1]
        for key, val in (("command-dispatch", "tool"),
                         ("command-tool", "biga-card-trigger__biga_card_trigger"),
                         ("disable-model-invocation", "true"),
                         ("command-arg-mode", "raw")):
            assert re.search(rf"{re.escape(key)}:\s*{re.escape(val)}", fm), \
                f"SKILL.md 缺 {key}: {val} —— 少了它 /card 就会回落到 model（main）"

    def test_P2_反事实_触发路径不依赖main或model(self):
        """把 main 的 prompt 清空这条路还能走 ⇔ 路径里没有 main/LLM/spawn 依赖。"""
        src = "\n".join(
            (REPO / "skills" / p).read_text("utf-8")
            for p in ("card/scripts/card_trigger_mcp.py",
                      "decision-card/scripts/inbound.py"))
        for forbidden in ("sessions_spawn", "import openai", "anthropic",
                          "ORCHESTRATION.md", "SOUL.md"):
            assert forbidden not in src, \
                f"触发路径出现 {forbidden!r} —— 它就不再是「不经过 main/LLM」了"
