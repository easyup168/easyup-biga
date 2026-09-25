"""P3-11..P3-17 · 回放 / 血缘审计 / 降级 / 验收账本 / 发布闸门。

覆盖 / 不覆盖
-------------
- 覆盖：从一份**真造出来的** EvidenceSet 出发做离线回放与 PIT 审计、
  provider 降级链的顺序与出处、验收账本的哈希链、发布闸门两类结论分开算
- **不覆盖**：真实上线证据（连续五个交易日、真降级、真隔离）——
  那些按定义只能在生产里发生，代码只负责在它们发生时认得出来

🔴 本文件里没有一条是「函数存在性」断言
---------------------------------------
外部实现包里那批 P3-4..P3-10 的测试是靠 `"read_parquet" in source` 这类写法过的 ——
把被测函数整个删掉，它们照样绿。所以这里每条都必须**调用**它，并对结果取值。
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from easyup_biga.application import SnapshotCoordinator
from easyup_biga.data.contracts import ProviderRole
from easyup_biga.data.acceptance import (
    DRILL_TYPES,
    REQUIRED_EOD_DAYS,
    evaluate_acceptance,
    load_acceptance_events,
    record_acceptance_event,
)
from easyup_biga.data.drills import raw_tamper_drill
from easyup_biga.data.failover import (
    ProviderChainExhausted,
    execute_with_fallback,
    provider_chain,
)
from easyup_biga.data.file_store import FileStore, FileStoreError
from easyup_biga.data.lineage_audit import audit_evidence_set_point_in_time
from easyup_biga.data.manifest_compat import validate_evidence_manifest
from easyup_biga.data.replay import (
    NetworkForbiddenError,
    ReplayIntegrityError,
    build_replay_bundle,
    network_forbidden,
    offline_replay_bundle,
)
from easyup_biga.persistence import init_schema
from easyup_biga.providers import parse_index_daily

TID = "BIGA-20260302-001"


def _rows(symbol: str, n: int) -> list[dict]:
    off = 0.0 if symbol == "sh000001" else 1000.0
    base = date(2026, 3, 2)
    return [
        {
            "day": (base + timedelta(days=i)).strftime("%Y-%m-%d"),
            "open": 3000.0 + off + i, "high": 3010.0 + off + i,
            "low": 2990.0 + off + i, "close": 3000.0 + off + i,
            "volume": 10_000_000 + i,
        }
        for i in range(n)
    ]


def _sina(symbol: str, *, bars: int):
    rows = _rows(symbol, bars)
    return parse_index_daily(symbol, rows, raw_text=json.dumps(rows))


def _frozen_evidence_set(db):
    init_schema(db)
    coord = SnapshotCoordinator(fetcher=_sina, path=db)
    return coord.freeze_index_daily(TID, ["sh000001", "sz399106"], bars=60)


# ── P3-11 回放 ──────────────────────────────────────────────────────────────
def test_回放走的是冻结的那串id并重算哈希(tmp_path):
    db = tmp_path / "biga.db"
    esid = _frozen_evidence_set(db)

    bundle = offline_replay_bundle(esid, path=db, data_root=str(tmp_path / "data"))

    assert bundle.evidence_set_id == esid
    assert bundle.manifest_version == "2"
    ref = bundle.datasets["cn.index.daily_bars"]
    assert ref.snapshot_id and ref.data_version == 1
    assert len(ref.partition_ids) == 1
    # 两个 symbol 的旧 raw 行都被重算过 —— 不是只看了一眼 manifest
    assert len(bundle.legacy_raw_snapshot_ids) == 2


def test_raw正文被改过就拒绝回放(tmp_path, monkeypatch):
    """探针：正文与记录的哈希对不上，回放必须抛，而不是照常给出清单。

    🔴 这里用 monkeypatch 而不是 `UPDATE raw_market_snapshot`，是因为**库本身
    改不动** —— 只追加由触发器强制，`UPDATE` 当场 `AppendOnlyViolation`。
    这恰恰说明这条回放校验防的不是「有人改了库」，而是「字节在库外的某一段
    路上变了」：备份恢复、跨机拷贝、文件系统损坏。所以模拟点放在**读取边界**
    才对得上它真正要防的场景。
    """
    import easyup_biga.data.replay as replay_mod

    db = tmp_path / "biga.db"
    esid = _frozen_evidence_set(db)
    assert build_replay_bundle(esid, path=db, data_root=str(tmp_path / "data"))

    real = replay_mod.load_raw_snapshot

    def _drifted(snapshot_id, *, path=None):
        row = dict(real(snapshot_id, path=path))
        row["raw_text"] = (row.get("raw_text") or "") + " "   # 一个空格就够
        return row

    monkeypatch.setattr(replay_mod, "load_raw_snapshot", _drifted)
    with pytest.raises(ReplayIntegrityError, match="哈希漂移"):
        build_replay_bundle(esid, path=db, data_root=str(tmp_path / "data"))


def test_manifest没记sha时_raw自存的哈希是唯一防线(tmp_path, monkeypatch):
    """两道哈希检查互为冗余 —— 而这条构造出**只有一道能生效**的场景。

    🔴 为什么值得单独一条：manifest v1 的老条目只有 `snapshot_id`，没有
    `content_sha256`。那些证据集的字节完整性**全靠 raw 行自存的那个 sha**。
    不单独钉住它，把它删掉时没有任何测试会红（另一道会替它接住今天的用例），
    而线上那批老证据集从此裸奔。
    """
    import easyup_biga.data.replay as replay_mod

    db = tmp_path / "biga.db"
    esid = _frozen_evidence_set(db)
    real_es = replay_mod.load_evidence_set
    real_raw = replay_mod.load_raw_snapshot

    def _v1_style(evidence_set_id, *, path=None):
        es = dict(real_es(evidence_set_id, path=path))
        manifest = json.loads(json.dumps(es["manifest"]))
        for entry in manifest["symbols"].values():
            entry.pop("content_sha256", None)   # 回到 v1 的样子
        es["manifest"] = manifest
        return es

    def _drifted(snapshot_id, *, path=None):
        row = dict(real_raw(snapshot_id, path=path))
        row["raw_text"] = (row.get("raw_text") or "") + " "
        return row

    monkeypatch.setattr(replay_mod, "load_evidence_set", _v1_style)
    monkeypatch.setattr(replay_mod, "load_raw_snapshot", _drifted)
    with pytest.raises(ReplayIntegrityError, match="旧 raw 快照哈希漂移"):
        build_replay_bundle(esid, path=db, data_root=str(tmp_path / "data"))


def test_manifest自述的哈希与raw实算不符也拒绝(tmp_path, monkeypatch):
    """与上一条**不是同一道检查**。

    上一条动的是正文 ⇒ 撞的是「raw 行自己存的 sha 与实算不符」。
    这一条正文不动、只让 manifest 记着另一个 sha ⇒ 撞的是「EvidenceSet 当初
    记下的和今天算出来的不是一份东西」。两道都得有，因为它们坏的方式不同：
    前者是字节被动过，后者是**清单与字节从一开始就没对齐**（拼错了证据集）。
    """
    import easyup_biga.data.replay as replay_mod

    db = tmp_path / "biga.db"
    esid = _frozen_evidence_set(db)
    real = replay_mod.load_evidence_set

    def _wrong_manifest(evidence_set_id, *, path=None):
        es = dict(real(evidence_set_id, path=path))
        manifest = json.loads(json.dumps(es["manifest"]))
        for entry in manifest["symbols"].values():
            entry["content_sha256"] = "0" * 64
        es["manifest"] = manifest
        return es

    monkeypatch.setattr(replay_mod, "load_evidence_set", _wrong_manifest)
    with pytest.raises(ReplayIntegrityError, match="manifest 哈希漂移"):
        build_replay_bundle(esid, path=db, data_root=str(tmp_path / "data"))


def test_回放拒绝未知的证据集(tmp_path):
    db = tmp_path / "biga.db"
    init_schema(db)
    with pytest.raises(ReplayIntegrityError, match="未知的 evidence_set_id"):
        build_replay_bundle("es-not-there", path=db, data_root=str(tmp_path))


def test_禁网围栏是fail_closed():
    with network_forbidden():
        with pytest.raises(NetworkForbiddenError):
            __import__("socket").create_connection(("127.0.0.1", 1), timeout=0.01)


def test_禁网围栏退出后会还原():
    """探针：围栏若不还原，后面所有用到 socket 的测试都会被它污染。"""
    import socket
    before = socket.create_connection
    with network_forbidden():
        assert socket.create_connection is not before
    assert socket.create_connection is before


# ── P3-12 PIT 审计 ──────────────────────────────────────────────────────────
def test_PIT审计确认冻结引用没漂(tmp_path):
    db = tmp_path / "biga.db"
    esid = _frozen_evidence_set(db)
    audit = audit_evidence_set_point_in_time(esid, path=db, data_root=str(tmp_path / "data"))
    assert audit.ok, audit.errors
    assert any("精确冻结" in c for c in audit.checks)


def test_PIT审计查不下去时返回不通过而不是抛(tmp_path):
    """审计的契约是**返回一个结论**，不是让调用方去 try/except。

    ⚠️ 这里没法删快照来构造（`dataset_snapshots` 只追加，`DELETE` 会被触发器
    拦下），所以用「证据集根本不存在」这条同样走 `except` 分支的路径。
    """
    db = tmp_path / "biga.db"
    init_schema(db)
    audit = audit_evidence_set_point_in_time(
        "es-not-there", path=db, data_root=str(tmp_path / "data"))
    assert not audit.ok
    assert "未知的 evidence_set_id" in audit.errors[0]


# ── manifest 兼容 ───────────────────────────────────────────────────────────
def test_manifest_v1与v2都可回放():
    v1 = validate_evidence_manifest({"symbols": {"sh000001": {"snapshot_id": 7}}})
    assert v1.replayable and v1.version == "1"
    v2 = validate_evidence_manifest({
        "manifest_version": "2",
        "symbols": {"sh000001": {"snapshot_id": 7}},
        "datasets": {"cn.index.daily_bars": {"snapshot_id": "dss-1"}},
    })
    assert v2.replayable and v2.version == "2"


def test_manifest_v2缺snapshot_id判不可回放():
    got = validate_evidence_manifest({
        "manifest_version": "2", "datasets": {"cn.index.daily_bars": {}}})
    assert not got.replayable
    assert "cn.index.daily_bars" in got.errors[0]


def test_未来的manifest版本不装作能读():
    got = validate_evidence_manifest({"manifest_version": "9"})
    assert not got.replayable and "9" in got.errors[0]


# ── P3-15 降级链 ────────────────────────────────────────────────────────────
def test_降级链是PRIMARY到FALLBACK且不含校验源():
    chain = provider_chain("cn.trading_calendar")
    assert chain[0] == ("sina_calendar", ProviderRole.PRIMARY)
    assert [pid for pid, _role in chain] == ["sina_calendar", "szse"]


def test_降级后返回的是真正取到数的那个provider():
    """🔴 出处必须是实际赢的那个 —— 写成 primary 会让降级的那天看起来很正常。"""
    calls: list = []
    result = execute_with_fallback(
        "cn.trading_calendar",
        {
            "sina_calendar": lambda: (_ for _ in ()).throw(RuntimeError("primary down")),
            "szse": lambda: "fallback-ok",
        },
        on_attempt=calls.append,
    )
    assert result.value == "fallback-ok"
    assert result.provider_id == "szse"
    assert [x.succeeded for x in result.attempts] == [False, True]
    # 失败那次也被回调出去了 —— 落账的人才看得见「降过级」
    assert len(calls) == 2 and calls[0].error.startswith("RuntimeError")


def test_全挂时抛出且带上每一次失败():
    with pytest.raises(ProviderChainExhausted) as exc:
        execute_with_fallback("cn.trading_calendar", {})
    assert len(exc.value.attempts) == 2


def test_没有降级源的dataset链上只有primary():
    assert [pid for pid, _r in provider_chain("cn.index.daily_bars")] == ["sina"]


# ── P3-15 演练 ──────────────────────────────────────────────────────────────
def test_篡改演练真的改了文件并被抓到(tmp_path):
    result = raw_tamper_drill(work_root=tmp_path)
    assert result.passed and result.name == "RAW_TAMPER"
    assert "hash mismatch" in result.detail["detected_error"]


def test_文件被删也走同一个异常类型(tmp_path):
    """🔴 最彻底的篡改是删掉它。若它抛 FileNotFoundError，演练的 except 捕不到。"""
    store = FileStore(tmp_path)
    uri, sha, _ = store.write_raw_text("d.raw", "p", "20260926", "a", '{"x":1}')
    __import__("pathlib").Path(uri).unlink()
    with pytest.raises(FileStoreError, match="unreadable"):
        store.verify_file_hash(uri, sha)


# ── P3-16 验收账本 ──────────────────────────────────────────────────────────
def _fill_ledger(ledger, days):
    for day in days:
        record_acceptance_event(ledger, "EOD_COMPLETE", trade_date=day)
    for kind in DRILL_TYPES:
        record_acceptance_event(ledger, kind)


def test_账本链完整时闸门才过(tmp_path):
    ledger = tmp_path / "acceptance.jsonl"
    days = ["20260921", "20260922", "20260923", "20260924", "20260925"]
    _fill_ledger(ledger, days)
    status = evaluate_acceptance(
        load_acceptance_events(ledger), trading_days_between=lambda s, e: tuple(days))
    assert status.passed
    assert status.eod_days == tuple(days)
    assert all(status.drills.values())


def test_改过任何一行都验不过(tmp_path):
    ledger = tmp_path / "acceptance.jsonl"
    _fill_ledger(ledger, ["20260921", "20260922", "20260923", "20260924", "20260925"])
    rows = ledger.read_text(encoding="utf-8").splitlines()
    first = json.loads(rows[0])
    first["status"] = "FAIL"
    rows[0] = json.dumps(first, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    ledger.write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_acceptance_events(ledger)


def test_删掉中间一行会让链断掉(tmp_path):
    """探针：只验单行哈希是不够的 —— 删行不改任何一行的内容。"""
    ledger = tmp_path / "acceptance.jsonl"
    _fill_ledger(ledger, ["20260921", "20260922", "20260923", "20260924", "20260925"])
    rows = ledger.read_text(encoding="utf-8").splitlines()
    del rows[2]
    ledger.write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="链断了"):
        load_acceptance_events(ledger)


def test_没有日历就判不出连续性而不是猜(tmp_path):
    """🔴 R-3：算不出来必须说算不出来。周一到周五在 A 股不等于五个交易日。"""
    ledger = tmp_path / "acceptance.jsonl"
    _fill_ledger(ledger, ["20260921", "20260922", "20260923", "20260924", "20260925"])
    status = evaluate_acceptance(load_acceptance_events(ledger), trading_days_between=None)
    assert not status.passed
    assert any(str(REQUIRED_EOD_DAYS) in e for e in status.errors)


def test_日历里有休市日就不算连续(tmp_path):
    """记满五天，但日历说中间那天休市 ⇒ 不够连续。"""
    ledger = tmp_path / "acceptance.jsonl"
    days = ["20260921", "20260922", "20260923", "20260924", "20260925"]
    _fill_ledger(ledger, days)
    open_days = tuple(d for d in days if d != "20260923")
    status = evaluate_acceptance(
        load_acceptance_events(ledger), trading_days_between=lambda s, e: open_days)
    assert not status.passed


def test_未知事件类型当场拒绝(tmp_path):
    with pytest.raises(ValueError, match="未知的验收事件类型"):
        record_acceptance_event(tmp_path / "l.jsonl", "MADE_UP")


def test_EOD事件必须带交易日(tmp_path):
    with pytest.raises(ValueError, match="trade_date"):
        record_acceptance_event(tmp_path / "l.jsonl", "EOD_COMPLETE")
