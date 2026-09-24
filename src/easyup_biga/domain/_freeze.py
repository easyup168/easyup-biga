"""递归冻结 / 解冻 —— 契约对象「构造后不可变」的唯一实现（批 R，评审 E-17）。

为什么需要它
------------
`AgentVerdict` / `FactBundle` 早就做了两件事：`@dataclass(frozen=True)` 挡住
重新赋值，`MappingProxyType(dict(self.result))` 挡住往顶层加删键。两道都对，
但**只盖住了第一层**：

    inner = [1, 2]
    fb = FactBundle(..., result={"k": inner}, evidence=(Evidence(value=inner),))
    inner.append(777)
    fb.result["k"]          # → [1, 2, 777]   ← 已校验过的对象变了

`Evidence.value` 更彻底 —— 它连第一层都没包，外部还能往里**加新键**。

🔴 要害不是「值变了」，是**校验发生在 `__post_init__`、变化发生在那之后** ——
落库的那个对象与被校验的那个对象不是同一份内容。铁律是构造时拒绝的，
构造完再改就绕过了全部铁律。

🔴 还有更隐蔽的一层：`result[k]` 与对应那条 `evidence.value` 在生产里**经常是
同一个对象**（上面的例子就是）。批 P 加的「证据值必须与 result 对得上」
（`_same_value`）因此可以被一次原地 mutate **同时满足** —— 两边一起变，
永远相等。一道交叉校验被「两个指针指同一处」架空，是最难看出来的那种绿。

实测（2026-09-24，生产库 3152 条证据）：**464 条（14.7%）的 value 是 dict/list**，
且这 464 条同时也是 `result` 的顶层值。不是理论风险。

口径
----
`deep_freeze` 只改容器，不碰标量；`thaw` 是它的逆，用在**序列化边界**上
（`to_dict`）—— `json.dumps` 不认 `MappingProxyType`，不解冻会在落库那一刻才炸。

⚠️ `thaw` 一律把 tuple 还原成 `list`：JSON 里没有 tuple，冻结前是 list 还是
tuple 都序列化成数组。所以 `to_dict` 对「冻结前」与「冻结后」输出逐字节相同，
回放比对不受影响 —— 这是本改动能安全落地的前提。
"""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

__all__ = ["deep_freeze", "thaw"]


def deep_freeze(x: Any) -> Any:
    """递归冻结：dict→只读映射，list/tuple→tuple，set→frozenset，标量原样返回。

    ⚠️ dict 先**复制**再包 —— `MappingProxyType(d)` 是视图不是副本，调用方手上
    那个 `d` 之后被改了会跟着变，那只是把同一个洞换了个入口。
    """
    if isinstance(x, Mapping):
        return MappingProxyType({k: deep_freeze(v) for k, v in x.items()})
    if isinstance(x, (list, tuple)):
        return tuple(deep_freeze(v) for v in x)
    if isinstance(x, (set, frozenset)):
        return frozenset(deep_freeze(v) for v in x)
    return x


def thaw(x: Any) -> Any:
    """`deep_freeze` 的逆，用于序列化边界。tuple/frozenset 一律还原成 `list`。

    ⚠️ frozenset 还原后**顺序不保证** —— 但 set 本来就不是合法 JSON 值，写边界那道
    `allow_nan=False` 的严格校验之前它就该被挡住。这里还原它只是为了不把一个
    本该报错的值变成「序列化时才炸」。
    """
    if isinstance(x, Mapping):
        return {k: thaw(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set, frozenset)):
        return [thaw(v) for v in x]
    return x
