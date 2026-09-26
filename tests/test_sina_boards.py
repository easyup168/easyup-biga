"""`cn.sector.board_snapshot` 的降级源 —— 新浪板块排行。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：GBK + `var … = {…}` 的解析、字段位序、缺字段的表示、降级接线、
  以及 Evidence 的 `source` 写不写实际供数方
- **不覆盖**：真实网络
"""
from __future__ import annotations

import json

import pytest

from easyup_biga.providers.http import SourceError
from easyup_biga.providers.sina_boards import BOARD_KINDS, parse_boards

#: 🔴 这一行是 2026-09-26 从真实响应里取的，并与新浪实时行情**逐位对拍**过：
#:    `sz000829` 昨收 9.06 / 现价 9.32 ⇒ 涨幅 2.870%、涨跌额 0.260
#:    —— 与第 9、11 位吻合。猜错这个顺序不会报错，只会让领涨股的涨幅变成价格。
_REAL_ROW = ("gn_hwqc,华为汽车,97,25.049166666667,-0.59583333333333,"
             "-2.3233898745694,1486461364,28685781469,sz000829,2.870,9.320,"
             "0.260,天音控股")


def _payload(rows):
    return "var S_Finance_bankuai_class = " + json.dumps(
        {f"k{i}": r for i, r in enumerate(rows)}, ensure_ascii=False)


def test_字段位序与真实响应一致():
    result = parse_boards(_payload([_REAL_ROW]), "concept")
    board = result.boards[0]
    assert board.code == "gn_hwqc"
    assert board.name == "华为汽车"
    assert board.pct == pytest.approx(-2.3233898745694)
    assert board.leader == "天音控股", "领涨股取的是名称（与主源同口径）"


def test_这个源不给的三个字段写None而不是0():
    """🔴 写 0 会让「没有资金进出」和「没给这个字段」长得一模一样。

    消费方对前者会照常排名、对后者会上报 missing ——
    而「第一名」带着 `0.0 亿` 上卡读起来毫无破绽。
    （那条 missing 是外部评审 F6 逼出来的。）
    """
    board = parse_boards(_payload([_REAL_ROW]), "concept").boards[0]
    assert board.main_inflow is None
    assert board.advance is None and board.decline is None


def test_缺领涨股不当成错误():
    row = "gn_x,某板块,10,1,2,3.5,100,200"        # 只有 8 位
    board = parse_boards(_payload([row]), "concept").boards[0]
    assert board.pct == 3.5 and board.leader is None


def test_字段太少要响亮失败():
    with pytest.raises(SourceError, match="上游可能改了布局"):
        parse_boards(_payload(["a,b,c"]), "concept")


def test_涨跌幅不是数字要响亮失败():
    with pytest.raises(SourceError, match="涨跌幅不是数字"):
        parse_boards(_payload(["gn_x,某板块,10,1,2,不是数,100,200"]), "concept")


def test_没有var赋值说明被限流或改了形状():
    with pytest.raises(SourceError, match="没有 `var"):
        parse_boards("<html>请稍后再试</html>", "concept")


def test_未知kind不许猜():
    with pytest.raises(ValueError, match="未知 kind"):
        parse_boards(_payload([_REAL_ROW]), "area")


def test_只登记真的有消费方的kind():
    """⚠️ 新浪还给地域板块（31 个），但本仓库没有消费方 ⇒ 不登记。
    登记一个没人读的东西是 L-1 的形状。
    """
    assert set(BOARD_KINDS) == {"industry", "concept"}


# ── 降级接线 ───────────────────────────────────────────────────────────────
def test_板块有了真跑通过的备用源():
    from easyup_biga.data.failover import provider_chain

    assert [p for p, _ in provider_chain("cn.sector.board_snapshot")] == [
        "eastmoney", "sina_boards"]


