"""`trade_date` 这个字段名**只能**表示「这批行情属于哪个交易日」。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：各 skill 里 `trade_date` 字段的**标签**是否一致；
  news 是否误用了这个名字
- **不覆盖**：`trade_date` 的值对不对（那在各 skill 自己的测试里）、
  risk 的判断表（在 `agents/risk/AGENTS.md`）

🔴 为什么需要这道守卫
---------------------
`news` 曾经用 `trade_date` 装「最新一条快讯发生在哪天」。
7x24 是**连续事件流**，它没有「交易日」这个概念 ——
那个数和 market/technical/sector/emotion 的「这批行情属于哪个交易日」
是**两件事**，只是碰巧都长成 `YYYYMMDD`。

而 `risk` 把**所有**上游的 `trade_date` 放进同一个一致性判据：

    dates = {v.agent: v.result.get("trade_date") for v in upstream ...}
    consistent = len(set(dates.values())) <= 1

于是**每一个非交易日**（以及每天日线更新之前）都必然报
「上游报告了不同的交易日，不能当作同一天的事实一起审」——
而 news 说 09-26、行情说 09-24，**两边都是对的**。

实测代价（`BIGA-20260926-003`）：这条假冲突叠加 sector 缺席，
让 risk 按判断表给出「无法判定」，整张卡压成 `WAIT`。

> 同名不同义**不会报错**。它只会让一个判据长期报一个假结论，
> 而读的人以为那是真的。

判据的形状
----------
钉「名字 ⇒ 标签」的绑定，而不是列一份 agent 白名单：
白名单挡不住「新 agent 又用这个名字装别的东西」，而标签不一致挡得住。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILLS = REPO / "skills"

#: `trade_date` 唯一允许的标签。
_LABEL = "交易日"

#: 会往 FactBundle 里加字段的函数名。
_ADDERS = {"add", "add_live"}

#: ⚠️ 用 glob 不手写清单 —— 手写的清单会在下一个 skill 出现时静默漏掉它。
_SCRIPTS = sorted(SKILLS.glob("*/scripts/*_calc.py")) + sorted(
    SKILLS.glob("*/scripts/*_scan.py")) + sorted(SKILLS.glob("*/scripts/*_check.py"))


def _trade_date_labels(tree: ast.AST):
    """产出这个文件里每一处 `add("trade_date", 值, 标签, ...)` 的标签。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name not in _ADDERS or len(node.args) < 3:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == "trade_date"):
            continue
        label = node.args[2]
        yield (label.value if isinstance(label, ast.Constant) else f"<非字面量:{ast.dump(label)[:40]}>")


def test_扫到了足够多的skill脚本():
    """🔴 探针的探针：清单空了它也会「全绿」。"""
    assert len(_SCRIPTS) >= 5, [p.name for p in _SCRIPTS]


@pytest.mark.parametrize("path", _SCRIPTS, ids=lambda p: p.name)
def test_trade_date的标签只能是交易日(path: pathlib.Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bad = [lab for lab in _trade_date_labels(tree) if lab != _LABEL]
    assert not bad, (
        f"{path.name} 里 `trade_date` 用了别的标签：{bad}\n"
        f"  🔴 这个字段名**只能**表示「这批行情属于哪个交易日」——\n"
        f"     risk 把所有上游的 trade_date 放进同一个一致性判据，\n"
        f"     装别的东西进去会让它每个非交易日都报一个假冲突，而且不报错。\n"
        f"  ⇒ 换一个说得清自己是什么的名字（news 用的是 `newest_flash_date`）。"
    )


def test_news不产出trade_date():
    """🔴 反向那一半：上面那条只管「叫这个名字的必须是交易日」。

    news 可能哪天又被加回一个标签写对、语义却不对的 `trade_date`
    （比如把「最新一条所属日期」的标签改成「交易日」就能骗过上面那条）。
    这里直接钉死：**news 这个 agent 不产出 `trade_date`**。

    理由是它根本没有：7x24 是连续事件流。
    它要表达的那件事叫 `newest_flash_date`。
    """
    src = (SKILLS / "news-scan" / "scripts" / "news_scan.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) in _ADDERS
        and node.args and isinstance(node.args[0], ast.Constant)
    ]
    assert "trade_date" not in names, (
        "news 不该产出 `trade_date` —— 7x24 是连续事件流，它没有交易日。\n"
        "  它要表达的是「最新一条快讯发生在哪天」⇒ `newest_flash_date`。"
    )
    assert "newest_flash_date" in names, "那条事实本身不该丢"
