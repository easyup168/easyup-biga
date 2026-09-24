"""递归冻结 —— 契约对象构造之后，内容不能再被外部改动（批 R，评审 E-17）。

这些测试守的是一个很容易被读成「已经做过了」的洞：契约类早就是
`@dataclass(frozen=True)` 且 `result` 包了 `MappingProxyType`，看起来严丝合缝，
**但两道都只盖住第一层**。嵌套的 list/dict 仍然是外部那个对象本身。

🔴 要害是时序：铁律在 `__post_init__` 里校验，而穿透发生在那之后 ——
落库的内容与被校验的内容不是同一份，且全程不报错。

实测（2026-09-24 生产库 3152 条证据）：464 条（14.7%）的 `value` 是 dict/list。
"""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

from _contract import AgentVerdict, Evidence, FactBundle, now_cn  # noqa: E402

sys.path.insert(0, str(REPO / "src"))
from easyup_biga.domain._freeze import deep_freeze, thaw  # noqa: E402
from easyup_biga.domain.verdict import _same_value  # noqa: E402

TASK = "BIGA-20260924-001"


def _ev(field: str, value, *, at: datetime | None = None) -> Evidence:
    t = at or (now_cn() - timedelta(seconds=60))
    return Evidence(field=field, source="probe:x", value=value,
                    as_of=t, retrieved_at=t, calc_version="v1", raw_hash="a" * 64)


def _fb(result, evidence) -> FactBundle:
    return FactBundle(task_id=TASK, agent="market", status="completed", verdict="PASS",
                      result=result, data_completeness=1.0, evidence=tuple(evidence))


class TestE17外部改动不能穿透:
    def test_evidence_value_嵌套list改不动(self):
        payload = {"items": [1, 2, 3]}
        e = _ev("f", payload)
        payload["items"].append(999)
        assert e.value["items"] == (1, 2, 3)

    def test_evidence_value_不能被事后加键(self):
        """比嵌套更糟的一种：`value` 原本连第一层都没包，外部能直接塞新键。"""
        payload = {"a": 1}
        e = _ev("f", payload)
        payload["injected"] = True
        assert "injected" not in e.value

    def test_factbundle_result_嵌套层改不动(self):
        inner = [1, 2]
        fb = _fb({"k": inner}, [_ev("k", inner)])
        inner.append(777)
        assert fb.result["k"] == (1, 2)

    def test_result与evidence共享同一对象时也挡得住(self):
        """🔴 生产里这两处**经常是同一个对象** —— 一次原地 mutate 同时改两边，
        于是批 P 那条「证据值必须与 result 对得上」的交叉校验永远相等。
        一道交叉校验被「两个指针指同一处」架空，是最难看出来的那种绿。
        """
        inner = [1, 2]
        fb = _fb({"k": inner}, [_ev("k", inner)])
        inner.append(777)
        assert fb.result["k"] == (1, 2)
        assert fb.evidence[0].value == (1, 2)

    def test_agentverdict同样受保护(self):
        inner = {"deep": [1]}
        v = AgentVerdict(task_id=TASK, agent="market", status="completed", verdict="PASS",
                         result={"k": inner}, data_completeness=1.0,
                         evidence=(_ev("k", inner),))
        inner["deep"].append(2)
        inner["new"] = "x"
        assert v.result["k"]["deep"] == (1,)
        assert "new" not in v.result["k"]

    def test_嵌套写入直接抛错而不是静默生效(self):
        fb = _fb({"k": {"a": 1}}, [_ev("k", {"a": 1})])
        with pytest.raises(TypeError):
            fb.result["k"]["a"] = 2


class TestE17序列化逐字节不变:
    """本改动能安全落地的前提：**卡片与落库的字节不变**，只有内存形态变了。"""

    def test_result序列化与冻结前一致(self):
        raw = {"lst": [1, 2], "dct": {"b": [3]}, "s": "x", "n": 1.5}
        fb = _fb(raw, [_ev(k, v) for k, v in raw.items()])
        assert json.dumps(fb.to_dict()["result"], sort_keys=True) == json.dumps(raw, sort_keys=True)

    def test_evidence_value序列化与冻结前一致(self):
        raw = {"items": [1, {"z": [2]}]}
        assert json.dumps(_ev("f", raw).to_dict()["value"], sort_keys=True) == \
               json.dumps(raw, sort_keys=True)

    def test_往返稳定(self):
        raw = {"lst": [1, 2], "dct": {"b": [3]}}
        v = AgentVerdict(task_id=TASK, agent="market", status="completed", verdict="PASS",
                         result=raw, data_completeness=1.0,
                         evidence=tuple(_ev(k, val) for k, val in raw.items()))
        once = v.to_dict()
        assert AgentVerdict.from_dict(once).to_dict() == once


class TestE17不能把批P的门重新打开:
    """🔴 这条守的是本批次自己差点造成的回归，不是上游的洞。

    `_same_value` 取 canonical JSON 判等（`1` 与 `1.0` 算不同，与回放同口径）。
    递归冻结之后它拿到的是 mappingproxy —— `json.dumps` 不认，抛 TypeError，
    退回 `==`，于是 `1 == 1.0` 又成立了。**全程不报错**，批 P 的守卫静默失效。
    """

    def test_冻结后仍按canonical_json判等(self):
        assert _same_value(deep_freeze({"n": 1}), deep_freeze({"n": 1.0})) is False

    def test_冻结后同值仍判相等(self):
        assert _same_value(deep_freeze({"n": [1, 2]}), deep_freeze({"n": [1, 2]})) is True

    def test_证据值与result不符仍然被拒(self):
        """端到端：绕过 `_same_value` 单测，确认铁律本身还在。"""
        with pytest.raises(ValueError):
            _fb({"k": [1, 2]}, [_ev("k", [1, 2, 3])])


class TestE17冻结与解冻本身:
    def test_各容器类型(self):
        f = deep_freeze({"d": {"x": 1}, "l": [1], "t": (1,), "s": {1}})
        assert isinstance(f["l"], tuple) and isinstance(f["t"], tuple)
        assert isinstance(f["s"], frozenset)
        with pytest.raises(TypeError):
            f["d"]["x"] = 2

    def test_标量与字符串原样返回不被拆成字符(self):
        assert deep_freeze("abc") == "abc"
        assert deep_freeze(None) is None
        assert deep_freeze(1.5) == 1.5

    def test_thaw是逆运算(self):
        raw = {"a": [1, {"b": [2, 3]}], "c": "str", "d": None}
        assert thaw(deep_freeze(raw)) == raw

    def test_深层嵌套也冻(self):
        f = deep_freeze({"a": [{"b": [{"c": [1]}]}]})
        assert f["a"][0]["b"][0]["c"] == (1,)


class TestE17不许再出现第二份浅冻结:
    """防 L-3：将来加第四个契约类时，不能再手写一份只盖第一层的归一化。"""

    def test_契约层没有裸的MappingProxyType浅包(self):
        bad = []
        for p in (REPO / "src" / "easyup_biga" / "domain").glob("*.py"):
            if p.name == "_freeze.py":
                continue
            for i, line in enumerate(p.read_text().splitlines(), 1):
                if "MappingProxyType(" in line and not line.lstrip().startswith("#"):
                    bad.append(f"{p.name}:{i}: {line.strip()}")
        assert not bad, (
            "契约层出现了 MappingProxyType 的直接使用 —— 冻结只有一份实现，\n"
            "走 `_freeze.deep_freeze`。手写的那份只盖第一层，正是 E-17 的洞：\n  "
            + "\n  ".join(bad))
