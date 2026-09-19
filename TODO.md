# TODO

> 全局待办。每次新会话启动必读；完成事项立即更新。
> 格式：`- [ ] <事项>` + **验收** + **命令/路径**

---

## Phase 1 · 最简功能验证

目标：**一条最细的、端到端能走通的线** —— 验证机制（跨 agent spawn / 契约落库 / 回放），
不是覆盖面。机制通了，其余 agent 是复制。

### ✅ 已完成（2026-09-19）

- [x] 装 node v24.21.0（增量；未改 `default`、未卸旧版本）
- [x] 装 openclaw 2026.9.5 到 `~/.openclaw-biga/runtime/` —— **未进 nvm bin**（红线 R-2）
- [x] `bin/biga` wrapper —— 强制注入 `--profile biga`（红线 R-1）
- [x] 隔离实测：跑 BigA 命令后，另一套系统的 state / config mtime 未变
- [x] 守卫测试落在另一套仓库：一条常驻守卫测试（3 项）
- [x] workspace 骨架 + git init + 首个 commit
- [x] 文档：架构 / 安装指南 / 上游参考 / CLAUDE.md
- [x] 公开化处理：抽掉实盘系统可识别细节（`architecture.md` §9 改写）

### ⬜ 待做

- [ ] **决定 Phase 1 建几个 agent**
      建议 2 个（`main` + `emotion`）先验证机制。8 个全建 = 6 个零消费方组件。
      **等小飞裁定**
- [ ] `biga setup` —— profile 初始化，端口 **19789**，**跳过飞书**
      验收：`~/.openclaw-biga/openclaw.json` 生成，`biga agents list` 正常
- [ ] `skills/_contract/` —— `Evidence` / `AgentVerdict` / `DecisionCard`
      验收：`tests/test_contract_single_impl.py` 绿（AST 扫描确认无第二份实现）
- [ ] `skills/_store/db.py` + 三张表
      `decision_records` / `agent_runs` / `raw_market_snapshot`
      验收：`tests/test_no_raw_sqlite.py` 绿
- [ ] `skills/emotion-calc/` —— 真采一次 A 股情绪数据，输出合法 `AgentVerdict`
      验收：命令行跑出带 `as_of` 的 JSON
- [ ] 建 agent `emotion` + 写 `agents/emotion/AGENTS.md`
      `biga agents add emotion --workspace ~/.openclaw-biga/workspace/agents/emotion`
- [ ] 配 `main`(Supervisor)：仓库根的 `AGENTS.md` / `SOUL.md` / `IDENTITY.md`
      \+ `subagents.allowAgents: ["emotion"]` + `tools.agentToAgent.allow`
- [ ] 端到端跑通：`biga agent --agent main -m "今天市场情绪怎么样？"`
- [ ] `replay <decision_id>` —— 与在线路径共用同一份合成代码
- [ ] 隔离演练：`kill -9` BigA gateway，确认另一套系统 gateway pid 不变

### Phase 1 验收（全部满足才算过）

1. [ ] Supervisor **确实 spawn 了** `emotion`（`agent_runs` 有该行，不是自己编的）
2. [ ] `emotion` 返回**合法 `AgentVerdict`**，含 ≥1 条带 `as_of` 的 `Evidence`
3. [ ] 输出 Decision Card，含 状态 / 证据 / **缺失项** 三段
4. [ ] `decision_records` 落库 1 行，`replay` 重跑出一致结论
5. [ ] 故意打断数据源 → Card 显示 `UNKNOWN` + `missing` 非空，**不是 PASS**（红线 R-3）
6. [ ] 另一套系统：gateway pid / openclaw 版本 / `nvm default` / PATH / 18789 监听 五项未变
7. [ ] 生产侧的 PATH 解析 仍解析到 `v24.18.0`（红线 R-2 未被破坏）
8. [ ] 单次端到端 **< 60s**（只有 2 个 agent；8 个时才允许到 105s）

### Phase 1 明确不做

接飞书 / 建任何 cron / 任何下单路径 / 建其余 6 个 agent /
PostgreSQL / Redis / 回测 / 历史数据回补 / Web UI

---

## 待裁定

- [ ] **Phase 1 建 2 个还是 8 个 agent**（见上）
- [ ] GitHub 远端 —— 仓库建好后执行：
      `git remote add origin git@github.com-easyup168:easyup168/easyup-biga.git && git push -u origin main`
      （本机无 `gh` CLI；SSH 已通）
- [ ] 要不要装 `gh` CLI

---

## Phase 2+（不要提前做）

| Phase | 内容 | 出口条件 |
|---|---|---|
| 2 | 补齐 8 个 agent + Stage1/2 并行 + 完整 Card | 端到端 <105s；`missing[]` 在真实缺数据时非空 ≥5 次 |
| 3 | 数据层加厚 + 独立飞书应用 + 第一条 cron | 每条 cron 都有**被证明的**消费方 |
| 4 | 测量层：安慰剂基准 + Card 状态的区分力检验 | `BLOCK/WARNING` 与 T+5 结果按日聚类 t 有区分力 |
| 5 | （很久以后）自动下单 | **硬前提：Phase 4 通过**。在 Card 的 BLOCK 被证明有区分力之前接下单 = 新增一道空转门 |
