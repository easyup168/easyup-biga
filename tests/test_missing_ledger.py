"""台账工具与 skill 文案的耦合 —— 这是它唯一会静默出错的地方。

`missing_ledger.py` 靠 `DRILL_MARK`（「演练：人为中断」）把注入故障从
真实缺失里剔掉。文案在各 skill 的 `--break-source` 分支里写死。

**改了 skill 的文案而没改这里，台账会把演练算成真实缺失** ——
于是出口条件 4 会凭空达标。不报错，不报警。
"""

from __future__ import annotations

import importlib.util
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]


def _ledger():
    p = REPO / "tools/verify/missing_ledger.py"
    spec = importlib.util.spec_from_file_location("ml", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ML = _ledger()


def test_演练分支构造的missing都带得上标记():
    """判据落在 `MissingItem(` 那几行，不是整份源码。

    整份源码里 `--break-source` 到处都是（argparse 的 help、docstring 的例子），
    那样测等于没测 —— 第一版就是这么写的，所以它没能发现文案有两套。
    """
    bad = []
    for f in (REPO / "skills").glob("*/scripts/*.py"):
        src = f.read_text(encoding="utf-8")
        if "--break-source" not in src:
            continue
        # 找 break_source 分支里构造的 MissingItem
        lines = src.splitlines()
        hits = [i for i, ln in enumerate(lines) if "MissingItem(" in ln]
        ok = any(ML.DRILL_MARK in "\n".join(lines[i:i + 4]) for i in hits)
        if not ok:
            bad.append(str(f.relative_to(REPO)))
    assert not bad, (
        f"这些 skill 有 --break-source，但没有一条 MissingItem 文案带 "
        f"{ML.DRILL_MARK!r}：{bad}\n"
        "  后果：台账会把演练注入的缺失算成真实缺失，\n"
        "        出口条件 4 凭空达标 —— 不报错，不报警。")


def test_弱证据前缀不为空且都带域名():
    """弱证据分类不能悄悄退化成空集 —— 那样所有缺失都成了强证据。"""
    assert ML.WEAK_PREFIXES
    assert all("." in p or p == "legacy" for p in ML.WEAK_PREFIXES)


def test_达标线没有被悄悄调低():
    assert ML.REQUIRED == 5, "出口条件写的是 ≥5，改这个数要先改设计文档"
