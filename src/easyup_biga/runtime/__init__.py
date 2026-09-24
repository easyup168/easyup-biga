"""BigA 运行时适配层 —— Specialist 生命周期的唯一入口。

上层（C-II 的 DecisionOrchestrator）只跟 `OpenClawRuntimeAdapter` 打交道，
不碰运行时的 MCP 工具名、JSON-RPC、也不碰运行时的原始状态措辞。

    from easyup_biga.runtime import OpenClawRuntimeAdapter, SpawnStatus

存量调用方写的 `from _runtime import ...` 经 skills/_runtime 薄壳等价可用。
"""

from .adapter import (
    SPAWN_STATUSES,
    TERMINAL_SPAWN_STATUSES,
    OpenClawRuntimeAdapter,
    SpawnHandle,
    SpawnResult,
    SpawnStartError,
    SpawnStatus,
    normalize_status,
)
from .mcp import Grant, MCPClient, MCPError, MCPTransportError

__all__ = [
    "OpenClawRuntimeAdapter",
    "SpawnHandle",
    "SpawnResult",
    "SpawnStartError",
    "SpawnStatus",
    "SPAWN_STATUSES",
    "TERMINAL_SPAWN_STATUSES",
    "normalize_status",
    "Grant",
    "MCPClient",
    "MCPError",
    "MCPTransportError",
]
