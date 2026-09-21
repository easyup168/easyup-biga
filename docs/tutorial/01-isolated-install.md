# 第 01 章 · 隔离安装：在已有实例旁边装第二套 OpenClaw

> 📄 **过程** · 写完即冻结（只追加「⏩ 后续变动」指针）
> **覆盖**：这一段是怎么建起来的 ｜ **不覆盖**：当前设计（见 [`../design/`](../design/architecture.md)）


> **本章目标**：在一台已经跑着 OpenClaw 的机器上，装出第二套完全独立的实例，
> 且让第一套**毫无感知**。
>
> 命令级的完整步骤在 [`docs/guide/install.md`](../guide/install.md)。
> 本章讲的是**为什么** —— 两个陷阱，都属于「默认行为在共享资源上悄悄生效」。

---

## 1. 先定义「隔离」到底要隔什么

把两套系统并排画出来，交集就一目了然：

```
┌────────────────────────────┐   ┌────────────────────────────┐
│  已有实例（先来的，别碰它）  │   │  BigA 2.0（本教程要建的）   │
│                            │   │                            │
│  node    : v24.18.0        │   │  node    : v24.21.0        │
│  openclaw: 较早版本         │   │  openclaw: 2026.9.5        │
│  profile : default         │   │  profile : biga            │
│  port    : 18789           │   │  port    : 19789           │
│  state   : ~/.openclaw     │   │  state   : ~/.openclaw-biga│
└────────────────────────────┘   └────────────────────────────┘
        唯一的交集是宿主机资源（CPU / 内存 / 磁盘 / 端口空间）
```

由此得到本项目的**第一条不变式**：

> **I-1**：BigA 的任何进程**不得以写模式**打开 `~/.openclaw/` 下的任何文件。

注意措辞是「写模式」。读是允许的 —— 真正会造成伤害的是写。

### 端口不能只挑个「看起来没人用的」

OpenClaw 一个实例实际占**一片**端口，不是一个：

| 用途 | 端口 |
|---|---|
| Gateway | `base` |
| Browser control | `base + 2` |
| CDP 自动分配 | `base + 11` ~ `base + 110` |

所以两个 base 之间**至少要隔 120**。本项目取 18789 / 19789，间距 1000，宽裕。

随手挑 18800 会怎样？它正好落在第一套的 CDP 区间里，
平时相安无事，直到某天对方开了第 12 个浏览器标签页 —— 然后两边都开始诡异地失败。

---

## 2. 陷阱一：`openclaw` 这个 CLI 不是只读的

最自然的写法：

```bash
openclaw agents list        # ❌
```

看起来只是列一下 agent。但 OpenClaw CLI 在启动时会**自动跑 doctor 迁移** ——
它会按当前版本去升级 state 目录的结构。而不带 `--profile` 时，它操作的是
`~/.openclaw`，也就是**已有实例的状态目录**。

于是：用新版本（2026.9.5）的 CLI 去迁移老版本实例的 state。对方下次启动时读到的
是一份被「升级」过的状态。

> 本项目作者在调研阶段实际踩过这一次。不是假想风险。

### 解法：一个 wrapper，把正确性变成默认值

```bash
# ~/.openclaw-biga/bin/biga
#!/usr/bin/env bash
set -euo pipefail

BIGA_NODE="${BIGA_NODE:-$HOME/.nvm/versions/node/v24.21.0/bin/node}"
BIGA_HOME="${BIGA_HOME:-$HOME/.openclaw-biga}"
BIGA_ENTRY="$BIGA_HOME/runtime/node_modules/openclaw/dist/index.js"

[ -x "$BIGA_NODE" ]  || { echo "biga: node 不存在: $BIGA_NODE" >&2; exit 1; }
[ -f "$BIGA_ENTRY" ] || { echo "biga: openclaw 不存在: $BIGA_ENTRY" >&2; exit 1; }

# 让 openclaw 派生的子进程也拿到 BigA 的 node，而不是 PATH 里的那个
export PATH="$(dirname "$BIGA_NODE"):$PATH"

exec "$BIGA_NODE" "$BIGA_ENTRY" --profile biga "$@"
```

从此项目里的所有命令都是：

```bash
BIGA=~/.openclaw-biga/bin/biga
$BIGA agents list           # ✅ --profile biga 不可能漏
```

**这个 wrapper 的价值不在于它做了什么，而在于它让「做错」变得不可能。**
靠纪律记住加 `--profile` 是行不通的 —— 你会记得 99 次，第 100 次就是事故。

