# 第 09 章 · 隔离演练：杀死自己，确认邻居毫发无伤

> 📄 **过程** · 写完即冻结（只追加「⏩ 后续变动」指针）
> **覆盖**：这一段是怎么建起来的 ｜ **不覆盖**：当前设计（见 [`../design/`](../design/architecture.md)）


> **本章目标**：`kill -9` 自己的 gateway，逐项核对邻居没有受到任何影响。
>
> **本章产出**：一次真实演练记录 + 一个意料之外的发现。

---

## 1. 为什么隔离要**演练**，不能只靠设计

前面八章做了大量隔离设计：独立 profile、独立端口、独立 state 目录、
wrapper 强制 `--profile`、openclaw 装在 nvm 之外。

这些都是「按设计不会互相影响」。

但整个项目的第二条不变式写的是：

> **I-2**：BigA 崩溃、写坏自己的库、把端口占死 —— 邻居必须**毫发无伤**。

「按设计不会」和「实际不会」之间隔着所有你没想到的耦合点。
唯一能跨过这条鸿沟的办法是**真的把自己杀掉，然后去看邻居**。

> **通用原则**：隔离性是一个**可测量的属性**，不是一个设计意图。
> 没做过破坏性演练的隔离，只能算「打算隔离」。

---

## 2. 先记基线 —— 而且要记「会变但不该变」的东西

演练的价值完全取决于基线记得够不够细。记了六项：

```bash
echo "--- 18789 监听者 ---"; ss -lntp | grep 18789
echo "--- 该进程启动时间 ---"; ps -o lstart= -p <pid>
echo "--- state mtime ---";  stat -c '%y' ~/.openclaw/state/openclaw.sqlite
echo "--- config mtime ---"; stat -c '%y' ~/.openclaw/openclaw.json
echo "--- nvm default ---";  cat ~/.nvm/alias/default
echo "--- nvm bin 里有 openclaw 的版本 ---"; ls ~/.nvm/versions/node/*/bin/openclaw
```

结果：

```
18789 监听 pid:   391
启动时间:         Sat Sep 19 06:17:40 2026
state mtime:      2026-09-19 15:47:43.855767307 +0800
config mtime:     2026-08-16 18:59:15.046429632 +0800
nvm default:      24.18.0
nvm bin openclaw: /home/…/node/v24.18.0/bin/openclaw
```

### 为什么要记**启动时间**，而不只是 pid

这是本章最容易被跳过、也最值得说的一条。

只核对 pid 是不够的：**一个被重启的服务完全可能拿回同一个 pid**（尤其在 pid 回绕时），
更常见的是 supervisor 把它拉起来后 pid 变了但你以为「本来就是这个数」。

`lstart` 能区分「它一直活着」和「它死过又被拉起来了」。
这两件事对不变式 I-2 来说是**天差地别**的 —— 后者意味着你确实把邻居搞崩了，
只是有人替它兜了底。

> **通用原则**：验证「某个东西没被影响」时，
> 要找一个**如果它被影响过就一定会改变**的观测量。
> pid 不是那样的量，进程启动时间是。

### `nvm bin openclaw` 这一项在验什么

第 01 章那个陷阱：如果 BigA 不小心把 openclaw 装进了 nvm 的 bin，
邻居的定时任务会静默切到 BigA 的 binary。

这一项确认「nvm 里只有 v24.18.0 带 openclaw」——
也就是**那条选最高版本的扫描逻辑仍然会选到邻居自己的那个**。

---

## 3. 起自己的 gateway

```bash
~/.openclaw-biga/bin/biga gateway --port 19789 > gw.log 2>&1 &
sleep 12
ss -lntp | grep 19789
```

```
LISTEN 0 511 127.0.0.1:19789 users:(("MainThread",pid=64645,fd=52))
LISTEN 0 511     [::1]:19789 users:(("MainThread",pid=64645,fd=54))
```

进程命令行确认 wrapper 生效了：

```
/home/…/node/v24.21.0/bin/node
  /home/…/.openclaw-biga/runtime/node_modules/openclaw/dist/index.js
  --profile biga gateway --port 19789
```

三样都对：BigA 自己的 node（v24.21.0）、profile 目录里的 openclaw 本体、
`--profile biga`。

---

## 4. ⚠️ 起 gateway 时的一个意外发现

日志里有这么一行：

```
[plugins] memory-core: created managed dreaming cron job.
```

