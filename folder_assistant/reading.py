"""Deterministic reading of pinned extractions, independent of query words or scores.

Offsets count Unicode codepoints in extracted blocks, never PDF byte positions.
No header inference, cross-page table joins, or business-key matching is performed.
"""
import re
from knowledge.documents import sha

SEGMENT_CHARS=700
BATCH_SEGMENTS=6


def table_key(location):
    kind=location['kind']
    if kind.startswith('spreadsheet_'):return ('sheet',location['sheet'])
    if kind.startswith('table_'):return ('table',location['table'])
    if kind=='csv_record':return ('csv',)
    return None


class ReadingPlan:
    def __init__(self,index,fid,generation,file_ids=None):
        self.generation=index.store(fid).generation(generation)
        if not self.generation or self.generation['status']!='ready':raise ValueError('读取版本未就绪')
        docs=self.generation['files']
        if file_ids is not None and (not file_ids or not set(file_ids).issubset({d['file_id'] for d in docs})):
            raise ValueError('读取范围不属于固定版本')
        self.documents=sorted([d for d in docs if file_ids is None or d['file_id'] in file_ids],key=lambda d:(d['name'],d['id']))
        self.segments=[];self.origins={};self.headings={}
        for doc in self.documents:
            heading=None
            for page in doc['pages']:
                text=page['text'];loc=page['location'];key=table_key(loc)
                items=[]
                for start in range(0,max(1,len(text)),SEGMENT_CHARS):
                    part=text[start:start+SEGMENT_CHARS]
                    item={'doc_id':doc['id'],'file_id':doc['file_id'],'name':doc['name'],
                          'file_sha256':doc['file_sha256'],'source_location':loc,'source_block_id':page['block']['id'],
                          'text':part,'start':start,'end':start+len(part),'partial':len(text)>len(part),
                          'generation':self.generation['id'],'role':'sequential','segment':len(self.segments)}
                    self.segments.append(item);items.append(item)
                    if heading is not None:self.headings[item['segment']]=heading
                if key is not None:self.origins.setdefault((doc['id'],key),items[0])
                # Only explicit Markdown heading syntax; not an inferred Office/PDF title.
                if loc['kind']=='line' and re.match(r'^#{1,6}\s+\S',text):heading=items[0]
        self.signature=sha(str(SEGMENT_CHARS)+'\0'+'\0'.join(d['id'] for d in self.documents))

    def summary(self):
        return {'generation':self.generation['id'],'plan_sha256':self.signature,'total_segments':len(self.segments),
                'total_blocks':sum(len(d['pages']) for d in self.documents),
                'total_characters':sum(len(s['text']) for s in self.segments),
                'files':[{'file_id':d['file_id'],'name':d['name'],'blocks':len(d['pages'])} for d in self.documents],
                'meaning':'仅覆盖本版本已解析文字；未含OCR、未提取内容。送达不认证完整提取或语义正确。'}

    def batch(self,offset,limit=BATCH_SEGMENTS,*,context=True):
        if type(offset) is not int or not 0<=offset<=len(self.segments) or not 1<=limit<=BATCH_SEGMENTS:
            raise ValueError('读取范围无效')
        selected=[dict(s) for s in self.segments[offset:offset+limit]]
        extra=[]
        if context:
            for item in selected:
                key=table_key(item['source_location'])
                candidates=[(self.origins.get((item['doc_id'],key)),'table_origin_unverified_header'),
                            (self.headings.get(item['segment']),'explicit_markdown_heading')]
                for origin,role in candidates:
                    if (origin and origin['segment'] not in {s['segment'] for s in selected+extra} and len(extra)<2
                        and sum(len(s['text']) for s in selected+extra)+len(origin['text'])<=5500):
                        extra.append({**origin,'role':role})
        return {'hits':selected+extra,'next_offset':offset+len(selected),'selected_segments':len(selected),
                'context_segments':len(extra),'total_segments':len(self.segments),'generation':self.generation['id']}


def read_batch(index,fid,generation,file_ids=None,offset=0,limit=BATCH_SEGMENTS):
    index.folders.authorized(fid)
    plan=ReadingPlan(index,fid,generation,file_ids)
    return {**plan.summary(),**plan.batch(offset,limit),'cloud_calls':False}


def neighbors(index,fid,generation,doc_id,block_id):
    plan=ReadingPlan(index,fid,generation)
    doc=next((d for d in plan.documents if d['id']==doc_id),None)
    if not doc:raise KeyError('文档不属于引用版本')
    position=next((i for i,p in enumerate(doc['pages']) if p['block']['id']==block_id),None)
    if position is None:raise KeyError('引用位置不存在')
    pages=doc['pages'][max(0,position-1):position+2]
    # Local historical viewing, independent of original authorization; no cloud call.
    return {'generation':generation,'name':doc['name'],'file_sha256':doc['file_sha256'],
            'blocks':[{'id':p['block']['id'],'location':p['location'],'text':p['text'][:4000],
                       'partial':len(p['text'])>4000} for p in pages],
            'cloud_calls':False,'meaning':'相邻提取块供本地核对，未发送模型；没有推断跨页表格关系。'}
