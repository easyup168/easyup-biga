"""BigA 快照层 —— 一次决策的数据「冻结一次、多处读」（设计文档 §6 批 D）。

用法::

    from easyup_biga.application import SnapshotCoordinator

存量调用方写的 `from _snapshot import ...` 经 skills/_snapshot 薄壳等价可用。

它是「所有 Specialist 看的是同一份数据」这句话（设计文档 §4 的 `evidence_set_id`
一行）从**无法验证**变成**可核对**的那一层。
"""

from .coordinator import MANIFEST_KIND, SnapshotCoordinator, SnapshotReadError

__all__ = ["SnapshotCoordinator", "SnapshotReadError", "MANIFEST_KIND"]
