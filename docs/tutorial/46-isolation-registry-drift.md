# 第 46 章 · 测试和生产检查项漂移：干净机器上的假红（外部评审 A 节 · 二）

> 📄 **过程** · 写完即冻结
> **覆盖**：A2（`tools/verify/isolation.py` 的三态测试与它实际调用的检查列表
> 各写一份，漂移到漏了一项）｜**不覆盖**：A3（stdout/stderr 契约——排查过，
> 没找到可复现的缺陷）、A4（ZIP/Git 双模式——发现在更早一轮评审里已经修过）、
> A5（subprocess cleanup——排查过，现有代码已经是 `try/finally`，没找到泄漏）

## 目标 / 产出

| 产出 | 内容 |
|---|---|
| `isolation.checks(before)` | 三态汇总要跑哪些检查的**唯一**名单，`main()` 循环调用它 |
| `tests/test_isolation.py::_check_names()` | 测试从 `isolation.checks()` 取要 monkeypatch 的名字，不再手抄一份 |
| 新探针 | `test_干净机器上不装systemd服务也不会污染三态模拟` —— 直接模拟"没装过 BigA 服务的机器" |
| 测试 | 1485 → 1486 |

## 为什么这么做

### 1. 评审这四个词到底指什么

外部评审的分类清单里，A 节剩下几项只有一行短语，没有详细的编号小节展开
（不像 B/C/D/E 节那些 P1 发现，逐条给了复现步骤）。逐个排查之后：

- **A4「验证 ZIP / Git 两种测试模式」**——`tests/_scan.py` 与
  `tests/test_scan_fallback.py` 早就存在，且后者的
  `test_把仓库剥掉git之后守卫仍然全绿` 真的把整个仓库复制到一个没有 `.git`
  的临时目录、清掉 `GIT_DIR` 类环境变量、在里面跑真实的守卫子进程，断言
  `returncode == 0`。`git log --follow` 显示这套机制来自**更早一轮**评审
  （commit `d83ff2c`/`d20bed1`/`72cce1b`），当前这轮评审显然是在一份更旧的
  快照上看到问题、沿用了措辞，但机制已经不是空的。⇒ **不需要再做**，
  是评审清单没跟上代码。
- **A3「统一 stdout/stderr 契约」**——排查了 `tools/verify/*.py` 里
  `print()` 与 `file=sys.stderr` 的分布，以及仅有的两个产出 JSON 的脚本
  （`phase1_acceptance.py` 的 `--save-baseline` 写的是文件不是 stdout；
  `adapter_spike.py` 是探索性 spike 脚本，JSON 只是人读诊断行的一部分）。
  没找到"机器可读输出被人类文本污染"这类具体后果。`latency_report.py`
  另一半的评审措辞（三态退出码）已经由 `tests/test_verify_exit_codes.py`
  覆盖。⇒ 没有可复现的缺陷，不强行改。
- **A5「收紧 subprocess cleanup」**——仓库里两处真正 `Popen` 长跑子进程的测试
  （`tests/test_spawn_proof.py` 的单实例锁测试与 wrapper-被杀测试）都已经是
  `try/finally`，失败路径也会执行 `.terminate()`/`.kill()`。唯一一处生产代码
  的 `Popen`（`inbound.py` 里 `setsid` + `start_new_session=True`）是**故意**
  让子进程脱离父进程存活——那是出卡异步触发的设计，不是泄漏。⇒ 同样没找到
  可复现的缺陷。

### 2. 🔴 A2「Isolation Registry 漂移」是真的，而且是本仓库最常踩的那个坑的新实例

评审原话是「Isolation 测试和生产检查项漂移」。`isolation.py::main()` 依次调用
五个检查：`check_i1` / `check_i2` / `check_r2` / `check_namespaces` /
`check_ports`。而 `tests/test_isolation.py::TestThreeState` 里两条测试各自手抄
了一份"要 monkeypatch 哪些检查"的元组：

```python
for fn in ("check_i1", "check_i2", "check_r2", "check_ports"):
    monkeypatch.setattr(isolation, fn, fake)
```

**漏了 `check_namespaces`。**

这份手抄名单为什么一直没被发现：在**这台**开发机上，`check_namespaces` 早就
装好了三个正确命名的 systemd 单元（`notify-worker-biga` / `openclaw-gateway-biga`
/ `stale-run-reaper-biga`），所以它真实调用时刚好返回 `res.ok(...)`——这个结果
"恰好"和 `test_三态各自的退出码可区分` 的 "ok" 场景一致，测试因此在这台机器上
一直是绿的，掩盖了名单缺一项这件事。

### 3. 真实复现：换一台"干净"的机器会怎样

`check_namespaces` 自己的三态判据写得很清楚：`SYSTEMD_USER` 目录不存在（还没装
过任何 BigA 服务）⇒ `UNKNOWN`（"没装服务时没占别人名字是平凡成立的，不算证据"）。
把这个真实分支接到没被 mock 的 `test_三态各自的退出码可区分` 上：

