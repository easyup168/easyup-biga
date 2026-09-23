"""VerdictRef —— Card 记的「这条判定用的是库里哪一行」。

为什么需要它
------------
设计文档 §6 A6：Card 现在只装着 `AgentVerdict` 对象本身的一份拷贝
（合成时从 `agent_verdicts` 读出来的那份），却不记录**它是从哪一行读出来的**。
回放时想证明「这张卡当初用的判定原件，和库里现在这一行还是同一份」，
无从查起——对象的内容相同不代表可以拿去核对，因为 `AgentVerdict` 本身
不携带它自己的 `verdict_id`。

⇒ Card 额外记一份 `VerdictRef(agent, verdict_id, content_sha256,
contract_version)`。核对时不是「把 `AgentVerdict` 重新序列化再比对」
（那条路径已经被 `tests/test_write_boundary.py::
TestVerdictContentShaIsHashOfStoredText` 钉死为**错误**答案——
`_canonical_dumps` 的格式不是冻结的，A-I 就改过一次分隔符），
而是直接比对 `agent_verdicts.content_sha256` 这一列**当时写入时算好的值**。

⚠️ 与 stderr 上 `verdict_ref=NN` 那个约定不是一回事
----------------------------------------------------
`amend_verdict.py` / 各 skill 在 stderr 上打的 `verdict_ref=NN` 是一个裸
整数（`verdict_id`），历史上就这么叫。这里的 `VerdictRef` 是批 A-II 新加的
一个值对象，字段里恰好也有 `verdict_id`——两者说的是同一类东西（指向
`agent_verdicts` 某一行），但不是同一个符号，不要混着读。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = ["VerdictRef", "CONTRACT_VERSION"]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: 契约形状的版本号。A-II 改了 AgentVerdict/DecisionCard/MissingItem 的
#: 不变量与字段（frozen、confidence→data_completeness……），这是那次改动
#: 之后落下的第一个值。将来契约形状再变（不是加一个新字段，而是像这次
#: 一样动了序列化后的含义）就 +1——用途类比 `card_ops.SYNTHESIS_VERSION`：
#: 让「结论变了」与「契约形状变了」不必事后靠猜区分。
CONTRACT_VERSION = "contract/1"


@dataclass(frozen=True)
class VerdictRef:
    """指向 `agent_verdicts` 一行的引用，连同「当时它长什么样」的哈希。

    Attributes:
        agent: 与被引用那条 `AgentVerdict.agent` 一致，冗余存一份是为了
            不用每次核对都先解出 verdict_id 对应哪个 agent。
        verdict_id: `agent_verdicts.verdict_id`。
        content_sha256: 合成时 `agent_verdicts.content_sha256` 这一列的值——
            即 `hashlib.sha256(当时写入的 verdict_json 文本)`，不是
            「把 AgentVerdict 对象重新序列化再算一遍」。
        contract_version: 合成这条引用时契约层的 `CONTRACT_VERSION`。
        run_id: 🔴 批 J-I（可选、默认 None）：被引用那条原件是哪次编排执行尝试产生的，
            从 `agent_verdicts.run_id` 那一列**直接搬过来**，不重新推导。历史行没有这个
            值时为 None（capture 不 enforce，缺了不算不一致）。
    """

    agent: str
    verdict_id: int
    content_sha256: str
    contract_version: str
    run_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.agent, str) or not self.agent.strip():
            raise ValueError(f"VerdictRef.agent 必须是非空字符串，收到 {self.agent!r}")
        if not isinstance(self.verdict_id, int) or isinstance(self.verdict_id, bool) \
                or self.verdict_id <= 0:
            raise ValueError(
                f"VerdictRef.verdict_id 必须是正整数，收到 {self.verdict_id!r}")
        if not isinstance(self.content_sha256, str) or not _SHA256_RE.match(self.content_sha256):
            raise ValueError(
                f"VerdictRef.content_sha256 必须是 64 位十六进制哈希（小写），"
                f"收到 {self.content_sha256!r}")
        if not isinstance(self.contract_version, str) or not self.contract_version.strip():
            raise ValueError(
                f"VerdictRef.contract_version 必须是非空字符串，收到 {self.contract_version!r}")
        # run_id 可空（capture 不 enforce）；给了就必须是非空字符串，不接受空串冒充。
        if self.run_id is not None and (not isinstance(self.run_id, str) or not self.run_id.strip()):
            raise ValueError(
                f"VerdictRef.run_id 要么是 None，要么是非空字符串，收到 {self.run_id!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "verdict_id": self.verdict_id,
            "content_sha256": self.content_sha256,
            "contract_version": self.contract_version,
            "run_id": self.run_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VerdictRef":
        return cls(
            agent=d["agent"],
            verdict_id=d["verdict_id"],
            content_sha256=d["content_sha256"],
            contract_version=d["contract_version"],
            # 🔴 历史卡的 JSON 里没有这个键 —— `.get` 缺省 None，不是必填，
            #    否则旧卡一律读不回来（P4/P2 要防的正是这个）。
            run_id=d.get("run_id"),
        )
