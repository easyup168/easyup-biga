# 第 02 章 · Profile 初始化：为什么不走 onboarding 向导

> 📄 **过程** · 写完即冻结（只追加「⏩ 后续变动」指针）
> **覆盖**：这一段是怎么建起来的 ｜ **不覆盖**：当前设计（见 [`../design/`](../design/architecture.md)）


> **本章目标**：生成 `openclaw.json`，把端口、模型、并发配好，且全程不接 IM 通道。
>
> **本章产出**：`~/.openclaw-biga/openclaw.json`（不进 git）+ 仓库根的四个 bootstrap 文件。

---

## 1. 三条路，为什么选最笨的那条

OpenClaw 提供了三种初始化方式：

| 方式 | 行为 | 本项目是否采用 |
|---|---|---|
| `setup --wizard` | 交互式向导，一路问下来 | ❌ |
| `setup --non-interactive --flow quickstart ...` | 一条命令配完所有东西 | ❌ |
| `setup --baseline` | **只**创建配置骨架和目录，不做 onboarding | ✅ |

选 `--baseline` 的理由有两层。

**表层理由**：向导会顺手做一堆事 —— 配 auth、装 gateway 守护服务、配 channels、
装 skills、起 Control UI。每一件在这台机器上都需要单独判断（例如「装 gateway 守护服务」
意味着往 systemd 里塞一个常驻 unit，而本阶段明确不建任何常驻任务）。
一条命令里塞八件事，出问题时你不知道是哪一件。

**深层理由**：这是在一台**已经跑着别的服务**的机器上施工。
`--baseline` 的产出是可以完整预测的 —— 它只写配置文件和建目录。
而向导的产出取决于它问了什么、你答了什么、以及它对环境的自动探测结果。
**在敏感环境里，可预测性比便利性值钱得多。**

> 通用原则：脚手架工具的「一键完成」是为干净环境设计的。
> 环境越复杂，越应该拆成一组小的、各自可验证的步骤。

---

## 2. 执行

```bash
BIGA=~/.openclaw-biga/bin/biga

# 施工前先记基线（第 01 章的 ③，每次改环境都要做）
stat -c '%y' ~/.openclaw/state/openclaw.sqlite

$BIGA setup --baseline --workspace ~/.openclaw-biga/workspace

stat -c '%y' ~/.openclaw/state/openclaw.sqlite     # 必须与上面相同
```

输出：

```
Wrote ~/.openclaw-biga/openclaw.json
Workspace OK: ~/.openclaw-biga/workspace
Sessions OK: ~/.openclaw-biga/agents/main/sessions

Setup complete: config, workspace, and session directories are ready.
```

### 坑：`--baseline` 和 `--skip-bootstrap` 互斥

第一次尝试时我加了 `--skip-bootstrap`，想阻止它往仓库里写默认文件：

```
--baseline cannot be combined with: --skip-bootstrap.
```

所以 bootstrap 一定会发生。跑完 `git status` 会看到四个新文件：

```
?? AGENTS.md
?? IDENTITY.md
?? SOUL.md
?? USER.md
```

**跑完立刻 `git status` 看一眼**是个好习惯 —— 它能告诉你这条命令到底往仓库里放了什么。
本项目仓库在执行前是干净的，所以这四个文件百分之百是它造的，没有歧义。

四个文件都是空模板，没有敏感内容。处理方式：

| 文件 | 处理 |
|---|---|
| `AGENTS.md` | 第 06 章用 Supervisor 的角色契约覆盖 |
| `SOUL.md` / `IDENTITY.md` | 同上 |
| `USER.md` | **加进 `.gitignore`** —— 它存的是使用者的个人偏好，不是项目契约，且本仓库要公开 |

---

## 3. 为什么 `main` 不用配 workspace

生成的配置里，`main` 的 workspace 正好是仓库根：

```json
"agents": {
  "defaults":  { "workspace": "~/.openclaw-biga/workspace" },
  "entries": {
    "main": {
      "workspace": "~/.openclaw-biga/workspace",
      "agentDir":  "~/.openclaw-biga/agents/main/agent"
    }
  }
}
```

这是**刻意设计的零配置**：OpenClaw 默认 agent 就叫 `main`，默认 workspace 就是
`<stateDir>/workspace`。把代码仓库直接放在那个位置，Supervisor 就不需要任何 workspace 配置。

而 7 个 Specialist 会在建立时用 `--workspace` 显式指到 `workspace/agents/<id>/`。

**为什么 Supervisor 的 cwd 要大、Specialist 的要小**：
Supervisor 是人直接对话的对象，它需要能看到整个仓库；
Specialist 是有界工人，窄 cwd 反而防止它乱翻。

### `agentDir` 绝不跨 agent 复用

`agentDir` 存的是**认证凭据 + 会话历史**。每个 agent 一个，路径是
`~/.openclaw-biga/agents/<id>/agent/`。

共用会导致会话历史串台 —— 而这种串台在多 Agent 系统里极难排查，
因为症状是「某个 Agent 偶尔知道它不该知道的事」。

---

## 4. 改配置：用 `config patch`，不要手改 JSON

`--baseline` 只给骨架，端口和模型还得自己配。

