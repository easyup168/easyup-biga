# EasyUp for BigA 2.0 — 环境安装指南

> 📄 **操作** · 随环境更新；**跑不通就是错的**
> **覆盖**：在已有 OpenClaw 实例旁并排装第二套 ｜ **不覆盖**：为什么这么设计（见 [`../design/architecture.md`](../design/architecture.md)）


> 在一台**已经跑着另一套 OpenClaw 生产实例**的机器上，
> 并排装第二套隔离的 OpenClaw，且保证生产零影响。
>
> 环境：WSL2 (Linux 6.6.87.2-microsoft-standard) / bash / nvm
> 本文所有 `$HOME` 均指当前用户家目录。命令可逐条复制执行。

---

## 0. 这份指南解决的问题

单机跑两套 OpenClaw，表面上官方支持（`--profile`），**实际有三个坑不在官方文档里**：

| 坑 | 后果 | 本文的解法 |
|---|---|---|
| npm 全局安装是**一个 node 版本一份** | 两套版本互相覆盖 | 新 node + `npm --prefix` 装到 profile 目录内（§3） |
| 少打一次 `--profile` 就读写生产状态 | OpenClaw CLI 会自动跑 doctor 迁移，**不是只读** | 强制 wrapper（§4） |
| 🔴 若你的生产有「扫 nvm 挑最高版 openclaw」的脚本 | 新装的会**静默劫持**生产 PATH | openclaw 刻意装在 nvm 之外 + 常驻守卫测试（§2/§5） |

第三个坑最隐蔽 —— 详见 §2。

---

## 1. 目标与前置状态

### 1.1 装完之后的两套系统

| | 生产（已存在） | BigA 2.0（本文要装） |
|---|---|---|
| Node | `v24.18.0` | **`v24.21.0`** |
| OpenClaw | `2026.7.1-2` | **`2026.9.5`** |
| 程序本体 | `$HOME/.nvm/versions/node/v24.18.0/lib/node_modules/openclaw/` | **`$HOME/.openclaw-biga/runtime/`** |
| 状态目录 | `$HOME/.openclaw/` | **`$HOME/.openclaw-biga/`** |
| Gateway 端口 | `18789` | **`19789`** |
| 命令 | `openclaw` | **`biga`**（wrapper） |

### 1.2 开工前先记录基线

出问题时要能证明「哪些东西没变」。

```bash
mkdir -p /tmp/biga-baseline && cd /tmp/biga-baseline
{
  echo "date: $(date -Is)"
  echo "node_versions: $(ls $HOME/.nvm/versions/node/ | tr '\n' ' ')"
  echo "nvm_default: $(cat $HOME/.nvm/alias/default)"
  echo "which_node: $(command -v node) $(node -v)"
  echo "which_openclaw: $(command -v openclaw)"
  echo "openclaw_version: $(openclaw --version 2>&1 | head -1)"
  echo "gateway_active: $(systemctl --user is-active openclaw-gateway.service)"
  echo "gateway_pid: $(systemctl --user show -p MainPID --value openclaw-gateway.service)"
  echo "port_18789: $(ss -lntp 2>/dev/null | grep -c ':18789')"
  echo "disk: $(df -h $HOME | tail -1)"
} | tee baseline.txt
```

⚠️ **`gateway_pid` 是最有力的证据** —— 整个安装过程它都不该变。

### 1.3 磁盘预算

| 项 | 体积 |
|---|---|
| node v24.21.0 | ~786 MB |
| openclaw 2026.9.5 + 331 依赖 | ~579 MB |
| **合计** | **~1.4 GB** |

---

## 2. 🔴 最重要的一条约束：openclaw 不要装进 nvm 的 bin

### 2.1 现象

许多生产系统（包括本项目）会用这种方式在 systemd 里定位 node 与 openclaw ——
因为 systemd service 的 PATH 是最小集（`/usr/bin:/bin`），不会 source 用户 profile，
而 `openclaw` 的 shebang 是 `#!/usr/bin/env node`，解释器仍从 PATH 取 `node`：

```python
# 扫 nvm，挑「装了 openclaw 的最高版本」
with_oc = [d for d in nvm_versions if (d / "bin" / "openclaw").exists()]
return max(with_oc or with_node, key=version_key) / "bin"
```

BigA 的 node（`v24.21.0`）**比生产的 `v24.18.0` 高**。
所以只要你在 BigA 的 node 下跑一次 `npm i -g openclaw`：

