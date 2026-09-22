"""OpenClawRuntimeAdapter 离线测试（确定性编排批 C-I）。

🔴 这里的 fake 返回的是**真实运行时实测出来的响应形状**（spawn 的 accepted 体、
   agents_wait 的 completed 体、subagents list 的 active/tasks/recent），不是我
   凭空编的形状 —— 否则测的是自己的假货，不是产品（F3/L-13）。真实形状是怎么抓到
   的、live 三项补验的结果，见 `tools/verify/adapter_spike.py` 与 CHANGELOG。

   live 部分（真实 spawn、要花钱、要联网）不在 pytest 里：conftest 禁网，且 pytest
   不该花钱。那三项走 `adapter_spike.py` 手工跑。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

from _runtime import (  # noqa: E402
    SPAWN_STATUSES,
    OpenClawRuntimeAdapter,
    SpawnHandle,
    SpawnStartError,
    SpawnStatus,
    normalize_status,
)
from _runtime.mcp import (  # noqa: E402
    Grant,
    MCPError,
    _extract_first_json_object,
    _parse_rpc_response,
    _text_of,
)


# ── 真实响应形状（实测抓的，见 adapter_spike.py 的注释）──────────────────

def _spawn_ok(run_id: str, agent: str = "market") -> dict:
    return {
        "status": "accepted",
        "runId": run_id,
        "childSessionKey": f"agent:{agent}:subagent:{run_id}",
        "sessionKey": f"agent:{agent}:subagent:{run_id}",
        "mode": "run", "context": "isolated", "resolvedModel": "anthropic/claude-sonnet-5",
    }


def _wait_completed(run_id: str, *, status="done", result="收到。",
                    inp=123, out=5) -> dict:
    return {"completed": [{"runId": run_id, "status": status, "result": result,
                           "sessionKey": f"agent:x:subagent:{run_id}",
                           "usage": {"inputTokens": inp, "outputTokens": out}}],
            "pending": []}


def _list(active: list[dict], tasks: list[dict], recent: list[dict] | None = None) -> dict:
    return {"status": "ok", "action": "list", "active": active, "tasks": tasks,
            "recent": recent or []}


class FakeClient:
    """脚本化的 MCPClient 替身：记录调用，按工具名弹出预设响应（Exception 则抛）。"""

    def __init__(self, scripted: dict):
        self._q = {k: (v if isinstance(v, list) else [v]) for k, v in scripted.items()}
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        q = self._q.get(name)
        if not q:
            raise AssertionError(f"未预期的工具调用：{name}({arguments})")
        resp = q.pop(0) if len(q) > 1 else q[0]
        if isinstance(resp, Exception):
            raise resp
        return resp

    def args_for(self, name: str) -> dict:
        for n, a in self.calls:
            if n == name:
                return a
        raise AssertionError(f"没调用过 {name}")


# ══ P4：状态归一化 ═══════════════════════════════════════════════════════


class TestNormalize:
    @pytest.mark.parametrize("raw,expected", [
        ("accepted", SpawnStatus.RUNNING), ("queued", SpawnStatus.RUNNING),
        ("running", SpawnStatus.RUNNING),
        ("done", SpawnStatus.SUCCEEDED), ("completed", SpawnStatus.SUCCEEDED),
        ("failed", SpawnStatus.FAILED), ("forbidden", SpawnStatus.FAILED),
        ("timeout", SpawnStatus.TIMEOUT),
        ("killed", SpawnStatus.CANCELLED), ("cancelled", SpawnStatus.CANCELLED),
    ])
    def test_实测过的原始词都映射对(self, raw, expected):
        assert normalize_status(raw) == expected

    def test_大小写与空白不影响(self):
        assert normalize_status("  DONE ") == SpawnStatus.SUCCEEDED

    def test_认不出来的落UNKNOWN不落SUCCEEDED(self):
        """🔴 R-3：未知状态绝不能被当成成功。"""
        for weird in ("gremlin", "weird_新词", "", "succeeded_maybe", None):
            assert normalize_status(weird) == SpawnStatus.UNKNOWN
            assert normalize_status(weird) != SpawnStatus.SUCCEEDED

    def test_归一化结果永远在SPAWN_STATUSES里(self):
        for raw in ("accepted", "done", "killed", "xyz", None):
            assert normalize_status(raw) in SPAWN_STATUSES


# ══ start ════════════════════════════════════════════════════════════════


class TestStart:
    def test_按硬约束拼参数(self):
        c = FakeClient({"sessions_spawn": _spawn_ok("r1")})
        ad = OpenClawRuntimeAdapter(c)
        h = ad.start("market", "BIGA-20260101-001", "干活", group_id="g1",
                     run_timeout_sec=90)
        args = c.args_for("sessions_spawn")
        assert args["agentId"] == "market"
        assert args["collect"] is True          # 🔴 §7-1 硬约束
        assert args["groupId"] == "g1"          # 🔴 §7-1 硬约束
        assert args["context"] == "isolated"
        assert args["task"] == "干活"
        assert args["runTimeoutSeconds"] == 90
        assert h.run_id == "r1" and h.agent == "market" and h.group_id == "g1"
        assert h.session_key.startswith("agent:market:subagent:")

    def test_运行时结构化拒绝翻成SpawnStartError(self):
        c = FakeClient({"sessions_spawn": MCPError('{"status":"forbidden"}')})
        with pytest.raises(SpawnStartError, match="被运行时拒绝"):
            OpenClawRuntimeAdapter(c).start("nope", "BIGA-20260101-001", "x", group_id="g")

    def test_响应缺runId也当失败_不返回坏handle(self):
        c = FakeClient({"sessions_spawn": {"status": "accepted"}})  # 没有 runId
        with pytest.raises(SpawnStartError, match="没有 runId"):
            OpenClawRuntimeAdapter(c).start("market", "BIGA-20260101-001", "x", group_id="g")


# ══ wait ═════════════════════════════════════════════════════════════════


class TestWait:
    def test_单个完成_取结果与usage(self):
        c = FakeClient({"agents_wait": _wait_completed("r1")})
        ad = OpenClawRuntimeAdapter(c)
        h = SpawnHandle("r1", "market", "BIGA-20260101-001", "g", "sk")
        [res] = ad.wait([h], timeout_sec=30)
        assert res.status == SpawnStatus.SUCCEEDED
        assert res.result == "收到。"
        assert res.usage == {"input": 123, "output": 5}
        assert res.raw_status == "done"

    def test_先pending后completed(self):
        c = FakeClient({"agents_wait": [
            {"completed": [], "pending": ["r1"]},
            _wait_completed("r1"),
        ]})
        ad = OpenClawRuntimeAdapter(c)
        h = SpawnHandle("r1", "market", "BIGA-20260101-001", "g", "sk")
        [res] = ad.wait([h], timeout_sec=30)
        assert res.status == SpawnStatus.SUCCEEDED

    def test_超时未返回记TIMEOUT不是UNKNOWN(self):
        # agents_wait 一直只回 pending，wait 到期
        c = FakeClient({"agents_wait": {"completed": [], "pending": ["r1"]}})
        ad = OpenClawRuntimeAdapter(c)
        h = SpawnHandle("r1", "market", "BIGA-20260101-001", "g", "sk")
        [res] = ad.wait([h], timeout_sec=0)  # 立即到期
        assert res.status == SpawnStatus.TIMEOUT
        assert res.result is None

    def test_返回顺序与入参一致(self):
        c = FakeClient({"agents_wait": {
            "completed": [
                {"runId": "r2", "status": "done", "result": "b", "usage": {"inputTokens": 1, "outputTokens": 1}},
                {"runId": "r1", "status": "done", "result": "a", "usage": {"inputTokens": 1, "outputTokens": 1}},
            ], "pending": []}})
        ad = OpenClawRuntimeAdapter(c)
        h1 = SpawnHandle("r1", "market", "BIGA-20260101-001", "g", "sk")
        h2 = SpawnHandle("r2", "sector", "BIGA-20260101-001", "g", "sk")
        out = ad.wait([h1, h2], timeout_sec=30)
        assert [r.handle.run_id for r in out] == ["r1", "r2"]  # 与入参同序，不随运行时乱序

    def test_agents_wait整批被拒_不静默丢(self):
        c = FakeClient({"agents_wait": MCPError("all ids invalid")})
        ad = OpenClawRuntimeAdapter(c)
        h = SpawnHandle("r1", "market", "BIGA-20260101-001", "g", "sk")
        [res] = ad.wait([h], timeout_sec=30)
        assert res.status == SpawnStatus.FAILED
        assert "agents_wait 失败" in res.error


# ══ status（P4 的另一面：对外只暴露归一化）════════════════════════════════


class TestStatus:
    def test_active里running归一成running(self):
        c = FakeClient({"subagents": _list(
            active=[{"runId": "r1", "status": "running", "execution": {"state": "running"}}],
            tasks=[{"taskId": "t1", "status": "running"}])})
        ad = OpenClawRuntimeAdapter(c)
        assert ad.status(SpawnHandle("r1", "m", "BIGA-20260101-001", "g", "sk")) == SpawnStatus.RUNNING

    def test_对外永远是归一化状态而非原始词(self):
        """🔴 P4 的落点：喂一个**原始词与归一化不同**的状态（killed→cancelled），
        断言 status() 返回归一化值、且落在 SPAWN_STATUSES 里。
        把 status() 改成直接返回原始 'killed' 会两条都红。"""
        c = FakeClient({"subagents": _list(
            active=[], tasks=[],
            recent=[{"runId": "r1", "status": "killed"}])})
        ad = OpenClawRuntimeAdapter(c)
        s = ad.status(SpawnHandle("r1", "m", "BIGA-20260101-001", "g", "sk"))
        assert s == SpawnStatus.CANCELLED       # 归一化了，不是原始 'killed'
        assert s in SPAWN_STATUSES
        assert s != "killed"

    def test_查不到的handle给UNKNOWN(self):
        c = FakeClient({"subagents": _list(active=[], tasks=[])})
        ad = OpenClawRuntimeAdapter(c)
        assert ad.status(SpawnHandle("ghost", "m", "BIGA-20260101-001", "g", "sk")) == SpawnStatus.UNKNOWN


# ══ cancel（runId→taskId 的同序对应）══════════════════════════════════════


class TestCancel:
    def test_按active里的位置映射到tasks再取消(self):
        """🔴 用真实抓到的**乱序**形状：active[] 是 [r2, r1]（不是 spawn 顺序），
        取消 r1 必须命中 tasks[1]（与 active[1] 同位），不是 tasks[0]。"""
        c = FakeClient({
            "subagents": [
                _list(active=[{"runId": "r2", "status": "running"},
                              {"runId": "r1", "status": "running"}],
                      tasks=[{"taskId": "task-for-r2", "status": "running"},
                             {"taskId": "task-for-r1", "status": "running"}]),
                {"status": "cancelled", "taskId": "task-for-r1", "cancelled": True},
            ]})
        ad = OpenClawRuntimeAdapter(c)
        ad.cancel(SpawnHandle("r1", "m", "BIGA-20260101-001", "g", "sk"))
        cancel_args = [a for n, a in c.calls if n == "subagents" and a.get("action") == "cancel"]
        assert cancel_args and cancel_args[0]["taskId"] == "task-for-r1"

    def test_不在活跃列表里就不发cancel(self):
        c = FakeClient({"subagents": _list(active=[], tasks=[])})
        ad = OpenClawRuntimeAdapter(c)
        ad.cancel(SpawnHandle("gone", "m", "BIGA-20260101-001", "g", "sk"))
        assert not any(a.get("action") == "cancel" for n, a in c.calls if n == "subagents")


# ══ mcp.py：grant 与 JSON-RPC 解析 ═══════════════════════════════════════


class TestGrantAndMCP:
    def test_从attach输出抠出JSON对象_忽略后面的人话(self):
        # attach 真实输出：JSON 后跟一行提示。token 用假值（别在仓库里放真 token）。
        out = ('{"sessionKey":"agent:main:x","expiresAt":"2026-01-01T00:00:00Z",'
               '"env":{"OPENCLAW_MCP_TOKEN":"FAKE-not-a-real-token"},'
               '"configPath":"/tmp/openclaw-attach-x/.mcp.json"}\n'
               'Grant is live until ... delete it when done.')
        obj = _extract_first_json_object(out)
        assert obj["configPath"] == "/tmp/openclaw-attach-x/.mcp.json"
        assert obj["env"]["OPENCLAW_MCP_TOKEN"] == "FAKE-not-a-real-token"

    def test_grant_close删掉临时目录(self, tmp_path):
        d = tmp_path / "openclaw-attach-abc"
        d.mkdir()
        cfg = d / ".mcp.json"
        cfg.write_text("{}", encoding="utf-8")
        g = Grant(url="http://127.0.0.1:1/mcp", token="FAKE", config_path=cfg,
                  expires_at="", session_key="agent:main:x")
        assert cfg.exists()
        g.close()
        assert not cfg.exists() and not d.exists()

    def test_grant是context_manager用完即清(self, tmp_path):
        d = tmp_path / "openclaw-attach-def"
        d.mkdir()
        cfg = d / ".mcp.json"
        cfg.write_text("{}", encoding="utf-8")
        with Grant("http://127.0.0.1:1/mcp", "FAKE", cfg, "", "agent:main:x"):
            assert cfg.exists()
        assert not cfg.exists()

    def test_解析纯JSON响应(self):
        raw = '{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
        assert _parse_rpc_response(raw, "application/json", 1)["result"] == {"ok": True}

    def test_解析SSE响应(self):
        raw = ("event: message\n"
               'data: {"jsonrpc":"2.0","id":2,"result":{"v":9}}\n\n')
        assert _parse_rpc_response(raw, "text/event-stream", 2)["result"] == {"v": 9}

    def test_text_of拼接content文本(self):
        tr = {"content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]}
        assert _text_of(tr) == "hello\nworld"
