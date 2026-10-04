# 第 59 章 · ACK 抢在闸门前面许诺了一件闸门几秒后会否决的事

> 📄 **过程** · 写完即冻结
> **覆盖**：真机触发飞书 `/card` 时撞见的一个信息差 —— ACK 消息与预算闸门
> 的判断谁先谁后 ｜ **不覆盖**：预算闸门本身的判断逻辑（那是 `budget.py` 与
> `test_budget_gate.py` 的事，这次没动它）

## 目标 / 产出

| 产出 | 内容 |
|---|---|
| `accept_trigger()` 新增一次预检 | 拉起后台运行之前先问一遍 `check_budget()` |
| 3 条新测试 + 1 条改写 | 覆盖"会拒就不拉起"、"放行照常拉起"、"不会跟自己比出假 gap"三件事 |

## 为什么这么做

### 1. 真机撞见的，不是读代码找的

G 节 Live Acceptance 的第 1/2/3/8 项只能真的触发一次才能看。为了核实，先用
CLI 触发了一次真实出卡（`BIGA-20260924-007`），几分钟后用飞书发了一次 `/card`
（`BIGA-20260924-008`）。飞书那边回了"收到，正在出卡……不用等也不用重发"，
但**三分钟过去，什么都没有**。

查库：`decision_ids` 里有 `008` 这个号，`decision_runs` 里一行都没有。查
journal：

```
biga-card[1623430]: 🔴 出卡预算闸门拒绝了这次请求：
  距上次占号只有 390s，最小间隔 400s（BIGA-20260924-007，by=orchestrator）
run-rff56d032bede4e65a432b1488f28aae8.service: Main process exited, code=exited, status=3/NOTIMPLEMENTED
```

闸门做的事完全正确——两次真实触发确实隔得太近，$0 代价拦下，就是它该做的。
但用户拿到的信息是错的：一句"正在处理"，而这句话发出的同一秒，下游已经
决定不处理了。

### 2. 根因是两个「完成」不是同一个「完成」

`accept_trigger()` 的 ACK 逻辑：

```python
try:
    launcher(origin, trigger_id, decision_id)
except Exception as e:
    return AckResult(accepted=False, ..., message=f"...没拉起来：{e}...")
return AckResult(accepted=True, ..., message="收到，正在出卡……")
```

`launcher()`（`detached_biga_card_launcher`）用 `subprocess.run(["systemd-run",
...], check=True)`——这一步的"完成"指的是**瞬态单元被成功排上去了**，不是
"单元跑完了"。`systemd-run` 命令本身几乎总是成功（除非 systemd 不可用），
所以 `launcher()` 几乎从不抛异常，ACK 几乎总是走"正在出卡"那一支。

而预算闸门的判断在**另一个进程**（那个刚被排上去的瞬态单元）**内部**才执行。
两件事看起来像同一个时间点，实际隔着一次进程调度。

> 通用原则：**"异步任务已提交"和"异步任务会成功"是两个不同的事实，
> 分别在两个不同的时间点才能确定。** 只知道第一个就说第二个，说的是一句
> 提前下注的话。

### 3. 修法：在能确定的时候就把话说清楚，不要提前下注

`check_budget()` 本身设计成只读、无副作用（判据只有一份，`bin/biga-card` 与
这里共用同一次实现），代价是一次 DB 读，不占号、不花钱。`accept_trigger()`
占号之后、拉起之前，自己先问一遍：

```python
reasons = check_budget(exclude_decision_id=decision_id, path=path)
if reasons:
    return AckResult(accepted=False, ...,
        message=f"决策 {decision_id} 已占号，但{explain(reasons)}\n"
                f"这次没有拉起后台运行，不会再有后续推送——不是没反应，是没起跑。")
```

闸门会拒的话，直接不拉起——省下一次注定立刻自杀的 `systemd-run` + Python
解释器开销，也不需要再等那个进程真的跑起来再失败一次才知道结果。

🔴 **`exclude_decision_id` 不能漏**：`accept_trigger()` 在问闸门之前已经把
这个决策号占上了（占号是幂等键，必须先做）。如果不排除掉它自己，"距上次占号
多久"永远是在跟**这次自己**比，gap 恒为 0s，会导致**每一次全新请求**都被
误拒——`bin/biga-card` 自己那道检查（在瞬态单元内部）早就踩过这个坑，
文档字符串里专门记着，这次是同一个坑的第二处，提前照抄了修法。

### 4. 这不是新增一道闸门

拦截的权力**仍然只在** `bin/biga-card` 内部——那五道守卫（总闸/ownership/
单实例锁/预算闸门/第一次付费）的位置一个没动。这里新加的调用只是**多问了
一遍同一个只读函数**，用途是让 ACK 说真话，不是多一道能拦人的关卡。就算这次
新加的调用本身有 bug（比如漏判了某个条件），代价也只是"ACK 说错话"，不会
漏放任何真正该拦的请求——那道真正的防线不依赖它。

## 坑

改完顺手跑全量，一条既有测试红了：

```
test_不同trigger各拉起一次
  AssertionError: assert 1 == 2
```

这条测试背靠背触发两个不同的 trigger（`evt-A`、`evt-B`），断言两个都被拉起。
它原本要验证的是"不同 trigger 各自占自己的决策号，不会共用一个"（去重身份），
而两次调用在**测试代码里**几乎是同一时刻——现实世界里两次真实飞书事件隔这么
近本来就该被 `MIN_GAP` 拦下。这不是这条测试要验证的性质，让它跟新加的闸门
预检搅在一起就是测试范围没收住 ⇒ `monkeypatch.setattr(inbound, "check_budget",
lambda **kw: [])` 短路掉，测试回到它本来的焦点。

## 验证

```bash
cd ~/.openclaw-biga/workspace

# 1. 新增/改写的测试
python3 -m pytest tests/test_inbound_trigger.py -q
# 预期：32 passed

# 2. sabotage：把新加的预检那几行删掉，
#    test_闸门会拒时不拉起也不假装在处理 应该变红（已手工验证，见 CHANGELOG 本条）

# 3. 全量回归
python3 -m pytest -q
```

## 本章要点

| # | 要点 |
|---|---|
| 1 | "异步任务已提交"和"异步任务会成功"是两个不同的事实，分别在两个时间点才能确定——只知道前者就说后者，是提前下注 |
| 2 | 🔴 这个坑是真机触发撞见的，不是读代码找到的——G 节清单里"只能真的跑一次才能看"的那 4 项，价值就在这里 |
| 3 | 加一次预检不等于加一道闸门——如果真正的拦截权只在一处，新加的调用出错的代价上限就是"说错话"，不是"漏放" |
| 4 | `exclude_decision_id` 这类"排除自己刚做的那件事"的参数，本质是在防"用现在的自己当作比较基准" —— 同一个坑在两个不同的调用点都真实发生过 |
| 5 | 一条测试红了先问它原本要验证的是什么——如果新行为和它的焦点是两件不相关的事，短路掉新行为比改测试逻辑更对 |
