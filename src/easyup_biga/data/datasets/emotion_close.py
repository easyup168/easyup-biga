from __future__ import annotations
import json
from typing import Mapping, Any, Protocol
from easyup_biga.data.contracts import DatasetStatus
from easyup_biga.domain import now_cn
from ..records import EmotionCloseRecord
from ..publication import DatasetRowPublisher, PublishResult
DATASET_ID='cn.market.emotion_close'; PROVIDER_ID='derived_biga'; JOB_ID='emotion-close'
class EmotionCollector(Protocol):
    def collect(self,trade_date:str)->Mapping[str,Any]: ...
class LegacyEmotionCollectorAdapter:
    def __init__(self,fn): self.fn=fn
    def collect(self,trade_date:str)->Mapping[str,Any]: return self.fn(trade_date)
def normalize(data:Mapping[str,Any],trade_date:str)->EmotionCloseRecord:
    adv=data.get('advance_count'); dec=data.get('decline_count'); ratio=(float(adv)/float(dec) if adv is not None and dec not in (None,0) else None)
    return EmotionCloseRecord(trade_date,data.get('limit_up_count'),data.get('limit_down_count'),data.get('broken_limit_count'),data.get('broken_limit_rate'),data.get('max_consecutive_limit'),adv,dec,ratio,now_cn().isoformat())
def run(collector:EmotionCollector,trade_date:str,*,db_path=None,data_root='data')->PublishResult:
    raw=collector.collect(trade_date); rec=normalize(raw,trade_date)
    return DatasetRowPublisher(db_path=db_path,data_root=data_root).publish(dataset_id=DATASET_ID,job_id=JOB_ID,provider_id=PROVIDER_ID,partition_key={'trade_date':trade_date},raw_text=json.dumps(raw,ensure_ascii=False,sort_keys=True),rows=[rec.to_dict()],as_of=trade_date,quality_status=DatasetStatus.COMPLETE,quality_metrics={'row_count':1})