```
with_oc = [v24.18.0, v24.21.0]   →   max = v24.21.0
```

**生产所有 cron 的 PATH 会被静默换成 BigA 的 binary**，
而 cron 命令不带 `--profile` ⇒ 拿新版 binary 去操作生产状态目录。

### 2.2 为什么它特别危险

失败是**静默**的：推送函数只返回 `False`，调用方照样把条目记成「已推送」，
退出码 0，cron 状态全绿，审计全绿。

同型事故实际发生过：某个定时推送任务连续失败数百次而无人察觉，
根因就是 PATH 上的 node 版本差了 3 个 patch 号。

### 2.3 解法

**nvm 只提供 node 本体，openclaw 用 `--prefix` 装到 profile 目录内。**

这样 `$HOME/.nvm/versions/node/v24.21.0/bin/` 里只有 `node/npm/npx/corepack`，
没有 `openclaw` ⇒ 上面那个 `max()` 永远选不到它。

§5 会加一个常驻测试把这条钉死。

---

## 3. 安装

### 步骤 1 — 装 node v24.21.0（增量，不改默认）

```bash
export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh"
nvm install 24.21.0
```

⚠️ `nvm install` 会切换**当前 shell** 的 node，但不改 `default` alias（前提是 default 已存在）。

**验证**：

```bash
ls $HOME/.nvm/versions/node/                 # 应出现 v24.21.0
cat $HOME/.nvm/alias/default                 # 必须仍是旧版本（生产的）
ls $HOME/.nvm/versions/node/v24.21.0/bin/    # 应只有: corepack node npm npx
```

预期输出：
```
v18.20.8 v22.22.0 v24.13.1 v24.18.0 v24.21.0
24.18.0
corepack  node  npm  npx
```

> **为什么选 v24.21.0**：`openclaw@2026.9.5` 的 engines 是 `>=24.16.0 <25 || >=26.1.0`。
> v24.21.0 是 Latest LTS (Krypton)，与生产同主线。
> v26.x 到 2026-09 仍是 Current 未进 LTS，native 模块 prebuild 有缺口风险。
>
> ⚠️ 注意：生产的 v24.18.0 **其实也满足** 2026.9.5 的要求。
> 装新 node **不是版本需要，是隔离需要** —— 让两套系统连 node 都不共用。

### 步骤 2 — 装 openclaw 到 profile 目录（不进 nvm bin）

```bash
export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh"
nvm use 24.21.0

mkdir -p $HOME/.openclaw-biga/runtime
npm i --prefix $HOME/.openclaw-biga/runtime openclaw@latest
```

预期输出尾部：
```
added 331 packages in 18s
npm warn install-scripts 5 packages have install scripts not yet covered by allowScripts:
```

> **关于 `install-scripts` 警告**：npm 默认拦截 postinstall 脚本。
> 既有的那套实例也是这样装的（`npm config get allow-scripts` 为空），
> 运行数月无异常 —— 所以**不需要放行**。
> 若你的场景需要，可用 `npm i --allow-scripts=openclaw,...`。

**验证**：

```bash
RT=$HOME/.openclaw-biga/runtime
$RT/node_modules/.bin/openclaw --version
du -sh $RT
ls $HOME/.nvm/versions/node/v24.21.0/bin/       # 🔴 必须没有 openclaw
```

预期：
```
OpenClaw 2026.9.5 (ec9c1a1)
579M    <...>/.openclaw-biga/runtime
corepack  node  npm  npx
```

### 步骤 3 — 写 `biga` wrapper

**这一步不是便利性优化，是安全措施。** 少打一次 `--profile` 就会读写生产状态。

