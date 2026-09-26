# 68 · 合一份「已经自测过」的增量包 —— 那 93 条测不到的四件事

> 📄 **过程** · 写完即冻结
> **覆盖**：合并 P3-R2 运行时对账增量包；为什么第一件事是全量回归；闸门自己带的 false-green
> **不覆盖**：这个包实现的那些能力本身的设计（见 `docs/design/phase-3-data-platform.md`）

---

## 目标 / 产出

这是本仓库第**四**次合外部交付，但前三次的经验只覆盖了一半：

| 前三次的形状 | 这次的形状 |
|---|---|
| 交付方基线是**别的树**（或没说） | 基线就是**我们的 HEAD** |
| 自陈「没跑全量」或压根没说 | 自陈 `93 passed / 1 skipped`、两道闸门 exit 0 |
| 判据：数它包不包含你已知的修正 | 那个判据**这次会给出「已包含」** |

交付方还主动写明边界：「**没有运行全量回归和真实多交易日 Live Acceptance**」。
这句话是真的 —— 而它恰好是这一章的全部内容。

产出：v0.9.0，24 个文件合入，**4 条交付方那 93 条测不到的缺陷**被修掉。

---

## 为什么这么做

### 一、基线相同 ⇒ 用 git，不要手工缝

前三次是手工按层合，因为基线不同、没有共同祖先。这次不一样：

```bash
# 包里的 CHANGELOG 顶部版本 = 我们 HEAD 的版本（0.8.2.dev0）
# 包里 registry.py 的 docstring = 我们的原文
# 包里引用了 tests/test_phase3_gate.py —— 那是我们上一轮写的
```

⇒ 开一个 baseline 分支、把增量包整个 apply 上去、再和我们的分支做三方合并。
git 会把「他们改的」和「我们改的」分开，而手工缝做不到 ——
手工缝的产物是「我读 diff 时觉得该留的那些」。

> 🔴 **基线相同时，三方合并是一个判据；手工缝是一次判断。**
> 前者会在冲突时停下来问你，后者只会在你没注意到的地方悄悄选边。

### 二、「93 passed」是一个**口径**，不是一个结论

交付方跑的 93 条是他们**挑出来的相关测试**。这棵树有 2100+ 条。
两者的差集不是「冗余」，而是**这个包的改动会经过、而他们没想到要跑的那些路径**。

合进来第一件事跑全量回归，多出 4 条红的：

| # | 缺陷 | 为什么那 93 条测不到 |
|---|---|---|
| 1 | 调了一个**不存在的** `save_trading_calendar` 签名 | 那条路只在真实日历刷新里走 |
| 2 | 一条测试断言「崩溃重试会收养孤儿分区」，而代码**没实现收养** | 断言写在被 `importorskip` 跳过的模块里 |
| 3 | `producers.py` 的 `import_module` 触发间接导入守卫 | 守卫在另一个 suite 里 |
| 4 | 🔴 新闸门比的是**它自己那份抄件** | 见下 |

> **外部交付的验证口径是「我跑的那些测试」，不是「这棵树的全部测试」。**
> ⇒ 合包的第一件事是全量回归，不是读 diff。

第 2 条值得单独看一眼：他们写了一条测试，断言崩溃重试会收养上次留下的孤儿
分区 —— 而他们的代码没实现这个行为。那条测试之所以「通过」，是因为它所在的
模块顶上有 `pytest.importorskip("duckdb")`，而他们的环境没装 DuckDB。

> 🔴 **一条永远被 skip 的测试，和一条不存在的测试，在 summary 行上长得不一样
> （`s` 对 `.`），在「我跑过了」这句话里长得一模一样。**

### 三、第 4 条：一个为了消灭 false-green 而写的闸门，自己带了一处

这个包最有价值的东西是 **Producer Registry**：12 个 ACTIVE dataset 各自声明
「谁在生产我」。它关掉的是一处真实的假绿 —— 在此之前：

