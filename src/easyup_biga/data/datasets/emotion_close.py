from __future__ import annotations
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
def run(collector:EmotionCollector,trade_date:str,*,upstream_artifact_ids:tuple[str,...],db_path=None,data_root='data')->PublishResult:
    """发布收盘情绪。**必须**带上游 raw 血缘 —— 它是派生数据集。

    🔴 这里曾经把 collector 给的那个 dict 重新序列化当 raw。
    在生产路径上那个 dict 是 `decision_client` **自己从股池算出来的计数**
    （`up.total` / `broken.total / denom` / `max(streaks)`）——
    也就是说 raw 存的是我们的中间结果，不是任何人发给我们的字节。

    ⇒ `content_sha256` 因此是对我们自己的计算结果算的，
    回放校验它等于自己证明自己。真正的来源是同一次冻结的
    `cn.market.limit_pool`（它有真实的 eastmoney 响应）。

    ⚠️ 引用，不复制 —— 复制会让那份 raw 的 `provider_id` 写成
    `derived_biga`，而字节其实是行情源给的。
    """
    if not upstream_artifact_ids:
        raise ValueError(
            "收盘情绪是派生数据集，必须声明它派生自哪份原始响应。"
            "调用方应把股池那次发布的 raw_artifact_ids 传进来。")
    raw=collector.collect(trade_date); rec=normalize(raw,trade_date)
    return DatasetRowPublisher(db_path=db_path,data_root=data_root).publish(dataset_id=DATASET_ID,job_id=JOB_ID,provider_id=PROVIDER_ID,partition_key={'trade_date':trade_date},upstream_artifact_ids=upstream_artifact_ids,rows=[rec.to_dict()],as_of=trade_date,quality_status=DatasetStatus.COMPLETE,quality_metrics={'row_count':1})
