from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence, Mapping
@dataclass(frozen=True,slots=True)
class ReviewOutcome:
    entry_close:float; t1_return:float|None; t5_return:float|None; t20_return:float|None; max_runup:float|None; max_drawdown:float|None

def _ret(base,v): return None if v is None else v/base-1.0
def calculate(entry_close:float,future_bars:Sequence[Mapping])->ReviewOutcome:
    closes=[float(x['close']) for x in future_bars]; highs=[float(x['high']) for x in future_bars]; lows=[float(x['low']) for x in future_bars]
    def at(n): return closes[n-1] if len(closes)>=n else None
    return ReviewOutcome(entry_close,_ret(entry_close,at(1)),_ret(entry_close,at(5)),_ret(entry_close,at(20)),(max(highs)/entry_close-1.0 if highs else None),(min(lows)/entry_close-1.0 if lows else None))
