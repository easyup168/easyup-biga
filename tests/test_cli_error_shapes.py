"""CLI 的**报错形状** —— 报错要指路，不是抛一屏 traceback。

覆盖 / 不覆盖
-------------
- 覆盖：`budget_report.py` 的参数解析、`bin/biga-card` 的 `_sql()` 在库不存在时的行为
- **不覆盖**：这两个命令的功能本身（那要有库，且随数据变）

🔴 两条都是同一个形状
---------------------
一个**不影响结果**的异常打出完整调用栈 —— 屏幕上看起来像出了大事，
而读的人的第一反应是「这个工具是不是坏了」。

· `budget_report.py --day 20260922` 把 `"--day"` 当成日期，走到 `strptime` 才炸
· `bin/biga-card` 的 `_sql()` 在库还不存在时抛 `StoreNotInitialised`，
  而它的调用点本来就把空值当合法兜底

两者都非零/有栈，而**只有说清「谁错了、下一步做什么」的那种报错有用**。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
# ⚠️ 只挂 pythonpath 没覆盖的两处。`skills` 已由 pyproject 的 pythonpath 挂好，
#    再挂一遍不改变任何结果（批 U-III 删掉的就是这一类）。
sys.path.insert(0, str(REPO / "tools" / "verify"))
sys.path.insert(0, str(REPO / "skills" / "decision-card" / "scripts"))

import budget_report as br  # noqa: E402


def test_位置参数():
    assert br._parse_day(["20260922"]) == "20260922"


def test_day_flag两种写法都收():
    assert br._parse_day(["--day", "20260922"]) == "20260922"
    assert br._parse_day(["-d", "20260922"]) == "20260922"


def test_不给参数时取今天():
    got = br._parse_day([])
    assert len(got) == 8 and got.isdigit()


def test_非法日期当场指路而不是抛traceback():
    """🔴 判据是**报错内容**，不是「有没有抛异常」。

    原来的行为也「抛异常」—— 抛的是 `ValueError: time data '--day' does not
    match format '%Y%m%d'`，带着 `_strptime.py` 的调用栈。
    两者都非零退出，而只有一种告诉得了人下一步做什么。
    """
    with pytest.raises(SystemExit) as exc:
        br._parse_day(["2026-09-22"])
    msg = str(exc.value)
    assert "YYYYMMDD" in msg
    assert "2026-09-22" in msg, "报错要说清楚是哪个值不对"
    assert "用法" in msg, "报错要指路"


def test_day后面缺参数():
    with pytest.raises(SystemExit) as exc:
        br._parse_day(["--day"])
    assert "用法" in str(exc.value)


def test_main真的用了_parse_day():
    """🔴 否则 `_parse_day` 是个零消费方的解析器 —— 上面五条全绿，而命令行照样崩。

    探针实测：把 `main()` 里那行换回 `argv[0]`，上面五条**一条都不红**。
    判据落在 AST 上（`main` 的函数体里有没有调用它），不是字符串扫描 ——
    docstring 里提到这个名字是正常的（本文件就提了）。
    """
    import ast

    src = (REPO / "tools" / "verify" / "budget_report.py").read_text(encoding="utf-8")
    main_fn = next(n for n in ast.walk(ast.parse(src))
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    called = {n.func.id for n in ast.walk(main_fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_parse_day" in called, (
        "budget_report.main() 没有调用 _parse_day —— "
        "那个解析器因此是零消费方，命令行仍会拿 argv[0] 当日期。")


# ── bin/biga-card 的 _sql()：库不存在时不打 traceback ──────────────────────
def test_biga_card的_sql在库不存在时静默返回空(tmp_path):
    """🔴 判据是**跑一遍看 stderr**，不是「源码里有没有 try」。

    `connect(readonly=True)` 按设计会抛 `StoreNotInitialised`（读一个不存在的
    库该报错）。但 `_sql()` 的调用点全是 `BEFORE=$(_sql "SELECT MAX(...)")`
    这种取基线值的用法，空字符串本来就是语义正确的兜底。

    不接住的后果不是功能坏了，是**一屏 Python 调用栈进了 stderr** ——
    而那会让第一次跑这条命令的人以为出卡失败了。

    > 报错要指路。一个不影响结果的异常打出完整调用栈，指的是错的路。
    """
    import re
    import subprocess

    text = (REPO / "bin" / "biga-card").read_text(encoding="utf-8")
    body = re.search(r"_sql\(\) \{ \$PY - \"\$@\" <<'EOF'\n(.*?)\nEOF", text, re.S)
    assert body, "bin/biga-card 里找不到 _sql() 的 heredoc —— 结构变了？"

    script = tmp_path / "probe.py"
    script.write_text(body.group(1), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(script), "SELECT MAX(decision_id) FROM decision_ids"],
        cwd=REPO, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
             "BIGA_DB_PATH": str(tmp_path / "nope" / "biga.db")})
    assert r.returncode == 0, f"库不存在不该非零退出：{r.stderr}"
    assert r.stdout.strip() == "", "没有库就没有基线值，不该有输出"
    assert "Traceback" not in r.stderr, (
        f"库不存在时打了调用栈 —— 读的人会以为出卡失败了：\n{r.stderr}")


def test_biga_card的_sql只咽库不存在_别的照样炸(tmp_path):
    """探针方向：库存在但 SQL 写错了，必须炸 —— 不许被那个 except 一起咽掉。"""
    import re
    import subprocess

    from _store import init_schema

    db = tmp_path / "biga.db"
    init_schema(db)
    text = (REPO / "bin" / "biga-card").read_text(encoding="utf-8")
    body = re.search(r"_sql\(\) \{ \$PY - \"\$@\" <<'EOF'\n(.*?)\nEOF", text, re.S)
    script = tmp_path / "probe.py"
    script.write_text(body.group(1), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(script), "SELECT * FROM no_such_table"],
        cwd=REPO, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "BIGA_DB_PATH": str(db)})
    assert r.returncode != 0, "SQL 写错了却静静退 0 —— except 咽得太宽"
