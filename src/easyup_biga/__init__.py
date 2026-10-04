"""EasyUp for BigA 2.0 —— 迁移中的应用包（确定性编排升级批 H-I 起）。

三个共享基础设施包的**真实实现**从 `skills/` 迁到这里：

    skills/_contract/  →  easyup_biga.domain/
    skills/_store/     →  easyup_biga.persistence/
    skills/_sources/   →  easyup_biga.providers/

`skills/_contract` / `_store` / `_sources` 原地留下**薄壳**（re-export），
让全仓既有的 `from _contract import ...` 之类的导入语句一个字符都不用改。
薄壳靠自身 `__file__` 相对路径把本包所在的 `src/` 挂上 `sys.path` ——
因此 `bin/biga-card` 拉起的子进程、`systemd-run` 脱树跑的那些（不经过
pytest 的 `pythonpath`）也能 import 到本包。

⚠️ 这一批只搬**目录位置**，文件内容与内部相对导入原样不动 ——
   跨包的 `from _contract import ...`（`persistence`/`providers` 里那几处）
   仍经旧壳解析，是有意为之的 Strangler 中间态。往哪继续迁、
   `_runtime`/`_snapshot` 与 `application`/`integrations`/`cli` 三个命名空间
   装什么，留给 H-II —— 没有设计依据之前不预建空目录。
"""
