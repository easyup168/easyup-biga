"""`as_of` 的归属 —— 一个「看起来板上钉钉」的错值。

事故（2026-09-21，`BIGA-20260921-017`）
----------------------------------------
周一 12:41 出的卡，`market` 那行证据写着::

    [market] 上涨家数 = 4385   as_of 09-18 15:00   src em:push2delay/ulist.np

**4385 是当天此刻的数**，却挂着上周五收盘的时间戳。

两层原因：

1. `as_of` 只从日线的 `trade_date` 算**一次**，然后被所有证据共用。
   而涨跌家数、板块榜这两个端点**连日期字段都没有** ——
   我们能诚实声明的只有「取回它的时刻」。
2. 代码其实知道（它发了 warning），但 `card.render()` **从不渲染 warnings**，
   于是卡面上看起来板上钉钉。

⚠️ 注意这里的不对称：带日期的源（腾讯行情）会被核对、不一致就报
`date_mismatch`；**唯独没有日期的那个源反而被默认对齐** ——
而它恰恰是最可能对不上的。
"""

from __future__ import annotations

import ast
import pathlib
import sys
from datetime import timedelta

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))

from _contract import AgentVerdict, DecisionCard, Evidence, now_cn  # noqa: E402

#: 无日期字段的实时端点 → 它喂出来的字段必须走 `add_live`。
#:
#: ⚠️ 只列**字面量**字段名。f-string 拼出来的（`add_live(f"{tag}_top"`）
#:    AST 扫不到，由下面 `test_f_string注册的也要走add_live` 单独兜住。
_LIVE_FIELDS = {
    "skills/market-calc/scripts/market_calc.py": {
        "advance_count", "decline_count", "flat_count", "advance_ratio"},
    "skills/sector-calc/scripts/sector_calc.py": {
        "industry_bottom", "main_inflow_top", "main_inflow_total_yi",
        "board_counts"},
}


def _registered(path: pathlib.Path, fn: str) -> set[str]:
    """用 `fn(...)` 登记了哪些字面量字段。"""
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
        if name == fn and node.args:
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                out.add(a0.value)
    return out


class TestLiveFieldsUseLiveAsOf:
    """无日期端点来的字段，`as_of` 必须是取回时刻。"""

    def test_它们都走add_live(self):
        bad = {}
        for rel, fields in _LIVE_FIELDS.items():
            live = _registered(REPO / rel, "add_live")
            missed = fields - live
            if missed:
                bad[rel] = sorted(missed)
        assert not bad, (
            f"这些字段来自**无日期**端点，却用了日线推断的 as_of：{bad}\n"
            "  后果：一个今天的数挂着上一个交易日的时间戳，\n"
            "        而卡面上看起来板上钉钉（BIGA-20260921-017 真出过）。\n"
            "  怎么办：改用 add_live()，as_of = 取回时刻。")

    def test_f_string注册的也要走add_live(self):
        """`sector` 的 `{tag}_top` / `{tag}_advance_ratio` 用 f-string 拼名字，
        AST 扫不到字面量 ⇒ 改成断言**源码里没有残留的 `add(f"`**。

        这对 `sector` 成立，因为它所有 f-string 注册的字段都来自板块榜
        （无日期端点）。`market` 不成立 —— 它的 `{key}_close` 来自日线，
        所以这条只管 `sector`。
        """
        src = (REPO / "skills/sector-calc/scripts/sector_calc.py").read_text(
            encoding="utf-8")
        assert 'add(f"' not in src, (
            "sector 里还有用 add(f\"…\") 注册的字段 —— 它们全部来自板块榜"
            "（无日期端点），必须走 add_live")

    def test_日线派生的字段不要误用add_live(self):
        """反向：真正来自日线的字段，as_of 就该是那根 K 的收盘时刻。"""
        live = _registered(REPO / "skills/market-calc/scripts/market_calc.py",
                           "add_live")
        for f in ("trade_date", "sh_close", "sh_pct"):
            assert f not in live, f"{f} 来自日线，as_of 不该是取回时刻"


class TestCardRendersWarnings:
    """warning 与 missing 的分工是「能用但要注意」vs「没有」。

    把前者藏起来，等于只保留了它的名字。
    """

    @staticmethod
    def _card(warnings: list[str]) -> DecisionCard:
        t = now_cn()
        v = AgentVerdict(
            agent="market", task_id="BIGA-20260921-001", status="completed",
            verdict="PASS", stance="分化",
            result={"trade_date": "2026-09-18"},
            evidence=[Evidence(field="trade_date", value="2026-09-18",
                               source="probe", as_of=t, retrieved_at=t)],
            warnings=warnings)
        return DecisionCard(decision_id="BIGA-20260921-001", status="WAIT",
                            headline="h", verdicts=[v], synthesis="s",
                            model_ref="m")

    def test_warning_出现在卡面上(self):
        text = self._card(["涨跌家数接口不返回交易日字段"]).render()
        assert "提请注意" in text
        assert "涨跌家数接口不返回交易日字段" in text

    def test_没有warning时不占版面(self):
        assert "提请注意" not in self._card([]).render()


