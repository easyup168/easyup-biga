"""隔离自检自己的守卫 —— 外部评审 F2 / F18 / F19。

为什么自检工具也需要被测
------------------------
`isolation.py` 是 `CLAUDE.md` 指定的「改动环境后跑一次」的第一道关卡。
评审的一句话说得最准：

> **这份文档自己用来证明「我们没有自欺」的示范检查，其中至少两项本身就是会自欺的。**

三条缺陷是同一个根：**它只有「过 / 不过」两态，没有「判不了」**。
于是「什么都没扫到」和「扫了，没问题」写出来一模一样。

覆盖
----
==========  ==================================================
F18         三态：`UNKNOWN` 不算通过，退出码非零
F2          真运行时不在 / 端口没人监听 ⇒ 判不了，不是通过
F19         I-1 的判据全仓只有一份实现
==========  ==================================================
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "verify"))

from _scan import repo_files  # noqa: E402

import isolation  # noqa: E402


class TestThreeState:
    """F18：`UNKNOWN` ≠ `PASS`（红线 R-3 在输出上的落点）。"""

    def test_判不了不许说全绿(self):
        res = isolation.Result()
        res.ok("甲")
        res.unknown("乙")
        out = res.render()
        assert "全绿" not in out, f"有判不了的项却说全绿：\n{out}"
        assert "判不了" in out

    def test_全过才说全绿(self):
        res = isolation.Result()
        res.ok("甲")
        res.ok("乙")
        assert "全绿" in res.render()

    def test_判不了的退出码非零(self, monkeypatch, capsys):
        """🔴 最常用的读法是「跑完没报错就是过了」。

        所以判不了**必须**是非零退出码 —— 否则三态在渲染层做对了，
        在调用层又退化回两态。
        """
        def fake(res, *a, **kw):
            res.unknown("模拟：什么都没扫到")

        for fn in ("check_i1", "check_i2", "check_r2", "check_ports"):
            monkeypatch.setattr(isolation, fn, fake)
        rc = isolation.main([])
        capsys.readouterr()
        assert rc != 0, "判不了却返回 0 —— 调用方会读成通过"

    def test_三态各自的退出码可区分(self, monkeypatch, capsys):
        """「坏了」和「没验到」要能分开，否则排查方向一开始就是错的。"""
        codes = {}
        for kind in ("ok", "unknown", "fail"):
            def fake(res, *a, _k=kind, **kw):
                getattr(res, _k)("模拟")
            for fn in ("check_i1", "check_i2", "check_r2", "check_ports"):
                monkeypatch.setattr(isolation, fn, fake)
            codes[kind] = isolation.main([])
            capsys.readouterr()
        assert codes["ok"] == 0
        assert len({codes["unknown"], codes["fail"]}) == 2, codes
        assert 0 not in (codes["unknown"], codes["fail"])


class TestNothingToCheck:
    """F2：「找到了，但找到的是无关的东西」——比「一个都没找到」更常见。"""

    def test_只有蹭cwd的会话时判不了(self, monkeypatch):
        """扫到 10 个进程、0 个真运行时 ⇒ 判不了。

        实测的形状：只要有人开着终端或编辑器 cwd 在仓库下，
        即使真网关早已崩溃，旧实现也会报「检查了 N 个进程，✅ 通过」。
        """
        monkeypatch.setattr(isolation, "_biga_pids",
                            lambda: [(i, f"bash cwd-in-repo {i}", False)
                                     for i in range(10)])
        res = isolation.Result()
        isolation.check_i1(res)
        assert res.rows[0][0] == isolation.UNKNOWN, res.rows[0]

    def test_有真运行时才算证据(self, monkeypatch):
        monkeypatch.setattr(isolation, "_biga_pids",
                            lambda: [(1, "bash", False), (2, "node …/runtime/…", True)])
        res = isolation.Result()
        isolation.check_i1(res)
        assert res.rows[0][0] == isolation.PASS, res.rows[0]

    @pytest.mark.parametrize("listening,want", [
        (set(),                 isolation.UNKNOWN),   # 两边都没在听
        ({19789},               isolation.UNKNOWN),   # 只有我们在听
        ({18789},               isolation.UNKNOWN),   # 只有对方在听
        ({19789, 18789},        isolation.PASS),      # 都在听，不重叠
    ])
    def test_端口没人监听时判不了(self, monkeypatch, listening, want):
        """🔴 空集不冲突是**平凡成立**的，证明不了端口分配是对的。

        旧实现里 `clash = ours & theirs` 为空 ⇒ `not clash` 为真 ⇒ ✅。
        紧挨着的 I-1 至少防住了「0 个进程」，这里连那个都没防。
        """
        monkeypatch.setattr(isolation, "_listening_ports", lambda: set(listening))
        res = isolation.Result()
        isolation.check_ports(res)
        assert res.rows[0][0] == want, res.rows[0]


class TestSingleImplementation:
    """F19：同一条不变式，仓库里曾经有两份判据，同时刻相差 3 倍以上。"""

    def test_只有一处在走proc的fd表(self):
        """判据取自 AST：谁在遍历 `/proc/<pid>/fd`。

        ⚠️ 不用「文件名里有 isolation」这种名字判据 —— 第二份实现
        当初就叫 `biga_fds_into_neighbour()`，名字里一个相关的词都没有。
        """
        walkers = []
        for path in repo_files(".py"):
            # 🔴 自匹配豁免：描述这条规则就必须写出被找的那个路径，
            #    于是扫描器自己永远是第一个命中。与 audit_public.sh
            #    用 `[a]bcd` 躲开自己是同一个问题的两种解法。
            if path == pathlib.Path(__file__).resolve():
                continue
            src = path.read_text(encoding="utf-8")
            if "/proc" not in src:
                continue
            tree = ast.parse(src, filename=str(path))
            for node in ast.walk(tree):
                # 🔴 判据必须是「走 **fd 表**」，不是「提到 /proc」。
                #
                # 原来写的是 `value.startswith("/proc")` —— 而这条测试的名字
                # 说的是 fd 表。两者不是同一件事：`entry_guard.py` 读
                # `/proc/<pid>/status` 追进程血缘，与 I-1 的 fd 判据毫无关系，
                # 却被判成「第二份实现」。
                #
                # ⚠️ 那是本仓库的 L-13：**守卫查的地方和它声称守的地方不是同一处。**
                #    这次的方向是**假阳**（把无关文件算进来）——
                #    比假阴温和，但同样会逼人去加豁免，而豁免会掩盖真的第二份实现。
                # ⚠️ 必须同时认 `Constant` 与 `JoinedStr`：真实写法是
                #    `pathlib.Path(f"/proc/{pid}/fd")` —— f-string 在 AST 里
                #    被拆成 `"/proc/"` 和 `"/fd"` 两段，按 Constant 查
                #    **一处都找不到**（第一版缩窄之后就是 0 命中，
                #    而 0 命中会让这条守卫平凡通过 —— 比假阳危险）。
                if not isinstance(node, (ast.Constant, ast.JoinedStr)):
                    continue
                if isinstance(node, ast.Constant) and not isinstance(node.value, str):
                    continue
                text = ast.unparse(node)
                if "/proc" in text and ("/fd" in text or "fdinfo" in text):
                    walkers.append(path.relative_to(REPO).as_posix())
                    break
        assert walkers == ["tools/verify/isolation.py"], (
            f"I-1 的判据出现在多处：{walkers}\n"
            "  同一判据两份实现，两边都报绿时你不知道该信哪一份 ——\n"
            "  这正是 architecture.md §9 L-3 点名的失败模式。"
        )


class TestSharedNamespace:
    """R-2 第 2 处：systemd 用户单元名也是共享命名空间。

    端口分开了、状态目录分开了，**名字还是共享的**。

    实测（2026-09-21 装服务时发现）：默认名 `openclaw-gateway.service`
    正被同机已有实例用着，旁边还有个 `.bak` —— **有人已经覆写过一次**。

    🔴 当时 R-2 只写了 nvm bin 一处，差点按「R-2 说的是 nvm，这里不是」放行。
    ⇒ 红线已改写成**模式级**：共享命名空间里不许占默认名，
      已知三处（nvm bin / systemd 单元名 / 端口）列成一张表。
    """

    def _units(self, tmp_path, monkeypatch, files: dict[str, str]):
        d = tmp_path / "systemd" / "user"
        d.mkdir(parents=True)
        for name, body in files.items():
            (d / name).write_text(body, encoding="utf-8")
        monkeypatch.setattr(isolation, "SYSTEMD_USER", d)
        res = isolation.Result()
        isolation.check_namespaces(res)
        return res.rows[0]

    def _body(self, path_str: str) -> str:
        return f"[Service]\nExecStart=/usr/bin/node {path_str}/runtime/x.js\n"

    def test_带后缀的单元是合规的(self, tmp_path, monkeypatch):
        got = self._units(tmp_path, monkeypatch, {
            "openclaw-gateway-biga.service": self._body(str(isolation.BIGA))})
        assert got[0] == isolation.PASS, got

    def test_占了默认名就是覆写(self, tmp_path, monkeypatch):
        """🔴 一个不叫 `-biga` 的单元里出现 BigA 路径 = 我们顶掉了别人的。"""
        got = self._units(tmp_path, monkeypatch, {
            "openclaw-gateway.service": self._body(str(isolation.BIGA))})
        assert got[0] == isolation.FAIL, got
        assert "openclaw-gateway.service" in got[2]

    def test_别人的单元不受影响(self, tmp_path, monkeypatch):
        """同机已有实例的单元不引用 BigA ⇒ 与我们无关，不该被算进来。"""
        # ⚠️ 假路径故意不写成家目录形态 —— 公开审查会抓 `/home/...`
        got = self._units(tmp_path, monkeypatch, {
            "openclaw-gateway.service": self._body("/opt/other-instance"),
            "openclaw-gateway-biga.service": self._body(str(isolation.BIGA))})
        assert got[0] == isolation.PASS, got

    def test_没装服务是判不了_不是通过(self, tmp_path, monkeypatch):
        """🔴 什么都没装的时候「没占别人名字」是**平凡成立**的。"""
        got = self._units(tmp_path, monkeypatch, {
            "unrelated.service": "[Service]\nExecStart=/bin/true\n"})
        assert got[0] == isolation.UNKNOWN, got

    def test_目录不存在也是判不了(self, tmp_path, monkeypatch):
        monkeypatch.setattr(isolation, "SYSTEMD_USER", tmp_path / "nope")
        res = isolation.Result()
        isolation.check_namespaces(res)
        assert res.rows[0][0] == isolation.UNKNOWN

    def test_环境变量泄漏会被提示(self, tmp_path, monkeypatch):
        """`OPENCLAW_SYSTEMD_UNIT` 会覆盖 profile 推导的名字 ——
        它一旦漏进 shell，`gateway install` 就会写到生产那个文件上。"""
        monkeypatch.setenv("OPENCLAW_SYSTEMD_UNIT", "openclaw-gateway.service")
        got = self._units(tmp_path, monkeypatch, {
            "openclaw-gateway-biga.service": self._body(str(isolation.BIGA))})
        assert "OPENCLAW_SYSTEMD_UNIT" in got[2], "泄漏没被提示出来"

    def test_红线写成了模式级而不是三条规则(self):
        """判据落在 `CLAUDE.md` 上 —— 下次发现第四处该补进表，不该新开红线。"""
        text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
        i = text.index("### R-2")
        section = text[i:text.index("### R-3")]
        assert "共享命名空间" in section
        for kind in ("nvm", "systemd", "端口"):
            assert kind in section, f"R-2 的实例表里少了 {kind}"