def test_evidence的source写实际供数方():
    """🔴 skill 里曾经写死 `f"em:clist/{kind}"`。

    板块有了备用源之后，降级过的那天 Evidence 会指着一个**没供过数的源** ——
    那比缺字段更糟：缺字段会进 `missing[]`，说谎不会。
    """
    import importlib.util
    import pathlib

    path = (pathlib.Path(__file__).resolve().parents[1]
            / "skills" / "sector-calc" / "scripts" / "sector_calc.py")
    spec = importlib.util.spec_from_file_location("_sector_calc_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module._board_source("industry", "eastmoney") == "em:clist/industry"
    assert module._board_source("industry", "sina_boards") == "sina:bankuai/industry"
    # 没走数据层的直连路径只有东财那一条
    assert module._board_source("concept", None) == "em:clist/concept"


def test_前缀来自注册表而不是自己拼():
    """⚠️ `sina` / `sina_boards` / `sina_eod` 的前缀都是 `sina` —— 猜不得。"""
    from easyup_biga.data.provider_registry import (
        ProviderNotRegistered,
        source_prefix_of,
    )

    assert source_prefix_of("sina_boards") == "sina"
    assert source_prefix_of("eastmoney") == "em"
    with pytest.raises(ProviderNotRegistered):
        source_prefix_of("不存在的源")


def test_主源挂了由备用源供数且溯源写实际供数方(tmp_path, monkeypatch):
    """判据打在**冻结之后库里有什么**，不在「代码里有没有 outcome.provider_id」。"""
    import easyup_biga.data.decision_client as dc
    from easyup_biga.persistence import connect, init_schema, save_evidence_set

    pytest.importorskip("pyarrow")
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "biga.db"
    init_schema(db)
    save_evidence_set(evidence_set_id="es-bk", decision_id=None,
                      manifest={"kind": "t"}, path=db)
    monkeypatch.setattr(dc, "_fetch_boards",
                        lambda kind: (_ for _ in ()).throw(SourceError("主源 502")))
    monkeypatch.setattr(
        dc, "_sina_fetch_boards",
        lambda kind: parse_boards(_payload([_REAL_ROW]),
                                  "concept" if kind == "concept" else "industry"))
    client = dc.DecisionDataClient(db_path=db, data_root="data")
    assert client.freeze_required(
        "es-bk", ["cn.sector.board_snapshot"], trade_date="20260926").complete

    with connect(db, readonly=True) as conn:
        provider = conn.execute(
            "SELECT provider_id FROM dataset_partitions "
            "WHERE dataset_id='cn.sector.board_snapshot'").fetchone()["provider_id"]
        attempts = [(str(r["provider_id"]), str(r["provider_role"]), str(r["status"]))
                    for r in conn.execute(
                        "SELECT provider_id,provider_role,status FROM provider_attempts "
                        "ORDER BY attempt_no")]
    assert provider == "sina_boards"
    assert attempts == [("eastmoney", "PRIMARY", "FAILED_RETRYABLE"),
                        ("sina_boards", "FALLBACK", "SUCCEEDED")]


def test_降级读回来时带出实际供数方(tmp_path, monkeypatch):
    """`read_boards` 的第三项 —— skill 靠它写 Evidence 的 source。"""
    import easyup_biga.data.decision_client as dc
    from easyup_biga.persistence import init_schema, save_evidence_set

    pytest.importorskip("pyarrow")
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "biga.db"
    init_schema(db)
    save_evidence_set(evidence_set_id="es-rb", decision_id=None,
                      manifest={"kind": "t"}, path=db)
    monkeypatch.setattr(dc, "_fetch_boards",
                        lambda kind: (_ for _ in ()).throw(SourceError("主源 502")))
    monkeypatch.setattr(
        dc, "_sina_fetch_boards",
        lambda kind: parse_boards(_payload([_REAL_ROW]),
                                  "concept" if kind == "concept" else "industry"))
    client = dc.DecisionDataClient(db_path=db, data_root="data")
    client.freeze_required("es-rb", ["cn.sector.board_snapshot"], trade_date="20260926")

    _result, _raw_hash, served_by = client.read_boards("es-rb", "industry")
    assert served_by == "sina_boards"
