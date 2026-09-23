---
name: card
description: "触发一次 A 股决策出卡（幂等 + 异步）。飞书 owner 发 /card 直达工具、绕开 main 的路由判断；同一请求重投不会重复出卡。"
user-invocable: true
disable-model-invocation: true
command-dispatch: tool
command-tool: biga-card-trigger__biga_card_trigger
command-arg-mode: raw
---

# /card —— 飞书触发出卡（结构性绕开 main，批 G-II）

> 📄 **类别**：这是一个 OpenClaw **技能**（`command-dispatch: tool`），不是常青设计文档。
> **覆盖**：`/card` 命令怎么被路由、它触发什么 ｜ **不覆盖**：出卡本身怎么跑
> （那在 `orchestrator.py` / `docs/design/deterministic-orchestration.md`）。

## 这个技能存在的唯一理由

把飞书「出卡」从**一次自由对话**（main 的 LLM 读消息、自己决定要不要出卡、怎么出）
变成一个**结构性命令**：owner 发 `/card` → OpenClaw 命令层识别 → `command-dispatch: tool`
**绕过 queue + model**、直达 `biga_card_trigger` 工具 → 幂等受理 + 异步拉起出卡。

🔴 **main 的 LLM 在这条链上没有一步。** 这不是「教 main 认出出卡请求再转发」，是
**main 压根收不到这条消息**（命令消息在命令层就被消费了）。它关掉的是 2026-09-21
两次事故的根子（19:31 四孤儿 spawn、21:03 出卡递归 L-14）——入口从 prompt 层面
挪到了代码/配置层面。反事实检验（探针 P2）：把 main 的 system prompt 整个清空，
`/card` 照样出卡。

## 前置（由 `deploy/openclaw/` 配置保证，`apply_config.py` 落地）

- `commands.text: true` —— 飞书没有原生斜杠菜单，`/card` 作为纯文本命令发。
- `commands.ownerAllowFrom` 含出卡人的飞书 id —— 只有 owner 能触发（付费动作）。
- `mcp.servers` 注册本技能的工具服务（`scripts/card_trigger_mcp.py`）——
  `command-tool: biga-card-trigger__biga_card_trigger` 才有得可指。
  🔴 **必须带 `biga-card-trigger__` 前缀**（P6 live 才暴露的坑）：`card_trigger_mcp.py`
  内部用 `FastMCP("biga-card-trigger")` + `@server.tool(name="biga_card_trigger")`
  注册，OpenClaw 对外暴露时按 `<server>__<tool>` 拼成完整名——只写裸名
  `biga_card_trigger` 时，command-dispatch 找不到这个工具，`/card` 直接报
  `Tool not available`。main 自己用 LLM 判断调用同一个工具时反而能算对
  （它看到的是解析后的完整工具列表），只有走 command-dispatch 这条**静态声明**
  的路径才会踩这个名字缺前缀的坑——离线测试只查字符串存在，测不出这个。
- `disable-model-invocation: true` —— model 选不到它，只能由人显式 `/card` 触发。

## 触发之后（都在代码里，不在这段文字里）

1. `biga_card_trigger` 工具 → `inbound.accept_trigger(origin="feishu", trigger_id=<飞书 event id>)`
2. **幂等**：`trigger_id` 原子占号（`reserve_decision_for_trigger`）。同一个 event 重投
   第二次 → 回「已在处理」、**不重跑**（探针 P1）。
3. **异步**：受理后**立刻**回 ACK，不等 170~200s 的出卡（探针 P3）。后台脱离进程树
   拉起 `bin/biga-card`（带 origin/trigger/decision），走**同一套五道守卫 + 同一个
   orchestrator**（人工 CLI 与飞书 inbound 不分叉）。
4. 出卡完成 → outbox 入队一条通知（批 G-I）→ `notify_worker --deliverer feishu`
   把结论投回飞书（`feishu_deliverer.py`）。

## 人工 CLI 不受影响

终端直接 `bin/biga-card` 仍是**同步**的、体验一字不变（没有那三个环境变量 ⇒
origin=cli ⇒ orchestrator 自己现占号）。异步只发生在飞书这条 inbound 路径上。
