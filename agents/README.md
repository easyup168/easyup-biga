# Agents

每个子目录是一个 OpenClaw agent 的 **workspace**（即它的默认 cwd），
其中的 `AGENTS.md` 是该角色的**唯一契约载体**。

> ⚠️ 角色契约必须写在 `AGENTS.md`，不能写在 `SOUL.md` ——
> OpenClaw 官方明确：**spawned session 不加载 `SOUL.md` / `IDENTITY.md`**。
> 只有直接与人对话的 Supervisor 才额外需要那两个文件。

## 规划中的 roster（8 个）

| agentId | 角色 | workspace | 说明 |
|---|---|---|---|
| `main` | **BigA Supervisor** | **仓库根**（非本目录） | 人的唯一接触点；OpenClaw 默认 agent，零配置 |
| `market` | Market Agent | `agents/market/` | 市场状态。不选股 |
| `sector` | Sector Agent | `agents/sector/` | 资金与共识方向 |
| `news` | News Agent | `agents/news/` | 消息面 + **来源与新鲜度核验** |
| `technical` | Technical Agent | `agents/technical/` | 指标由 Python 算，Agent 只解释 |
| `emotion` | Emotion Agent | `agents/emotion/` | 情绪周期 |
| `risk` | Risk Agent | `agents/risk/` | 交易风险，**有否决权** |
| `discipline` | Trading Discipline Agent | `agents/discipline/` | 人的行为风险 |

**子目录按需创建** —— 不预建空目录。
每个 agent 在真正实现时才落地，避免产生「建了但没有消费方」的占位组件。

详见 `docs/design/architecture.md` §3。