```bash
mkdir -p $HOME/.openclaw-biga/bin
cat > $HOME/.openclaw-biga/bin/biga <<'EOF'
#!/usr/bin/env bash
# BigA 2.0 —— OpenClaw CLI wrapper
#
# 为什么必须走这个 wrapper：
#   1. 强制注入 --profile biga。少写一次就会读写【生产】的 ~/.openclaw 状态目录
#      （openclaw CLI 会自动跑 doctor 迁移，这不是只读操作）。
#   2. 钉死 node 版本，与生产的 node 解耦。
#   3. 钉死 openclaw 本体路径 —— 它刻意装在 nvm 之外。
set -euo pipefail

BIGA_NODE="${BIGA_NODE:-$HOME/.nvm/versions/node/v24.21.0/bin/node}"
BIGA_HOME="${BIGA_HOME:-$HOME/.openclaw-biga}"
BIGA_ENTRY="$BIGA_HOME/runtime/node_modules/openclaw/dist/index.js"

[ -x "$BIGA_NODE" ]  || { echo "biga: node 不存在: $BIGA_NODE" >&2; exit 1; }
[ -f "$BIGA_ENTRY" ] || { echo "biga: openclaw 不存在: $BIGA_ENTRY" >&2; exit 1; }

# 让 openclaw 派生的子进程也拿到 BigA 的 node，而不是 PATH 里生产那个
export PATH="$(dirname "$BIGA_NODE"):$PATH"

exec "$BIGA_NODE" "$BIGA_ENTRY" --profile biga "$@"
EOF
chmod +x $HOME/.openclaw-biga/bin/biga
```

**验证**：

```bash
$HOME/.openclaw-biga/bin/biga --version      # → OpenClaw 2026.9.5 (ec9c1a1)
```

> 可选：加进 PATH —— `echo 'export PATH="$HOME/.openclaw-biga/bin:$PATH"' >> ~/.bashrc`
> ⚠️ 加之前确认 `biga` 这个名字不与其它命令冲突：`command -v biga`

---

## 4. 隔离验证（不要跳过）

`--profile` 的隔离承诺必须**实测**，不能只信文档。

```bash
# 记录生产状态文件的 mtime
stat -c '%y' $HOME/.openclaw/state/openclaw.sqlite
stat -c '%y' $HOME/.openclaw/openclaw.json

# 跑一条会读状态的 BigA 命令
$HOME/.openclaw-biga/bin/biga agents list

# 再看一次 mtime —— 必须完全没变
stat -c '%y' $HOME/.openclaw/state/openclaw.sqlite
stat -c '%y' $HOME/.openclaw/openclaw.json
```

`biga agents list` 预期输出：

```
Agents:
- main (default)
  Workspace: ~/.openclaw-biga/workspace
  Agent dir: ~/.openclaw-biga/agents/main/agent
  Routing rules: 0
```

> **顺带解决一处官方文档矛盾**：
> `gateway/multiple-gateways` 说命名 profile 的 workspace 默认在 `~/.openclaw/workspace-<profile>`，
> 而 `concepts/multi-agent` 的 Paths 表说是 `~/.openclaw-<profile>/workspace`。
> **实测以后者为准**：`~/.openclaw-biga/workspace`。
>
> 不想依赖这个默认值的话，显式配 `agents.defaults.workspace`。

### 对照实验：证明 wrapper 确实在起作用

```bash
# 不带 --profile 直接调（❌ 演示用，别在生产机器上随便跑）
$HOME/.openclaw-biga/runtime/node_modules/.bin/openclaw agents list
```
它会读**生产**的状态目录并打印生产的 agent 与 workspace。
这就是为什么 §3 步骤 3 的 wrapper 是必须的。

---

## 5. 常驻守卫（把 §2 的约束钉死）

光靠文档提醒不够 —— 几个月后没人记得。加一个测试，装错位置当场报红。

放进**生产仓库**的测试目录（不是 BigA 的），因为受害者是生产：

```python
# 生产仓库里的常驻守卫测试
_NVM_ROOT = Path.home() / ".nvm" / "versions" / "node"

def _nvm_versions_with_openclaw() -> list[Path]:
    if not _NVM_ROOT.is_dir():
        return []
    return sorted(d for d in _NVM_ROOT.glob("v*") if (d / "bin" / "openclaw").exists())

def test_only_one_nvm_version_has_openclaw():
    """有且只有一个 nvm 版本装 openclaw —— 否则挑「最高版」的逻辑可能选错。"""
    found = _nvm_versions_with_openclaw()
    assert len(found) == 1, (
        f"期望恰好 1 个 nvm 版本装有 openclaw，实际 {len(found)} 个: "
        f"{[d.name for d in found]}\n"
        "卸载方法：nvm use <那个版本> && npm uninstall -g openclaw\n"
        "改装到：npm i --prefix ~/.openclaw-biga/runtime openclaw@latest"
    )
```

完整版另含两项：生产侧的 PATH 解析指向那个唯一安装、以及 BigA 本体确实在 nvm 之外。

---

## 6. 完整验收清单

全部满足才算装完。

### BigA 侧

