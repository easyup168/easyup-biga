"""缺失项 —— 一条机器可读的代码 + 一句给人看的话。

为什么需要代码
--------------
`missing[]` 原本是纯中文散文。上 Card 好读，但**不能分类统计**：

- Phase 2 的出口条件是「`missing[]` 在真实缺数据时非空 ≥5 次」——
  散文只能数**次数**，说不出是哪一类缺失、是不是同一个源反复挂
- Phase 4 要把 Card 状态与 T+5 结果做相关，「因为什么缺」是必须的维度
- 可达性巡检（L-7）需要知道哪条缺失项从未出现过 —— 那说明它是死分支

🔴 为什么是 `str` 的子类
------------------------
现有代码里到处是 `f"{m}"`、`"某某" in m`、`sorted(missing)`。
如果把 `missing` 换成一个普通 dataclass，这些地方会**静默改变行为**
（`in` 直接报错还算好的，`f"{m}"` 会打出 `MissingItem(code=...)` 这种东西上卡）。

做成 `str` 子类，字符串值就是给人看的那句话，代码挂在 `.code` 上：
旧代码一行不用改，新代码能拿到分类。

🔴 旧数据怎么办
---------------
Phase 1/2 早期落库的卡里，`missing` 是裸字符串。回放必须还能读它们 ——
`coerce()` 把裸字符串收成 `code="legacy.unclassified"`。
**不给它们编个像样的代码**：那等于伪造分类，而 `legacy.unclassified`
一眼就能看出是历史遗留。

代码命名约定
------------
``<域>.<对象>.<原因>``，全小写点分。例如::

    market.turnover.unavailable      数据源取不到
    market.volume.insufficient_bars  历史根数不够
    market.quote.date_mismatch       两源日期对不上
    emotion.pool.source_broken       股池被人为中断
    supervisor.agent_offline         某个 agent 尚未上线
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["MissingItem", "LEGACY_CODE", "CODE_RE"]

#: 历史遗留：Phase 1/2 早期落库的裸字符串缺失项。
LEGACY_CODE = "legacy.unclassified"

#: `<域>.<对象>.<原因>` —— 至少两段，全小写点分。
CODE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class MissingItem(str):
    """一条缺失项。字符串值 = 给人看的话；`.code` = 给机器看的分类。"""

    __slots__ = ("code",)

    def __new__(cls, detail: str, code: str = LEGACY_CODE) -> "MissingItem":
        if not isinstance(detail, str) or not detail.strip():
            raise ValueError(f"缺失项的说明不能为空，收到 {detail!r}")
        if code != LEGACY_CODE and not CODE_RE.match(code):
            raise ValueError(
                f"缺失项代码 {code!r} 不合规。要求 <域>.<对象>.<原因> 全小写点分，"
                f"例如 market.turnover.unavailable —— "
                f"代码要能被聚合，自由文本请写在说明里")
        obj = super().__new__(cls, detail)
        object.__setattr__(obj, "code", code)
        return obj

    @property
    def detail(self) -> str:
        return str(self)

    @property
    def is_legacy(self) -> bool:
        return self.code == LEGACY_CODE

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": str(self)}

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"MissingItem({str(self)!r}, code={self.code!r})"

    @classmethod
    def coerce(cls, x: Any) -> "MissingItem":
        """把任意历史形态收成 `MissingItem`。

        - `MissingItem` → 原样
        - `{"code","detail"}` → 按字段构造
        - 裸字符串（Phase 1 遗留） → `legacy.unclassified`
        """
        if isinstance(x, MissingItem):
            return x
        if isinstance(x, dict):
            return cls(x.get("detail") or x.get("text") or "", x.get("code", LEGACY_CODE))
        if isinstance(x, str):
            return cls(x)
        raise TypeError(f"缺失项只能是字符串或 {{code,detail}}，收到 {type(x).__name__}")
