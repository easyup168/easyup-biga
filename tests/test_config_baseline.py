"""tools/verify/config_baseline.py —— H 节 Baseline 冻结的 Hash 计算。"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "verify"))

import config_baseline as cb  # noqa: E402


class TestPortable:
    def test_替换home_递归到字典与列表深处(self):
        obj = {
            "workspace": "/opt/users/alice/.openclaw-biga/workspace",
            "entries": [{"agentDir": "/opt/users/alice/.openclaw-biga/agents/main/agent"}],
        }
        out = cb.portable(obj, home="/opt/users/alice")
        assert out["workspace"] == "~/.openclaw-biga/workspace"
        assert out["entries"][0]["agentDir"] == "~/.openclaw-biga/agents/main/agent"

    def test_不含home的值原样保留(self):
        obj = {"model": "anthropic/claude-sonnet-5", "maxConcurrent": 6}
        assert cb.portable(obj, home="/opt/users/alice") == obj


class TestCanonicalHash:
    def test_键顺序不同_hash相同(self):
        a = {"b": 1, "a": 2}
        b = {"a": 2, "b": 1}
        assert cb.canonical_hash(a, home="/opt/users/alice") == cb.canonical_hash(b, home="/opt/users/alice")

    def test_内容不同_hash不同(self):
        a = {"maxConcurrent": 6}
        b = {"maxConcurrent": 7}
        assert cb.canonical_hash(a, home="/opt/users/alice") != cb.canonical_hash(b, home="/opt/users/alice")

    def test_只是换了用户名_hash不变(self):
        """同一份逻辑配置，在不同机器（不同 $HOME）上算出的 hash 必须一致——
        否则这个 hash 就只能证明"在同一台机器上没变"，证不了"配置本身没变"。
        """
        alice = {"workspace": "/opt/users/alice/.openclaw-biga/workspace"}
        bob = {"workspace": "/opt/users/bob/.openclaw-biga/workspace"}
        assert cb.canonical_hash(alice, home="/opt/users/alice") == cb.canonical_hash(bob, home="/opt/users/bob")

    def test_sabotage_不做home替换就会随机器漂(self):
        """哨兵测试：如果有人删掉 canonical_hash 里的 portable() 调用，这条测试要能抓到。"""
        alice = {"workspace": "/opt/users/alice/.openclaw-biga/workspace"}
        bob = {"workspace": "/opt/users/bob/.openclaw-biga/workspace"}
        # 不做 home 替换的朴素版本 —— 证明「不替换」确实会导致 hash 漂移，
        # 从而证明 canonical_hash 里那次 portable() 调用是必要的，不是摆设。
        import hashlib
        import json

        def naive_hash(obj):
            blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            return hashlib.sha256(blob.encode("utf-8")).hexdigest()

        assert naive_hash(alice) != naive_hash(bob)
        assert cb.canonical_hash(alice, home="/opt/users/alice") == cb.canonical_hash(bob, home="/opt/users/bob")


class TestOpenclawVersion:
    @pytest.mark.installed
    def test_解析真实biga_cli输出(self):
        version = cb.openclaw_version(cb.BIGA_CLI)
        assert version.startswith("OpenClaw ")


class TestMain:
    def test_配置文件不存在_返回1(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cb, "CONFIG_PATH", tmp_path / "不存在.json")
        assert cb.main() == 1

    @pytest.mark.installed
    def test_真实环境跑通_返回0(self, capsys):
        rc = cb.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "OpenClaw Version" in out
        assert "Tool Policy Hash   : sha256:" in out
        assert "Agent Config Hash  : sha256:" in out
