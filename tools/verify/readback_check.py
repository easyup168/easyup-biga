#!/usr/bin/env python3
"""毒行巡检 —— `agent_verdicts` / `decision_records` 里「存在但读不回来」的行。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：遍历两张判定/卡片表，对每一行走**正规读取路径**
  （`db.load_verdict` / `db.load_card_by_record_id`，即 `from_dict()`），
  统计有多少行会在读取时炸出 `__post_init__` 校验错误
- **不覆盖**：为什么会出现这种行（那是 `_store.db` 的写边界重校验，
  设计文档 §6 A3）—— 本工具只负责发现，不负责修（raw 层永不改写，L-8）

为什么需要这个巡检
------------------
写边界重校验（A3）挡住的是**新写入**的非法状态；它不能替历史行背书 ——
`agent_verdicts` / `decision_records` 建表时都没有这道校验，理论上可能已经
存在写入时未被拦下、只有读取时才会炸的行。而两张表都是只追加表，
这种行**删不掉也改不掉**，只能巡检出来、记下来。

🔴 判据必须走 `db.load_verdict()` / `db.load_card_by_record_id()`，不能自己
`json.loads(verdict_json)` —— 那样会绕开 `from_dict()` 的读取时校验，
本工具自己就会变成 `tests/test_contract_single_impl.py` 钉住的那个反例
（`missing_ledger.py` 当年就是这么被抓到的）。
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "skills"))

from _store import StoreNotInitialised, db  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _verdict as _v  # noqa: E402


def scan_verdicts(path: pathlib.Path | str | None = None) -> list[tuple[int, str]]:
    """返回 `[(verdict_id, 错误信息), ...]` —— 读不回来的那些行。"""
    with db.connect(path, readonly=True) as conn:
        ids = [r["verdict_id"] for r in
               conn.execute("SELECT verdict_id FROM agent_verdicts ORDER BY verdict_id")]
    bad = []
    for vid in ids:
        try:
            db.load_verdict(vid, path=path)
        except Exception as e:  # noqa: BLE001 - 巡检工具，任何读取失败都算毒行
            bad.append((vid, f"{type(e).__name__}: {e}"))
    return bad


def scan_cards(path: pathlib.Path | str | None = None) -> list[tuple[int, str, str]]:
    """返回 `[(record_id, decision_id, 错误信息), ...]`。"""
    with db.connect(path, readonly=True) as conn:
        rows = [(r["record_id"], r["decision_id"]) for r in conn.execute(
            "SELECT record_id, decision_id FROM decision_records ORDER BY record_id")]
    bad = []
    for rid, did in rows:
        try:
            db.load_card_by_record_id(rid, path=path)
        except Exception as e:  # noqa: BLE001
            bad.append((rid, did, f"{type(e).__name__}: {e}"))
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="毒行巡检：存在但读不回来的行")
    ap.parse_args(argv)

    bad_verdicts = scan_verdicts()
    bad_cards = scan_cards()

    print("═" * 78)
    print("毒行巡检 · agent_verdicts / decision_records")
    print("═" * 78)
    print(f"agent_verdicts   读不回来：{len(bad_verdicts)} 行")
    for vid, err in bad_verdicts:
        print(f"  · verdict_id={vid}  {err}")
    print(f"decision_records 读不回来：{len(bad_cards)} 行")
    for rid, did, err in bad_cards:
        print(f"  · record_id={rid} decision_id={did}  {err}")

    total = len(bad_verdicts) + len(bad_cards)
    print(f"\n{'─' * 78}")
    if total == 0:
        print("✅ 全部行都能正常读回，0 条毒行")
        return _v.PASS
    print(f"⚠️ 发现 {total} 条毒行 —— raw 层永不改写（L-8），"
          f"这些行删不掉也改不掉，只能记下来避免被误当作真实数据使用")
    return _v.FAIL


if __name__ == "__main__":
    # 🔴 F23：库不存在是全新环境的正常状态，不该是一屏 traceback。
    #    退出码 2 = 判不了，与 isolation.py / missing_ledger.py 一致。
    try:
        raise SystemExit(main())
    except StoreNotInitialised as e:
        # 🔴 业务结论（PASS/FAIL/UNKNOWN）一律走 **stdout**，只有参数错误与
        #    程序异常走 stderr。「事实库还不存在」是一个**业务结论**——
        #    R-3 的「算不出来」，不是程序出错。
        #    ⚠️ 这里原来打 stderr，与同一批工具的其他 UNKNOWN 分支（走 stdout）
        #      构成两套口径：两条测试各钉一边，**都绿**，因为它们走的是不同
        #      代码路径。外部评审把它并排放在一起才看出来。
        print(f"\n🔶 判不了 —— {e}")
        raise SystemExit(_v.UNKNOWN) from None
