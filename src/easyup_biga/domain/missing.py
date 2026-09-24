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

🔴 「显示」与「身份」是两件事（批 A-II · A1）
--------------------------------------------
`__str__`（继承自 `str`，值是 `detail`）负责**显示**；`__eq__`/`__hash__`
负责**身份**，而这两者不能共用同一个信号。

实测（`replay.py` 反推 `extra_missing`）：两条 verdict 各自报「数据源不可用」，
一条 code 是 `market.turnover.unavailable`，另一条是 `supervisor.agent_no_response`
—— 文本相同，说的不是同一件事。旧实现把 `__eq__`/`__hash__` 留给 `str` 默认
（按文本比较），于是 `{m for v in verdicts for m in v.missing}` 这类去重
会把它们**塌成一条**，回放据此反推出的 `extra_missing` 少了一条 ——
「回放悄悄让卡变好看」，这正是本项目最怕的静默失真。

⇒ `__eq__`/`__hash__` 改成只看 `.code`：两个 `MissingItem` 是否算「同一条」，
只问它们的机器可读代码，不问措辞。**且只在两边都是 `MissingItem` 时才生效**
（`__eq__` 对非 `MissingItem` 一律返回 `False`，不回退到 `str` 的内容比较）——
否则 `hash()` 与 `==` 会不一致（同一个哈希桶里，`==` 却按不同信号判断），
这本身就是另一种静默 bug。

⚠️ 这意味着 `MissingItem("x") == "x"`（对纯字符串）不再成立。
仓库里靠这个成立的地方只有 5 处（都在测试里，都是拿 `.missing` 与一份
纯文本列表比较），已经改成显式取 `.detail` 再比较 —— 这是 A1 要求的
「找出所有把 MissingItem 当字符串用的地方，逐个改掉」，而不是给身份判据
开一个不一致的后门。

`sorted(missing)` / `"某某" in m` / `f"{m}"` 这三类不受影响：
它们分别走 `str` 继承来的 `__lt__`（显示序，不代表身份）、
`__contains__`（子串匹配，不经过 `__eq__`）、`__str__`/`__format__`
（走 `detail`），没有一处依赖 `__eq__`/`__hash__`。

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
from dataclasses import FrozenInstanceError
from typing import Any

__all__ = ["MissingItem", "LEGACY_CODE", "CODE_RE",
           "ABSENT_REASONS", "absent_agent_code", "absent_agent_of",
           "absent_agent_missing"]

#: 历史遗留：Phase 1/2 早期落库的裸字符串缺失项。
LEGACY_CODE = "legacy.unclassified"

#: `<域>.<对象>.<原因>` —— 至少两段，全小写点分。
CODE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

#: 「某个 agent 缺席」这类缺失项的代码前缀与原因词（批 P，外部评审 §18）。
#:
#: 🔴 形状是 `supervisor.<agent>.<reason>` —— **agent 名字进代码**。
#: 在它之前所有缺席共用一个 `supervisor.agent_no_response`，于是「缺了谁」这件事
#: 只存在于给人看的那句话里。后果不是卡上少一条（实测不会少：`card_ops.synthesize`
#: 按「代码+文本」复合键去重，文本不同就都留着），而是**机器无从核对**：
#: `_check_roster` 只能比较条数，5 个 agent 缺席 + 5 条毫不相干的 missing 照样通过
#: （实测复现）。
#:
#: 为什么以前只比条数：按文本去猜「这条 missing 说的是不是那个缺席的 agent」是
#: L-13（按字符串形状分类）。**把 agent 放进代码之后，对应关系变成结构化的**，
#: 不需要猜任何文本 —— 这是本批能收紧那道检查的前提。
ABSENT_REASONS: frozenset[str] = frozenset({"agent_no_response", "agent_offline"})


def absent_agent_code(agent: str, reason: str = "agent_no_response") -> str:
    """造一条「`agent` 缺席」的缺失项代码。生产方唯一入口。"""
    if reason not in ABSENT_REASONS:
        raise ValueError(
            f"缺席原因只能是 {sorted(ABSENT_REASONS)} 之一，收到 {reason!r}")
    return f"supervisor.{agent}.{reason}"


def absent_agent_missing(agent: str, *, reason: str = "agent_no_response",
                         detail: str | None = None) -> "MissingItem":
    """造一条「`agent` 缺席」的缺失项（代码 + 给人看的话）。

    编排器与测试共用它 —— 代码怎么拼只有一份实现，`_check_roster` 的解析端
    才不会某天与生产端漂移（L-3）。
    """
    return MissingItem(detail or f"{agent} 未返回结果", absent_agent_code(agent, reason))


def absent_agent_of(code: str) -> str | None:
    """从代码里解出它说的是哪个 agent 缺席；不是这类代码就返回 None。

    🔴 判据是**结构**（第一段 `supervisor` + 第三段在 `ABSENT_REASONS` 里），
    不是文本相似度。老卡的 `supervisor.agent_offline` 只有两段、解不出 agent ⇒
    返回 None —— 那是如实的「这条代码说不出是谁」，不是错误（见
    `DecisionCard._check_roster` 的三段式）。
    """
    parts = str(code).split(".")
    if len(parts) != 3 or parts[0] != "supervisor" or parts[2] not in ABSENT_REASONS:
        return None
    return parts[1]


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

    # --- 冻结：A2 把 AgentVerdict/DecisionCard/Evidence 都做成
    # `@dataclass(frozen=True)`，但 MissingItem 是手写的 str 子类，不是
    # dataclass——`__slots__` 只声明了存储位置，从不阻止赋值。评审 F-5 抓到：
    # `m.code = "换一个"` 在 A2 之后照样成功，而 `.code` 正是 A1 刚建立的
    # 身份信号（`__eq__`/`__hash__` 都只看它）。`TestVerdictFrozen` 测的是
    # "容器不能原地追加"，从没测过"容器里的元素本身不能被改"——
    # 同一个 L-13 形状，这次的载体是元素而不是容器。
    #
    # ⇒ 覆写 `__setattr__`/`__delattr__`，统一抛
    # `dataclasses.FrozenInstanceError`——与另外三个契约对象被篡改时
    # 抛出的异常类型一致，不需要调用方分两套写法处理。
    # `__new__` 里的 `object.__setattr__` 直接调用基类实现，不经过这里，
    # 构造阶段不受影响。
    def __setattr__(self, name: str, value: Any) -> None:
        raise FrozenInstanceError(f"MissingItem 是不可变值对象，不能给 {name!r} 赋值")

    def __delattr__(self, name: str) -> None:
        raise FrozenInstanceError(f"MissingItem 是不可变值对象，不能删除 {name!r}")

    @property
    def detail(self) -> str:
        return str(self)

    @property
    def is_legacy(self) -> bool:
        return self.code == LEGACY_CODE

    # --- 身份：只看 code，不看措辞（A1） ---

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, MissingItem):
            return self.code == other.code
        # 🔴 不回退到 str 的内容比较：那会让 `==` 与 `__hash__` 依据不同信号，
        #    hash 相同的两个对象却可能因为对比对象类型不同而判定不一致。
        return False

    def __ne__(self, other: Any) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return hash(("_contract.MissingItem", self.code))

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
