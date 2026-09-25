from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

class TradabilityStatus(StrEnum):
    OPEN='OPEN'; SUSPENDED='SUSPENDED'; NOT_LISTED='NOT_LISTED'; DELISTED='DELISTED'; UNKNOWN='UNKNOWN'

@dataclass(frozen=True, slots=True)
class DailyBar:
    instrument_id:str; trade_date:str; open:float; high:float; low:float; close:float; pre_close:float|None
    volume_shares:float; amount_cny:float; change:float|None; change_pct:float|None
    available_at:str; retrieved_at:str; provider_id:str
    def to_dict(self)->dict[str,Any]: return {k:getattr(self,k) for k in self.__dataclass_fields__}

@dataclass(frozen=True, slots=True)
class TradabilityRecord:
    instrument_id:str; trade_date:str; status:TradabilityStatus; suspension_reason:str|None=None
    limit_up_price:float|None=None; limit_down_price:float|None=None; available_at:str|None=None
    def to_dict(self)->dict[str,Any]:
        d={k:getattr(self,k) for k in self.__dataclass_fields__}; d['status']=self.status.value; return d

@dataclass(frozen=True, slots=True)
class AdjustmentFactorRecord:
    instrument_id:str; trade_date:str; adjustment_factor:float; available_at:str; provider_id:str
    def to_dict(self)->dict[str,Any]: return {k:getattr(self,k) for k in self.__dataclass_fields__}

@dataclass(frozen=True, slots=True)
class EmotionCloseRecord:
    trade_date:str; limit_up_count:int|None; limit_down_count:int|None; broken_limit_count:int|None
    broken_limit_rate:float|None; max_consecutive_limit:int|None; advance_count:int|None; decline_count:int|None
    advance_decline_ratio:float|None; available_at:str
    def to_dict(self)->dict[str,Any]: return {k:getattr(self,k) for k in self.__dataclass_fields__}

@dataclass(frozen=True, slots=True)
class ResolvedSnapshot:
    dataset_id:str; snapshot_id:str; status:str; knowledge_cutoff:str; partition_key:Mapping[str,str]
