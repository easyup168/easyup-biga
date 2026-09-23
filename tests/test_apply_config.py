"""批 G-II 配置即代码：apply_config 渲染 + R-2 守卫 + tools.deny 策略。

探针（设计文档 §6 批 G-II 的 P4 / P5，离线可判的部分）：

  P4  R-2：`check_r2` 只放行 -biga 单元名；`OPENCLAW_SYSTEMD_UNIT` 设成非 -biga
      （同机另一套实例的默认名）⇒ 拒绝（fail closed）。装服务一律经 bin/biga daemon
      （--profile biga 推导 -biga 名），本脚本里没有任何一处裸 openclaw。
  P5  tools.deny：出卡流水线里**已建成的**非交互 agent 一律禁 ask_user；**main 不在
      patch 里**（它的 ask_user 原样保留 —— 这正是「配了 tools.deny 后 main 的正常
      飞书/TUI 交互不受影响」在配置层的落点，设计文档点名要补的那条）。

  另钉：agent 名单从 _contract 派生、不手写（裁定 15 / dev-workflow 第五问）——
  discipline 在 STAGE2 名单里但没建 ⇒ 不给它写配置。

真装服务 + 装后 isolation.py 核对是 live（探针 P4 的 live 半段），不在离线判据里。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))
sys.path.insert(0, str(REPO / "deploy" / "openclaw"))

import apply_config as ac  # noqa: E402


class TestRenderPatch:

    def test_只对已建成的流水线agent禁ask_user(self):
        agents = set(ac.built_pipeline_agents())
        assert agents == {"market", "sector", "news", "technical", "emotion",
                          "risk", "synthesizer"}
        assert "discipline" not in agents, "discipline 在 STAGE2 名单但没建（裁定 13），不该配"
        assert "main" not in agents, "main 是交互入口，不在任何 stage 名单"

    def test_P5_patch给7个agent禁ask_user_main不在其中(self):
        patch = ac.render_patch()
        entries = patch["agents"]["entries"]
        assert set(entries) == {"market", "sector", "news", "technical", "emotion",
                                "risk", "synthesizer"}
        for a, spec in entries.items():
            assert spec["tools"]["deny"] == ["ask_user"], f"{a} 应禁 ask_user"
        # 🔴 P5 的核心：main 绝不出现在 deny patch 里 —— 它的 ask_user 不被动。
        assert "main" not in entries

    def test_commands_text_true(self):
        assert ac.render_patch()["commands"]["text"] is True

    def test_patch显式删掉曾经注册的mcp_server(self):
        """出卡触发是纯 skill（shell 跑 inbound.py），不再注册 MCP server。

        🔴 必须是**显式 null**（删），不是"patch 里不提 mcp 键"——省略在 `config
        patch` 的合并语义下是"原样保留"，早期 apply 过旧版 patch 的 live 配置里那个
        键不会自己消失（真复现过：live 上那个键一直指向一个已经从仓库删掉的脚本
        路径，对应的 stdio 子进程也还在跑）。只有显式 null 才是「删」。
        """
        mcp = ac.render_patch()["mcp"]
        assert mcp == {"servers": {"biga-card-trigger": None}}, \
            "必须显式 null 掉曾经写过的那个键，不能只是不提它"

    def test_patch关掉飞书流式卡片(self):
        """默认 "partial" 撞 HTTP 400（docs/troubleshooting/feishu-streaming-card-400.md）。

        钉进配置即代码而不是靠一次性 `config patch`：后者重装/迁移到新环境时
        不会重放，会原样复现同一个静默故障（网关日志记成发出成功，用户收不到）。

        🔴 必须是 `{"mode": "off"}`，不能是布尔值 —— live 的 schema 校验拒绝过
        一次 `"streaming": false`（真实报错：`must be object`）。bool 是旧版
        OpenClaw 的写法，现在只在 `openclaw doctor --fix` 的迁移路径里认。
        """
        assert ac.render_patch()["channels"]["feishu"]["streaming"] == {"mode": "off"}

    def test_patch里没有任何凭据或可识别id(self):
        """公开仓库纪律：patch 只碰它管的键，绝不含 appSecret / token / ownerAllowFrom。"""
        import json
        blob = json.dumps(ac.render_patch(), ensure_ascii=False).lower()
        for secretish in ("appsecret", "app_secret", "token", "ownerallowfrom",
                          "allowfrom", "ou_"):
            assert secretish not in blob, f"patch 里不该出现 {secretish!r}"


class TestR2Guard:

    def test_P4_干净env推导biga单元名(self):
        assert ac.check_r2({}) == ac.EXPECTED_UNIT
        assert ac.EXPECTED_UNIT.endswith("-biga.service")

    def test_P4_biga单元名的env覆盖放行(self):
        assert ac.check_r2({"OPENCLAW_SYSTEMD_UNIT": "openclaw-gateway-biga.service"})

    def test_P4_非biga单元名被拒_这是R2的止血点(self):
        # 同机另一套实例的默认名 —— 写上去就静默顶掉它的生产单元。
        with pytest.raises(ac.R2Violation, match="-biga"):
            ac.check_r2({"OPENCLAW_SYSTEMD_UNIT": "openclaw-gateway.service"})

    def test_P4_任意非biga名字都被拒(self):
        with pytest.raises(ac.R2Violation):
            ac.check_r2({"OPENCLAW_SYSTEMD_UNIT": "whatever.service"})