查了一下状态库，发现**自动创建了 4 条定时任务**：

```
memory-core:memory-dreaming-promotion
heartbeat:main
skill-collection-review:main
skill-collection-review:emotion
```

第四条尤其说明问题：它是在我**添加 emotion agent 的那一刻**被创建的。

这与本项目的两条明文规定冲突：

- 架构 §8：BigA 只有**一个调度域**，不使用运行时内置 cron
- Phase 1 边界：**一条定时任务都不建**

它们目前不会造成实际影响（只在 gateway 运行期间生效，而 Phase 1 不常驻 gateway），
但它属于本项目重点防范的那一类：**存在但没有被证明的消费方**。

**这个发现本身就是演练的产出。** 如果只是「设计上应该没问题」，
不会有人去看 gateway 的启动日志，也就不会发现有人在替你建定时任务。

> **通用原则**：演练的价值不只在于验证你**预期的**那几项，
> 更在于逼你去看那些平时不看的输出（启动日志、状态表、进程树）。

---

## 5. 演练

```bash
BIGA_PID=$(ss -lntpH | grep 19789 | grep -oP 'pid=\K[0-9]+' | sort -u | head -1)
kill -9 "$BIGA_PID"
sleep 6
```

用 `kill -9` 而不是 `kill`：SIGTERM 会走正常关闭流程，
而我们要测的恰恰是**没有机会做任何清理的崩溃**。

### 核对结果

```
--- BigA 19789 ---
  ✅ 19789 不再监听

--- 邻居六项 ---
  18789 监听 pid:    391                              （基线 391）
  该进程启动时间:     Sat Sep 19 06:17:40 2026         （基线一致）
  state mtime:       2026-09-19 15:47:43.855767307    （基线一致）
  config mtime:      2026-08-16 18:59:15.046429632    （基线一致）
  nvm default:       24.18.0                          （基线一致）
  nvm bin openclaw:  …/v24.18.0/bin/openclaw          （基线一致）

--- 邻居 gateway 还活着吗 ---
  ✅ pid 391 存活
```

**六项逐位一致，启动时间没变** —— 邻居不但活着，而且根本没被重启过。

### 顺带检查孤儿进程

`kill -9` 不会给进程清理子进程的机会。BigA 的 gateway 启动时 spawn 过一个 broker
（日志里 `spawn broker ready pid=64737`），要确认它没变成孤儿：

```bash
ps -eo pid,ppid,args | grep -F 'openclaw-biga' | grep -v grep
```

```
  ✅ 无残留
```

孤儿进程是会累积的 —— 每崩一次留一个，跑几个月之后内存和端口都会出问题。
**这一项属于「不检查就永远不会有人发现」的那类。**

---

## 6. 这次演练证明了什么，没证明什么

**证明了**：

- BigA 进程被强杀，邻居的进程、端口、状态文件、配置文件全部不受影响
- BigA 不会在崩溃后留下孤儿进程
- 第 01 章那条 nvm 约束在经历了完整的安装 + 建 agent + 起 gateway 之后仍然完好

**没有证明**：

- BigA **写坏自己的库**时邻居会怎样（本次只测了进程崩溃）
- 长时间运行后的资源竞争（两套系统共享 CPU / 内存 / 磁盘）
- BigA 占死端口的情况（端口间距 1000，理论上碰不到，但没实测）

把「没证明的」明确写出来，是因为一次通过的演练很容易被当成
「隔离性已验证」。它验证的只是**你测过的那几条路径**。

---

## 7. 本章要点

| 要点 | 一句话 |
|---|---|
| 隔离性是可测量属性 | 没做过破坏性演练的隔离，只能算「打算隔离」 |
| **记启动时间，不只记 pid** | pid 相同可能是被重启过；`lstart` 能区分 |
| 找「被影响就一定会变」的观测量 | 这是设计核对项的判据 |
| 用 `kill -9` 不用 `kill` | 要测的是没有机会清理的崩溃 |
| 检查孤儿进程 | 不检查就永远不会有人发现，直到几个月后资源耗尽 |
| 演练会逼你看平时不看的输出 | 4 条自动创建的 cron 就是这么发现的 |
| 明确写出「没证明什么」 | 否则一次通过会被当成「隔离性已验证」 |

---

上一章：[08 · 回放](08-replay.md)　|　返回：[教程索引](README.md)
