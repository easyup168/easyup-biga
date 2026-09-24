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
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills"))

from _contract import Evidence, input_ids_for, now_cn  # noqa: E402

from datetime import timedelta  # noqa: E402

T = now_cn() - timedelta(seconds=60)
SKILLS = ("market-calc/scripts/market_calc.py", "emotion-calc/scripts/emotion_calc.py",
          "sector-calc/scripts/sector_calc.py", "technical-calc/scripts/technical_calc.py",
          "news-scan/scripts/news_scan.py")


def _ev(**kw) -> Evidence:
    return Evidence(field=kw.pop("field", "f"), source=kw.pop("source", "probe:x"),
                    value=kw.pop("value", 1), as_of=T, retrieved_at=T, **kw)


class Test派生值必须说得出出处:
    def test_两者皆无被拒(self):
        with pytest.raises(ValueError) as ei:
            _ev(kind="derived")
        assert "从**什么**算出来" in str(ei.value)

    def test_单一来源走raw_hash(self):
        assert _ev(kind="derived", raw_hash="a" * 64).kind == "derived"

    def test_多输入走input_evidence_ids(self):
        assert _ev(kind="derived", input_evidence_ids=("b" * 64,)).kind == "derived"

    def test_规则不是派生一律要inputs(self):
        """🔴 technical 的 `ma5` 是从**整份** K 线算的，`raw_hash` 已经完整回答了
        它的出处 —— 再要一串 evidence id 才是硬凑。两条出路都合法正是这个道理。"""
        assert _ev(kind="derived", raw_hash="a" * 64).input_evidence_ids == ()

    def test_observed与parameter不受这条约束(self):
        assert _ev(kind="observed").raw_hash is None
        assert _ev(kind="parameter").input_evidence_ids == ()

    def test_未声明的历史证据不受约束(self):
        assert _ev().kind is None


class Test血缘解析:
    def _chain(self):
        a = _ev(field="a", kind="observed")
        b = _ev(field="b", kind="observed")
        return [a, b]

    def test_按字段名解析成id(self):
        ev = self._chain()
        ids = input_ids_for(ev, ("a", "b"), of="c")
        assert ids == (ev[0].evidence_id, ev[1].evidence_id)

    def test_去重保序(self):
        ev = self._chain()
        assert input_ids_for(ev, ("a", "b", "a"), of="c") == \
               (ev[0].evidence_id, ev[1].evidence_id)

    def test_字段不在场直接抛(self):
        """🔴 找不到就抛，不是跳过。静默跳过会产出一条**输入列表不完整**的派生证据：
        它看起来声明过血缘、实际漏了一截，比完全没声明更难发现。"""
        with pytest.raises(ValueError) as ei:
            input_ids_for(self._chain(), ("a", "zzz"), of="c")
        assert "zzz" in str(ei.value) and "还没有任何证据" in str(ei.value)

    def test_报错里给出已有字段便于排查(self):
        with pytest.raises(ValueError) as ei:
            input_ids_for(self._chain(), ("zzz",), of="c")
        assert "'a'" in str(ei.value) and "'b'" in str(ei.value)

    def test_输入必须先于使用它的派生值产出(self):
        """顺序要求是真的：`add()` 按调用顺序追加，后面的才引用得到前面的。"""
        ev = [_ev(field="a", kind="observed")]
        with pytest.raises(ValueError):
            input_ids_for(ev, ("b",), of="c")


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
