# 第 52 章 · 一个字段装下四种出处（裁定 16 · 批 4）

> 📄 **过程** · 写完即冻结
> **覆盖**：`OriginRef` 结构化来源、`input_evidence_ids` → `derived_from` 的迁移、
> risk 11 个字段全部接线、批 3 留下的 6 处 `kind=None` 全部消化 ｜
> **不覆盖**：`observed ⇒ 必须有 raw_hash`（直接证据仍有 13.8% 缺，见 `TODO.md`）、
> `tripped_thresholds` 空列表的 R-3 形状（独立一块，已记进 TODO）

## 目标 / 产出

| 产出 | 内容 |
|---|---|
| `OriginRef(kind, ref)` | `raw` / `evidence` / `verdict` / `fact` 四种出处 |
| `Evidence.derived_from` | 取代 `input_evidence_ids` |
| 四个构造器 | `evidence_origins` / `verdict_origins` / `raw_origins` / `fact_origin` |
| risk 11 个字段 | 值类 / 全量类 / 元数据类各按裁定 16 的粒度接线 |
| 批 3 的 6 处留白 | **全部消化**，全仓再无 `kind=None` 调用点 |
| 探针 | 新增 18 条，5 处 sabotage（其中 1 处**补出了一条缺失的守卫**）|

## 为什么这么做

### 1. `input_evidence_ids` 装不下三种真实形态

批 1/3 的 `input_evidence_ids` 只能说一句话：「我是从**另几条证据**算出来的」。
而剩下待接线的字段里，有三种它根本说不了：

| 字段 | 真实依据 | `input_evidence_ids` 能说吗 |
|---|---|---|
| `risk.coverage_ratio` | **哪些 verdict 到场了** | ❌ 依据是 verdict 的存在，不是任何证据的值 |
| `market.volume_total` | 沪深**两份** raw 快照 | ❌ 没有 per-symbol 成交量证据可引 |
| `news.market_open` | **交易日历** fact 的那一行 | ❌ 事实层不是证据 |

三者硬塞进去都会变成「说了一句不成立的话」—— 而那比不说更糟，
因为下一个人会以为这条链是通的。

⇒ `OriginRef(kind, ref)`，一个字段装下四种。

### 2. 为什么不是 `"verdict:123"` 这种前缀字符串

前缀字符串看起来更省事，但消费方要**解析字符串形状**才知道这是什么 ——
正是 L-13。本仓库刚用两批（批 2 的 `derived:` 收敛、批 3 的 `kind` 显式化）
把这个模式收拾干净，不该在同一处再种一个。

> 通用原则：把类型编码进字符串，等于把「这是什么」交给正则去猜。
> 多写一个小 dataclass，换掉所有下游的解析代码 —— 这笔账永远是划算的。

### 3. 校验要按类别分开

```python
if o.kind in ("raw", "evidence") and not SHA256_RE.match(o.ref):
    raise ValueError(...)
```

只有这两类的 `ref` 是内容哈希；`verdict` 是行号、`fact` 是事实表里的定位串。
统一按 sha256 校验会把合法的那两类**全部误杀**，而误杀的表现是
「明明填对了却构造不出来」，排查方向天生是错的。

### 4. 留白的理由消失了，留白本身就该消失

批 3 留了 6 处 `kind=None`，每处都写了「卡在哪」。批 4 把那个障碍搬掉之后，
这 6 处**一处都不该剩**。所以加了一条 AST 扫描钉死它：

> 通用原则：**留白要带到期日。** 一个写明了理由的 `None` 是诚实的；
> 理由消失之后还留着的 `None`，就退化成了「懒得想」的挡箭牌。
> 让守卫替你记住这个到期日，别指望下一个人读得到那条注释。

## 执行

```bash
domain/provenance.py   OriginRef + ORIGIN_KINDS + 四个构造器
domain/evidence.py     input_evidence_ids → derived_from；校验按 kind 分开
skills/*/scripts/*.py  add() 增 origins=；批 3 的 6 处留白全部接线
                       risk 11 个字段：值类 4 / 全量类 3 / 元数据类 4
```

## 坑

### 坑 1 · `grep -c FAILED` 得 0，而一条测试都没跑

改完字段名跑全量，`grep -c "^FAILED"` 返回 **0**，我当成全绿继续往下做。
实际是：

```
ERROR tests/test_evidence_kind_declared.py
!!!! Interrupted: 1 error during collection !!!!
```

**收集阶段就炸了**（那个文件还在 import 已删除的 `input_ids_for`），
整个 run 被中止，一条测试都没执行 ⇒ 输出里自然没有 `FAILED` 行。

