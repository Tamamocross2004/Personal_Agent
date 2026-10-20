"""只读取本次已经送达并保留的证据，不能从网页缓存扩大已读范围。"""
from typing import Annotated
from pydantic import Field
from tools.registry import tool, Tool
from tools.session import ResearchSession


def make_read_evidence(session: ResearchSession) -> Tool:
    @tool(name='read_evidence')
    def read_evidence(
        source_id: Annotated[str,Field(max_length=60,description='本次ID，如web-1、calculation-1、execution-1、history-1；空值列出最近10项索引')] = '',
        query: Annotated[str,Field(max_length=120,description='可选字面关键词；只定位本次已送达的证据内容，不检索外部网页')] = '',
    ) -> dict:
        """查看本次已经收到的证据ID与精确原文；摘录校验失败时先读取，不反复猜空格或重新计算。"""
        ledger=session.evidence
        if ledger is None:
            raise ValueError('本次尚无证据账本')
        if not source_id:
            rows=list(ledger.records.values())[-10:]
            return {'records':[{'id':r['id'],'kind':r['kind'],'start':r.get('start'),'end':r.get('end'),
                                'preview':r['text'][:100]} for r in rows],
                    'other_retained_records':max(0,len(ledger.records)-len(rows)),'omitted_records':ledger.omitted}
        if source_id not in ledger.records:
            raise ValueError('未保留该ID；请先空参数列出索引')
        row=ledger.records[source_id]
        pos=row['text'].lower().find(query.lower()) if query else 0
        if pos<0:
            return {'id':source_id,'match':False,'meaning':'仅本次已送达片段内字面未命中，不证明网页全文不存在。'}
        left=max(0,pos-250)
        end=min(len(row['text']),left+1600)
        return {'id':source_id,'kind':row['kind'],'excerpt':row['text'][left:end],
                'record_text_range':[left,end],'source_sha256':row['text_sha256'],
                **({'read_coverage':ledger.web_coverage(row)} if row['kind']=='web' else {}),
                'partial':left>0 or end<len(row['text']),'semantic_support':'not_certified'}
    return read_evidence
