---
name: card
description: "触发一次 A 股决策出卡（幂等 + 异步）。收到出卡请求就跑一次 inbound.py、把它打印的 ACK 原样转达；出卡本身在后台脱离进程树跑，不要自己编排、不要等。同一请求重投不会重复出卡。"
user-invocable: true
---

# card —— 触发出卡（main 发起，程序编排，批 G-II）

> 📄 **类别**：这是一个 OpenClaw **技能**，不是常青设计文档。
> **覆盖**：收到出卡请求时 main 该跑什么 ｜ **不覆盖**：出卡本身怎么跑
> （那在 `orchestrator.py` / `docs/design/deterministic-orchestration.md`）。

## 什么时候用这个技能

用户在飞书里**要一张决策卡**时 —— 发 `/card`，或明确说「出卡 / 现在给我出张卡 /
帮我看看现在能不能进」这类意图。**只有这种「要出卡」的意图才用它。** 用户只是随口
问行情、聊某只票，不属于这里，照常正常回答。

## 收到出卡请求，跑这一条

```bash
python3 skills/card/scripts/inbound.py \
  --origin feishu \
  --trigger-id "<本次飞书请求的 event / message id>" \
  --json
```

- `--trigger-id` 传**本次触发消息的稳定 event / message id**（幂等键）——照原样传，
  **不要改写、不要编**。真的拿不到就跑不了这条、如实说明请在终端排查，**绝不要自己
  瞎填一个 id**（幂等全靠它，编一个等于让「同一请求重投只跑一次」这条保证悄悄失效）。
- 脚本会**立刻**打印一段 JSON（含 `message` 字段：ACK 文本）。把那句 `message`
  **原样转达给用户**。
- **跑完就停。** 不要再说别的，不要分析行情，不要预测结论。

## 🔴 你不做、也不能做的事

- **不要 spawn 任何 Specialist / risk / synthesizer**，不要自己去拉 Stage 1，不要直接
  跑 `bin/biga-card`、`orchestrator.py` 或任何出卡脚本。出卡的**编排、占号、等待、
  合成全在程序里** —— `inbound.py` 会用 `systemd-run` 把出卡**脱离你的会话进程树**、
  在后台异步拉起（约 170~200 秒），跑完自动把结论推回飞书。**那不是你的活。**
- **不要等它跑完。** 你跑完 `inbound.py`（它秒回）、转达完那句 ACK 就返回。一次出卡
  塞不进一次对话的响应窗口，等它 = 把你自己卡死两三分钟。
- 脚本回「已经在处理了」就**别再触发第二次**。

## 为什么是「跑一条脚本」而不是「你自己编排」

2026-09-21 出过两次事故（19:31 四个孤儿 spawn、21:03 出卡递归 L-14），根子都是
**出卡入口经过自由判断、由会话自己去拼一套 `sessions_spawn`**。所以出卡编排被收进了
**程序**：`inbound.py` → `accept_trigger` → 用 `systemd-run` 把 `bin/biga-card` 拉成
一个**脱离 agent 进程树**的后台运行（`entry_guard` 因此判成 HUMAN、不是 AGENT ⇒
递归那条路根本到不了）。

你（main）在这条链上只做**一件事**——跑一次 `inbound.py`（发起）。你**不参与编排**，
也没有能力参与（编排在另一个脱离你的进程里）。这就是这次要防的东西：**入口可以经过
你，但编排绝不经过你。**

## 前置（由 `deploy/openclaw/` 配置保证，`apply_config.py` 落地）

- `commands.text: true` —— 飞书没有原生斜杠菜单，`/card` 作为纯文本命令发、由命令层
  识别成对本技能的调用。
- 出卡流水线里被 spawn 的那些 agent（market/sector/…/risk/synthesizer）配了
  `tools.deny:[ask_user]`（非交互路径，禁死锁）；**main 不在其中** —— 你的 ask_user
  照常可用（探针 P5）。

## 人工 CLI 不受影响

终端直接 `bin/biga-card` 仍是**同步**的、体验一字不变（没有那三个环境变量 ⇒
origin=cli ⇒ orchestrator 自己现占号）。异步 + 脱离进程树只发生在 `inbound.py`
这条 inbound 路径上。
