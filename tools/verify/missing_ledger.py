#!/usr/bin/env python3
"""缺失项台账 —— Phase 2 出口条件 4 的判据。

出口条件原文：**`missing[]` 在真实缺数据时非空 ≥5 次（不是注入故障）**。

为什么不是手工记
----------------
设计文档原本写的是「需要手工日跑 + 一份台账：日期 / 哪个源缺 / 缺失代码 /
Card 状态」。但这四项**全都已经在 `decision_records` 里了** ——
手工再记一遍就是第二套口径（L-3），而且它会先漂。

⇒ 台账从库里生成。人要做的只是**跑**，不是**记**。

🔴 「真实」与「注入」必须分开数
------------------------------
演练用 `--break-source` 制造的缺失，文案里带「演练：人为中断」。
把它们算进出口条件，等于用自己制造的故障证明自己能发现故障 ——
那不是证据，是同义反复。

本工具按这条特征把两类分开，并且**只有真实的那些才计入达标**。

⚠️ 这个判据依赖 skill 里的固定文案。文案改了这里就会静默少算 ——
`tests/test_missing_ledger.py` 钉死了这个耦合。
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "skills"))

from _store import StoreNotInitialised, db  # noqa: E402

# 退出码的唯一定义 —— 见 tools/verify/_verdict.py 的 docstring
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _verdict as _v  # noqa: E402

#: 演练注入的标记。
#:
#: 🔴 用的是 **CLI 开关的字面量**，不是中文散文。
#:
#: 第一版写的是「演练：人为中断」—— 测试立刻抓到 `market_calc` 与
#: `emotion_calc` 用的是另一句「数据源被人为中断（--break-source X）」。
#: 两套文案 ⇒ 台账会把那两个 skill 的**注入故障算成真实缺失**，
#: 于是出口条件 4 凭空达标，不报错也不报警。
#:
#: `--break-source` 在两套文案里都有，而且它是个 CLI 开关 ——
#: 散文会被顺手改写，开关名不会。
DRILL_MARK = "--break-source"

#: 出口条件要求的真实缺失次数。
REQUIRED = 5

#: 🔴 **不是所有「真实缺失」都是同等强度的证据。**
#:
#: 出口条件问的是「系统在**源真的缺数据**时会不会报」。
#: 但 `missing[]` 里还有另外两类，它们也是真的，证明力却弱得多：
#:
#: * `supervisor.agent_offline` —— 某个 Agent 还没建。
#:   这证明的是「我们知道自己少了个 Agent」，不是「我们能发现数据缺了」
#: * `legacy.*` —— `MissingItem` 之前的裸字符串，没有机器可读代码
#:
#: 把它们混进去数，达标会来得太容易。⇒ 分开列。
WEAK_PREFIXES = ("supervisor.agent_offline", "supervisor.agent_no_response",
                 "legacy")


def _weak(code: str) -> bool:
    return code.startswith(WEAK_PREFIXES)


def collect(path: pathlib.Path | str | None = None) -> list[dict]:
    """🔴 走 `load_online_card()`，**不自己 `json.loads(card_json)`**。

    外部评审 F11 的结论是：静态扫描能被绕过，但 `from_dict()` 在每次
    反序列化时重跑一遍 `__post_init__`，构成运行时的第二道防线 ——
    **前提是所有消费方都老实走 `from_dict`**。

    ⚠️ 而这个文件当初正是那个反例：它直接 `json.loads(card_json)`，
       绕开了那道防线。评审检查过 `synthesize.py` 和 `card_ops.py`
       都是干净的，没查到这里。
       ⇒ 「目前检查过的路径都是」这种前提，**得有机器守着才站得住**。
    """
    with db.connect(path, readonly=True) as conn:
        ids = [r["decision_id"] for r in conn.execute(
            "SELECT DISTINCT decision_id FROM decision_records "
            "WHERE replay_of IS NULL ORDER BY decision_id")]
    out = []
    for did in ids:
        obj = db.load_online_card(did, path=path)
        if obj is None:
            continue
        card = obj.to_dict()
        r = {"decision_id": did,
             "status": obj.status,
             "generated_at": card.get("generated_at", "")}
        real, drill = [], []
        for m in card.get("missing", []):
            code = m.get("code", "legacy") if isinstance(m, dict) else "legacy"
            text = (m.get("detail") or m.get("text") or "") if isinstance(m, dict) \
                else str(m)
            (drill if DRILL_MARK in str(text) else real).append(code)
        out.append({
            "decision_id": r["decision_id"],
            "day": r["generated_at"][:10],
            "time": r["generated_at"][11:16],
            "status": r["status"],
            "real": real,
            "drill": drill,
        })
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="缺失项台账（出口条件 4）")
    ap.add_argument("--verbose", action="store_true", help="逐条列出缺失代码")
    a = ap.parse_args(argv)

    rows = collect()
    if not rows:
        # 🔴 判不了。空库是全新环境的正常状态，不是「缺失项台账不合格」。
        print("🔶 判不了 —— 库里一张卡都没有。先跑一次 bin/biga-card。")
        return _v.UNKNOWN

    print("═" * 78)
    print("缺失项台账 · Phase 2 出口条件 4")
    print("═" * 78)
    print(f"{'决策':<22} {'时间':<12} {'状态':<6} {'真实':>4} {'演练':>4}")
    print("─" * 78)
    for r in rows:
        print(f"{r['decision_id']:<22} {r['day'][5:]+' '+r['time']:<12} "
              f"{r['status']:<6} {len(r['real']):>4} {len(r['drill']):>4}")
        if a.verbose and r["real"]:
            for c in r["real"]:
                print(f"{'':>24}· {c}")

    codes = collections.Counter(c for r in rows for c in r["real"])
    strong_codes = {c: n for c, n in codes.items() if not _weak(c)}
    weak_codes = {c: n for c, n in codes.items() if _weak(c)}

    print("\n源真的缺数据（强证据）")
    for code, n in sorted(strong_codes.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>3}  {code}")
    print("\n编排层缺失（弱证据 —— 不计入达标）")
    for code, n in sorted(weak_codes.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>3}  {code}")

    # 达标只看强证据
    hit = [r for r in rows if any(not _weak(c) for c in r["real"])]
    days = len({r["day"] for r in hit})
    print(f"\n{'─' * 78}")
    print(f"有**源级**真实缺失的决策：{len(hit)} / {len(rows)} 张卡"
          f"    要求 ≥{REQUIRED}    "
          f"{'✅ 达标' if len(hit) >= REQUIRED else '⬜ 还差 %d' % (REQUIRED - len(hit))}")
    print(f"不同的源级缺失代码：{len(strong_codes)} 种      覆盖天数：{days} 天")
    if days < 2:
        print("\n⚠️ 全部来自同一天。这条出口条件要的是「真实缺数据」，"
              "而同一天的\n   多次运行往往缺的是**同一个东西** —— "
              "它证明的是「这个源今天不好使」，\n   而不是「系统能发现各种缺失」。"
              "⇒ 需要跨天累积。")
    return _v.PASS


if __name__ == "__main__":
    # 🔴 F23：库不存在是**全新环境的正常状态**，不该是一屏 traceback。
    #    统一在入口转成人话 —— 每个工具各写一遍就又是一份散开的判据。
    #    退出码 2 = 判不了，与 isolation.py 的三态口径一致。
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
