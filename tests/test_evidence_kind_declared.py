"""证据类别的显式声明与派生血缘（裁定 16 批 3）。

批 1 加了字段、批 2 收敛了溯源口径，本批让 `kind` 真正被各 skill **声明出来**，
并给「从别的值算出来」的派生值接上 `input_evidence_ids`。

三组判据：
  1. 契约铁律：`kind="derived"` 必须说得出它从**什么**算出来
  2. `input_ids_for` 的解析与 fail-closed
  3. 各 skill 的 `add()` helper **不给 `kind` 默认值** —— 有默认值，
     「忘了想」和「想过了」就写出来一模一样
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

from _contract import (Evidence, OriginRef, evidence_origins,  # noqa: E402
                       now_cn, verdict_origins)

from datetime import timedelta  # noqa: E402

T = now_cn() - timedelta(seconds=60)
SKILLS = ("market-calc/scripts/market_calc.py", "emotion-calc/scripts/emotion_calc.py",
          "sector-calc/scripts/sector_calc.py", "technical-calc/scripts/technical_calc.py",
          "news-scan/scripts/news_scan.py")


def _ev(**kw) -> Evidence:
    """构造一条测试证据。

    ⚠️ `raw_hash` 默认给值（批 5）：规则收紧到「声明了类别就必须说得出出处」之后，
    一条裸的 `kind="observed"` 证据本身就是非法的 —— 夹具不该再造它。
    要测「没有出处」的场景就显式传 `raw_hash=None`。
    """
    return Evidence(field=kw.pop("field", "f"), source=kw.pop("source", "probe:x"),
                    value=kw.pop("value", 1), as_of=T, retrieved_at=T,
                    raw_hash=kw.pop("raw_hash", "d" * 64), **kw)


class Test派生值必须说得出出处:
    def test_两者皆无被拒(self):
        with pytest.raises(ValueError) as ei:
            _ev(kind="derived", raw_hash=None)
        assert "出自什么" in str(ei.value)

    def test_单一来源走raw_hash(self):
        assert _ev(kind="derived", raw_hash="a" * 64).kind == "derived"

    def test_多输入走input_evidence_ids(self):
        assert _ev(kind="derived", derived_from=(OriginRef("evidence", "b" * 64),)).kind == "derived"

    def test_verdict来源也算说得出出处(self):
        """🔴 裁定 16：risk 的元数据类依据「哪些 verdict 到场了」，
        不是任何一条证据的值 —— 这正是 `input_evidence_ids` 表达不了、
        批 4 换成 `OriginRef` 的原因。"""
        assert _ev(kind="derived", derived_from=verdict_origins([41, 42])).kind == "derived"

    def test_规则不是派生一律要inputs(self):
        """🔴 technical 的 `ma5` 是从**整份** K 线算的，`raw_hash` 已经完整回答了
        它的出处 —— 再要一串 evidence id 才是硬凑。两条出路都合法正是这个道理。"""
        assert _ev(kind="derived", raw_hash="a" * 64).derived_from == ()

    def test_observed也必须说得出出处(self):
        """🔴 批 5 把 observed 也纳入这条约束。
        「我观察到的」如果指不回任何一份响应，那它和「我编的」在卡面上
        长得一模一样 —— 而卡面正是人做决策的地方。"""
        with pytest.raises(ValueError):
            _ev(kind="observed", raw_hash=None)
        assert _ev(kind="observed").raw_hash                       # 有 raw_hash 就行
        from _contract import raw_origins
        assert _ev(kind="observed", raw_hash=None,                 # 跨源也行
                   derived_from=raw_origins(["a" * 64, "b" * 64])).kind == "observed"

    def test_parameter仍不受约束(self):
        """参数是我们自己的设定，本来就没有数据出处 —— 要求它指回一份响应是荒谬的。"""
        assert _ev(kind="parameter", raw_hash=None).derived_from == ()

    def test_未声明的历史证据仍不受约束(self):
        """三段式的「旧卡可读」：生产库 3152 条历史证据 kind 全是 None。"""
        assert _ev(raw_hash=None).kind is None

    def test_未声明的历史证据不受约束(self):
        assert _ev().kind is None


class Test血缘解析:
    def _chain(self):
        a = _ev(field="a", kind="observed")
        b = _ev(field="b", kind="observed")
        return [a, b]

    def test_按字段名解析成来源(self):
        ev = self._chain()
        assert evidence_origins(ev, ("a", "b"), of="c") == (
            OriginRef("evidence", ev[0].evidence_id),
            OriginRef("evidence", ev[1].evidence_id))

    def test_去重保序(self):
        ev = self._chain()
        assert evidence_origins(ev, ("a", "b", "a"), of="c") == (
            OriginRef("evidence", ev[0].evidence_id),
            OriginRef("evidence", ev[1].evidence_id))

    def test_字段不在场直接抛(self):
        """🔴 找不到就抛，不是跳过。静默跳过会产出一条**输入列表不完整**的派生证据：
        它看起来声明过血缘、实际漏了一截，比完全没声明更难发现。"""
        with pytest.raises(ValueError) as ei:
            evidence_origins(self._chain(), ("a", "zzz"), of="c")
        assert "zzz" in str(ei.value) and "还没有任何证据" in str(ei.value)

    def test_报错里给出已有字段便于排查(self):
        with pytest.raises(ValueError) as ei:
            evidence_origins(self._chain(), ("zzz",), of="c")
        assert "'a'" in str(ei.value) and "'b'" in str(ei.value)

    def test_输入必须先于使用它的派生值产出(self):
        """顺序要求是真的：`add()` 按调用顺序追加，后面的才引用得到前面的。"""
        ev = [_ev(field="a", kind="observed")]
        with pytest.raises(ValueError):
            evidence_origins(ev, ("b",), of="c")


class Test各skill都显式声明kind:
    """🔴 `add()` 不许给 `kind` 默认值。

    有默认值，「忘了想这条是什么」和「想过了，就是这个」写出来一模一样 ——
    而裁定 16 要的恰恰是**让人想一遍**。
    """

    @pytest.mark.parametrize("rel", SKILLS)
    def test_add系列helper的kind没有默认值(self, rel):
        tree = ast.parse((REPO / "skills" / rel).read_text())
        checked = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("add"):
                continue
            kinds = [a for a in node.args.kwonlyargs if a.arg == "kind"]
            if not kinds:
                continue
            checked.append(node.name)
            i = node.args.kwonlyargs.index(kinds[0])
            assert node.args.kw_defaults[i] is None, (
                f"{rel} 的 {node.name}() 给 kind 加了默认值 —— 那让「忘了想」和"
                f"「想过了」写出来一模一样，裁定 16 要的是让人想一遍。")
        assert checked, f"{rel} 里没找到带 kind 参数的 add 系列 helper"

    @pytest.mark.parametrize("rel", SKILLS)
    def test_每个add调用点都传了kind(self, rel):
        tree = ast.parse((REPO / "skills" / rel).read_text())
        bad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if not node.func.id.startswith("add"):
                continue
            if not any(k.arg == "kind" for k in node.keywords):
                bad.append(f"{rel}:{node.lineno} {node.func.id}(...)")
        assert not bad, (
            "这些调用点没声明 kind —— 不声明就没人回答得出「这个数是观察到的、"
            "算出来的、还是我们自己的设定」：\n  " + "\n  ".join(bad))


class Test四种来源都表达得出来:
    """🔴 批 4 的核心：`input_evidence_ids` 只能说「从另几条证据算出来」，
    而 risk 的元数据类依据的是「哪些 verdict 到场」、跨源聚合出自**多份** raw、
    `market_open` 依据的是交易日历这一行 —— 三种都表达不了。

    `OriginRef` 用一个字段把四种都装下，且**是结构化的不是前缀字符串**：
    前缀字符串要消费方解析形状才知道是什么，正是刚收拾干净的 L-13。
    """

    def test_verdict来源(self):
        from _contract import verdict_origins
        e = _ev(kind="derived", derived_from=verdict_origins([41, 42]))
        assert [o.kind for o in e.derived_from] == ["verdict", "verdict"]
        assert [o.ref for o in e.derived_from] == ["41", "42"]

    def test_raw来源(self):
        from _contract import raw_origins
        e = _ev(kind="derived", derived_from=raw_origins(["a" * 64, "b" * 64]))
        assert [o.kind for o in e.derived_from] == ["raw", "raw"]

    def test_raw来源自动丢掉空值(self):
        """跨源聚合时某一份没抓到 ⇒ 它的哈希是 None，不该变成一条空来源。"""
        from _contract import raw_origins
        assert raw_origins(["a" * 64, None, ""]) == \
               tuple(raw_origins(["a" * 64]))

    def test_fact来源(self):
        from _contract import fact_origin
        e = _ev(kind="derived",
                derived_from=(fact_origin("fact_trading_calendar", "20260924"),))
        assert e.derived_from[0].ref == "fact_trading_calendar/20260924"

    def test_四种可以混在一条证据里(self):
        from _contract import OriginRef
        e = _ev(kind="derived", derived_from=(
            OriginRef("evidence", "a" * 64), OriginRef("raw", "b" * 64),
            OriginRef("verdict", "41"), OriginRef("fact", "t/k")))
        assert len({o.kind for o in e.derived_from}) == 4

    @pytest.mark.parametrize("bad", ["Raw", "snapshot", "", "verdicts", None, 1])
    def test_非法的来源类别被拒(self, bad):
        """⚠️ 这条是 sabotage 补出来的：破坏 `OriginRef.kind` 的校验之后
        **一条测试都没红** —— 当时只测了「不是 OriginRef」和「ref 格式」，
        唯独没测「是 OriginRef 但 kind 是瞎写的」。"""
        from _contract import OriginRef
        with pytest.raises(ValueError):
            OriginRef(bad, "a" * 64)

    def test_来源的ref不许为空(self):
        from _contract import OriginRef
        with pytest.raises(ValueError):
            OriginRef("verdict", "   ")

    def test_不是OriginRef直接拒(self):
        with pytest.raises(ValueError) as ei:
            _ev(kind="derived", derived_from=("a" * 64,))
        assert "OriginRef" in str(ei.value)

    def test_内容哈希类的ref才校验sha256(self):
        """⚠️ verdict 是行号、fact 是定位串 —— 统一按 sha256 校验会把它们误杀。"""
        from _contract import OriginRef
        with pytest.raises(ValueError):
            _ev(kind="derived", derived_from=(OriginRef("raw", "nope"),))
        assert _ev(kind="derived", derived_from=(OriginRef("verdict", "41"),))

    def test_来源参与身份计算(self):
        """来源不同 ⇒ 是不同的证据。否则「同一个数、不同出处」会撞成一个 id。"""
        from _contract import OriginRef
        a = _ev(kind="derived", derived_from=(OriginRef("verdict", "41"),))
        b = _ev(kind="derived", derived_from=(OriginRef("verdict", "42"),))
        assert a.evidence_id != b.evidence_id


class Test再没有尚未归类的调用点:
    def test_全仓skill不再留kindNone(self):
        """🔴 批 3 留了 6 处 `kind=None`（当时 Evidence 表达不了它们的来源）。
        批 4 补上 `OriginRef` 之后**一处都不该剩** —— 留白的理由消失了，
        留白本身就该消失，否则它会退化成「懒得想」的挡箭牌。

        ⚠️ 判据走 AST 不走文本：第一版用正则扫源码行，被一句**docstring**
        里提到 `kind=None` 的话绊住了。要判的是「有没有这样的**调用**」，
        而调用是语法结构，不是字符串形状（同 L-13 的道理）。
        """
        bad = []
        for p in sorted((REPO / "skills").glob("*/scripts/*.py")):
            tree = ast.parse(p.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if not node.func.id.startswith("add"):
                    continue
                for kw in node.keywords:
                    if kw.arg == "kind" and isinstance(kw.value, ast.Constant) \
                            and kw.value.value is None:
                        bad.append(f"{p.relative_to(REPO)}:{node.lineno} {node.func.id}(...)")
        assert not bad, (
            "又出现了 kind=None 的调用点。批 4 之后 Evidence 能表达四种来源，"
            "没有哪一条该说不清自己出自什么：\n  " + "\n  ".join(bad))
