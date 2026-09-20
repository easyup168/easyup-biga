"""Decision Card 的合成、落库、渲染 —— **在线与回放共用的唯一一份实现**。

架构要求：

> 回放必须与在线路径共用同一份合成代码，不许另写一套。

原因很直接：回放的用途是「换个模型重跑，看结论会不会变」。
如果回放用的是另一份组装代码，那你看到的差异里就混进了**代码差异**，
而你本来只想看**模型差异**。这个实验就废了。

职责边界
--------
本模块做的是**组装**：把 Verdict 聚合成 Card、校验契约、落库、渲染。
**判断**（status / headline / synthesis）由 LLM 给，作为 `Judgment` 传进来。

换句话说：**代码负责「怎么拼」，模型负责「拼出什么结论」。**
共用的是前者。
"""

from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass, field as dc_field

_HERE = pathlib.Path(__file__).resolve()
_REPO = _HERE.parent.parent.parent.parent
sys.path.insert(0, str(_REPO / "skills"))

from _contract import (  # noqa: E402
    AgentVerdict,
    CardStatus,
    DecisionCard,
    MissingItem,
)
from _store import load_card, save_card  # noqa: E402

__all__ = ["Judgment", "synthesize", "persist", "SYNTHESIS_VERSION"]

#: 组装逻辑的版本。改了组装方式就要 +1，
#: 否则「回放结论变了」会分不清是模型变了还是代码变了。
SYNTHESIS_VERSION = "synth/1"


@dataclass(frozen=True)
class Judgment:
    """模型给出的判断部分。组装逻辑之外的一切都在这里。"""

    status: CardStatus
    headline: str
    synthesis: str = ""
    #: Supervisor 自己发现的缺失项（例如「risk agent 尚未上线，未经风险审查」）。
    #: 与各 Verdict 的 missing 合并后上 Card。
    extra_missing: list[MissingItem] = dc_field(default_factory=list)


def synthesize(
    *,
    decision_id: str,
    verdicts: list[AgentVerdict],
    judgment: Judgment,
    model_ref: str,
    elapsed_ms: int = 0,
    generated_at: str = "",
) -> DecisionCard:
    """把 Verdict 组装成 Card。**纯函数，不碰 IO。**

    纯函数是「在线与回放一致」的前提：
    只要输入相同，两条路径的输出必然逐字节相同，不依赖当时的库状态或网络。

    缺失项的聚合规则：各 Verdict 的 `missing` 之并集 ∪ Supervisor 自己发现的。
    去重但**保持首次出现的顺序** —— 顺序稳定，回放的 diff 才是干净的。
    """
    seen: set[str] = set()
    missing: list[MissingItem] = []
    for m in [m for v in verdicts for m in v.missing] + list(judgment.extra_missing):
        item = MissingItem.coerce(m)
        # 🔴 按「代码 + 文本」去重，不只按文本：两个 agent 报同一句话但代码不同，
        #    那是两件事（例如两个源各自不可用），合并会让统计少一条。
        key = f"{item.code}\x00{item}"
        if key not in seen:
            seen.add(key)
            missing.append(item)

    # 契约层会在这里拒绝：missing 非空却给 BUY、BLOCK 却给 BUY、缺失项没上浮……
    return DecisionCard(
        decision_id=decision_id,
        status=judgment.status,
        headline=judgment.headline,
        verdicts=verdicts,
        synthesis=judgment.synthesis,
        model_ref=f"{model_ref} ({SYNTHESIS_VERSION})",
        missing=missing,
        generated_at=generated_at,
        elapsed_ms=elapsed_ms,
    )


def persist(card: DecisionCard, *, replay_of: int | None = None) -> int:
    """落库，返回 `record_id`。在线路径 `replay_of=None`，回放路径填原始 record_id。"""
    return save_card(card, replay_of=replay_of)


def comparable(card: DecisionCard) -> dict:
    """剥掉「每次必然不同」的字段，用于比较两张 Card 是否等价。

    去掉 `generated_at` / `elapsed_ms` —— 它们描述的是**这次执行**，
    不是**这个结论**。拿它们比较会让任何两次回放都「不一致」，
    于是一致性检查就退化成永远报警，很快没人看（又一个被忽略的守卫）。
    """
    d = card.to_dict()
    d.pop("generated_at", None)
    d.pop("elapsed_ms", None)
    return d


def load_original(decision_id: str) -> DecisionCard | None:
    """取回在线路径存下的那张 Card。"""
    return load_card(decision_id)
