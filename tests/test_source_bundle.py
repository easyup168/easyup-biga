"""P3-15 配套 · 源码包导出/恢复 与 CLI 顶层标志解析。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：`tools/verify/source_bundle.py` 的 export/restore 行为，
  以及 `biga-data` 顶层带值标志（`--db` / `--data-root`）的 argv 解析
- **不覆盖**：回放本身过没过。那是 `source_zip_replay_drill` 的事
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import zipfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "verify" / "source_bundle.py"
BIGA_DATA = REPO / "bin" / "biga-data"



# ── 顶层带值标志：值不是子命令 ──────────────────────────────────────────────
def test_顶层带值标志的值不会被当成子命令():
    """🔴 `--db data/biga.db snapshots ...` 曾经报 `未知子命令 'data/biga.db'`。

    旧实现用「不以 `-` 开头」挑位置参数，于是标志的**值**成了第一个位置参数。
    它比「报错了」更糟：错误信息把人引向「子命令拼错了」，
    而子命令是对的 —— 真正的原因是它前面有个带值的全局标志。
    """
    from easyup_biga.data.cli import _positional_tokens, build_parser

    _parser, _known, value_flags = build_parser()
    got = _positional_tokens(
        ["--db", "data/biga.db", "--data-root", "data", "snapshots", "cn.news.flash"],
        value_flags,
    )
    assert got[0] == "snapshots", f"第一个位置参数应是子命令，实际 {got!r}"


def test_带值全局标志后面的子命令真的能跑起来(tmp_path):
    """判据打在**进程的退出码**上，不在 `_positional_tokens` 的返回值上。

    ⚠️ 只测那个辅助函数是不够的：把 `main()` 改回旧的
    「不以 `-` 开头就算位置参数」，辅助函数仍然在、仍然对，
    而命令行照样坏 —— 探针实测过这一点（破坏后仍绿）。
    """
    from easyup_biga.persistence import init_schema

    db = tmp_path / "biga.db"
    init_schema(db)
    r = subprocess.run([str(BIGA_DATA), "--db", str(db), "snapshots", "cn.news.flash"],
                       capture_output=True, text=True, cwd=REPO, timeout=60)
    assert r.returncode == 0, f"退出码 {r.returncode}\nstderr: {r.stderr}"
    assert json.loads(r.stdout) == []


def test_等号写法不需要跳过下一个词():
    from easyup_biga.data.cli import _positional_tokens, build_parser

    _parser, _known, value_flags = build_parser()
    got = _positional_tokens(["--db=x.db", "snapshots", "cn.news.flash"], value_flags)
    assert got[0] == "snapshots"


def test_顶层带值标志清单与注册到parser的是同一份():
    """防 L-3：跳过用的那份表若与注册用的那份分家，漏改时不会报错。"""
    from easyup_biga.data.cli import _GLOBAL_VALUE_OPTIONS, build_parser

    _parser, _known, value_flags = build_parser()
    assert value_flags == frozenset(flag for flag, _ in _GLOBAL_VALUE_OPTIONS)


def test_未知子命令仍然报未知子命令():
    """P10b 的措辞不能被上面那条修复顺手改掉。"""
    r = subprocess.run([str(BIGA_DATA), "bogus"], capture_output=True, text=True,
                       cwd=REPO, timeout=60)
    assert r.returncode != 0
    assert "未知子命令 'bogus'" in r.stderr


# ── 导出 / 恢复 ────────────────────────────────────────────────────────────
@pytest.fixture()
def frozen_decision(tmp_path, monkeypatch):
    """造一份**真实形状**的冻结血缘：raw 文件 + Parquet 分区 + 快照 + 血缘行。

    ⚠️ 只桩掉最外层那次取数（L-12）—— 归一化、质量裁定、两阶段提交、
    账本写入全部走真实代码，否则这个 fixture 证明不了导出工具面对的
    是不是真实结构。
    """
    pytest.importorskip("pyarrow")
    import easyup_biga.data.decision_client as dc
    from easyup_biga.persistence import init_schema, save_evidence_set
    from easyup_biga.providers.eastmoney import BreadthResult

    # ⚠️ `data_root` 传**相对路径**，与生产路径一致 —— 绝对路径会让
    #    `storage_uri` 存成绝对的，那种包搬不动（导出会当场拒绝）。
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "biga.db"
    init_schema(db)
    save_evidence_set(evidence_set_id="es-bundle-test", decision_id=None,
                      manifest={"kind": "bundle-test"}, path=db)
    monkeypatch.setattr(
        dc, "_fetch_breadth",
        lambda: BreadthResult(
            advance=1200, decline=3000, flat=100,
            per_market=[{"code": "sh", "advance": 600, "decline": 1500, "flat": 50}],
            raw={"ok": True}, raw_text='{"ok":true}'))
    client = dc.DecisionDataClient(db_path=db, data_root="data")
    result = client.freeze_required(
        "es-bundle-test", ["cn.market.breadth"], trade_date="20260925")
    assert result.complete, result.errors
    return db, tmp_path


def _export(evidence_set_id, *, db, out, cwd):
    return subprocess.run(
        [sys.executable, str(TOOL), "export", evidence_set_id,
         "--db", str(db), "--out", str(out)],
        capture_output=True, text=True, cwd=cwd, timeout=120)


def test_导出的包只带这次决策引用到的字节(tmp_path, frozen_decision):
    """🔴 不扫整棵 `data/` —— 多塞进去的文件会让回放在一棵本不该跑通的树上跑通。

    那会把「我恢复对了」和「我什么都带上了」混为一谈，
    而后者根本证明不了离线回放是确定的。
    """
    db, root = frozen_decision
    out = tmp_path / "bundle.zip"
    r = _export("es-bundle-test", db=db, out=out, cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert "data/biga.db" in names, names
    assert "bundle_manifest.json" in names
    parquet = {n for n in names if n.endswith(".parquet")}
    assert parquet, f"数据面文件一个都没带上：{names}"
    # 冻结的是 breadth 这一个 dataset ⇒ 包里不该出现别的 dataset 的分区
    assert all("cn_market_breadth" in n for n in parquet), parquet


def test_导出的库是一致副本而不是cp(tmp_path, frozen_decision):
    """WAL 模式下 `cp` 会漏掉未 checkpoint 的事务，而漏掉的部分不报错。"""
    db, root = frozen_decision
    out = tmp_path / "bundle.zip"
    assert _export("es-bundle-test", db=db, out=out, cwd=root).returncode == 0
    extracted = tmp_path / "x"
    with zipfile.ZipFile(out) as zf:
        zf.extractall(extracted)
    from easyup_biga.persistence import connect

    with connect(extracted / "data" / "biga.db", readonly=True) as conn:
        n = conn.execute(
            "SELECT COUNT(*) n FROM evidence_set_datasets WHERE evidence_set_id=?",
            ("es-bundle-test",)).fetchone()["n"]
    assert n == 1, "恢复出来的库里查不到血缘行 —— 副本不一致"


def test_血缘引用的文件不在盘上要响亮失败(tmp_path, frozen_decision):
    """控制面说有、数据面已经没了 ⇒ 导出必须当场红，而不是打出一个残包。"""
    db, root = frozen_decision
    for item in (root / "data" / "lake").rglob("*.parquet"):
        item.unlink()
    r = _export("es-bundle-test", db=db, out=tmp_path / "y.zip", cwd=root)
    assert r.returncode != 0
    assert "找不到" in r.stdout + r.stderr


def test_导出不存在的evidence_set要响亮失败(tmp_path, frozen_decision):
    db, root = frozen_decision
    r = _export("es-does-not-exist", db=db, out=tmp_path / "x.zip", cwd=root)
    assert r.returncode != 0
    assert "没有冻结任何 dataset" in (r.stdout + r.stderr)


def test_恢复必须落在干净目录上(tmp_path):
    """⚠️ 往非空目录里摊开，分不清哪些字节来自包 —— 那会让回放的结论失去意义。"""
    fake = tmp_path / "bundle.zip"
    with zipfile.ZipFile(fake, "w") as zf:
        zf.writestr("bundle_manifest.json", json.dumps({"evidence_set_id": "es-x"}))
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "leftover.txt").write_text("x", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(TOOL), "restore", str(fake), "--into", str(dirty)],
        capture_output=True, text=True, cwd=REPO, timeout=60)
    assert r.returncode != 0
    assert "非空" in r.stdout + r.stderr


def test_绝对storage_uri要拒绝导出(tmp_path, monkeypatch):
    """🔴 绝对路径的包恢复不出来，而失败的方式是**通过**。

    恢复出来的库仍然指着导出那台机器上的目录。那个目录多半还在
    （就是刚导出的那台机器），于是回放读到的是**原树**的字节 ——
    它会报 PASS，而这个 PASS 与恢复出来那棵树毫无关系。
    「文件不存在」反而是好结局：至少它红了。
    """
    import easyup_biga.data.decision_client as dc
    from easyup_biga.persistence import init_schema, save_evidence_set
    from easyup_biga.providers.eastmoney import BreadthResult

    pytest.importorskip("pyarrow")
    db = tmp_path / "biga.db"
    init_schema(db)
    save_evidence_set(evidence_set_id="es-abs", decision_id=None,
                      manifest={"kind": "abs"}, path=db)
    monkeypatch.setattr(
        dc, "_fetch_breadth",
        lambda: BreadthResult(advance=1, decline=2, flat=3,
                              per_market=[{"code": "sh"}], raw={"ok": 1},
                              raw_text='{"ok":1}'))
    # 绝对 data_root ⇒ storage_uri 存成绝对路径
    client = dc.DecisionDataClient(db_path=db, data_root=str(tmp_path / "data"))
    assert client.freeze_required("es-abs", ["cn.market.breadth"],
                                  trade_date="20260925").complete

    r = subprocess.run(
        [sys.executable, str(TOOL), "export", "es-abs", "--db", str(db),
         "--out", str(tmp_path / "abs.zip")],
        capture_output=True, text=True, cwd=tmp_path, timeout=120)
    assert r.returncode != 0
    assert "绝对路径" in r.stdout + r.stderr
    assert not (tmp_path / "abs.zip").exists(), "拒绝了却还留下一个包"


def test_回放演练记下自己站在哪棵树上(tmp_path, frozen_decision):
    """🔴 `cwd` + `db_path` 是区分「真绿」与「读了原树的假绿」的唯一事实。

    分区 URI 是相对路径，按**进程工作目录**解析（与 `--data-root` 无关）。
    库指着恢复树、而工作目录在别处时，读到的是别处的字节 —— 且会 PASS。
    判定交给人，但账本必须记得下当初读的是哪棵树。
    """
    from easyup_biga.data.drills import source_zip_replay_drill

    db, root = frozen_decision
    result = source_zip_replay_drill("es-bundle-test", path=db, data_root="data")
    assert result.detail["cwd"] == str(root)
    assert result.detail["db_path"] == str(db.resolve())
