import json

from agent.loop import Agent
from agent.report_validation import compact_feedback
from fakes import ScriptedLLM,completion
from tools.registry import ToolRegistry,ToolResult,tool
from tools.session import ResearchSession


def test_duplicate_spans_reuse_id_and_overlap_adds_only_new_characters(tmp_path):
    @tool(name='search_knowledge')
    def search(offset:int=0)->ToolResult:
        """返回带实际位置的原文；不同参数可能命中同一区间。"""
        start=0 if offset<3 else 5
        text='abcdefghij'[start:]
        content='资料：'+text
        return ToolResult(content,evidence=[{'doc_id':'doc','name':'guide.txt','file_sha256':'file',
            'page_sha256':'page','generation':'g','page':1,'chunk_id':offset,'start':start,
            'role':'hit','output_start':3,'output_end':len(content),'text':text}])
    session=ResearchSession(); events=[]
    calls=[completion('{}')]+[completion(tool_calls=[(str(i),'search_knowledge',{'offset':i})]) for i in range(4)]+[completion('完成')]
    llm=ScriptedLLM(calls)
    agent=Agent(llm,ToolRegistry([search]),task_dir=tmp_path,research_session=session,on_event=events.append,max_steps=8)
    agent.run('读取')
    progress=[e.data for e in events if e.kind=='retrieval_progress']
    assert [p['new_retained_document_characters'] for p in progress]==[10,0,0,0]
    assert [p['consecutive_no_new_returns'] for p in progress]==[0,1,2,3]
    assert session.document_characters()==10
    assert len([r for r in session.evidence.records.values() if r['kind']=='document'])==2
    assert any('连续检索未增加' in (m.get('content') or '') for m in llm.requests[-1]['messages'])


def test_coverage_keeps_distinct_document_versions_and_partial_overlap(tmp_path):
    from agent.evidence import EvidenceLedger
    s=ResearchSession(); s.evidence=EvidenceLedger(tmp_path,'问题')
    for version,start,end in [('a',0,10),('a',5,17),('b',0,10)]:
        s.evidence.add('document','x'*(end-start),document_id='doc',file_sha256=version,generation='g',page=1,start=start,end=end)
    assert s.document_characters()==27


def test_feedback_keeps_valid_json_latest_diagnostics_and_explicit_omission():
    rows=[{'candidate_sha256':str(i),'issues':[{'code':'quote_missing','message':'x'*120}]} for i in range(10)]
    text=compact_feedback(rows,limit=450); result=json.loads(text)
    assert len(text)<=450 and result['omitted_rejections']>0
    assert result['rejections'][0]['candidate_sha256']=='9'
