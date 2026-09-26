from __future__ import annotations
import json, math, urllib.parse
from typing import Any, Mapping
from easyup_biga.domain import now_cn
from .eod_bar import EodBar, EodFetchResult
from .http import SourceError, get_json_and_text

_HOSTS=('82.push2.eastmoney.com','push2.eastmoney.com'); _PATH='/api/qt/clist/get'; _REF='https://quote.eastmoney.com/'
_FS='m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23'; _FIELDS='f2,f3,f4,f5,f6,f12,f13,f14,f15,f16,f17,f18'
def _page(pn:int,pz:int)->tuple[Mapping[str,Any],str]:
    q=urllib.parse.urlencode({'pn':pn,'pz':pz,'po':1,'np':1,'fltt':2,'invt':2,'fid':'f12','fs':_FS,'fields':_FIELDS},safe='+:')
    errors=[]
    for h in _HOSTS:
        try:
            obj,text=get_json_and_text(f'https://{h}{_PATH}?{q}',referer=_REF)
            if not isinstance(obj,Mapping): raise SourceError('EOD response is not object')
            return obj,text
        except SourceError as e: errors.append(f'{h}: {e}')
    raise SourceError('EOD all hosts failed — '+' | '.join(errors))

def fetch_eod_snapshot(page_size:int=100,max_pages:int=80)->EodFetchResult:
    first,text=_page(1,page_size); data=first.get('data') or {}; total=int(data.get('total') or 0); rows=list(data.get('diff') or [])
    if total<=0 or not rows: raise SourceError('EOD first page empty')
    pages=math.ceil(total/page_size); texts=[text]
    if pages>max_pages: raise SourceError(f'EOD page count {pages} > {max_pages}')
    for pn in range(2,pages+1):
        obj,t=_page(pn,page_size); rows.extend((obj.get('data') or {}).get('diff') or []); texts.append(t)
    if len(rows)!=total: raise SourceError(f'EOD declared total={total}, rows={len(rows)}')
    # 🔴 翻译成 **provider 中立行** 就在这里做 —— 归一化层不再认识 `f17` 这些名字。
    #    东财的 `f5` 单位已经是「股」，不需要换算（新浪给的是「手」）。
    bars = tuple(
        EodBar(
            symbol=row.get('f12'), name=row.get('f14'),
            open=row.get('f17'), high=row.get('f15'), low=row.get('f16'),
            close=row.get('f2'), prev_close=row.get('f18'),
            volume=row.get('f5'), amount=row.get('f6'),
            change_amount=row.get('f4'), change_percent=row.get('f3'),
        )
        for row in rows
    )
    return EodFetchResult(bars,json.dumps(texts,ensure_ascii=False),now_cn().isoformat(),total,provider_id='em')
