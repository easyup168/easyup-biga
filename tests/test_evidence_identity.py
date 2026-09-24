"""Evidence 的身份与类别 —— 裁定 16 的批 1（只加字段，不强制）。

批 1 的范围**刻意很窄**：让证据「可以被引用」和「可以声明自己是哪一类」，
但**不要求**任何人去引用或声明。各 skill 的改造是批 2。

这里钉三组东西：
  1. 身份是内容寻址的，且**只由内容算出** —— 构造方传不进来
  2. `kind` / `input_evidence_ids` 的自洽校验
  3. 三段式的「旧卡可读」那一段 —— 历史证据没有这三个键，照样读得出来

⚠️ 冻结向量（`test_身份的冻结向量`）是本文件最重要的一条：`evidence_id` 的算法一改，
   全仓 id 集体变化，而 `input_evidence_ids` 里的引用是按旧算法存的 ——
   引用会**静默失效**。沿用 `payload_sha256` 历史向量的既有做法。
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

from _contract import Evidence, OriginRef, now_cn  # noqa: E402

from easyup_biga.domain.evidence import EVIDENCE_KINDS  # noqa: E402

CN = timezone(timedelta(hours=8))
T = datetime(2026, 9, 20, 15, 0, tzinfo=CN)


def _ev(**kw) -> Evidence:
    """构造一条测试证据。

    ⚠️ `raw_hash` 默认给值（批 5）：规则收紧到「声明了类别就必须说得出出处」之后，
    一条裸的 `kind="observed"` 证据本身就是非法的 —— 夹具不该再造它。
    要测「没有出处」的场景就显式传 `raw_hash=None`。
    """
    """构造一条测试证据。

    ⚠️ 逐个参数给默认值，**不先搭一个 dict 再 `Evidence(**d)`** —— 那种写法会被
    铁律 4 的守卫判成「手搓字典版契约」，而它判得对：一个带 field/source/value/
    as_of/retrieved_at 的字典就是第二套 Evidence 的形状，哪怕只活在测试里。
    """
    return Evidence(
        field=kw.pop("field", "f"),
        source=kw.pop("source", "probe:x"),
        value=kw.pop("value", 1),
        as_of=kw.pop("as_of", T),
        retrieved_at=kw.pop("retrieved_at", T),
        raw_hash=kw.pop("raw_hash", "d" * 64),
        **kw)


class Test身份是内容寻址的:
    def test_同内容同id(self):
        assert _ev().evidence_id == _ev().evidence_id

    @pytest.mark.parametrize("kw", [
        {"field": "g"}, {"source": "probe:y"}, {"value": 2},
        {"as_of": T - timedelta(seconds=1)}, {"retrieved_at": T + timedelta(seconds=1)},
        {"calc_version": "v2"}, {"label": "别的名字"}, {"raw_hash": "c" * 64},
        {"evidence_set_id": "ES-1"}, {"kind": "observed"},
    ])
    def test_任何一个字段变了id就变(self, kw):
        """🔴 逐字段参数化，而不是「改一个试试」—— 漏掉哪个字段没进哈希，
        表现是两条**不同**的证据拿到**同一个** id，而那是静默的。"""
        assert _ev(**kw).evidence_id != _ev().evidence_id

    def test_derived_from不同则id不同(self):
        assert _ev(kind="derived", derived_from=(OriginRef("evidence", "a" * 64),)).evidence_id != \
               _ev(kind="derived", derived_from=(OriginRef("evidence", "b" * 64),)).evidence_id

    def test_构造方传不进evidence_id(self):
        """允许外部传 id = 允许传一个与内容不符的 id，而「引用对不上」正是
        这套东西要防的事。所以它是 init=False。"""
        with pytest.raises(TypeError):
            Evidence(field="f", source="probe:x", value=1, as_of=T, retrieved_at=T,
                     evidence_id="a" * 64)

    def test_id是64位小写十六进制(self):
        from easyup_biga.domain.evidence import SHA256_RE
        assert SHA256_RE.match(_ev().evidence_id)

    def test_往返之后id不变(self):
        e = _ev(value={"a": [1, 2]}, kind="observed", raw_hash="b" * 64)
        assert Evidence.from_dict(e.to_dict()).evidence_id == e.evidence_id

    def test_第三方能从序列化结果独立算出同一个id(self):
        """🔴 身份必须是**公开序列化形式**的函数 —— 拿到 `verdict_json` 的人
        不依赖我们的代码也能自己验一遍 id 对不对。

        ⚠️ 这条替掉了一版**空测试**：原先写的是「`{"a":[1,2]}` 与 `{"a":(1,2)}`
        算出同一个 id」，而这两者经 `deep_freeze` 本就归一成同一个对象 ——
        它恒真，任何实现都能通过。sabotage 时才发现它什么也没守住。
        """
        import hashlib as _h, json as _j
        e = _ev(value={"a": [1, 2]}, kind="observed", raw_hash="b" * 64)
        d = e.to_dict()
        assert d.pop("evidence_id") == _h.sha256(_j.dumps(
            d, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=True).encode("utf-8")).hexdigest()


class Test身份的冻结向量:
    """🔴 算法一改，`input_evidence_ids` 里已存的引用会静默失效。改了这里就要
    回答「存量引用怎么办」，而不是顺手更新向量。"""

    def test_历史向量逐条吻合(self):
        """⚠️ 向量放在 `tests/fixtures/` 的 JSON 里，不写成源码里的字面量 ——
        沿用 `payload-sha256-vectors.json` 的既有做法。两个理由：

        1. 公开仓库审查把**裸的 64 位十六进制**一律判成疑似 Gateway token，
           豁免只认 `sha256:`/`raw_hash:` 这种键名。写成 `evidence_id == "<hex>"`
           会被拦下 —— 实测拦过（本批就是这么发现的）。
        2. 向量是**数据**不是代码，加一条不该改测试逻辑。
        """
        import json as _j
        vectors = _j.loads((REPO / "tests/fixtures/evidence-id-vectors.json")
                           .read_text(encoding="utf-8"))
        assert vectors, "向量文件是空的，这条测试就等于没测"
        for case in vectors:
            if "evidence" not in case:      # 说明条目（_why_changed 那种），跳过
                continue
            got = Evidence.from_dict(dict(case["evidence"])).evidence_id
            assert got == case["sha256"], (
                f"{case['note']}：身份算法变了。\n"
                f"  期望 {case['sha256']}\n  实得 {got}\n"
                "  改了算法，存量 input_evidence_ids 里的引用会**静默失效** —— "
                "先回答「那些引用怎么办」，不要顺手更新向量。")


class Test类别的自洽校验:
    @pytest.mark.parametrize("k", sorted(EVIDENCE_KINDS) + [None])
    def test_合法类别(self, k):
        # derived 另有一条铁律（必须说得出出处，批 3），给它一个 raw_hash 才构造得出来。
        extra = {"raw_hash": "a" * 64} if k == "derived" else {}
        assert _ev(kind=k, **extra).kind == k

    @pytest.mark.parametrize("k", ["observe", "DERIVED", "", "fact", 1])
    def test_非法类别被拒(self, k):
        with pytest.raises(ValueError):
            _ev(kind=k)

    def test_parameter不许带来源(self):
        """参数是我们自己的设定，没有数据输入。带了 ⇒ 要么它其实是 derived，
        要么这串来源是凑的 —— 两种都得当场说出来。"""
        with pytest.raises(ValueError):
            _ev(kind="parameter", derived_from=(OriginRef("evidence", "a" * 64),))

    @pytest.mark.parametrize("bad", ["", "xyz", "A" * 64, "a" * 63, 123])
    def test_内容哈希类来源的ref必须是合法指纹(self, bad):
        """⚠️ 只有 raw / evidence 的 ref 是内容哈希；verdict 是行号、fact 是定位串，
        统一校验成 sha256 会把合法的那两类误杀。"""
        with pytest.raises(ValueError):
            _ev(kind="derived", derived_from=(OriginRef("evidence", bad),))

    def test_verdict与fact的ref不套哈希格式(self):
        assert _ev(kind="derived", derived_from=(OriginRef("verdict", "41"),)).kind == "derived"
        assert _ev(kind="derived",
                   derived_from=(OriginRef("fact", "fact_trading_calendar/20260924"),)
                   ).kind == "derived"

    def test_来源被规范成tuple(self):
        e = _ev(kind="derived", derived_from=[OriginRef("evidence", "a" * 64)])
        assert e.derived_from == (OriginRef("evidence", "a" * 64),)

    def test_不许拿裸字符串当来源(self):
        """用构造器（evidence_origins / verdict_origins / …），别自己拼。"""
        with pytest.raises(ValueError):
            _ev(kind="derived", derived_from=("a" * 64,))

    def test_derived必须说得出出处(self):
        """🔴 批 3 翻转了批 1 的留白。派生值有两条合法出路，**不能两者皆无**：

        · 从**一份**原始响应算出来（MA、涨跌幅）⇒ `raw_hash` 指回那一份
        · 从**别的值**算出来（炸板率）⇒ `input_evidence_ids`

        规则不是「派生一律要 inputs」—— technical 的 `ma5` 是从整份 K 线算的，
        `raw_hash` 已经完整回答了它的出处，再要一串 id 才是硬凑。
        """
        with pytest.raises(ValueError) as ei:
            _ev(kind="derived", raw_hash=None)
        assert "出自什么" in str(ei.value)

    def test_derived两条出路各自成立(self):
        assert _ev(kind="derived", raw_hash="a" * 64).kind == "derived"
        assert _ev(kind="derived",
                   derived_from=(OriginRef("evidence", "b" * 64),)).kind == "derived"

    def test_未声明kind的历史证据不受这条约束(self):
        """三段式的「旧卡可读」：`kind=None` 是未声明，不是 derived。"""
        assert _ev().kind is None


class Test旧卡可读:
    def test_历史字典缺这三个键也读得出来(self):
        # 🔴 从**真的序列化结果**里删掉批 1 新增的三个键，而不是手写一个字典 ——
        #    手写的那份既会被铁律 4 判成第二套契约，也只是「我以为历史长这样」。
        legacy = _ev(field="market.x", source="sina:kline/sh000001", value=1.5,
                     calc_version="m/1", raw_hash="b" * 64).to_dict()
        for k in ("kind", "derived_from", "evidence_id"):
            legacy.pop(k)
        e = Evidence.from_dict(legacy)
        assert e.kind is None and e.derived_from == ()
        assert e.evidence_id

    def test_存量里的evidence_id不被信任而是重算(self):
        """存量那个值不进构造 ⇒ 不可能出现「id 与内容不符」的行被原样读回。"""
        d = _ev().to_dict()
        d["evidence_id"] = "0" * 64
        assert Evidence.from_dict(d).evidence_id == _ev().evidence_id


class Test身份标识的是内容不是行:
    def test_两份内容相同的证据拿到同一个id(self):
        """⚠️ 批 2 必读：生产库里有 1246 组这样的重复，来自**修订**
        （同一 (task, agent) 的第二行重述了同样的证据）。这是内容寻址的定义，
        不是缺陷 —— 但它意味着跨 verdict 引用时，`evidence_id` 回答的是
        「哪份内容」，不是「哪一行」。要定位到行得再带上 verdict。"""
        assert _ev().evidence_id == _ev().evidence_id
