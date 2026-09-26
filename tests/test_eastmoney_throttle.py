"""东财系取数点**必须限流** —— 判据打在调用点上，不在注释上。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：`providers/eastmoney*.py` 里每一个网络取数点，其所在函数是否调了
  `throttle("eastmoney")`
- **不覆盖**：限流间隔取多少（那在 `http.THROTTLE_SEC`）、多进程并发
  （进程内限流管不住，见 `throttle` 的 docstring）

🔴 为什么需要这道守卫
---------------------
`http.throttle()` 早就存在，docstring 把危害与实测证据都写清楚了：

> 东财这类端点对频率敏感 —— 实测连打十来个请求之后，
> 连**已知可用**的兄弟端点也一起返回 502，封的是来源而不是某个接口。

**然后它被接进了五个取数点里的一个。**（2026-09-26 清点：只有
`eastmoney_security_master` 调了；股池、涨跌家数、板块榜分页、
EOD 分页四处都没调 —— 而后三个恰恰是请求量最大的。）

当天的代价：`push2` / `push2delay` / `push2his` / `82.push2` 四台
**同时** 502 / RemoteDisconnected，而请求量小得多的 `push2ex` 照常 200。
`cn.market.breadth`、`cn.sector.board_snapshot` 全靠备用源顶住，
`cn.market.limit_pool` 没有备用源 ⇒ emotion 整组 UNKNOWN。

> 一个只接了 1/5 的限流器，和没有限流器的区别只是**它让人以为有**。

⚠️ 这条守卫查的是「同一个函数里有没有 throttle 调用」，
   **不保证顺序正确**（AST 层面判「在 get 之前」会把
   `for` 循环里的合法写法误判）。它挡的是「整个忘了」，不是「写错了位置」。
   这个边界要说出来 —— 否则读的人会以为它管得比实际更宽。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
PROVIDERS = REPO / "src" / "easyup_biga" / "providers"

#: 会真的发请求的函数名（`http.py` 导出的那几个）。
_FETCHERS = {"get_text", "get_json", "get_json_and_text"}

#: 东财系模块。⚠️ 用 glob 而不是手写清单 —— 手写的清单会在下一个
#: `eastmoney_*.py` 出现时静默漏掉它，而那正是本次事故的形状。
_MODULES = sorted(PROVIDERS.glob("eastmoney*.py"))


def _fetch_functions(tree: ast.AST):
    """产出 (函数名, 该函数内是否调过 throttle, 该函数内的取数次数)。"""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        fetches = 0
        throttled = False
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name in _FETCHERS:
                fetches += 1
            elif name == "throttle":
                throttled = True
        if fetches:
            yield node.name, throttled, fetches


def test_东财系模块被找到了():
    """🔴 探针的探针：清单空了它也会「全绿」。"""
    assert len(_MODULES) >= 3, [p.name for p in _MODULES]


@pytest.mark.parametrize("path", _MODULES, ids=lambda p: p.name)
def test_每个东财取数函数都限流(path: pathlib.Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    missing = [(fn, n) for fn, ok, n in _fetch_functions(tree) if not ok]
    assert not missing, (
        f"{path.name} 里这些函数会发东财请求但**没有调 throttle**：{missing}\n"
        "  🔴 风控是按来源算的：任何一处不限流，都会把**所有**东财 dataset\n"
        "     一起带下水（实测 2026-09-26：四台主机同时 502）。\n"
        "  ⇒ 在每次 get_* 之前加 `throttle(\"eastmoney\")`（换主机重试那次也要）。"
    )


def test_限流间隔与公开参考实现同量级():
    """1.0 秒不是拍的 —— 公开参考实现的东财统一入口用的就是这个量级。

    ⚠️ 这条只钉**量级**，不钉具体数字：真要调（比如批量任务调到 1.5~2）
    是正常的，但从 1.0 掉到 0.1 就不是调参而是把守卫关掉。
    """
    from easyup_biga.providers.http import THROTTLE_SEC

    assert THROTTLE_SEC.get("eastmoney", 0) >= 0.5, THROTTLE_SEC