**不要用编辑器改 `openclaw.json`。** 用官方的非交互接口：

```bash
cat > setup.patch.json5 <<'PATCH'
{
  gateway: {
    mode: "local",
    port: 19789,        // 与已有实例的 18789 间距 1000 ≥ 120（见第 01 章）
    bind: "loopback",
  },
  agents: {
    defaults: {
      model: {
        primary: "anthropic/claude-sonnet-5",
        fallbacks: ["anthropic/claude-haiku-4-5-20251001"],
      },
      subagents: {
        maxConcurrent: 6,       // ≥6 是功能要求，见下
        runTimeoutSeconds: 180,
      },
    },
  },
}
PATCH

$BIGA config patch --file setup.patch.json5 --dry-run   # 先干跑
$BIGA config patch --file setup.patch.json5             # 再真写
$BIGA config validate
```

`--dry-run` 的输出：

```
Dry run successful: 7 update(s) validated against ~/.openclaw-biga/openclaw.json.
```

三个好处，都不是「方便」：

1. **一次校验写入** —— 整个 patch 要么全过要么全不过，不会写出半截的坏配置。
2. **`--dry-run` 可以先验** —— 手改文件没有这一步。
3. **schema 校验** —— 打错一个键名会当场报错。手改的话，错误的键会被**静默忽略**，
   你以为配了，其实没配。

> 第 3 点对应本项目的一类重点防范对象：**死配置**。
> 一个因为拼写错误而永远不生效的配置项，不会报任何错，它只是安静地不存在。

---

## 5. 两个配置值的理由

### `maxConcurrent: 6` 是功能要求，不是性能调优

系统的调用链是分阶段并行的：

```
Stage 1：并行 5 个分析 Agent   → 取最慢那个，20-40s
Stage 2：并行 2 个制衡 Agent   → 15-25s
Stage 3：Supervisor 合成       → 20-30s
                       合计    ≈ 60-105s
```

如果 `maxConcurrent < 5`，Stage 1 就会退化成部分串行，
总耗时从 ~100s 掉到 **2-5 分钟**。对盘中使用来说，这是「能用」和「不能用」的区别。

**所以这个数字不是调出来的，是算出来的**：Stage 1 有 5 个并行任务，取 6 留一格余量。

### 模型：本阶段不做分层

架构文档里为不同角色规划了 Opus / Sonnet / Haiku 三档
（Supervisor 做最终裁定用最强的，规则化的角色用最便宜的）。

**但本阶段统一用 `sonnet-5` + `haiku-4.5` fallback，不做分层。**

理由：分层的前提是知道哪个角色需要多强的模型，而这件事**现在没有数据**。
先把机制跑通、把每次调用的耗时和 token 记进 `agent_runs` 表，
等拿到真实的成本分解，再决定给谁降档。

> 通用原则：**不预先优化，但要保证测得出来。**
> 「先记账，后优化」比「先猜一个配置，后面忘了改」强。

---

## 6. 不接 IM 通道，以及一个具体的失败场景

配置里的 `channels` 段是空的。本阶段唯一的交互入口是本地 TUI（`$BIGA chat`）。

这不只是「简化范围」。同机上的已有实例已经占用了一个 IM 机器人应用。
如果两个 gateway 共用同一个应用：

> **同一条消息，两个 bot 都会回。**

而且两边都「工作正常」—— 各自的日志里都是一次正常的消息处理。
要接 IM，必须先申请一个**独立的机器人应用**，那是后续阶段的事。

---

## 7. 验证

```bash
BIGA=~/.openclaw-biga/bin/biga

# ① 配置合法
$BIGA config validate
# → Config valid: ~/.openclaw-biga/openclaw.json

# ② 关键值确实写进去了（而不是被静默忽略）
for k in gateway.port gateway.bind agents.defaults.model.primary \
         agents.defaults.subagents.maxConcurrent; do
  printf '%-45s = %s\n' "$k" "$($BIGA config get $k | tr -d '\n')"
done

# ③ roster 正确，且 workspace / agentDir 都在 .openclaw-biga 下
$BIGA agents list

# ④ 没有接任何 IM 通道
$BIGA config get channels
# → Config path is valid but unset: channels.

# ⑤ 端口：19789 归自己，18789 仍归已有实例且没被动过
ss -lntp | grep -E '18789|19789'
```

第 ② 步是在防「死配置」：**不要相信命令返回 0 就等于配置生效了，读回来对一遍。**

---

## 8. 本章要点

| 要点 | 一句话 |
|---|---|
| 复杂环境下拆步骤 | 「一键完成」的产出不可预测，`--baseline` 的产出可预测 |
| 跑完就 `git status` | 让工具对仓库做的改动无所遁形 |
| 用 `config patch` 而非手改 | 一次校验写入 + `--dry-run` + schema 挡住拼写错误 |
| 配完要读回来 | 静默忽略的错键是「死配置」的主要来源 |
| 并发数是算出来的 | `maxConcurrent ≥ 5` 决定端到端是 100 秒还是 5 分钟 |
| 先记账后优化 | 模型分层需要数据支撑，先让成本可观测 |

---

上一章：[01 · 隔离安装](01-isolated-install.md)　|　下一章：03 · 契约层（编写中）
