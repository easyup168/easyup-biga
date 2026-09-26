# 带密钥的 skill 放哪 —— 仓库之外，靠机制而不是靠记性

> 📄 **操作** · 跑不通就是错的
> **覆盖**：怎么把一个需要 API Key 的 skill 接进 BigA 而不让它进 Public 仓库
> **不覆盖**：具体某个 skill 的用法（那在它自己的 `SKILL.md` 里）

---

## 问题

`workspace/skills/` 是 git 跟踪的目录，而**这个仓库是 Public 的 —— push 即发布**。
已公开的内容撤不回来（删分支也可能留在 fork、缓存与镜像里）。

所以带 API Key 的 skill 不能放那儿。

## 为什么不用 `.gitignore`

`.gitignore` 是一条**需要人记得**的规则：

- `git add -f` 绕得过
- 哪天有人整理 `.gitignore` 就没了
- 模板/脚手架复制时不会跟着走

> 一条只在「所有人都记得」时才成立的防线，不是防线。

## 做法：`skills.load.extraDirs`

OpenClaw 原生支持额外的 skill 根目录（schema 里的描述是「额外的共享 skill 根，
用于 sibling repos 或 shared skill packs」）。

⇒ skill 放 `~/.openclaw-biga/local-skills/`，**在仓库之外**。
`git add` 根本够不着它 —— 这条路不依赖任何人的记性。

进仓库的只有**路径与机制**（`deploy/openclaw/apply_config.py` 里的
`_LOCAL_SKILLS_DIR`），内容留在外面。与凭据那条纪律同一个形状：
可迁移的工程结论进仓库，本机的特殊情况不进。

---

## 步骤

### 1. 放 skill

```bash
mkdir -p ~/.openclaw-biga/local-skills/<name>
# 把 SKILL.md 与脚本放进去
```

🔴 **`SKILL.md` 必须有 YAML frontmatter**，否则 OpenClaw **不会报错，只是看不见它**：

```markdown
---
name: <name>
description: "一句话说清什么时候该用它。"
user-invocable: true
---
```

⚠️ 实测踩过：从别处搬来的 skill 用的是旧格式（直接 `# 标题` 开头），
装好之后 `skills list` 里**什么都没有**，也没有任何错误信息。

### 2. 登记目录（配置即代码）

已经登记在 `deploy/openclaw/apply_config.py` 里，跑一次即可：

```bash
python3 deploy/openclaw/apply_config.py            # dry-run，先看 patch
python3 deploy/openclaw/apply_config.py --apply
```

### 3. 配密钥

放 `~/.openclaw-biga/.env`（OpenClaw 从 `resolveConfigDir() + ".env"` 读，
对 biga profile 就是这个位置）：

```bash
printf 'YOUR_KEY_NAME=<值>\n' >> ~/.openclaw-biga/.env
chmod 600 ~/.openclaw-biga/.env
```

🔴 **不要**放 `workspace/.env`。OpenClaw 也会读进程 CWD 下的 `.env`，
而网关的 CWD 就是 workspace —— 那等于把密钥放回仓库里。

### 4. 验证

```bash
~/.openclaw-biga/bin/biga skills list | grep <name>
```

预期：出现一行，`Source` 列是 `openclaw-extra`。

---

## 两个容易踩的

### 位置约定型的路径计算，换个位置不报错，只指向别处

搬过来的脚本里常见这种写法：

```python
_WORKSPACE = Path(__file__).resolve().parent.parent.parent
_CACHE_DB = _WORKSPACE / "data" / "cache.db"
```

它依赖「脚本在 `<workspace>/skills/<name>/scripts/` 下」。搬到 `local-skills/`
之后层数**恰好还是三层**，所以它不报错 —— 只是悄悄指向了另一个目录。

⇒ 改成显式、让状态跟着 skill 走，整个目录自包含、可整体搬走。

### 共用一把 Key，配额是共享的，而用量统计不是

如果同一把 key 也在别处用着，「每天 N 次」是**两边一起算**的，
而各自的本地用量库互不知情 ⇒ **两边都会低报**。

⚠️ 这不会报错，只会在某天提前耗尽配额，而 `--usage` 说还剩很多。