- Dataset Registry 回答「平台拥有哪些数据集」
- Provider Registry 回答「谁可以供数」
- **没有任何一处回答「今天真的有代码在产它吗」**

配套的 `validate_producer_binding()` 核对 provider 身份是否一致。
它比的是两处：注册表的 `primary_provider` vs `producers.py` 的声明。

而 provider id 今天有**三份**手写副本：

| # | 在哪 | 谁用它 |
|---|---|---|
| 1 | `registry.py` 的 `primary_provider` | `role_of()` 查绑定时用 |
| 2 | `producers.py` 的 `ProducerDefinition.provider_id` | **只有这个闸门用** |
| 3 | dataset 模块里的 `PROVIDER_ID` 常量 | 🔴 **运行时真正传给 `publish()` 的那个** |

闸门比的是 **#2 vs #1**，运行时用的是 **#3**。

> 🔴 **守卫查的是它自己那份抄件，不是运行时真正用的那个值。** —— L-13。

这不是假想。合并过程中我把注册表里的 id 从连字符改成下划线
（`derived-biga` → `derived_biga`），**漏改了 dataset 模块的常量**。后果：

```
ValueError: 'derived-biga' 没有绑定到 'cn.market.emotion_close'
```

`emotion_close.run()` 从此必抛，而它在生产上走 `decision_client._freeze_one`
（limit_pool 那条分支）—— 当时正被行情源故障挡着，没炸出来。
**闸门全程报绿。**

#### 修法不是加第四份清单

第一反应是「把 #3 也抄进 `producers.py`」。那会变成四份。

真正的修法是**扫 `data/datasets/` 下每个模块自报的 `(DATASET_ID, PROVIDER_ID)`
配对** —— 配对是**自描述的**，不需要任何人维护一份对照表。

```python
for item in pkgutil.iter_modules(_pkg.__path__):
    mod = importlib.import_module(f"{_pkg.__name__}.{item.name}")
    ds_id, pv_id = getattr(mod, "DATASET_ID", None), getattr(mod, "PROVIDER_ID", None)
```

⚠️ 不能从 producer 的 `entrypoint` 反推：`cn.security.tradability` 的生产入口是
`eod_pipeline:run_eod_bundle`，而它的常量在 `datasets/tradability.py` 里 ——
**入口模块与常量模块不是同一个**。

### 四、顺序不变式翻了个面，而不变式本身没变

这个包把所有分析读取改成了「控制面先选 COMPLETE Snapshot，再读它的 URI」。
原因是 `query_eod_between` 原来**扫 `lake/` 目录**挑最高 `data_version` ——
崩溃遗留的孤儿分区**它看得见**、而 as-of 路径看不见。

改完之后，第 67 章那条两阶段提交的顺序必须**反过来**：

| | 那时 | 现在 |
|---|---|---|
| lake 有没有不看快照的读取路径 | 有（扫盘） | **没有了** |
| 谁最后写 | 文件 | **快照** |

不变式一个字没改：

> **最后写的那一样，必须是「它不在就整体不可见」的那一样。**

> 🔴 **这就是为什么不变式要写成一句话，而不是写成「文件最后写」。**
> 写成后者，这次改动会让它变成一条错的规矩，而且**没人会注意到它错了**。

---

## 执行

```bash
# 1. 在 baseline 上 apply 增量包，再三方合并
git checkout -b p3r2-base HEAD
# … 把包里的 24 个文件覆盖上去、提交 …
git checkout -b p3r2-merge phase3
git merge p3r2-base

# 2. 🔴 第一件事：全量回归（不是读 diff）
python3 -m pytest -q
# → 4 failed

# 3. 修完之后，两道闸门
python3 tools/verify/phase3_runtime.py --installed   # exit 0
python3 tools/verify/phase3_acceptance.py            # exit 2（证据不足，诚实的黄）
```

真实输出（修完后）：

