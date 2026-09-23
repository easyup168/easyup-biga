"""BigA 运行时适配层 —— 薄壳（re-export），真实实现在 easyup_biga.runtime（确定性编排批 H-II）。

保留旧包路径是为了让全仓既有的 `from _runtime import ...` 一个字符都不用改。
本壳用自身 __file__ 相对路径把仓库内 src/ 挂上 sys.path —— 不依赖 pytest 的
pythonpath，因此 bin/biga-card 拉起的子进程、systemd-run 脱树跑的（都不经过
pytest 配置）也能 import 到 easyup_biga。

⚠️ 真实实现看 src/easyup_biga/runtime/。这里只有 re-export，不要在此加逻辑。
"""
import pathlib as _p
import sys as _s

_SRC = _p.Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in _s.path:
    _s.path.insert(0, str(_SRC))

from easyup_biga.runtime import *  # noqa: F401,F403,E402
from easyup_biga.runtime import __all__  # noqa: F401,E402