```python
import isolation, pathlib
isolation.SYSTEMD_USER = pathlib.Path("/nonexistent/for/this/probe")
# 之后跑 pytest tests/test_isolation.py::TestThreeState -q
```

```
>       assert codes["ok"] == 0
E       assert 2 == 0
FAILED tests/test_isolation.py::TestThreeState::test_三态各自的退出码可区分
```

**一条与三态逻辑毫无关系的假红。** 任何全新 clone、任何还没跑过
`daemon install` 的 CI 容器，第一次跑 `pytest -q` 就会在这条测试上红——而
排查方向天然是错的：看起来像"三态汇总的代码坏了"，实际是"测试的 mock 名单
比生产代码的检查列表少一个"。

> 通用原则：**同一份"有哪些东西"的事实，只要被写成两份列表，就迟早会漂移。**
> 这正是本仓库反复踩的那个坑——`sys.modules` 覆写检查漏了 11 处复制体、
> roster 判据曾经只比条数、`EXCLUDE_DIRS` 曾经两个扫描器各抄一份——
> 这次的实例是"测试要 mock 哪些函数"与"生产代码实际调用哪些函数"分家。

### 4. 修法：不是"加一致性检查"，是把两份名单消灭成一份

可以在测试里加一条"断言这两份名单相等"的元测试，但那只是把 L-3 挪了个位置——
仍然是两份数据，只是多了一道人工去保持它们同步的义务。更彻底的修法是让它们
**本来就是同一份**：

```python
def checks(before: str | None) -> list[tuple[str, Callable[[Result], None]]]:
    return [
        ("check_i1", check_i1),
        ("check_i2", functools.partial(check_i2, before=before)),
        ("check_r2", check_r2),
        ("check_namespaces", check_namespaces),
        ("check_ports", check_ports),
    ]


def main(argv=None) -> int:
    ...
    res = Result()
    for _, check in checks(a.before):
        check(res)
    ...
```

`main()` 循环调用 `checks()`；测试用 `[name for name, _ in isolation.checks(before=None)]`
取要 monkeypatch 的名字。新增第六个检查以后只需要改 `checks()` 这一处，
测试自动跟上，不可能再漂移。

`check_i2` 的签名比其余四个多一个 `before` 参数——用 `functools.partial` 把
两种形状统一成同一种 `Callable[[Result], None]`，不需要给其余四个也硬塞一个
用不到的参数（那是为了"看起来整齐"而牺牲诚实的签名，本仓库不这么干）。

## 执行

```bash
$ python3 tools/verify/isolation.py     # 修前后输出逐字节相同，只是内部实现变了
✅ I-1 · ...
✅ R-2 · ...
✅ 共享命名空间 · systemd 单元名不重叠（3 个）
✅ 端口 · BigA 与同机已有实例不重叠
══ 1 项判不了 —— **本次未构成隔离证据** ══

$ python3 -m pytest tests/test_isolation.py -q
23 passed in 0.06s
```

## 坑

**sabotage 验证不能真的删掉这台机器的 systemd 目录**——那会影响这台机器上其他
真实在跑的服务状态检查。改用 `monkeypatch.setattr(isolation, "SYSTEMD_USER",
pathlib.Path("/nonexistent/..."))`，只在测试进程的内存里替换模块属性，不碰
真实文件系统，跑完自动还原。

## 验证

```bash
# 1. 全量测试绿，且数量对得上（新增 1 条）
python3 -m pytest -q                                   # 1486 passed

# 2. 新探针真的在守它声称守的东西——sabotage 验证
#    把 _check_names() 临时改回手抄的四元组（漏 check_namespaces），
#    新测试必须红：
#    AssertionError: 干净机器（没有 systemd 用户单元目录）上，纯 "ok" 模拟场景
#    的退出码变成了 2——说明有检查没被 mock 到 ...
#    还原后重跑，全绿。

# 3. 公开仓库审查
tools/verify/audit_public.sh --worktree                # 十一项全绿
```

## 本章要点

| # | 要点 |
|---|---|
| 1 | 评审给的是短语不是编号小节时，先逐项核实现状再动手——A4 已经在更早一轮修过，A3/A5 排查后没找到可复现的缺陷，不该被当成新任务重做一遍 |
| 2 | "测试该 mock 哪些检查"与"生产代码实际调用哪些检查"是同一个事实，写成两份列表就会漂移 |
| 3 | 漂移之所以没被发现，是因为**这台机器凑巧"干净场景"和"ok 场景"结果一致**——测试的正确性不该依赖跑测试的机器装过什么服务 |
| 4 | 修法是把两份列表变成一份（`main()` 和测试共用 `checks()`），不是加一条"断言两份列表相等"的检查——后者只是把重复搬了个地方 |
| 5 | sabotage 验证环境相关的守卫时，用 monkeypatch 替换模块属性，不要真的改这台机器的系统状态 |