> 通用原则：当一个操作「漏掉某个参数就会造成不可逆损害」时，
> 正确的做法不是写进文档提醒自己，而是**做一层让该参数无法被漏掉的封装**。

---

## 3. 陷阱二：程序本体装在哪里，比装什么版本更要命

这条是本章的核心，也是最不直观的一条。

### 现象

已有实例的定时任务，需要给自己注入 `PATH`。它的选法是这样的逻辑：

```python
# 扫 nvm 的所有 node 版本，挑「装了 openclaw 的最高版本」
with_oc = [d for d in nvm_versions if (d / "bin" / "openclaw").exists()]
return max(with_oc or with_node, key=version_key) / "bin"
```

这个写法在只有一套实例时完全正确。

现在我们装第二套，node 选了 **v24.21.0**（比对方的 v24.18.0 高）。
如果此时用最自然的命令装 openclaw：

```bash
npm i -g openclaw        # ❌ 装进 ~/.nvm/versions/node/v24.21.0/bin/
```

那么上面那段扫描的结果就变成：

```
with_oc = [v24.18.0, v24.21.0]   →   max = v24.21.0
```

**另一套实例的所有定时任务，PATH 会静默切到 BigA 的 binary。**
而那些定时命令是不带 `--profile` 的 —— 于是新版本 CLI 开始操作旧实例的 state。

### 为什么它特别危险

三个特征叠加：

1. **没有任何报错。** 版本更高、可执行文件存在、命令能跑。
2. **不是你动了对方的配置。** 你只是在自己的 node 下装了个包。
3. **触发点是「版本号比较」。** 只要你的 node 版本更高就会中招，
   而装新系统时选个更新的 node 几乎是本能。

这类事故的共同形态是：**测试绿、日志绿、监控绿，而事情已经坏了几个月。**

### 解法：nvm 只提供 node 本体，openclaw 装到 profile 目录里

```bash
npm i --prefix ~/.openclaw-biga/runtime openclaw@latest
```

于是 `v24.21.0/bin/` 里只有 `node`、没有 `openclaw`，那段扫描的结果仍是 `[v24.18.0]`。

升级时也只能用这条命令。**永远不用 `npm i -g openclaw`。**

### 双保险：把守卫测试放在「受害者」那一侧

这是本章最值得抄走的一个做法。

守卫测试不写在 BigA 仓库里，而是写在**另一套实例的仓库**里，断言它的
PATH 解析结果仍然指向 `v24.18.0`。

为什么？因为**风险的承受方才是应该报警的一方**。
如果 BigA 哪天不小心装错位置，BigA 自己一切正常 —— 它感觉不到任何异常。
只有对方能察觉。把测试放在对方那里，它会在 CI 里当场报红。

> 通用原则：守卫测试应该放在**会被伤害的系统**里，而不是**可能造成伤害的系统**里。
> 后者没有动机、也没有能力发现自己出了问题。

---

## 4. 验证

装完之后，这三条必须全过。

```bash
BIGA=~/.openclaw-biga/bin/biga

# ① 版本正确，且走的是 profile 目录里的本体
$BIGA --version
# 期望：OpenClaw 2026.9.5 (ec9c1a1)

# ② nvm 的 bin 里没有 openclaw
[ ! -e ~/.nvm/versions/node/v24.21.0/bin/openclaw ] && echo "✅ 陷阱二未被触发"

# ③ 跑 BigA 命令不会改到另一套实例的状态
stat -c '%y' ~/.openclaw/state/openclaw.sqlite     # 记下
$BIGA agents list
stat -c '%y' ~/.openclaw/state/openclaw.sqlite     # 必须逐位相同
```

第 ③ 条是整章的兜底验证。mtime 有任何变化都说明隔离没做到位，**停下来查清楚，别往下走**。

---

## 5. 本章要点

| 要点 | 一句话 |
|---|---|
| CLI 不是只读的 | `openclaw` 启动会跑 doctor 迁移，漏掉 `--profile` 就是在改别人的库 |
| 把正确性做成默认值 | wrapper 比「记得加参数」可靠一个数量级 |
| 安装位置 > 安装版本 | 装进共享的 `bin/` 目录，等于参与了别人的版本竞争 |
| 守卫测试放在受害者侧 | 加害方察觉不到自己出了问题 |
| 端口要按「片」隔离 | 一个实例占 100+ 个端口，间距至少 120 |

---

下一章：[02 · Profile 初始化](02-profile-setup.md) —— 为什么不走 onboarding 向导。
