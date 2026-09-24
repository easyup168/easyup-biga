"""溯源归属的唯一实现（裁定 16 批 2）。

在它之前，「这条派生证据要不要保留溯源」散在**五个 skill 里各写了一遍**，
写法各不相同。收成一份的同时改对了政策：

  · 旧规则：`source` 以 `derived:` 开头 ⇒ 一律丢弃溯源
  · 新规则：剥掉前缀后**精确或子路径命中**一个真实抓取源 ⇒ 保留

实测生产库 1721 条派生证据里 770 条属于后者 —— 它们的溯源一直都在，只是被丢掉了。

🔴 匹配方向只有一个，两条都由下面的测试钉死：
   source 比表键**更具体**（子路径）⇒ 命中；比表键**更泛** ⇒ 不命中。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills"))

from _contract import resolve_provenance, underlying_source  # noqa: E402

TBL = {
    "sina:kline/sh000001": "h_sh",
    "sina:kline/sz399106": "h_sz",
    "tencent:quote": "h_tx",
    "em:push2ex/limit_up": "h_lu",
}


class Test派生值拿回溯源:
    def test_剥掉前缀后精确命中(self):
        assert resolve_provenance("derived:sina:kline/sh000001", TBL) == "h_sh"

    def test_同一来源的直接与派生拿到同一个溯源(self):
        """🔴 这正是改动的意义：`ma20` 与 `close` 出自**同一份** raw，
        以前前者是 None、后者有值，卡面上看起来像两回事。"""
        assert resolve_provenance("derived:sina:kline/sh000001", TBL) == \
               resolve_provenance("sina:kline/sh000001", TBL)

    def test_只剥一层(self):
        """`derived:derived:x` 不是约定内的形状。一路剥到能命中为止 =
        为了凑出一个命中而放宽解析。"""
        assert resolve_provenance("derived:derived:sina:kline/sh000001", TBL) is None


class Test匹配方向只有一个:
    def test_source更具体时命中(self):
        """`tencent:quote/sh000001` 出自 `tencent:quote` 那一份响应，
        子路径只是在那份响应里定位 —— 溯源成立。
        ⚠️ 实测 market 的 turnover_sh/turnover_sz 就是这个形状，
        去掉这条它们会立刻失去 raw_hash。"""
        assert resolve_provenance("tencent:quote/sh000001", TBL) == "h_tx"

    def test_source更泛时不命中(self):
        """`sina:kline` 同时落在 sh 和 sz 上 —— 它真的跨两份快照
        （market 的 volume_total 是沪深之和），挑任何一个都是硬凑。"""
        assert resolve_provenance("sina:kline", TBL) is None
        assert resolve_provenance("derived:sina:kline", TBL) is None

    def test_取最长命中(self):
        tbl = {"a:b": "short", "a:b/c": "long"}
        assert resolve_provenance("a:b/c/d", tbl) == "long"

    def test_完全不沾边返回None(self):
        assert resolve_provenance("cls:12345", TBL) is None
        assert resolve_provenance("derived:risk-check", TBL) is None


class Test辅助函数:
    @pytest.mark.parametrize("src,want", [
        ("derived:sina:kline/sh000001", "sina:kline/sh000001"),
        ("sina:kline/sh000001", "sina:kline/sh000001"),
        ("derived:", ""),
    ])
    def test_underlying_source(self, src, want):
        assert underlying_source(src) == want

    def test_空表一律None(self):
        assert resolve_provenance("derived:sina:kline/sh000001", {}) is None


class Test只有一份实现:
    """🔴 防 L-3 复发：五份本地实现已经删掉，不许再长出第六份。"""

    def test_各skill不再自己判derived前缀(self):
        bad = []
        for p in sorted((REPO / "skills").glob("*/scripts/*.py")):
            for i, line in enumerate(p.read_text().splitlines(), 1):
                t = line.strip()
                if t.startswith("#") or "startswith(DERIVED_PREFIX)" in t:
                    continue
                if 'startswith("derived:")' in t:
                    bad.append(f"{p.relative_to(REPO)}:{i}: {t}")
        assert not bad, (
            "又有 skill 自己判 `derived:` 前缀来决定溯源了 —— 这个判断只有一份实现，\n"
            "走 `_contract.resolve_provenance`。五份各写一遍正是本批要消灭的：\n  "
            + "\n  ".join(bad))
