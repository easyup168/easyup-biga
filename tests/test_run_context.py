"""RunContext 契约测试（设计文档 §4）。

RunContext 是运行身份的值对象 —— 五个身份拆开之后打成一个包。
和其他契约对象一样：非法状态在 `__post_init__` 里**拒绝构造**，冻结不可改。
"""

from __future__ import annotations

import dataclasses

import pytest

from _contract import RunContext, new_run_context, new_run_id, new_trigger_id, now_cn


def _ok(**kw) -> RunContext:
    base = dict(
        trigger_id="cli-20260922T100000-abcd1234",
        decision_id="BIGA-20260922-007",
        run_id="deadbeef" * 4,
        evidence_set_id=None,
        origin="cli",
        non_interactive=True,
        created_at=now_cn().isoformat(),
    )
    base.update(kw)
    return RunContext(**base)


class TestConstruction:
    def test_合法对象可构造(self):
        c = _ok()
        assert c.origin == "cli" and c.non_interactive is True

    def test_decision_id可空(self):
        """🔴 legacy 路径在 RECEIVED 时还不知道号 —— None 必须合法。"""
        assert _ok(decision_id=None).decision_id is None

    def test_decision_id格式非法被拒(self):
        with pytest.raises(ValueError, match="decision_id"):
            _ok(decision_id="乱写的号")

    def test_evidence_set_id可空(self):
        assert _ok(evidence_set_id=None).evidence_set_id is None

    @pytest.mark.parametrize("bad", ["", "  ", "cli batch", "has\ttab"])
    def test_trigger_id必须非空无空白(self, bad):
        with pytest.raises(ValueError, match="trigger_id"):
            _ok(trigger_id=bad)

    @pytest.mark.parametrize("bad", ["", "  ", "run id with space"])
    def test_run_id必须非空无空白(self, bad):
        with pytest.raises(ValueError, match="run_id"):
            _ok(run_id=bad)

    def test_origin必须在白名单(self):
        with pytest.raises(ValueError, match="origin"):
            _ok(origin="邮件")

    @pytest.mark.parametrize("good", ["cli", "feishu", "cron"])
    def test_三种来源都合法(self, good):
        assert _ok(origin=good).origin == good

    def test_non_interactive必须是bool(self):
        # 🔴 0/1 都不是 bool —— 它决定 ask_user 会不会死锁，含糊不得。
        with pytest.raises(TypeError, match="non_interactive"):
            _ok(non_interactive=1)  # type: ignore[arg-type]

    def test_created_at必须带时区(self):
        with pytest.raises(ValueError, match="时区|created_at"):
            _ok(created_at="2026-09-22T10:00:00")  # naive

    def test_created_at必须是合法ISO(self):
        with pytest.raises(ValueError, match="created_at"):
            _ok(created_at="昨天下午")


class TestFrozen:
    def test_不可改字段(self):
        c = _ok()
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.origin = "feishu"  # type: ignore[misc]

    def test_不可改decision_id(self):
        c = _ok()
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.decision_id = "BIGA-20260922-999"  # type: ignore[misc]


class TestSerialization:
    def test_往返(self):
        c = _ok()
        assert RunContext.from_dict(c.to_dict()) == c

    def test_from_dict容忍缺省的可空字段(self):
        d = {
            "trigger_id": "cli-x", "run_id": "r" * 8, "origin": "cli",
            "non_interactive": True, "created_at": now_cn().isoformat(),
        }
        c = RunContext.from_dict(d)
        assert c.decision_id is None and c.evidence_set_id is None


class TestFactories:
    def test_new_run_id唯一(self):
        assert new_run_id() != new_run_id()

    def test_new_trigger_id带来源(self):
        assert new_trigger_id("feishu").startswith("feishu-")

    def test_工厂默认非交互且无号(self):
        c = new_run_context(origin="cli", non_interactive=True)
        assert c.decision_id is None
        assert c.run_id and c.trigger_id and c.created_at
        # created_at 是带时区的北京时间
        assert "+08:00" in c.created_at

    def test_工厂可带入号与trigger(self):
        c = new_run_context(origin="feishu", non_interactive=False,
                            decision_id="BIGA-20260922-003", trigger_id="evt-xyz")
        assert c.decision_id == "BIGA-20260922-003"
        assert c.trigger_id == "evt-xyz"
        assert c.non_interactive is False