> 🔴 通用原则：`grep -c FAILED == 0` 有**两种**含义 ——「全过了」和「根本没跑」。
> 它们在命令输出上长得一模一样。
> **看 summary 行**（`passed` / `failed` / `error` 的计数），别只数 FAILED。

这是我在第 47、49 章各写过一次的同一家族的第三次：都是**没看分母**。
前两次是 `tail` 截断，这次更隐蔽 —— 分母是 0 而我以为是全部。

### 坑 2 · sabotage 抓到一条**不存在**的守卫

破坏 `OriginRef.kind` 的校验（改成 `if False`）之后，**一条测试都没红**。

回头看：我测了「不是 OriginRef 直接拒」、测了「ref 的哈希格式」，
唯独没测「**是 OriginRef 但 kind 是瞎写的**」。那条校验从写下来的那一刻起
就没有任何东西守着它。

> 通用原则：sabotage 的价值不只是确认守卫有效，更是**发现守卫不存在**。
> 一条改坏了却没人红的代码，和没写过的区别只是它占了行数。

补上之后 S1 稳定变红。

### 坑 3 · 文本扫描被一句 docstring 绊住

「全仓不许再有 `kind=None`」第一版用正则扫源码行，结果被 technical 里
一句**解释性 docstring**（正好提到 `kind=None`）判成违例。

判据要判的是「有没有这样的**调用**」，而调用是语法结构，不是字符串形状 ——
改用 AST 遍历 `ast.Call`，问题消失。**又是 L-13 的同一个道理**，
只不过这次犯在测试里。

### 坑 4 · 冻结向量该变的时候要敢变，但要先回答一个问题

字段集从 `input_evidence_ids` 换成 `derived_from`，全部 `evidence_id` 随之变化，
冻结向量测试应声报红 —— **这正是它该红的时候**。

但更新它之前必须回答批 1 写下的那句话：「存量引用怎么办」。
这次的答案是量出来的：

```
生产库 3152 条证据，带 evidence_id / derived_from / kind 的：0 条
（批 1–4 从未在生产跑过）⇒ 没有任何存量引用会被打断
```

这个理由连同数字一起写进了向量文件本身，免得下次有人照着「上次不也改了」放行。

## 验证

```bash
cd ~/.openclaw-biga/workspace
python3 -m pytest tests/test_evidence_kind_declared.py tests/test_evidence_identity.py -q

# 全仓再无「尚未归类」的调用点
python3 - <<'PY'
import ast, pathlib
bad = []
for p in sorted(pathlib.Path("skills").glob("*/scripts/*.py")):
    for n in ast.walk(ast.parse(p.read_text())):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
           and n.func.id.startswith("add"):
            for kw in n.keywords:
                if kw.arg == "kind" and isinstance(kw.value, ast.Constant) \
                   and kw.value.value is None:
                    bad.append(f"{p}:{n.lineno}")
print("kind=None 的调用点:", bad or "无 ✅")
PY
```

## 本章要点

| # | 要点 |
|---|---|
| 1 | `input_evidence_ids` 只能说「从另几条证据算出来」，装不下 verdict / 多份 raw / fact 三种依据 |
| 2 | 硬塞一句不成立的血缘，比不说更糟 —— 下一个人会以为这条链是通的 |
| 3 | 🔴 把类型编码进字符串 = 把「这是什么」交给正则猜；多写一个小 dataclass 永远划算 |
| 4 | 校验要按类别分开：只有 `raw`/`evidence` 的 ref 是哈希，统一校验会误杀 verdict/fact |
| 5 | 🔴 **留白要带到期日** —— 理由消失后还留着的 `None` 退化成「懒得想」的挡箭牌 |
| 6 | 让守卫替你记住到期日（AST 扫 `kind=None`），别指望下一个人读得到注释 |
| 7 | 🔴 `grep -c FAILED == 0` 有两种含义：「全过了」和「**根本没跑**」——**看 summary 行** |
| 8 | 同一家族的第三次「没看分母」（前两次是 `tail` 截断）|
| 9 | 🔴 sabotage 的价值不只是确认守卫有效，更是**发现守卫不存在** —— 这次抓到一条 |
| 10 | 改坏了却没人红的代码，和没写过的区别只是它占了行数 |
| 11 | 文本扫描会被 docstring 绊住；判「有没有这样的调用」要用 AST（L-13 的同一个道理）|
| 12 | 冻结向量该变时要敢变，但先回答「存量引用怎么办」—— 这次答案是量出来的 0 条 |