class TestMixedAsOfIsVisible:
    """同一个 Agent 的证据可以横跨两个时刻 —— 那时它必须看得出来。"""

    def test_两个as_of并存且都能渲染(self):
        now = now_cn()
        old = now - timedelta(days=3)
        v = AgentVerdict(
            agent="market", task_id="BIGA-20260921-001", status="completed",
            verdict="PASS", stance="分化",
            result={"sh_close": 3911.87, "advance_count": 4385},
            evidence=[
                Evidence(field="sh_close", value=3911.87, source="sina:kline",
                         as_of=old, retrieved_at=now, label="上证收盘"),
                Evidence(field="advance_count", value=4385, source="em:ulist",
                         as_of=now, retrieved_at=now, label="上涨家数"),
            ])
        text = DecisionCard(decision_id="BIGA-20260921-001", status="WAIT",
                            headline="h", verdicts=[v], synthesis="s",
                            model_ref="m").render()
        assert old.strftime("%m-%d %H:%M") in text
        assert now.strftime("%m-%d %H:%M") in text, \
            "两个 as_of 必须都出现在卡上 —— 否则读的人以为它们是同一时刻"


# ══ 外部评审 P1-1：Decision Identity ═════════════════════════════
#
# 评审指出：`synthesize.py` 有「所有 verdict 的 task_id 必须一致」的检查，
# 但**契约层没有** ⇒ 可以构造「卡 001 装着 999 的 verdict」并落库（实测通过）。
#
# 守卫在编排层就只守得住走编排层的那条路。
#
# ⚠️ 评审建议的验收是「Replay 也无法绕过」，但实测已落库 31 张卡里有 20 张
#    的 verdict 写着别的号（Stage 0 占号是后来才加的）。一刀切会让它们永远读不出来。
#    ⇒ 改成三段式：新卡拒绝 / 旧卡可读但显示 / **落库永远拒绝**。
#    这与 stance 检查的取舍同源：能不能重建「当时看到的东西」优先于形式一致。


class TestDecisionIdentity:
    @staticmethod
    def _v(agent: str, task_id: str):
        t = now_cn()
        return AgentVerdict(
            agent=agent, task_id=task_id, status="completed", verdict="PASS",
            stance="分化", result={"trade_date": "2026-09-18"},
            evidence=[Evidence(field="trade_date", value="2026-09-18",
                               source="probe", as_of=t, retrieved_at=t)])

    def _card(self, did: str, tids: list[str], **kw):
        return DecisionCard(
            decision_id=did, status="WAIT", headline="h",
            verdicts=[self._v(f"a{i}", t) for i, t in enumerate(tids)],
            synthesis="s", model_ref="m", **kw)

    def test_新造的卡装着别人的判定要被拒(self):
        import pytest
        with pytest.raises(ValueError, match="不属于它的判定"):
            self._card("BIGA-20260921-001", ["BIGA-20260921-999"])

    def test_多个外来判定也要被拒(self):
        import pytest
        with pytest.raises(ValueError, match="不属于它的判定"):
            self._card("BIGA-20260921-001",
                       ["BIGA-20260921-998", "BIGA-20260921-999"])

    def test_一致的卡正常构造(self):
        c = self._card("BIGA-20260921-001",
                       ["BIGA-20260921-001", "BIGA-20260921-001"])
        assert c.identity_warning == ""

    def test_历史卡可读但卡面要显示(self):
        """🔴 这条是与评审建议不同的地方，理由见本节开头。"""
        c = self._card("BIGA-20260921-001", ["BIGA-20260921-999"],
                       from_store=True)
        assert c.identity_warning, "历史卡没有记下身份问题"
        assert "提请注意" in c.render() and "无法核实" in c.render()

    def test_from_dict_读历史卡不抛错(self):
        # contract-exempt: 这里就是要模拟「从库里读回来的 JSON」
        d = {"decision_id": "BIGA-20260921-001", "status": "WAIT",
             "headline": "h", "synthesis": "s", "model_ref": "m",
             "missing": [], "generated_at": "", "elapsed_ms": 0,
             "verdicts": [self._v("market", "BIGA-20260921-999").to_dict()]}
        c = DecisionCard.from_dict(d)
        assert c.identity_warning

    def test_历史卡不许再写回库(self, tmp_path):
        """读可以宽，**写必须严** —— 否则「读一张旧卡再存回去」就洗白了它。"""
        import pytest
        from _store import db
        p = tmp_path / "t.db"
        db.init_schema(p)
        c = self._card("BIGA-20260921-001", ["BIGA-20260921-999"],
                       from_store=True)
        with pytest.raises(ValueError, match="拒绝落库"):
            db.save_card(c, path=p)
