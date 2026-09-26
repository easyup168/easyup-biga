"""eod-daily-bars-biga.timer 的装/卸载脚本 —— 让 EOD 日线真的有调度方。

`bin/biga-data run-eod-bundle` 能一次取数跑完全市场日线，但在这个 timer
之前全仓 grep 不到任何调度方调用它 —— CLAUDE.md 那条「新增写数据模块
必须有被证明的读取方，**判据是调度命令的字面量**」在这里落了空。

🔴 单元名必须是 `-biga` **后缀**，不是 `biga-` 前缀
---------------------------------------------------
外部实现包给的是 `biga-eod-daily-bars.service`。`isolation.py` 的判据是
「引用 BigA 路径的单元名以 `-biga.service/.timer` 结尾」——
前缀式的名字它**根本不会把它算成 BigA 的单元**，于是一声不吭地放行。
**比报红更糟**：报红会被修，静默漏检不会。

探针对照（与 `test_notify_timer.py` / `test_reap_timer.py` 同构）：
* R-2：单元名不带 `-biga` 后缀 ⇒ `check_r2()` fail-closed
* install/uninstall：真跑一遍拷文件 + 记录 systemctl 调用（注入假 runner，
  不碰这台机器真实的 systemd 用户实例）
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "deploy" / "openclaw"))

import install_eod_timer as rt  # noqa: E402


class _FakeRunner:
    class _Result:
        def __init__(self, returncode=0, stderr=""):
            self.returncode = returncode
            self.stderr = stderr

    def __init__(self, returncode=0):
        self.calls: list[list[str]] = []
        self._returncode = returncode

    def __call__(self, cmd: list[str]):
        self.calls.append(cmd)
        return self._Result(returncode=self._returncode)


class TestR2:
    def test_两个单元名都带biga后缀_默认放行(self):
        rt.check_r2()  # 不抛就是过

    def test_service名不带biga后缀_拒绝(self):
        with pytest.raises(rt.R2Violation, match="biga"):
            rt.check_r2(service_name="stale-run-reaper.service")

    def test_timer名不带biga后缀_拒绝(self):
        with pytest.raises(rt.R2Violation, match="biga"):
            rt.check_r2(timer_name="stale-run-reaper.timer")

    def test_常量本身真的带后缀(self):
        """🔴 防止有人以后改了 UNIT_SERVICE/UNIT_TIMER 常量却没意识到会撞 R-2——
        这条测的是常量的**当前值**，不是 check_r2 的逻辑。"""
        assert rt.UNIT_SERVICE.endswith("-biga.service")
        assert rt.UNIT_TIMER.endswith("-biga.timer")


class TestInstall:
    @pytest.fixture
    def deploy_root(self, tmp_path):
        """一份最小可用的单元文件对，模拟 deploy/openclaw/ 里真实存在的那两个。"""
        d = tmp_path / "deploy"
        d.mkdir()
        (d / rt.UNIT_SERVICE).write_text("[Service]\nExecStart=/bin/true\n")
        (d / rt.UNIT_TIMER).write_text("[Timer]\nOnUnitActiveSec=2min\n")
        return d

    def test_真装_文件真的拷过去_命令真的按顺序发出(self, deploy_root, tmp_path):
        systemd_dir = tmp_path / "systemd-user"
        runner = _FakeRunner()
        rc = rt.install(dry_run=False, runner=runner,
                        deploy_root=deploy_root, systemd_dir=systemd_dir)
        assert rc == 0
        assert (systemd_dir / rt.UNIT_SERVICE).read_text() == \
               (deploy_root / rt.UNIT_SERVICE).read_text()
        assert (systemd_dir / rt.UNIT_TIMER).exists()
        assert runner.calls == [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", rt.UNIT_TIMER],
        ]

    def test_dry_run_什么都不碰(self, deploy_root, tmp_path):
        systemd_dir = tmp_path / "systemd-user"
        runner = _FakeRunner()
        rc = rt.install(dry_run=True, runner=runner,
                        deploy_root=deploy_root, systemd_dir=systemd_dir)
        assert rc == 0
        assert not systemd_dir.exists(), "dry-run 不该创建目标目录"
        assert runner.calls == [], "dry-run 不该真调 systemctl"

    def test_daemon_reload失败就不再往下enable(self, deploy_root, tmp_path):
        systemd_dir = tmp_path / "systemd-user"
        runner = _FakeRunner(returncode=1)
        rc = rt.install(dry_run=False, runner=runner,
                        deploy_root=deploy_root, systemd_dir=systemd_dir)
        assert rc == 1
        assert len(runner.calls) == 1, "reload 都失败了，不该继续 enable"

    def test_装之前先过R2检查(self, deploy_root, tmp_path, monkeypatch):
        """🔴 sabotage：让 check_r2 拒绝，install 必须在碰任何文件/systemctl
        之前就冒泡出去——不能"先拷文件再发现名字不对"。"""
        def _always_reject(*a, **kw):
            raise rt.R2Violation("sabotage")
        monkeypatch.setattr(rt, "check_r2", _always_reject)
        systemd_dir = tmp_path / "systemd-user"
        runner = _FakeRunner()
        with pytest.raises(rt.R2Violation):
            rt.install(dry_run=False, runner=runner,
                      deploy_root=deploy_root, systemd_dir=systemd_dir)
        assert not systemd_dir.exists()
        assert runner.calls == []


class TestUninstall:
    def test_真卸载_文件真的删掉_命令真的发出(self, tmp_path):
        systemd_dir = tmp_path / "systemd-user"
        systemd_dir.mkdir()
        (systemd_dir / rt.UNIT_SERVICE).write_text("x")
        (systemd_dir / rt.UNIT_TIMER).write_text("x")
        runner = _FakeRunner()
        rc = rt.uninstall(dry_run=False, runner=runner, systemd_dir=systemd_dir)
        assert rc == 0
        assert not (systemd_dir / rt.UNIT_SERVICE).exists()
        assert not (systemd_dir / rt.UNIT_TIMER).exists()
        assert runner.calls == [
            ["systemctl", "--user", "disable", "--now", rt.UNIT_TIMER],
            ["systemctl", "--user", "daemon-reload"],
        ]

    def test_卸载一个从没装过的单元不报错(self, tmp_path):
        """disable 一个不存在的单元，systemctl 真实会非零退出——卸载路径
        不该因此整个失败（人本来就是为了"确保它不在了"才跑卸载）。"""
        systemd_dir = tmp_path / "systemd-user"
        systemd_dir.mkdir()

        def runner(cmd):
            rc = 1 if cmd[:3] == ["systemctl", "--user", "disable"] else 0
            return _FakeRunner._Result(returncode=rc)

        rc = rt.uninstall(dry_run=False, runner=runner, systemd_dir=systemd_dir)
        assert rc == 0, "daemon-reload（最后一条命令）本身成功，就该返回 0"


class TestUnitFilesOnDisk:
    """真实的 deploy/openclaw/eod-daily-bars-biga.{service,timer} 本身要自洽。"""

    def test_两个真实单元文件都存在且带biga后缀(self):
        deploy_root = REPO / "deploy" / "openclaw"
        assert (deploy_root / rt.UNIT_SERVICE).exists()
        assert (deploy_root / rt.UNIT_TIMER).exists()

    def test_service指向bin_biga_data_用户级路径不硬编码家目录(self):
        text = (REPO / "deploy" / "openclaw" / rt.UNIT_SERVICE).read_text()
        # 🔴 `run-eod-bundle` 不是 `run-eod`：后者只发日线，覆盖率的分母会退回
        #    provider 自报的 total。差别在停牌多的日子才显出来 —— 而那正是
        #    最需要判准的日子。这条钉住调度命令的**字面量**。
        assert "bin/biga-data run-eod-bundle" in text
        assert "%h" in text, "路径必须用 systemd 的 %h 展开，不能写死 /home/<用户>"
        assert "/home/" not in text, "公开仓库纪律：不落真实用户家目录路径"
        assert "python " not in text, "本机只有 python3 —— 外部实现包在这里写的是 python"

    def test_ExecStart指向的可执行文件真的在(self):
        """判据是「那个文件存在且可执行」，不是「字符串看起来像条命令」。"""
        import os
        text = (REPO / "deploy" / "openclaw" / rt.UNIT_SERVICE).read_text()
        line = next(x for x in text.splitlines() if x.startswith("ExecStart="))
        rel = line.split("=", 1)[1].split()[0].replace("%h/.openclaw-biga/workspace/", "")
        target = REPO / rel
        assert target.exists(), f"ExecStart 指向的 {rel} 不存在"
        assert os.access(target, os.X_OK), f"{rel} 没有可执行位"

    def test_timer不判交易日是有意的(self):
        """⚠️ 这条钉的是一个**决定**，不是一个行为。

        把交易日判断塞进 `OnCalendar` 等于日历口径多一份实现（L-3），
        而 systemd 的 `OnCalendar` 根本表达不了 A 股的调休。
        跑空的那天由 `run-eod-bundle` 自己 fail-closed。
        改这条测试 = 明确声明要换这个决定。
        """
        text = (REPO / "deploy" / "openclaw" / rt.UNIT_TIMER).read_text()
        assert "OnCalendar=Mon..Fri" in text
        assert "Persistent=true" in text, "关机错过的那次要补跑"

    def test_timer指向正确的service单元(self):
        text = (REPO / "deploy" / "openclaw" / rt.UNIT_TIMER).read_text()
        assert f"Unit={rt.UNIT_SERVICE}" in text
