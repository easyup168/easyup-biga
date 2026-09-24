# 第 58 章 · 超时之后没人喊停：G 节 Live Acceptance 第 12 项

> 📄 **过程** · 写完即冻结
> **覆盖**：核实「无残留 Session / 子进程」这一条时发现的真实缺陷 —— 编排器
> 三处等待里，deadline 耗尽或单个 handle 到期都不会触发取消 ｜
> **不覆盖**：G 节其余 12 项的核实过程（那部分是读代码 + 查 schema + 实测
> systemd timer，没有代码改动）

## 目标 / 产出

| 产出 | 内容 |
|---|---|
| `_cancel_stragglers()` | 新的 static method，尽力取消一批 handle，cancel 本身失败不盖住原始异常 |
| 三处 wait 调用全部套上保护 | Stage 1（`handles`）、risk（`rh`）、synthesizer（`sh`） |
| 2 条新测试 | 一条扩了既有测试的断言，一条覆盖「全员到期未响应」的新场景 |

## 为什么这么做

### 1. G 节清单第 12 项不是"没测过"，是真的没做

核实外部评审 G 节「Live Acceptance」的 13 条时，前 11 条大多能在代码/schema
里直接找到对应实现（`ux_evidence_set_per_run` 唯一索引、`OrchestratorTimeout`
子类、`stale-run-reaper-biga.timer` 实测在跑……）。第 12 条「无残留 Session /
子进程」查下来是空的——没有一条测试断言过"跑完之后是不是还有会话挂着"。

按惯例，先假设这只是"没人补断言，行为本身是对的"，去读实际代码确认。

### 2. 🔴 读代码发现行为本身就不对，不是漏测

`_stage_timeout()` 的实现：

```python
def _stage_timeout(self, deadline, stage_budget):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OrchestratorTimeout(...)
    return min(float(stage_budget), remaining)
```

调用方写的是：

```python
r2 = ad.wait([rh], self._stage_timeout(deadline, self.risk_sec))
```

`rh` 在这行执行**之前**已经 `ad.start()` 成功了。Python 求值参数在调用之前——
`_stage_timeout()` 一抛，`ad.wait()` 根本不会被执行。`rh` 既没被等过，
也没被取消，就这样留在原地。Stage 1、risk、synthesizer 三处 wait 调用
全是这个形状。

> 通用原则：**函数调用的参数是先算完才传进去的。** 把一个可能抛异常的表达式
> 直接塞进另一个调用的参数位置，等于把"万一它抛了呢"这个问题让 Python 的
> 求值顺序帮你决定——而通常不是你想要的那个答案。

### 3. 更常见的第二条路：`ad.wait()` 正常返回，但単个 handle 已经到期

读 `OpenClawRuntimeAdapter.wait()` 才发现——它自己的超时分支是**本地生成**的：

```python
for rid, h in by_id.items():  # 到期还没回来的
    results[rid] = SpawnResult(handle=h, status=SpawnStatus.TIMEOUT, ...)
```

也就是说 `status == TIMEOUT` 只代表"我们等烦了、不再等了"，不代表运行时
那一侧的会话真的停了。这比第一条路更容易撞见——不需要**总**预算耗尽，
只要**这一阶段自己**的预算不够（比如某个 Specialist 那天恰好卡住），
就会产生一个状态是 TIMEOUT、但没人去 cancel 的 handle。

编排器拿到 `wait()` 的返回值只用来算 `_stage_detail()`（塞进转移记录的
usage/状态），从没检查过要不要顺手收尾。

### 4. 修法：一个 helper，套在三处

```python
@staticmethod
def _cancel_stragglers(ad, handles) -> None:
    for h in handles:
        with contextlib.suppress(Exception):
            ad.cancel(h)
```

三处 wait 调用各自套两层：

```python
try:
    stage_budget = self._stage_timeout(deadline, self.risk_sec)
except OrchestratorTimeout:
    self._cancel_stragglers(ad, [rh])
    raise
r2 = ad.wait([rh], stage_budget)
self._cancel_stragglers(
    ad, [r.handle for r in r2 if r.status == SpawnStatus.TIMEOUT])
```

`ad.cancel()` 本身是幂等且安全的——目标已经不在运行时的活跃列表里就静默
返回（它自己的文档字符串写着「取消一个已结束的东西不是错误」），所以两处
都不需要担心"万一它已经自己完成了怎么办"。

这不是新发明的模式——Stage 1 的启动循环本来就有一条一模一样的防线（批
C-III）：`start()` 中途某一路抛错，已经起来的兄弟会被逐个 cancel 掉。
这次只是把同一条防线延伸到"起是起来了，等的时候出问题"这另外两种触发方式。

## 执行

```bash
$ grep -n "ad.wait(" skills/decision-card/scripts/orchestrator.py
# 确认三处调用形状一致，逐一加保护
```

## 坑

无——这次的坑在"读代码发现问题"这一步，改动本身没有额外踩坑。

## 验证

```bash
cd ~/.openclaw-biga/workspace

# 1. 两条新/改测试
python3 -m pytest tests/test_orchestrator.py -k "超时进TIMEOUT or 全员到期未响应" -q
# 预期：2 passed

# 2. sabotage：把 Stage 1 那处改回没有保护的旧写法，两条测试都应该红
#    （已手工验证过，见 CHANGELOG 本条）

# 3. 全量回归
python3 -m pytest -q
```

## 本章要点

| # | 要点 |
|---|---|
| 1 | 核实一份评审清单时，别止步于"有没有测试覆盖"——读实际代码，确认行为本身是对的 |
| 2 | 把一个可能抛异常的表达式塞进另一个函数调用的参数位置，等于让 Python 的求值顺序决定"万一它抛了呢"这个问题的答案 |
| 3 | 🔴 运行时返回的 `TIMEOUT` 状态多数时候是**本地**判断"等烦了"，不代表对方真的停了——两者是不同的事实 |
| 4 | 新加的取消逻辑要接进**已有的**同类防线（Stage 1 的 start 失败 cancel），不是另起一套 |
| 5 | `cancel()` 若设计成幂等安全（目标不存在就静默返回），调用方就不需要为"要不要 cancel"做额外判断 |
| 6 | 测试基础设施（`FakeAdapter.cancelled`）可能早就具备验证能力，只是没人写断言去用它 |
