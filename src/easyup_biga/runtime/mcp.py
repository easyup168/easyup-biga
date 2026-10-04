"""MCP-over-HTTP 传输层 —— 铸 grant + 对运行时做 JSON-RPC。

设计文档 §7 定死的三条硬约束里，两条落在这一层：

  · grant 由 `biga attach --print-config` 铸出，带 TTL、绑定一个会话键，
    **不会自己回收，只会到期** ⇒ `Grant` 是 context manager，退出时删掉 `.mcp.json`。
  · 端点是 127.0.0.1 上的**临时端口** + `/mcp`（不是网关的 19789），
    token 在 `env.OPENCLAW_MCP_TOKEN` 里 —— 都从 attach 的输出读，绝不写进仓库。

🔴 **token 是敏感值**：只在内存里流转，`.mcp.json` 在 /tmp，退出即删。
   任何测试/文档都不许硬编码真实 token（audit_public.sh 会抓 64 位十六进制）。

为什么自己写 JSON-RPC 客户端而不装 SDK
--------------------------------------
只用到 `initialize` / `tools/call` / `tools/list` 三个方法，stdlib 的
`urllib` 足够；多一个依赖就多一份要在隔离环境里对齐的东西。协议是
MCP Streamable HTTP（JSON-RPC 2.0），服务端可能用 SSE 回，两种都要认。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

__all__ = [
    "Grant",
    "MCPClient",
    "MCPError",
    "MCPTransportError",
    "DEFAULT_BIGA",
]

DEFAULT_BIGA = str(pathlib.Path.home() / ".openclaw-biga" / "bin" / "biga")

#: 协议版本 —— 实测运行时（OpenClaw 2026.9.5）讲 2025-03-26。
_PROTOCOL_VERSION = "2025-03-26"


class MCPError(RuntimeError):
    """运行时**结构化地**回了一个 JSON-RPC error（有 code/message）。

    与 `MCPTransportError` 分开：这是「运行时听懂了、但拒绝/失败了」，
    调用方可以据此判断；后者是「根本没通上」。
    """

    def __init__(self, message: str, *, code: int | None = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class MCPTransportError(RuntimeError):
    """连不上 / 超时 / 回了非 JSON —— 传输层面的故障，不是运行时的业务失败。"""


@dataclass
class Grant:
    """一次 attach 铸出的 MCP grant：端点 + token + 那个临时 `.mcp.json` 的路径。

    用法::

        with Grant.mint("agent:main:orchestrator-<run_id>", ttl_ms=800_000) as g:
            client = MCPClient(g.url, g.token)
            ...
        # 退出：.mcp.json 被删（grant 本身到期自动失效）
    """

    url: str
    token: str
    config_path: pathlib.Path
    expires_at: str
    session_key: str

    @classmethod
    def mint(
        cls,
        session_key: str,
        *,
        ttl_ms: int,
        biga: str = DEFAULT_BIGA,
    ) -> "Grant":
        """`biga attach --print-config --session <key> --ttl <ms>` 铸一个 grant。

        🔴 TTL 必须 ≥ 本次运行的总预算（`BIGA_CARD_DEADLINE_SEC`），
        否则长跑到一半 grant 过期（设计文档 §7-2）。调用方负责传够。
        """
        if ttl_ms <= 0:
            raise ValueError(f"ttl_ms 必须为正毫秒数，收到 {ttl_ms}")
        proc = subprocess.run(
            [biga, "attach", "--print-config", "--session", session_key,
             "--ttl", str(ttl_ms)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise MCPTransportError(
                f"biga attach 铸 grant 失败（rc={proc.returncode}）：\n{proc.stderr.strip()}")
        info = _extract_first_json_object(proc.stdout)
        config_path = pathlib.Path(info["configPath"])
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        url = cfg["mcpServers"]["openclaw"]["url"]
        token = info["env"]["OPENCLAW_MCP_TOKEN"]
        return cls(url=url, token=token, config_path=config_path,
                   expires_at=info.get("expiresAt", ""), session_key=session_key)

    def close(self) -> None:
        """删掉临时 `.mcp.json`（连同它那个临时目录）。

        🔴 grant **不会被 attach 自己回收，只会到期** —— 不删就是每次运行都在攒的
        小泄漏（设计文档 §7-2）。删文件不撤销 grant（grant 到期才失效），
        但把承载 token 的文件从磁盘上清掉。
        """
        parent = self.config_path.parent
        # 只删 attach 自己造的临时目录，别误删别的
        if parent.name.startswith("openclaw-attach-"):
            shutil.rmtree(parent, ignore_errors=True)
        else:  # 防御：目录名不符合预期时只删那个文件
            self.config_path.unlink(missing_ok=True)

    def __enter__(self) -> "Grant":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _extract_first_json_object(text: str) -> dict[str, Any]:
    """从 attach 的输出里抠出第一个完整 JSON 对象（后面可能跟一行人话提示）。"""
    start = text.find("{")
    if start < 0:
        raise MCPTransportError(f"biga attach 没有输出 JSON：\n{text[:200]}")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise MCPTransportError(f"biga attach 的 JSON 不完整：\n{text[:200]}")


class MCPClient:
    """对一个 MCP 端点做 JSON-RPC。先 `initialize()` 握手，再 `call_tool()`。"""

    def __init__(self, url: str, token: str, *, timeout: float = 60.0):
        self.url = url
        self._token = token
        self._timeout = timeout
        self._session_id: str | None = None
        self._id = 0

    def initialize(self) -> dict[str, Any]:
        """MCP 握手：`initialize` + `notifications/initialized`。返回 server 的 initialize 结果。"""
        result = self._rpc("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "biga-runtime-adapter", "version": "0"},
        })
        self._rpc("notifications/initialized", is_notification=True)
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        return self._rpc("tools/list").get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调一个工具，返回**结构化结果**。

        MCP 工具结果可能在 `structuredContent`（带 outputSchema 时）或
        `content[].text`（纯文本，通常是一段 JSON）。两处都试，取到就解出来。
        工具报错（`isError=true`）当作 `MCPError` 抛 —— 让调用方能结构化处理。
        """
        res = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if res.get("isError"):
            raise MCPError(_text_of(res), data=res)
        if "structuredContent" in res:
            return res["structuredContent"]
        text = _text_of(res)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
        return {}

    # ── 底层 JSON-RPC ────────────────────────────────────────────────────

    def _rpc(self, method: str, params: dict | None = None, *,
             is_notification: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not is_notification:
            self._id += 1
            body["id"] = self._id
        if params is not None:
            body["params"] = params
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._token}",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode("utf-8"),
            headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self._session_id = sid
                ctype = resp.headers.get("Content-Type", "")
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise MCPTransportError(f"HTTP {e.code} from {method}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise MCPTransportError(f"{method} 连不上运行时：{e}") from e
        if is_notification:
            return {}
        obj = _parse_rpc_response(raw, ctype, self._id)
        if "error" in obj:
            err = obj["error"]
            raise MCPError(err.get("message", "unknown MCP error"),
                           code=err.get("code"), data=err.get("data"))
        return obj.get("result", {})


def _parse_rpc_response(raw: str, ctype: str, want_id: int) -> dict[str, Any]:
    if "text/event-stream" in ctype:
        for line in raw.splitlines():
            if line.startswith("data:"):
                obj = json.loads(line[5:].strip())
                if obj.get("id") == want_id or "result" in obj or "error" in obj:
                    return obj
        raise MCPTransportError(f"SSE 里没有可用的 JSON-RPC 响应：{raw[:200]}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise MCPTransportError(f"运行时回了非 JSON：{raw[:200]}") from e


def _text_of(tool_result: dict[str, Any]) -> str:
    """把 MCP 工具结果的 content[] 里的文本拼起来。"""
    parts = []
    for block in tool_result.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)
