from easyup_biga.data.datasets.eod_daily_bars import normalize, quality
from easyup_biga.data.records import TradabilityStatus
from easyup_biga.data.datasets.tradability import derive
from easyup_biga.data.review_outcomes import calculate

def test_eod_normalize_and_quality():
    rows=[{'f12':'600000','f17':10,'f15':11,'f16':9,'f2':10.5,'f18':10,'f5':100,'f6':1000,'f4':.5,'f3':5}]
    bars=normalize(rows,'20260925','2026-09-25T15:10:00+08:00'); st,m,issues=quality(bars,1)
    assert bars[0].instrument_id=='600000.SH' and st.value=='COMPLETE' and not issues

def test_tradability_missing_is_unknown():
    recs=derive([{'instrument_id':'600000.SH','list_date':'1999-01-01','delist_date':None}],[], '20260925','x')
    assert recs[0].status is TradabilityStatus.UNKNOWN

def test_review_metrics():
    r=calculate(10,[{'close':11,'high':12,'low':9},{'close':9,'high':10,'low':8}])
    assert round(r.t1_return,4)==0.1 and round(r.max_runup,4)==0.2 and round(r.max_drawdown,4)==-0.2


# ── 🔴 P3-4 的行为判据：真的写一份 Parquet 再读回来 ─────────────────────────
import pytest


@pytest.mark.installed
def test_parquet_真的能写进去再读回来(tmp_path):
    """P3-4 的核心能力，**外部实现里没有任何测试覆盖它**。

    它交付的三条相关断言分别是：
      · `parquet_path()` 拼出来的**路径字符串**对不对（不写文件）
      · pyproject 里**有没有** `"duckdb>=1.0,<2"` 这个子串
      · analytics 源码里**有没有** `read_parquet` 这个词

    三条都落在仓库那张表的「❌ 会漏的判据」一列：源码里有没有这个字符串。
    把 `write_parquet_rows` 整个删掉，三条照样绿。

    ⇒ 这一条打在行为上：写 → 读回 → 行数与内容都对得上。

    ⚠️ 标 `installed`：duckdb 是**声明了的运行时依赖**，但本仓库的生产执行模型
    是 `bin/*` 直接用系统 python3（不装包、无 venv），而 PEP 668 的
    externally-managed 环境会挡住 `pip install`。装法见 `docs/guide/install.md`。
    没装时这条跳过 —— 跳过显示成 `s`，不会被读成「通过」。
    """
    from easyup_biga.data.file_store import FileStore

    fs = FileStore(tmp_path)
    rows = [
        {"instrument_id": "600000.SH", "trade_date": "20260925", "close": 10.5},
        {"instrument_id": "000001.SZ", "trade_date": "20260925", "close": 11.5},
    ]
    path, sha, n = fs.write_parquet_rows(
        "cn.equity.daily_bars", {"trade_date": "20260925"}, rows, data_version=1)
    assert n == len(rows), "写进去的行数与声明不符"
    assert path.exists() and path.stat().st_size > 0
    assert len(sha) == 64

    import duckdb
    got = duckdb.connect().execute(
        f"SELECT instrument_id, close FROM read_parquet('{path}') ORDER BY instrument_id"
    ).fetchall()
    assert got == [("000001.SZ", 11.5), ("600000.SH", 10.5)], f"读回来的内容不对：{got}"
