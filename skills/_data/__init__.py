"""Decision-data thin shell.  Specialists import this boundary, never provider modules."""
import pathlib as _p
import sys as _s
_SRC = _p.Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in _s.path:
    _s.path.insert(0, str(_SRC))
from easyup_biga.data.decision_client import *  # noqa: F401,F403,E402
from easyup_biga.data.decision_client import __all__  # noqa: F401,E402
