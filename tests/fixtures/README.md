# 测试 fixture

## 为什么这里有一份「真实」日志

外部评审（`ask_user` hardening §25/§26）：

> Watchdog 必须读取**真实 OpenClaw Runtime representation**，
> 不要根据推测的 schema 编写。

理由是本项目刚栽过一次：检测器查 `tool_use` / `input`，
真实 transcript 是 `toolCall` / `arguments` ⇒ 扫出 0 条，
于是得出「递归假设不成立」这个错误结论（`architecture.md` §9 **L-13**）。

> 手造 fixture 只能证明「解析器能解析我自己写的 fixture」。

## `stalled-session.log`

2026-09-21 出卡递归事故期间，网关 journal 实际打出的
`[diagnostic] stalled session:` 行。

🔴 **字段名、顺序、格式一字未改** —— 那才是解析器依赖的东西。
只替换了两个标识符（`sessionId` / `activeToolCallId`），
因为公开仓库纪律不收录运行时产生的具体标识。

它守着一件手造 fixture 永远发现不了的事：**运行时换了日志格式**
⇒ 解析器静默失效 ⇒ 看门狗不再开火。