```bash
[ -x $HOME/.nvm/versions/node/v24.21.0/bin/node ]                 && echo "✅ node"
$HOME/.openclaw-biga/bin/biga --version | grep -q 2026.9          && echo "✅ openclaw"
[ ! -e $HOME/.nvm/versions/node/v24.21.0/bin/openclaw ]           && echo "✅ 未污染 nvm bin"
$HOME/.openclaw-biga/bin/biga agents list | grep -q openclaw-biga && echo "✅ profile 隔离"
```

### 生产侧 —— 六项必须与 §1.2 基线完全一致

```bash
diff <(
  echo "nvm_default: $(cat $HOME/.nvm/alias/default)"
  echo "which_node: $(command -v node) $(node -v)"
  echo "which_openclaw: $(command -v openclaw)"
  echo "openclaw_version: $(openclaw --version 2>&1 | head -1)"
  echo "gateway_active: $(systemctl --user is-active openclaw-gateway.service)"
  echo "gateway_pid: $(systemctl --user show -p MainPID --value openclaw-gateway.service)"
) <(grep -E '^(nvm_default|which_node|which_openclaw|openclaw_version|gateway_active|gateway_pid):' \
      /tmp/biga-baseline/baseline.txt) && echo "✅ 生产零变化"
```

🔴 **`gateway_pid` 不变 = 生产进程全程没重启过**，这是最硬的一条证据。

---

## 7. 回滚

```bash
# 1. 删 BigA 的一切（程序 + 状态 + wrapper）
rm -rf $HOME/.openclaw-biga

# 2. 删 node（nvm 会连它的 node_modules 一起清）
export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh"
nvm uninstall 24.21.0

# 3. 核对生产
cat $HOME/.nvm/alias/default
openclaw --version
systemctl --user show -p MainPID --value openclaw-gateway.service
```

回滚是**完全的** —— BigA 从未写入生产的任何路径（§4 已实测）。

---

## 8. 踩过的坑

| 坑 | 症状 | 原因 / 处理 |
|---|---|---|
| **少打 `--profile`** | 生产 `openclaw.sqlite` 的 mtime 变了 | OpenClaw CLI **会自动跑 doctor 迁移**，不是只读操作。这就是 wrapper 存在的理由。作者本人在调研阶段就踩过一次 |
| **以为要先升 node** | 走了弯路 | `openclaw@2026.9.5` engines 是 `>=24.16.0 <25 \|\| >=26.1.0`，很多现役 v24 已经满足。装新 node 是为**隔离**，不是为版本 |
| **node 差 3 个 patch 号起不来** | `Node.js >=22.22.3 <23 ... is required (current: v22.22.0)` | 装之前先 `npm view openclaw@<版本> engines` |
| **清理旧副本时误删整个 node** | 别的项目炸了 | 旧 node 版本下可能还有**别的**包在被引用（实例：某个 v22 下的 `puppeteer` 仍被一个脚本硬编码引用）。只 `npm uninstall -g openclaw`，别删整个版本目录 |
| **残留 systemd unit** | 指向已删路径的死引用 | 清理前先确认 `is-enabled` / `is-active`，移到备份目录而非直接删 |
| **两个实例共用同一个 IM 应用** | 同一条消息两个 bot 都回 | 第二个实例必须用**独立的**机器人应用；过渡期先只用本地 TUI |
| **端口间距不够** | browser/CDP 端口打架 | 官方要求 base 端口间距 **≥120**（browser = base+2，CDP 自动分配到 base+110）。本文用 18789 / 19789，间距 1000 |

---

## 9. 下一步

环境装完之后：

1. `biga setup` —— 初始化 profile，端口 `19789`，**跳过 IM 通道**
2. 在 `$HOME/.openclaw-biga/workspace/` 建 git 仓库与目录骨架
3. 建 agent、写角色契约、跑通第一条端到端链路

架构设计见 `docs/design/biga-2.0-architecture.md`。

---

## 附：版本与环境记录

| 项 | 值 |
|---|---|
| 安装日期 | 2026-09-19 |
| OS | WSL2 / Linux 6.6.87.2-microsoft-standard |
| nvm | `$HOME/.nvm` |
| BigA node | v24.21.0 (Latest LTS Krypton) / npm 11.19.0 |
| BigA openclaw | 2026.9.5 (ec9c1a1) |
| 生产 node | v24.18.0 |
| 生产 openclaw | 2026.7.1-2 (0790d9f) |
