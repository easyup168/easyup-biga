"""顶层入口的 ownership 守卫 —— 出卡递归事故（§9 L-14）的根因修复。

为什么锁不够
------------
事故当天的两道修复是**契约文字**（改写 `AGENTS.md`）加**单实例锁**。
外部评审指出还不够，说得对：

> AGENTS.md = Intent / Behavior Guidance
> Code Guard = Safety Invariant   —— 两者职责必须分开。

锁拦的是**并发**，不是**越权**：一次**串行**递归（上一层退出后下一层才起）
锁根本拦不住，而那同样是无限递归，只是慢一点。

⇒ `entry_guard.classify_caller()` 把「谁有权启动顶层流水线」变成代码判据。

关于「真实 Runtime Fixture」
---------------------------
评审 §20/§21 的要求是对的，而且**这次排查就栽在这上面**：检测器查
`tool_use` / `input`，真实 transcript 是 `toolCall` / `arguments` ⇒ 扫出 0 条，
于是推出「递归假设不成立」这个错误结论。

> Guard 必须验证**真实数据形状**，而不是我们认为它应该长什么样。

但这里有一个约束：真实的运行时 cmdline **含真实家目录路径**，
而本仓库是公开的（公开仓库纪律：不许出现真实用户家目录）。

⇒ 折中方案：**不把真实 cmdline 提交成 fixture**，改成在测试里读**活着的**
  网关进程，断言守卫把它判成 `AGENT`。网关没在跑就 `skip`（不是 pass ——
  红线 R-3 在测试上的落点）。这样「真实形状」的校验仍然存在，
  而仓库里一个真实路径都没有。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：发起方分类的三态判据、两个信号各自的边界、入口真的会拒
- **不覆盖**：并发（单实例锁管，见 `test_spawn_proof.py`）、成本（预算闸门管）
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "decision-card" / "scripts"))

import entry_guard as eg  # noqa: E402


def _proc(tmp_path, chain: list[tuple[int, int, str]]) -> pathlib.Path:
    """造一棵假 `/proc`。`chain = [(pid, ppid, cmdline), …]`。"""
    root = tmp_path / "proc"
    for pid, ppid, cmd in chain:
        d = root / str(pid)
        d.mkdir(parents=True)
        (d / "cmdline").write_bytes(cmd.encode() + b"\0")
        (d / "status").write_text(f"Name:\tx\nPPid:\t{ppid}\n", encoding="utf-8")
    return root


class TestEnvSignal:
    """信号一：env 里的服务标记。它补的是「`nohup &` 把血缘断掉」那个洞。"""

    @pytest.mark.parametrize("key", eg.SERVICE_ENV)
    def test_每个服务标记都算agent(self, key):
        kind, why = eg.classify_caller(environ={key: "x"}, proc="/nonexistent")
        assert kind == eg.AGENT, why
        assert key in why, "依据里要写出是哪个变量命中的 —— 报错要指路"

    def test_空值不算(self):
        """`FOO=` 与 `FOO` 未设置是同一件事，别把空串当信号。"""
        kind, _ = eg.classify_caller(
            environ={k: "" for k in eg.SERVICE_ENV}, proc="/nonexistent")
        assert kind == eg.UNKNOWN, "空值不该命中，且此时血缘也读不到 ⇒ 判不了"

    def test_不依赖凭据类变量(self):
        """🔴 判据不能挂在一个**可能被有意清掉**的敏感值上。

        `OPENCLAW_GATEWAY_TOKEN` 看起来是个好标记，但有人清理环境时
        第一个删的就是它 —— 那样守卫会**静默失效**。
        """
        assert not any("TOKEN" in k or "PASSWORD" in k for k in eg.SERVICE_ENV)


class TestAncestrySignal:
    """信号二：进程血缘。它补的是「网关是手工起的、env 里没有标记」那个洞。"""

    def test_血缘里有运行时就是agent(self, tmp_path):
        root = _proc(tmp_path, [
            (100, 90, "bash bin/biga-card"),
            (90, 80, "/bin/bash --noprofile --norc -c cd ... && bin/biga-card"),
            (80, 1, f"node /somewhere/{eg.RUNTIME_MARK}/node_modules/openclaw/dist/x.js"),
        ])
        kind, why = eg.classify_caller(pid=100, environ={}, proc=root)
        assert kind == eg.AGENT, why
        assert "pid 80" in why, "依据要给出链路，否则排查时无从下手"

    def test_人类的血缘是human(self, tmp_path):
        root = _proc(tmp_path, [
            (100, 90, "bash bin/biga-card"),
            (90, 1, "-bash"),
        ])
        kind, why = eg.classify_caller(pid=100, environ={}, proc=root)
        assert kind == eg.HUMAN, why

    def test_判据取路径不取进程名(self, tmp_path):
        """🔴 进程名是 `node`，机器上一万个东西都叫 node。

        用名字判会把无关的 node 进程算成 agent 会话 ——
        而那种误判的表现是「人也用不了这个命令了」。
        """
        root = _proc(tmp_path, [(100, 90, "bash x"), (90, 1, "node server.js")])
        kind, _ = eg.classify_caller(pid=100, environ={}, proc=root)
        assert kind == eg.HUMAN
        assert "/" in eg.RUNTIME_MARK, "判据必须是路径片段"

    def test_ppid解析不能按空格切stat(self, tmp_path):
        """🔴 差分测试：`comm` 里有空格和括号时按空格切 `stat` 会错位。

        那类 bug **不报错**，只是给出另一个合法的 pid ——
        然后血缘就走到别人家去了，守卫照样报绿。
        ⇒ 实现读的是 `status` 的 `PPid:` 行。这里把恶意 comm 摆上来。
        """
        root = _proc(tmp_path, [
            (100, 90, "bash x"),
            (90, 80, "x"),
            (80, 1, f"node {eg.RUNTIME_MARK}/openclaw/dist/x.js"),
        ])
        # 往 status 里塞一个含空格括号的 Name，正确实现不受影响
        (root / "90" / "status").write_text(
            "Name:\t(evil name) 1 2 3\nPPid:\t80\n", encoding="utf-8")
        kind, why = eg.classify_caller(pid=100, environ={}, proc=root)
        assert kind == eg.AGENT, why


class TestThreeState:
    """🔴 判不了必须能和「是人」区分开 —— 否则依据打不出来。"""

    def test_读不到proc是判不了(self, tmp_path):
        kind, why = eg.classify_caller(pid=7, environ={}, proc=tmp_path / "nope")
        assert kind == eg.UNKNOWN and "读不到" in why

    def test_血缘断了是判不了(self, tmp_path):
        root = _proc(tmp_path, [(100, 999, "bash x")])   # 父进程不存在
        kind, why = eg.classify_caller(pid=100, environ={}, proc=root)
        assert kind == eg.UNKNOWN, why

    def test_成环不会转死(self, tmp_path):
        """真机上不该出现，但深度上限是廉价保险 ——
        一个转不出来的守卫等于把入口锁死。"""
        root = _proc(tmp_path, [(100, 101, "a"), (101, 100, "b")])
        kind, why = eg.classify_caller(pid=100, environ={}, proc=root, max_depth=5)
        assert kind == eg.UNKNOWN and "层" in why


class TestAgainstRealRuntime:
    """评审 §20/§21：至少一条判据要对着**真实运行时**验，不能全是手造 fixture。

    ⚠️ 这里刻意**不提交**真实 cmdline 当 fixture —— 它含真实家目录路径，
       而本仓库公开（公开仓库纪律）。改成读活着的进程，没有就 skip。
    """

    @staticmethod
    def _gateway_pid() -> int | None:
        try:
            out = subprocess.run(
                ["systemctl", "--user", "show",
                 "openclaw-gateway-biga.service", "-p", "MainPID", "--value"],
                capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None
        pid = (out.stdout or "").strip()
        return int(pid) if pid.isdigit() and pid != "0" else None

    def test_真网关被判成agent(self):
        """判据落在**真实的** cmdline 上。

        它同时守着一件容易漏的事：运行时的安装路径变了
        （升级、换目录）⇒ `RUNTIME_MARK` 失配 ⇒ 守卫静默失效。
        手造 fixture 永远发现不了那个。
        """
        pid = self._gateway_pid()
        if pid is None or not pathlib.Path(f"/proc/{pid}").is_dir():
            pytest.skip("网关没在跑 —— 判不了，不当作通过（R-3）")
        kind, why = eg.classify_caller(pid=pid, environ={})
        assert kind == eg.AGENT, (
            f"真网关进程没被判成 agent 血缘：{why}\n"
            f"  多半是运行时安装路径变了，而 RUNTIME_MARK 还写着旧的："
            f"{eg.RUNTIME_MARK!r}")

    def test_当前测试进程被判成human(self):
        """反方向。pytest 是人跑的（CI 里也是 cron/CI，不是 agent 会话）。

        ⚠️ 如果哪天有人从 agent 会话里跑 pytest，这条会红 —— 那是**对的**：
           它说明 `classify_caller()` 确实在看真实血缘，不是在看常量。
        """
        if any(os.environ.get(k) for k in eg.SERVICE_ENV):
            pytest.skip("本次 pytest 自己就在 agent 会话里 —— 判据成立，跳过")
        kind, why = eg.classify_caller()
        assert kind == eg.HUMAN, why


class TestWiredIntoEntrypoint:
    """L-1：守卫必须有**被证明的**调用方。判据是入口自己的退出码。"""

    def test_agent调用出卡命令会被拒(self, tmp_path):
        """🔴 行为测试，不是「源码里有没有 import」。

        🔴 `BIGA` 必须换成桩，即使守卫本该在它之前就拒掉
        ------------------------------------------------
        第一版没换。它「逻辑上」不会走到 agent 调用 —— 因为守卫在前面。
        但**探针恰恰是把守卫拆掉**，于是这条测试当场跑了一次真实出卡：
        占号 `-025`、4 个 specialist、**$0.79**。

        > 验证 fail-closed 的测试，本身不能有 fail-open 的代价。
        > 这句话是我自己写进教程第 19 章的，然后在下一个小时里违反了它。

        判据要独立于「被测的守卫是否还在」—— 否则探针一拆，代价立刻真实。
        """
        stub = tmp_path / "fake-biga"
        stub.write_text("#!/usr/bin/env bash\necho '桩：不会真的起 agent' >&2\n",
                        encoding="utf-8")
        stub.chmod(0o755)
        env = {**os.environ, "OPENCLAW_SERVICE_KIND": "gateway",
               # 把后面几道关掉，确保红的原因就是 ownership 这一道
               "BIGA_CARD_NOLOCK": "1", "BIGA_CARD_FORCE": "1",
               "BIGA": str(stub),
               "BIGA_DB_PATH": str(tmp_path / "t.db"),
               "BIGA_CARD_DEADLINE_SEC": "30", "BIGA_CARD_SETTLE_SEC": "10",
               "BIGA_STOP_FILE": str(tmp_path / "no-such-stop-file")}
        r = subprocess.run(["bash", str(REPO / "bin" / "biga-card")],
                           cwd=REPO, env=env, capture_output=True,
                           text=True, timeout=120)
        assert r.returncode == 3, (
            f"从 agent 会话里调出卡命令没有被拒（rc={r.returncode}）——\n"
            "  那递归就还能重演，只要串行一点让单实例锁抓不到。\n"
            f"  stderr: {r.stderr[-400:]}")
        assert "不能从 agent 会话里调用" in r.stderr

    def test_守卫排在第一次花钱之前(self):
        """顺序判据：熔断 → ownership → 锁 → 预算 → 第一次付费调用。

        🔴 顺序错了等于没有：守卫排在付费动作后面，每次拒绝仍然要付一轮 ——
        事故里那 \\$8.99 就是这么来的。

        ⚠️ 批 C-II 收缩之后，第一次花钱的动作从 `$BIGA agent --agent main`（喂提示词
           给 main）变成 `orchestrator.py`（程序驱动、经 Adapter 真 spawn）。守卫顺序
           必须原样跟着这个新的付费点排 —— 换的是「谁花钱」，不是「守卫排哪」。
        """
        text = (REPO / "bin" / "biga-card").read_text(encoding="utf-8")
        run = text.split("# ── 出新卡")[1]
        order = []
        for name, needle in (("熔断", "BIGA_STOP_FILE"),
                             ("ownership", "entry_guard"),
                             ("锁", "flock -n 9"),
                             ("预算", "check_budget"),
                             ("付费调用", "orchestrator.py")):
            i = run.find(needle)
            assert i >= 0, f"出卡流程里找不到「{name}」这一道（{needle}）"
            order.append((i, name))
        got = [n for _, n in sorted(order)]
        assert got == ["熔断", "ownership", "锁", "预算", "付费调用"], \
            f"防护顺序不对：{got}"


# ─────────────────────────── 元守卫：测试自己不许花钱
#
# 🔴 这一条是**修复过程中犯的错**催生的，不是设计时想到的。
#
# `test_agent调用出卡命令会被拒` 第一版没有把付费调用换成桩。
# 「逻辑上」它走不到那里 —— 守卫在前面。但**探针恰恰是把守卫拆掉**，
# 于是那次探针跑了一次真实出卡：占号 -025、4 个 specialist、$0.79。
#
# > 验证 fail-closed 的测试，本身不能有 fail-open 的代价。
# > 这句话写在教程第 19 章，然后在下一个小时里被违反了。
#
# ⚠️ 批 C-II 收缩把**第一次花钱的动作**从 `$BIGA agent --agent main` 挪到了
#    `orchestrator.py`（程序驱动、经 Adapter 真 spawn）。而 Adapter 用的是
#    `DEFAULT_BIGA`（写死的真实路径），**根本不读 `BIGA` 环境变量** —— 所以
#    「把 BIGA 换成桩」这条老判据从此拦不住花钱了。守卫必须跟着付费点一起挪，
#    否则就是本仓库反复踩的 L-13：**守卫查的地方，和它声称守的地方，不是同一处。**
#
# ⇒ 判据改成：任何在测试里跑出卡入口、又能走到付费点的地方，都必须能看出
#   **付费调用被中和**了 —— 桩掉 orchestrator.py（`_seeded_repo` 系列），
#   或让某道守卫先把它拦下（总闸 / ownership / 只读子命令）。否则红。

_ENTRYPOINT = "biga-card"

#: 「这次调用花不了钱」的证据。命中任一即安全（判据落在**调用点附近**，
#: 不做文件级放行 —— 文件级会被「同文件另一个测试加了桩」满足，那正是 L-13）。
_PAID_NEUTRALIZED = (
    "_seeded_repo", "_orch_path", "_orch_stub",  # 桩掉了 orchestrator.py（付费点）
    "OpenClawRuntimeAdapter",                     # _run 自证「orchestrator 是桩」的断言
    "OPENCLAW_SERVICE_KIND",                      # 触发 ownership 守卫拒绝
    ".biga-card-stop",                            # 触发总闸拒绝
    "--list", "--show", "--check", "--status",    # 只读子命令，根本不花钱
)


def _tests_invoking_entrypoint() -> list[tuple[str, int, str]]:
    """找出所有「在测试里启动出卡入口」的调用点。

    判据取 AST，不取正则：`subprocess.run([... "bin/biga-card"], env=...)`
    的写法有很多种，而漏掉一种就等于这条守卫不存在。
    """
    import ast
    out = []
    for path in sorted((REPO / "tests").glob("test_*.py")):
        src = path.read_text(encoding="utf-8")
        if _ENTRYPOINT not in src:
            continue
        tree = ast.parse(src, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (fn.attr if isinstance(fn, ast.Attribute)
                    else getattr(fn, "id", ""))
            if name not in ("run", "Popen", "check_call", "check_output"):
                continue
            blob = ast.unparse(node)
            if _ENTRYPOINT not in blob:
                continue
            out.append((path.name, node.lineno, blob))
    return out


def test_扫到了出卡调用点():
    """否则下面那条是平凡通过 —— 「一个都没找到」不等于「都合规」。"""
    hits = _tests_invoking_entrypoint()
    assert len(hits) >= 3, f"只扫到 {len(hits)} 处，AST 判据可能坏了"


def test_测试里跑出卡必须把付费调用换成桩():
    """🔴 判据落在**调用点附近**，不做文件级放行。

    只查文件级会被「同文件里另一个测试加了桩」满足 ——
    那正是本仓库反复踩的「守卫查的地方和它声称守的地方不是同一处」（L-13）。

    ⚠️ 收缩后付费点是 `orchestrator.py`，不再是 `$BIGA agent`（见上方注释块）。
       所以这里查的是「付费调用被中和」的证据，而不再是「BIGA 被换成桩」。
    """
    bad = []
    for fname, lineno, blob in _tests_invoking_entrypoint():
        # 调用点前 40 行 + 调用本身：足以覆盖「先 _seeded_repo 造沙盒、再 _run」
        # 和「先写 .biga-card-stop、再跑」这类紧邻的写法，又不至于宽到退化成文件级。
        src = (REPO / "tests" / fname).read_text(encoding="utf-8").splitlines()
        window = "\n".join(src[max(0, lineno - 40):lineno]) + "\n" + blob
        if any(sig in window for sig in _PAID_NEUTRALIZED):
            continue
        bad.append(f"{fname}:{lineno}  {blob[:90]}")
    assert bad == [], (
        "这些测试会跑真实的出卡入口，却看不出付费调用被中和了：\n"
        + "".join(f"  · {b}\n" for b in bad)
        + "  🔴 一旦被测的守卫被拆掉（探针就是这么干的），它们会**真的花钱**。\n"
          "     实测代价：$0.79 / 次（占号 + 4 个 specialist）。\n"
          "  收缩后付费点是 orchestrator.py（Adapter 走 DEFAULT_BIGA，不读 BIGA）——\n"
          "  用 `_seeded_repo` 桩掉它，或让总闸 / ownership / 只读子命令先拦下。")
