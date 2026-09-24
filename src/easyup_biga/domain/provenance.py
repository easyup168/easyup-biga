"""溯源归属 —— 「这条证据出自哪份原始响应」的**唯一实现**（裁定 16 批 2）。

为什么要有这个模块
------------------
在它之前，同一个判断散在**五个 skill 里各写了一遍**：

    market     _lookup(source, table)        # 带前缀匹配
    emotion    _raw_hash_for(source)         # 纯 dict.get
    sector     raw_hash_for(source)          # 纯 dict.get
    technical  None if derived else raw_hash # 布尔标志
    news       None if source.startswith("derived:") else ...

五份都在回答同一个问题，写法各不相同 —— 这是 L-3 摆在明面上的样子。
真正的代价不是重复，是**改一处忘四处时剩下那四处仍然静默照旧**。

政策本身也改了
--------------
五份的共同规则是「`source` 以 `derived:` 开头 ⇒ 一律返回 None」，理由写在注释里：

> 派生字段没有单一来源，返回 None —— 不硬凑：凑出来的溯源比没有溯源更糟。

**理由是对的，规则过宽。** 很多派生值的 source 本身就点名了单一来源：

    derived:sina:kline/sh000001      ← 「我是从这一份 K 线算出来的」

这种剥掉前缀就是一个真实的表键，它**确实**出自那一份原始响应，
指回去不是硬凑，是如实记录。实测生产库 1721 条派生证据里有 **770 条**属于此类，
它们的溯源信息一直都在，只是被这条过宽的规则丢掉了。

🔴 而「不硬凑」那半句仍然生效，且是本模块的核心：**只认精确命中**。
   `derived:sina:kline`（market 的 `volume_total` 用它，值是沪深两市之和）
   剥掉前缀后不是任何表键 —— 它真的没有单一来源，返回 None 是对的。
   那种多输入的情况要用 `Evidence.input_evidence_ids` 表达，不是在这里凑一个。

🔴 **前缀匹配保留，且方向只能是一个**（`source.startswith(key)`，取最长命中）：

   * source **比表键更具体** ⇒ 命中。`tencent:quote/sh000001` 出自
     `tencent:quote` 那一份响应，子路径只是在那份响应里定位，溯源仍然成立。
     实测：market 的 `turnover_sh` / `turnover_sz` 正是这个形状，
     去掉前缀匹配它们会立刻失去 raw_hash（`test_market单源证据都有raw_hash` 抓到过）。
   * source **比表键更泛** ⇒ **不命中**。`sina:kline` 会同时落在
     `sina:kline/sh000001` 和 `sina:kline/sz399106` 上 —— 它真的跨两份快照
     （market 的 `volume_total` 是沪深两市之和），挑任何一个都是硬凑。
     这种多输入要用 `Evidence.input_evidence_ids` 表达，不在这里补。

   ⚠️ 反方向（`key.startswith(source)`）看起来「更宽容」，实际是把
      「我没法确定是哪一份」翻译成「就算它是某一份吧」——
      R-3 的形状，最该防的那种。
"""
from __future__ import annotations

from collections.abc import Mapping

__all__ = ["DERIVED_PREFIX", "underlying_source", "resolve_provenance",
           "input_ids_for"]

#: 派生值 source 的约定前缀。`derived:<真实来源>` 表示「从那一份算出来的」。
DERIVED_PREFIX = "derived:"


def underlying_source(source: str) -> str:
    """剥掉 `derived:` 前缀，得到它声称的底层来源。非派生值原样返回。

    只剥**一层** —— `derived:derived:x` 不是约定内的形状，剥一层之后
    自然落到「不是任何表键」那一档，返回 None，而不是被一路剥到能命中为止。
    """
    return source[len(DERIVED_PREFIX):] if source.startswith(DERIVED_PREFIX) else source


def resolve_provenance(source: str, table: Mapping[str, str]) -> str | None:
    """这条证据的 `raw_hash` / `evidence_set_id`（两者共用同一套匹配）。

    Args:
        source: `Evidence.source`，可能带 `derived:` 前缀。
        table: 真实抓取源 → 指纹 / 冻结集号。键是**实际发生过的抓取**
            （`sina:kline/sh000001` 这种），不是展示用的标签。

    Returns:
        精确命中时返回表里的值；否则 `None`。

    🔴 **匹配方向只有一个**：source 可以比表键更具体（子路径），不能更泛。
       命中不了就是命中不了 —— 返回 None 让它显示成「没有溯源」，
       好过安一个看起来查得到、实际指错地方的指纹（R-3 的形状）。
    """
    key = underlying_source(source)
    if key in table:
        return table[key]
    # 取**最长**命中：`tencent:quote/sh000001` 同时以 `tencent:quote` 为前缀时，
    # 若表里还有更长的键，更长的那个才是它真正出自的那一份。
    cand = [k for k in table if key.startswith(k)]
    return table[max(cand, key=len)] if cand else None


def input_ids_for(evidence, fields, *, of: str = "") -> tuple[str, ...]:
    """把「我是从这几个字段算出来的」翻译成它们的 `evidence_id`（裁定 16 批 3）。

    Args:
        evidence: **已经产出**的证据序列（各 skill 自己那个 `evidence` 列表）。
        fields: 输入的 `field` 名。调用方写字段名，不碰哈希。
        of: 谁在声明（只用于报错信息）。

    Returns:
        去重保序的 `evidence_id` 元组。

    Raises:
        ValueError: 声明了一个**还没有证据**的输入字段。

    🔴 找不到就抛，不是跳过。声明「我从 X 算出来」而 X 不在场，只有两种可能：
       字段名写错了，或者算的时候它根本不存在 —— 两种都是错的，
       而静默跳过会产出一条**输入列表不完整**的派生证据：
       它看起来声明过血缘，实际漏了一截，比完全没声明更难发现（R-3 的形状）。

    ⚠️ 同名多条证据时**全部**收进去（多来源合成同一个字段是允许的），
       顺序按 `evidence` 里的出现顺序，可回放。
    """
    ids: list[str] = []
    for f in fields:
        hits = [e.evidence_id for e in evidence if e.field == f]
        if not hits:
            raise ValueError(
                f"{of or '某个派生值'} 声明输入字段 {f!r}，但此刻还没有任何证据支撑它。\n"
                f"  已有字段：{sorted({e.field for e in evidence})}\n"
                f"  要么字段名写错了，要么 add({f!r}, ...) 排在了它后面 —— "
                f"输入必须先于使用它的派生值产出。")
        ids.extend(hits)
    return tuple(dict.fromkeys(ids))