```
✅ Phase 3 Runtime Gate
  ✅ 12 个 ACTIVE dataset producer/provider 可解析
  ✅ DuckDB runtime OK: 1.5.5
```

---

## 坑

### 坑 1 · 探针没红，因为我构造的场景触发不到守卫

给「收养孤儿分区时必须校验内容哈希」写完代码后，探针把
`if str(orphan["content_sha256"]) != str(request.content_sha256):`
改成 `if False:` —— **全套测试照样绿**。

那不是守卫坏了，是**那个分支当时没有任何测试覆盖**。
（同一家族的第 N 次；见第 67 章的「探针没红时先问」。）

### 坑 2 · 降级扫描与 git 扫描扫的不是同一棵树

`tests/_scan.py` 在 git 模式下走 `git ls-files -co --exclude-standard`
（排除已忽略文件），降级模式却走 `rglob` ⇒ `docs/external/` 下的外部交付包
**会被扫进来**。

于是「`producers.py` 的 `import_module` 触发间接导入守卫」这条，
在两种模式下红的原因还不一样。

> ⚠️ 同一道守卫在两种模式下检查的**不是同一棵树** —— 这本身就是一处 L-3。

### 坑 3 · 发布服务允许「派生数据集也造一份 raw」

这个包没有引入这个问题，但合并过程中读到了它。留到下一章。

---

## 验证

```bash
# 三份 provider id 必须一致 —— 把 dataset 模块的常量改坏，闸门要当场红
purge() { find . -name __pycache__ -type d -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null; }

sed -i 's/PROVIDER_ID = "derived_biga"/PROVIDER_ID = "derived-biga"/' \
    src/easyup_biga/data/datasets/tradability.py
purge; python3 tools/verify/phase3_runtime.py ; echo "exit=$?"     # 预期 exit=1
git checkout src/easyup_biga/data/datasets/tradability.py
purge; python3 tools/verify/phase3_runtime.py ; echo "exit=$?"     # 预期 exit=0
```

🔴 **那两个 `purge` 不是保险起见 —— 不加就是错的。**
写这一章时照第一版（没有 `purge`）跑了一遍，还原之后闸门**仍然退 1**。

`derived_biga` → `derived-biga` 是**等长替换**，加上同一秒内还原 ⇒
源码 sha256 逐字节回到原样，而 `.pyc` 的失效判据是 **mtime + size**，
两项都没变 ⇒ Python 继续用那份带着 `derived-biga` 的缓存。

> ⚠️ 第 61 章记过同一个坑，这次是**在写验证命令时又踩了一次**。
> 说明它不是一次性的手滑：**还原源码不等于还原工作区状态。**

```bash
# 全量回归
python3 -m pytest -q      # 预期 0 failed
```

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 基线相同时用三方合并 —— 它会在冲突时**问你**，手工缝只会悄悄选边 |
| 2 | 「93 passed」是交付方的**口径**，不是这棵树的结论 ⇒ 合包第一件事是全量回归 |
| 3 | 一条永远被 `skip` 的测试，在「我跑过了」这句话里和真跑过长得一模一样 |
| 4 | 🔴 守卫查它自己那份抄件，就是 L-13 —— 而这次它发生在一个**专门消灭假绿**的闸门里 |
| 5 | 同一个值有 N 份手写副本时，修法不是加第 N+1 份，是找到**自描述**的那个配对 |
| 6 | 不能从 producer 的 entrypoint 反推常量位置：入口模块与常量模块可以不是同一个 |
| 7 | 顺序不变式要写成「最后写的必须是它不在就整体不可见的那一样」，不写成「文件最后写」 |
| 8 | 同一道守卫在 git 模式与降级模式下扫不同的文件集合，本身就是 L-3 |
| 9 | 🔴 **还原源码 ≠ 还原工作区状态**：等长替换 + 同秒还原会骗过 `.pyc` 的 mtime+size 失效判据 —— 第 61 章记过，写这一章的验证命令时又踩了一次 |
