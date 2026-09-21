"""同一个事实只能有一个生产方（裁定 15）—— 全仓静态扫描。

为什么需要一个机器检查
----------------------
上游文档把「上涨/下跌家数」同时派给了 market 与 emotion。本项目只让 market 算，
因为那个端点是**不带日期的实时快照**：两个 agent 并行各调一次，
同一张 Card 上就会出现同一个字段两个值，而两个都带着推断出来的 `as_of`。

🔴 它的失败方式是静默的：两个数都「看起来合理」，没有任何异常。
靠人记得这条规则是不够的 —— 下一个 skill（sector / technical / news）
和 market、emotion 都有重叠面，同样的事会再发生一次。

🔴 一个例外：溯源字段
---------------------
`trade_date` **故意**由每个 skill 各自产出，这不违反裁定 15。

它不是「同一个事实的两份实现」，而是**各自的元数据**：
「我这些数字描述的是哪一天」。两个 skill 的来源根本不同 ——
emotion 取自股池的 `qdate`，market 取自日线每行的 `day`。

⇒ 它们**不一致本身就是一条重要信息**（某个源陈旧了）。
   强行只留一份，等于把这个信号抹掉，换来的只是形式上的整洁。
⇒ 所以这里不但不禁止，反而**要求每个 skill 都有它** ——
   一个不自报日期的 agent，它的证据没法核。
   跨 agent 的一致性核对是 Supervisor 在 Stage 3 的职责。

扫描范围与已知局限
------------------
扫的是各 skill 里 ``add("<field>", …)`` / ``add_live("<field>", …)``
与 ``Evidence(field="<field>", …)`` 的**字面量**字段名。

⚠️ **每加一个登记入口，都要同步这个列表** —— 否则那批字段会静默地
退出守卫的视野（`add_live` 加进来时就发生过，好在有另一条断言兜住）。

⚠️ 用 f-string 拼出来的字段名（`market-calc` 的 ``f"{key}_close"``）扫不到。
那类字段天然带市场前缀（`sh_` / `sz_`），跨 skill 撞名的可能性极低；
而「加了字段忘了改常数」由各 skill 自己的 `confidence` 钉死测试负责。
**把局限写出来，比假装覆盖全了更有用。**
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILLS = REPO / "skills"

#: 溯源字段 —— 见模块 docstring：要求人人都有，而不是只许一个有。
_PROVENANCE_FIELDS = {"trade_date"}


def _skill_scripts() -> list[tuple[str, pathlib.Path]]:
    out = []
    for skill_dir in sorted(SKILLS.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith("_"):
            continue
        for py in sorted((skill_dir / "scripts").glob("*.py")):
            out.append((skill_dir.name, py))
    return out


def _literal_fields(path: pathlib.Path) -> set[str]:
    """抽出这个文件里以字面量形式产出的字段名。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
        # add("<field>", …) / add_live("<field>", …)
        #
        # 🔴 `add_live` 是 2026-09-21 加的第二个登记函数（实时快照类证据，
        #    as_of = 取回时刻）。加它的时候这个扫描器只认 `add` ——
        #    **于是九个字段一瞬间从守卫的视野里消失了。**
        #    测试当场红了，因为另有一条断言「涨跌家数归 market」。
        #
        #    ⇒ 每加一个登记入口，都要同步这里。
        #      这正是「扫字面量」这种做法的固有代价，写下来免得下次忘。
        if name in ("add", "add_live") and node.args:
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                found.add(a0.value)
        # Evidence(field="<field>", …)
        if name == "Evidence":
            for kw in node.keywords:
                if kw.arg == "field" and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    found.add(kw.value.value)
    return found


def producers() -> dict[str, set[str]]:
    """``{field: {skill, …}}``"""
    out: dict[str, set[str]] = {}
    for skill, py in _skill_scripts():
        for field in _literal_fields(py):
            out.setdefault(field, set()).add(skill)
    return out


def test_扫描真的看到了字段():
    """防空转：扫描器本身坏掉时会「零冲突」通过，那是最糟的绿。"""
    p = producers()
    assert len(p) >= 10, f"只扫到 {len(p)} 个字段，扫描器多半坏了"
    assert "limit_up_count" in p and "advance_count" in p


@pytest.mark.parametrize("field,skills", sorted(
    (f, tuple(sorted(s))) for f, s in producers().items()
    if f not in _PROVENANCE_FIELDS))
def test_每个事实字段只有一个生产方(field, skills):
    assert len(skills) == 1, (
        f"字段 {field!r} 同时由 {list(skills)} 产出 —— 裁定 15：同一个事实只能有一个"
        "生产方。两个 agent 并行各算一遍不会报错，只会某天悄悄给出两个数。"
        f"（若它其实是溯源元数据，加进 _PROVENANCE_FIELDS 并写明理由）")


@pytest.mark.parametrize("field", sorted(_PROVENANCE_FIELDS))
def test_每个skill都自报溯源字段(field):
    """反过来的要求：不自报日期的 agent，它的证据没法核。"""
    skills = {s for s, _ in _skill_scripts()}
    # decision-card 是组装方，不产出事实字段
    producing = {s for s, py in _skill_scripts() if field in _literal_fields(py)}
    expected = {s for s in skills if s.endswith("-calc")}
    assert producing >= expected, \
        f"{sorted(expected - producing)} 没有产出 {field!r}"


def test_涨跌家数归market():
    """把裁定 15 的首次适用钉死，而不只是靠上面的通用规则。"""
    p = producers()
    for field in ("advance_count", "decline_count", "flat_count"):
        assert p.get(field) == {"market-calc"}, \
            f"{field} 的生产方应当只有 market-calc，实际 {p.get(field)}"


class TestDeclaredDuplicates:
    """🔴 裁定 15 的受控例外：允许重复，**前提是有人核对**。

    2.3 加 `technical` 时引入了一个漏网的重复：`market.sh_close` 与
    `technical.close` 是同一个事实（上证收盘价），而单一生产方守卫
    **比的是字段名**，所以完全没看见。

    修法不是删掉其中一个 —— 两个 agent 从同一个源独立取值，
    不一致正说明它们看到的不是同一份数据。⇒ 声明它，并要求 risk 核对。
    """

    def test_声明的重复必须有人核对(self):
        from _contract import CROSS_CHECK_PAIRS
        src = (REPO / "skills/risk-check/scripts/risk_check.py").read_text(encoding="utf-8")
        assert "CROSS_CHECK_PAIRS" in src, \
            "声明了重复事实却没人核对，就退化成裁定 15 要防的那种情况"
        assert "cross_check_conflict" in src
        assert CROSS_CHECK_PAIRS, "表为空说明这条机制没有被用上"

    def test_声明里的字段确实都存在(self):
        """防止表项写错字段名 —— 那样核对会永远静默跳过。"""
        from _contract import CROSS_CHECK_PAIRS
        prod = producers()
        for a, fa, b, fb, _ in CROSS_CHECK_PAIRS:
            assert fa in prod or fb in prod, f"{fa}/{fb} 都不存在，表项写错了"

    def test_未声明的重复仍然要红(self):
        """例外只对**声明过的**生效，不是把规则废掉。"""
        from _contract import CROSS_CHECK_PAIRS
        declared = {f for p in CROSS_CHECK_PAIRS for f in (p[1], p[3])}
        for field, skills in producers().items():
            if field in _PROVENANCE_FIELDS or field in declared:
                continue
            assert len(skills) == 1, f"{field} 有多个生产方且未声明"
