"""非交互 `ask_user` 死锁看门狗 —— 外部评审 ASK-04 / ASK-06。

为什么目标不是「禁掉 ask_user」
------------------------------
探查结论（和评审一致）：这个运行时**没有**按次禁用工具的能力 ——
`agent` 没有 tool 参数；`agent exec` 是隔离模式、spawn 不了 specialist；
配置级 `tools.deny` 粒度是按 agent，会把交互式 `main` 的能力一起砍掉。

⇒ 目标降级为评审那句话：

> 不要求 `ask_user` 不出现，而是保证它即使出现也只能造成
> **有限、可检测、可审计**的失败，不能造成永久死锁。

🔴 为什么 fixture 必须来自真实日志
----------------------------------
`stalled session` 是**日志行**，不是结构化接口（三处找过，只有 journal 带
`reason` / `activeTool`）。日志格式会变，而变了之后解析器**静默失效**。

而本项目刚在「按我以为的形状写检测器」上栽过一次：查 `tool_use` / `input`，
真实是 `toolCall` / `arguments` ⇒ 扫出 0 条 ⇒ 推出「递归假设不成立」这个错误结论。

> 手造 fixture 只能证明「解析器能解析我自己写的 fixture」。

⇒ `tests/fixtures/stalled-session.log` 是**真实事故那几行**，
  字段名/顺序/格式一字未改，只抹了两个标识符。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：真实行能被认出、三个条件缺一不可、只管自己那次、阈值、接进出卡流程
- **不覆盖**：别的阻塞工具（硬超时兜）、交互式会话（那里 `ask_user` 正常）
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "decision-card" / "scripts"))

import stall_watchdog as wd  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "stalled-session.log"
INCIDENT_KEY = "agent:main:card-214144"


def _real_lines() -> list[str]:
    return FIXTURE.read_text(encoding="utf-8").splitlines()


class TestAgainstRealFixture:
    """🔴 判据落在**真实那一行**上（评审 §26）。"""

    def test_fixture在且是真实形状(self):
        lines = _real_lines()
        assert lines, "fixture 空了 —— 那下面每一条都变成平凡通过"
        ln = lines[0]
        # 这些字段名是解析器真正依赖的东西。少一个就说明 fixture 被改坏了。
        for field in ("stalled session", "sessionKey=", "state=", "reason=",
                      "activeTool=", "activeToolAge=", "recovery="):
            assert field in ln, f"fixture 里少了 {field!r}"

    def test_fixture不含可识别信息(self):
        """公开仓库纪律：运行时产生的具体标识不入库。"""
        text = FIXTURE.read_text(encoding="utf-8")
        assert "/home/" not in text
        assert "REDACTED" in text, "标识符应当已被替换"

    def test_认出真实那次死锁(self):
        s = wd.parse_stalled(_real_lines()[0])
        assert s is not None
        assert s.session_key == INCIDENT_KEY
        assert s.reason == "blocked_tool_call"
        assert s.active_tool == "ask_user"
        assert s.recovery == "none"
        assert s.active_tool_age_sec > 0
        assert s.is_ask_user_deadlock

    def test_find_blocked命中真实那次(self):
        hit = wd.find_blocked(INCIDENT_KEY, lines=_real_lines())
        assert hit is not None and hit.is_ask_user_deadlock

    def test_取最新那一条(self):
        """journal 每 30s 打一条，年龄递增。取最新的才反映当前状态。"""
        lines = _real_lines()
        ages = [wd.parse_stalled(ln).active_tool_age_sec for ln in lines]
        hit = wd.find_blocked(INCIDENT_KEY, lines=lines)
        assert hit.active_tool_age_sec == max(ages), f"ages={ages}"


class TestThreeConditions:
    """🔴 三个条件缺一不可 —— 少一个就会误杀正常运行。"""

    @staticmethod
    def _line(**over) -> str:
        kv = {"sessionId": "x", "sessionKey": INCIDENT_KEY, "state": "processing",
              "age": "646s", "reason": "blocked_tool_call",
              "activeTool": "ask_user", "activeToolAge": "121s",
              "recovery": "none"}
        kv.update({k: str(v) for k, v in over.items()})
        return ("[diagnostic] stalled session: "
                + " ".join(f"{k}={v}" for k, v in kv.items()))

    def test_齐了就是死锁(self):
        assert wd.parse_stalled(self._line()).is_ask_user_deadlock

    def test_只是慢不算(self):
        """`reason` 不是 `blocked_tool_call` ⇒ 它在干活，只是慢。

        误杀这种的后果是「出卡永远跑不完」，比不杀更糟。
        """
        s = wd.parse_stalled(self._line(reason="slow_model"))
        assert not s.is_ask_user_deadlock

    def test_别的工具不在这里管(self):
        """卡在 `exec` 上由硬超时兜 —— 那一道不依赖日志，更可靠。

        看门狗只认 `ask_user`，因为只有它**确定**没人能回答。
        """
        s = wd.parse_stalled(self._line(activeTool="exec"))
        assert not s.is_ask_user_deadlock

    def test_运行时说它会救就别插手(self):
        """`recovery=none` 是运行时自己的声明。它不是 none 时抢着杀，
        等于和运行时的恢复机制打架。"""
        s = wd.parse_stalled(self._line(recovery="retry"))
        assert not s.is_ask_user_deadlock


class TestScopeAndThreshold:
    def test_只管自己那一次(self):
        """🔴 交互式会话里 `ask_user` 是**正常能力**，不能连坐。

        看全局等于替别人做决定 —— 而飞书那条路正好就靠它问人。
        """
        assert wd.find_blocked("agent:main:other", lines=_real_lines()) is None

    def test_没到阈值不动手(self):
        lines = [TestThreeConditions._line(activeToolAge="5s")]
        assert wd.find_blocked(INCIDENT_KEY, lines=lines, threshold_sec=30) is None

    def test_到阈值就命中(self):
        lines = [TestThreeConditions._line(activeToolAge="45s")]
        assert wd.find_blocked(INCIDENT_KEY, lines=lines, threshold_sec=30)

    def test_用工具年龄不用会话年龄(self):
        """🔴 `age` 是整个会话的年龄。一次正常出卡跑 170~200s ——
        拿它判会把**每一次正常运行**都算成卡住。"""
        lines = [TestThreeConditions._line(age="900s", activeToolAge="3s")]
        assert wd.find_blocked(INCIDENT_KEY, lines=lines, threshold_sec=30) is None

    def test_阈值取30秒有理由(self):
        """非交互会话里根本不存在能回答的人 ⇒ 没有「再等等」的可能性。"""
        assert wd.DEFAULT_BLOCKED_SEC <= 60


class TestParserRobustness:
    def test_无关行不抛异常(self):
        """journal 里绝大多数行都不是这种 —— 抛异常会让看门狗每轮都炸。"""
        for ln in ("", "hello", "[feishu] WebSocket client started",
                   "[diagnostic] stalled session:"):
            wd.parse_stalled(ln)      # 不抛就算过

    def test_字段顺序变了仍能解析(self):
        """按 `key=value` 抓，不按位置 —— 顺序是最容易变的东西。"""
        ln = ("[diagnostic] stalled session: recovery=none activeTool=ask_user "
              f"activeToolAge=99s reason=blocked_tool_call sessionKey={INCIDENT_KEY}")
        s = wd.parse_stalled(ln)
        assert s.is_ask_user_deadlock and s.active_tool_age_sec == 99

    def test_读不到journal时不开火(self):
        """🔴 失效方向必须是**不动手**。

        日志读不到 ⇒ 看门狗沉默 ⇒ 退化到硬超时（那一道不依赖日志）。
        反过来「读不到就杀」会把每一次正常运行都杀掉。
        """
        assert wd.find_blocked(INCIDENT_KEY, unit="no-such-unit.service",
                               since="-1min") is None

    def test_单元名带biga后缀(self):
        """红线 R-2：默认名 `openclaw-gateway.service` 属于同机另一套实例。"""
        assert wd.UNIT.endswith("-biga.service")


# ─────────────────────────── 看门狗与出卡流程的接线：批 C-II 之后不在这里了
#
# 老路径里 `bin/biga-card` 有一段 bash 轮询循环（`sleep 5` 等 main 的子会话落库），
# 看门狗就挂在那个循环里，专治「非交互会话卡在 `ask_user` 上永久死锁」。
#
# 批 C-II 把「等待」整段挪进了 `DecisionOrchestrator`（Python，同步）——
# bin/biga-card 不再有轮询循环，这段接线**按设计消失了**，原来钉它的
# `TestWiredIntoCardFlow` 也随之删除（它断言的 `sleep 5` / `stall_watchdog` /
# `exit 5` 都已不在脚本里）。
#
# 🔴 但「ask_user 死锁被有限化」这件事**不能静默丢**：
#    新路径里每个 spawn 都带 `runTimeoutSeconds`（Adapter 硬约束），
#    一个卡在 `ask_user` 的 specialist 理应被运行时按 timeout 收掉、
#    `agents_wait` 把它记成缺席。但「runTimeoutSeconds 是否在 `blocked_tool_call`
#    状态下真的开火」读代码验不了 —— 这条残留风险已按 P5 同样的处置写进
#    `TODO.md`「已知问题」，连同 stall_watchdog 模块目前**零消费方**这件事：
#    下一批要么验证运行时会收掉阻塞会话后正式退役它，要么把 find_blocked
#    接到 orchestrator 的超时诊断路径上。模块与其单元测试**原样保留**，
#    不在没验证「运行时确实兜住」之前就把一道安全网删掉（R-3：宁可留着待裁定）。


def test_fixture真的被git跟踪():
    """🔴 「文件在硬盘上」和「文件在仓库里」是两件事。

    实测：`.gitignore` 里的 `*.log` 把这份 fixture 一起忽略掉了。
    后果**完全静默** —— 本机跑全绿（文件就在那儿），
    克隆下来的人根本没有这个文件，而上面每一条测试都会莫名其妙地红。

    ⇒ 判据取 `git ls-files`（git 眼里的仓库内容），
      不取 `Path.exists()`（我这台机器上的内容）。

    ⚠️ 没有 `.git` 时 `skip` —— 那是**判不了**，不是通过（红线 R-3）。
    """
    import subprocess
    rel = FIXTURE.relative_to(REPO).as_posix()
    try:
        out = subprocess.run(["git", "ls-files", "--error-unmatch", rel],
                             cwd=REPO, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("没有 git —— 判不了")
    if "not a git repository" in (out.stderr or ""):
        pytest.skip("不是 git 仓库 —— 判不了")
    assert out.returncode == 0, (
        f"{rel} 没有被 git 跟踪。\n"
        "  多半是 `.gitignore` 的 `*.log` 把它吃掉了 ——\n"
        "  `tests/fixtures/` 需要一条否定规则（`!tests/fixtures/**`）。\n"
        "  这类故障是静默的：本机全绿，克隆下来全红。")
