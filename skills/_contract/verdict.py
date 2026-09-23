"""薄壳：真实实现已迁至 easyup_biga.domain.verdict（确定性编排批 H-I）。

父包 __init__.py 已把 src/ 挂上 sys.path（Python 保证先加载父包再加载子模块），
故此处直接 import。用 sys.modules 别名让 `_contract.verdict` 与
easyup_biga.domain.verdict 成为同一个模块对象 —— 连下划线私有名都一致，
杜绝"壳与本体漂移"。
"""
import sys as _sys

import easyup_biga.domain.verdict as _mod  # noqa: E402
_sys.modules[__name__] = _mod
