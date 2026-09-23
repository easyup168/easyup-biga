"""写边界重校验 + 严格 JSON —— 设计文档 §6 批 A-I（A3 + A4）。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：`save_verdict` / `save_card` / `save_raw_snapshot` 在 INSERT 之前的
  重校验（构造合法 → 事后改字段 → 写入必须拒绝）、NaN/Infinity 一律拒绝、
  `payload_sha256` 的历史哈希向量不变
- 不覆盖：`MissingItem` 值对象化本身、`frozen=True` 挡赋值这件事——
  那两条的探针在 `tests/test_contract_behavior.py`（批 A-II 的 A1/A2）。
  这里两处篡改改用 `object.__setattr__`只是因为 A2 之后 `.append()`
  已经不再可用（tuple 没有这个方法），手法换了，但本文件要证明的事
  没变：写边界重校验挡得住「绕过对象自身保护改字段」这整类篡改

背景
----
`save_verdict(非法对象)` 曾经会成功落库，`load_verdict(同一行)` 才在读取时
炸出 `ValueError`（`from_dict` 复校验，铁律 1）。写入不校验、读取校验，
而 `agent_verdicts` / `decision_records` 都是只追加表 —— 一次误写就让那次
决策永久无法回放。本文件钉死「写入时就必须拒绝」。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from datetime import timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))

from _contract import (  # noqa: E402
    STANCE_VOCAB,
    AgentVerdict,
    DecisionCard,
    Evidence,
    MissingItem,
    new_task_id,
    now_cn,
)
from _store import connect, init_schema, load_verdict, save_card, save_raw_snapshot, save_verdict  # noqa: E402
from _store.db import payload_sha256  # noqa: E402

TID = new_task_id(1, day="20260922")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "t.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    return p


def _legal_verdict(**kw) -> AgentVerdict:
    # contract-exempt: 构造真 dataclass 的 kwargs
    base = dict(
        task_id=TID, agent="market", status="completed", verdict="PASS",
        result={}, data_completeness=0.9, evidence=[], stance="放量上涨",
    )
    base.update(kw)
    return AgentVerdict(**base)


def _legal_buy_card(**kw) -> DecisionCard:
    did = kw.pop("decision_id", TID)
    # 🔴 满 roster：BUY 卡要求 missing == ()，而 F-6 之后「roster 不全
    #    且 missing 为空」会被 _check_roster 拒绝——两条约束只有「roster
    #    本身齐全」能同时满足，不能靠塞 missing 绕过（那会先撞上铁律 2）。
    verdicts = [_legal_verdict(task_id=did, agent=a, stance="无法判定")
                for a in sorted(STANCE_VOCAB)]
    # contract-exempt: 同上
    base = dict(
        decision_id=did, status="BUY", headline="核心矛盾一句话",
        verdicts=verdicts, synthesis="", model_ref="anthropic/claude-sonnet-5",
    )
    base.update(kw)
    return DecisionCard(**base)


class TestP1WriteBoundaryRejectsTamperedVerdict:
    """P1：合法构造 → 事后直接改字段（绕过 `__post_init__`）→ 写入必须拒绝。

    ⚠️ 这条探针写于批 A-I，那时 `missing` 还是 `list`，`v.missing.append(...)`
    不重跑 `__post_init__` 就能悄悄塞入非法状态。批 A-II 的 A2 把 `missing`
    换成了 `tuple` 并把 `AgentVerdict` 冻结 —— `.append()` 现在直接
    `AttributeError`（见 `test_contract_behavior.py::TestVerdictFrozen`），
    那条路已经从语法上被堵死了。

    但 frozen 挡得住的只是「正常途径」。`object.__setattr__` 能绕开任何
    frozen dataclass（Python 自己实现 `__init__` 时用的就是它），
    这里改用它模拟「有人绕过对象自己的保护，硬改了字段」——
    写边界重校验挡的正是**这种**篡改，不是某一种具体的 Python 语法。
    """

    def test_事后追加缺失项却仍是PASS会被拒(self, db):
        v = _legal_verdict()
        assert v.verdict == "PASS" and v.missing == ()
        object.__setattr__(
            v, "missing", (MissingItem("事后塞的", "market.turnover.unavailable"),))
        with pytest.raises(ValueError, match="铁律 1"):
            save_verdict(v, path=db)


class TestP2WriteBoundaryRejectsTamperedCard:
    """P2：合法的 BUY 卡 → 事后塞入缺失项 → 写入必须拒绝（铁律 2）。

    🔴 特意不用「装着外来判定」这种身份类篡改 —— `save_card` 里那道手写的
    `foreign` 检查已经管得到身份，用它做探针测不出**新加的**这一层。
    这里选的是 `foreign` 检查完全不查的维度（missing 与 status 的一致性），
    红了才能证明是这一批新加的重校验在起作用，不是蹭了旧检查的光。

    ⚠️ 篡改手法同 P1：A2 之后 `missing` 是 `tuple` 且 `DecisionCard` 已冻结，
    `.append()` 不再是一条可用的路（见 `TestCardFrozen`），改用
    `object.__setattr__` 绕过对象自身的保护。
    """

    def test_BUY卡事后被塞入缺失项会被拒(self, db):
        card = _legal_buy_card()
        assert card.status == "BUY" and card.missing == ()
        object.__setattr__(
            card, "missing", (MissingItem("事后塞的", "market.turnover.unavailable"),))
        with pytest.raises(ValueError, match="铁律 2"):
            save_card(card, path=db)


_BAD_FLOATS = [float("nan"), float("inf"), float("-inf")]


class TestP4StrictJSON:
    """P4：NaN / Infinity / -Infinity 一律在写入前拒绝，不许进库。"""

    @pytest.mark.parametrize("bad", _BAD_FLOATS, ids=["nan", "inf", "-inf"])
    def test_save_verdict拒绝result里的非法浮点(self, db, bad):
        t = now_cn()
        v = AgentVerdict(
            task_id=TID, agent="market", status="completed", verdict="PASS",
            result={"weird": bad}, data_completeness=0.9,
            evidence=[Evidence(field="weird", source="test", value=1.0,
                               as_of=t - timedelta(seconds=5), retrieved_at=t)],
            stance="放量上涨",
        )
        with pytest.raises(ValueError):
            save_verdict(v, path=db)

    @pytest.mark.parametrize("bad", _BAD_FLOATS, ids=["nan", "inf", "-inf"])
    def test_save_card拒绝verdict里携带的非法浮点(self, db, bad):
        """独立于 save_verdict 之外单测 save_card 自己的边界。

        正常在线路径里 verdict 会先经过 `save_verdict` 单独拒绝；
        但合成阶段是从内存里的 `AgentVerdict` 对象直接组卡，
        没有规则保证它一定先落过库 —— `save_card` 自己也必须挡得住。
        """
        t = now_cn()
        v = AgentVerdict(
            task_id=TID, agent="market", status="completed", verdict="PASS",
            result={"weird": bad}, data_completeness=0.9,
            evidence=[Evidence(field="weird", source="test", value=1.0,
                               as_of=t - timedelta(seconds=5), retrieved_at=t)],
            stance="放量上涨",
        )
        # 🔴 只有 1 个 agent，另外 5 个天然缺席——F-8 之后 roster 判据按
        #    计数比较，给 5 条占位 missing 才够（本测试不测 roster）。
        card = DecisionCard(decision_id=TID, status="WAIT", headline="h",
                            verdicts=[v], synthesis="", model_ref="m",
                            missing=[f"占位{i}——本文件不测 roster" for i in range(5)])
        with pytest.raises(ValueError):
            save_card(card, path=db)

    @pytest.mark.parametrize("bad", _BAD_FLOATS, ids=["nan", "inf", "-inf"])
    def test_save_raw_snapshot拒绝非法浮点(self, db, bad):
        with pytest.raises(ValueError):
            save_raw_snapshot(source="test:probe", as_of="2026-09-22T10:00:00+08:00",
                              retrieved_at="2026-09-22T10:00:01+08:00",
                              payload={"x": bad}, raw_text="[]", path=db)


class TestP5HistoricalHashVectorsUnchanged:
    """P5：`payload_sha256` 的输出必须与「改动之前」生成的向量逐字节一致。

    ⚠️ `tests/fixtures/payload-sha256-vectors.json` 是在本批**任何代码改动
    之前**用当时的实现生成、并立刻 `git add` 的（diff 可证明它早于改动）。
    这里只读、只比对，绝不重新生成 —— 重新生成就是 new == new，什么都没测。
    """

    def test_历史向量逐条吻合(self):
        vectors = json.loads(
            (REPO / "tests/fixtures/payload-sha256-vectors.json")
            .read_text(encoding="utf-8"))
        assert vectors, "向量文件是空的，这条测试就等于没测"
        for case in vectors:
            assert payload_sha256(case["payload"]) == case["sha256"], (
                f"历史哈希对不上了：{case['payload']!r} —— "
                "payload_sha256 的 separators/allow_nan 之外的任何参数变化"
                "都会静默改变所有历史哈希"
            )


class TestVerdictContentShaIsHashOfStoredText:
    """评审 F-3：`agent_verdicts.content_sha256` 锚定的是「存量文本」，
    不是「对象的规范形式」。

    `_canonical_dumps` 的格式不是冻结的（这一批就刚加过 `separators`）。
    如果批 A-II 的 `VerdictRef` 核对改成「重新序列化对象再比对」而不是
    「直接比对存量 `verdict_json` 文本的哈希」，这一批之前落库的行会
    集体核对不上，而且是静默的——两串 sha256 都「看起来正常」。

    ⇒ 钉死正确的核对方式：`content_sha256` 永远等于**当时写入的那段
    `verdict_json` 文本**的 sha256，而不是「今天重新序列化这个对象」的
    sha256。前者任何时候都成立，后者只在序列化格式从未变过时才成立。
    """

    def test_content_sha256是存量文本的哈希不是对象重算的哈希(self, db):
        vid = save_verdict(_legal_verdict(), path=db)
        with connect(db, readonly=True) as conn:
            row = conn.execute(
                "SELECT verdict_json, content_sha256 FROM agent_verdicts "
                "WHERE verdict_id=?", (vid,)).fetchone()

        # 正确的核对方式：直接哈希存量文本。
        assert row["content_sha256"] == hashlib.sha256(
            row["verdict_json"].encode("utf-8")).hexdigest()

        # 危险的核对方式（A-II 不该这么写）：从读回的对象重新序列化。
        # 今天两者恰好相等——但这只是因为序列化格式自这行落库以来没再变过，
        # 不是因为这个哈希本来就该由对象重算得出。
        reloaded = load_verdict(vid, path=db)
        recomputed = hashlib.sha256(
            json.dumps(reloaded.to_dict(), ensure_ascii=False, sort_keys=True,
                       allow_nan=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        assert recomputed == row["content_sha256"], (
            "今天这两者相等只是巧合（序列化格式还没变过一次）——\n"
            "  真正的不变量是「content_sha256 == sha256(存量 verdict_json 文本)」，\n"
            "  不是「content_sha256 == sha256(重新序列化对象)」。\n"
            "  A-II 的 VerdictRef 核对必须走前者，否则序列化格式一旦再变，\n"
            "  这一批之前落库的行会集体核对不上。"
        )
