# 更新日志

本文件记录 EasyUp for BigA 2.0 的版本与变更历史。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> **维护约定**
> - 每完成一段工作，先往 `[未发布]` 里追加条目，再开始下一段
> - 分类只用这六种：`新增` / `变更` / `修复` / `移除` / `废弃` / `安全`
> - 🔴 **条目要写「为什么」，不只是「做了什么」** —— 三个月后有价值的是前者
> - 发版时把 `[未发布]` 整段改成版本号 + 日期，并在文件底部补链接

---

## [未发布]

### 新增

- 接入 GitHub 远端 `easyup168/easyup-biga`（当前私有）。
  为该账号单独生成 SSH 密钥并用 Host 别名区分，本机另一账号的默认密钥不受影响

### 安全

- 重写全部 14 个 commit 的作者身份，统一为项目账号，并从文件内容中清除旧用户名。
  ⚠️ **转 Public 前需决定是否改用 GitHub 的 noreply 邮箱** ——
  真实邮箱进入公开 git 历史后全网可爬，而 noreply 同样能关联 commit 到账号。
  改的话要再次重写历史，最好在转公开前一次做完

### 待完成（Phase 1 验收第 8 条）

- 端到端延迟 **122.8s**，超出 60s 预算一倍。两次测量显示瓶颈在 LLM 轮次
  而非计算（skill 本身仅约 19s）。同步等待（`agents_wait`）未改善，
  下一步评估降低 `thinking` 档位

---

## [0.1.0] - 2026-09-19

**Phase 1 · walking skeleton。** 一条最细的端到端链路：
人提问 → Supervisor spawn Specialist → 真实采数 → 产出可回放的 Decision Card。

验收 8 过 / 1 未达标（延迟），117 条测试全绿。

### 新增

#### 契约层 `skills/_contract/`
- `Evidence` / `AgentVerdict` / `DecisionCard` 的**唯一实现**
- 四条契约铁律在 `__post_init__` 里**拒绝构造**而非记日志 ——
  静默 fail-open 时，放行与通过的日志长得一模一样，
  唯一可靠的对策是让非法状态无法被表示
- `as_of`（数据自身时间）与 `retrieved_at`（取回时间）分离；naive datetime 直接拒绝
- AST 扫描拦三种「第二套实现」：重名类、近名类、**字典版契约**
  （最隐蔽，不引入新符号，grep 搜不到）

#### 数据层 `skills/_store/`
- 三张表：`decision_records` / `agent_runs` / `raw_market_snapshot`
- **只追加由 SQLite 触发器强制**（6 个），不是代码约定 ——
  触发器连 `sqlite3` 命令行都绕不过
- `decision_records` 用 partial unique index 表达「一个 decision_id
  只能有一条在线记录，回放记录不限条数」
- 派生列只由唯一写入口从完整 Card 对象生成，不可能与真相源不一致
- AST 扫描禁止业务代码出现裸 `sqlite3`（import 与 connect 两条独立规则）

#### 技能 `skills/emotion-calc/`
- 真采 A 股情绪数据：涨停 / 跌停 / 炸板 / 炸板率 / 最高板 / 连板梯队 /
  封板质量 / 涨跌家数 / 情绪分
- 四个数据源并行采集（串行 26s → 并行 19s），备选主机链
- 只产出事实与分数，**不做周期归类** —— 判断逻辑散进 skill 会产生第二套口径

#### 技能 `skills/decision-card/`
- `card_ops.synthesize()` 纯函数，**在线与回放共用同一份组装代码**
- `replay --check` 一致性检查；回放追加不覆盖原始记录
- 组装版本写进 `model_ref`，「是模型变了还是代码变了」一秒有答案

#### Agent
- `main`（Supervisor）+ `emotion`（Specialist）
- 角色契约写在各自 `AGENTS.md` —— spawned session 不加载 `SOUL.md` / `IDENTITY.md`
- emotion 契约含「拿单日数据**说不了**什么」一节：LLM 会把不完整的信息
  补成完整的故事，必须明确告诉它哪些判断它的数据支撑不了

#### 验收与教程
- `tools/verify/phase1_acceptance.py` —— 8 条机器核对，**PENDING 不计为通过**
- `docs/tutorial/` 九章开源开发教程

### 变更

- 模型运行时由 `claude-cli` 改为 `openclaw` 内置 harness。
  桥接模式下本机***会拦掉 MCP server，导致跨 agent spawn 工具整体不可见

### 安全

- 按公开仓库纪律**重写全部 git 历史**，抽掉同机另一套实例的可识别细节
  （内部模块名、文件路径、具体数量、下单通道），保留工程教训与防护机制本身。
  ⚠️ 在新 commit 里脱敏是不够的 —— 旧 commit 里的原文原封不动还在，
  必须重写历史。仓库尚未 push，此时重写零代价
- `.gitignore` 排除 `USER.md` 与 Specialist 的脚手架残留
  （运行时每次 spawn 都会重建，`rm` 只解决当下）

### 已知问题

- **端到端 122.8s，超 60s 预算**（见 `[未发布]`）
- **anthropic 凭据与另一套实例共享同一条 *****。
  本机 Claude 账号为 ***，生成新长期 token 已被***。
  ⚠️ token 轮换时两套系统同时停，且排查方向天然指向 BigA（错的那边）。
  耦合与排查顺序记在 `CLAUDE.md`「已知耦合」一节，Phase 3 前必须替换
- 起 gateway 会自动创建 4 条运行时内置 cron，与「单一调度域」冲突。
  已裁定暂不处理，Phase 3 建自己的调度域时统一收编

---

## [0.0.1] - 2026-09-19

### 新增

- 隔离安装：node v24.21.0 + OpenClaw 2026.9.5，**刻意不装进 nvm 的 bin**
- `bin/biga` wrapper —— 强制注入 `--profile biga`。
  该 CLI 启动会跑 doctor 迁移，漏掉参数就是在改另一套实例的库
- workspace 骨架、架构设计文档、安装指南

[未发布]: https://github.com/easyup168/easyup-biga/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/easyup168/easyup-biga/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/easyup168/easyup-biga/releases/tag/v0.0.1
