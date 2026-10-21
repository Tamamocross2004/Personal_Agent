import json

from agent.evidence import EvidenceLedger, sha
from agent.loop import Agent
from agent.report_validation import ReportValidationError
from agent.task_contract import extract_contract
from fakes import ScriptedLLM, completion
from tools.registry import ToolRegistry
from tools.report import make_write_report
from tools.session import ResearchSession


def setup(root,question):
    session=ResearchSession(task_contract=extract_contract(question),evidence=EvidenceLedger(root,question))
    return session,ToolRegistry([make_write_report(root,session,question=question)])


def claim(quote='价格7元',source='user-1',excerpt='价格7元'):
    return {'quote':quote,'kind':'direct','supports':[{'source_id':source,'excerpt':excerpt}]}


def test_all_independent_errors_are_returned_without_overwriting_report_or_sidecars(tmp_path):
    question='材料：价格7元。全文不超过80字，表头 model | cost。'
    _,registry=setup(tmp_path,question)
    good='| model | cost |\n|---|---|\n| A | 7 |\n价格7元'
    assert not registry.execute('write_report',{'content':good,'claims':[claim()]}).is_error
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    bad='需要缩短的背景说明。'*40+'价格7元，1+2=8。'
    result=registry.execute('write_report',{'content':bad,'claims':[
        claim(quote='不在当前正文的原句',source='missing'),claim(excerpt='未读取的价格9元')]})
    assert result.is_error and result.validation['candidate_sha256']==sha(bad)
    assert result.validation['saved_report_sha256']==sha(good)
    codes={i['code'] for i in result.validation['issues']}
    assert {'task_contract','required_headers','arithmetic','quote_missing','source_missing','excerpt_missing'}<=codes
    paths={i['path'] for i in result.validation['issues']}
    assert {'claims[0].quote','claims[0].supports[0].source_id','claims[1].supports[0].excerpt'}<=paths
    assert {p.name:p.read_bytes() for p in tmp_path.iterdir()}==before


def test_soft_target_and_wrong_reference_both_return_and_target_does_not_change(tmp_path):
    _,registry=setup(tmp_path,'简述。材料：价格7元。')
    result=registry.execute('write_report',{'content':'背景说明。'*400,'claims':[claim()]})
    assert result.is_error
    assert {'brief_length','quote_missing'}<={i['code'] for i in result.validation['issues']}
    assert any(m.get('soft_target')==500 for m in result.validation['measurements'])
    assert not (tmp_path/'report.md').exists()
    assert result.validation['saved_report_sha256'] is None


def test_feedback_is_bounded_and_explicit_about_omitted_errors(tmp_path):
    _,registry=setup(tmp_path,'材料：价格7元。')
    claims=[{'quote':'缺失结论'+str(i),'kind':'direct','supports':[
        {'source_id':f'missing-{i}-{j}','excerpt':'价格7元'} for j in range(4)]} for i in range(8)]
    result=registry.execute('write_report',{'content':'未完成稿','claims':claims})
    assert result.is_error and result.validation['omitted_issues']>0
    assert len(result.validation['issues'])<=ReportValidationError.MAX_ISSUES
    assert len(result.content)<6200
    assert not (tmp_path/'report.md').exists()


def test_rejected_long_draft_is_removed_but_other_diagnostics_reach_next_call(tmp_path):
    question='不联网，全文不超过40字。材料：价格7元。'
    session=ResearchSession()
    bad='拒绝稿中的重复背景说明。'*100
    llm=ScriptedLLM([completion('{}'),completion(tool_calls=[('bad','write_report',{
        'content':bad,'claims':[claim(quote='不存在的原句',source='missing')]} )]),completion('尚未完成')])
    registry=ToolRegistry([make_write_report(tmp_path,session,question=question)])
    events=[]
    Agent(llm,registry,task_dir=tmp_path,research_session=session,on_event=events.append).run(question)
    messages=json.dumps(llm.requests[-1]['messages'],ensure_ascii=False)
    assert bad not in messages
    assert 'source_missing' in messages and 'quote_missing' in messages
    assert any(e.kind=='tool_result' and e.data.get('validation') for e in events)
    assert not (tmp_path/'report.md').exists()


def test_non_length_rejection_compacts_draft_without_repairing_claims(tmp_path):
    question='不联网，依据材料写报告。价格7元。'
    session=ResearchSession()
    bad='# 报告\n\n'+'背景内容。'*500
    llm=ScriptedLLM([completion('{}'),completion(tool_calls=[('bad','write_report',{
        'content':bad,'claims':[claim(quote='不在正文里的价格7元',source='user-1')]} )]),completion('尚未完成')])
    events=[]
    Agent(llm,ToolRegistry([make_write_report(tmp_path,session,question=question)]),task_dir=tmp_path,
          research_session=session,on_event=events.append).run(question)
    shown=json.dumps(llm.requests[-1]['messages'],ensure_ascii=False)
    assert bad not in shown and 'quote_missing' in shown and 'omitted_rejections' in shown
    assert any(e.kind=='tool_call' and json.loads(e.data['arguments']).get('content')==bad for e in events)
    assert not (tmp_path/'report.md').exists()


def test_corrected_submission_is_saved_and_only_then_reviewed(tmp_path):
    question='不联网，报告全文不超过500字。材料：价格7元。'
    text='# 核对\n\n## 摘要\n价格7元。\n\n## 方法与范围\n仅材料。\n\n## 正文\n价格7元。\n\n## 结论\n价格7元。\n\n## 局限性\n未外查。\n\n## 参考资料\n用户材料。'
    session=ResearchSession()
    review={'issues':[],'claim_checks':[{'claim_index':0,'target':'quote','status':'supported','reason':'符合材料'}]}
    llm=ScriptedLLM([completion('{}'),completion(tool_calls=[('bad','write_report',{
        'content':'重复背景。'*400,'claims':[claim(quote='不存在',source='missing')]} )]),
        completion(tool_calls=[('good','write_report',{'content':text,'claims':[claim()]} )]),
        completion(json.dumps(review,ensure_ascii=False)),completion('完成')])
    registry=ToolRegistry([make_write_report(tmp_path,session,question=question)])
    agent=Agent(llm,registry,task_dir=tmp_path,research_session=session,review_reports=True)
    result=agent.run(question)
    assert result.stop_reason=='final_answer' and agent._review_count==1
    assert (tmp_path/'report.md').read_text('utf-8')==text
    assert json.loads((tmp_path/'report.review.json').read_text('utf-8'))['status']=='reviewed'
    assert json.loads((tmp_path/'report.evidence.json').read_text('utf-8'))['claims'][0]['quote']=='价格7元'


def test_rejected_candidate_hash_cannot_replace_saved_patch_base(tmp_path):
    _,registry=setup(tmp_path,'材料：价格7元。全文不超过80字。')
    good='# 原稿\n价格7元'
    registry.execute('write_report',{'content':good,'claims':[claim()]})
    result=registry.execute('write_report',{'content':'重复背景。'*100,'claims':[claim()]})
    args={'mode':'patch','replacements':[{'old':'原稿','new':'修订'}],'claims':[claim()]}
    rejected=registry.execute('write_report',{**args,'base_sha256':result.validation['candidate_sha256']})
    assert rejected.is_error and (tmp_path/'report.md').read_text('utf-8')==good
    accepted=registry.execute('write_report',{**args,'base_sha256':result.validation['saved_report_sha256']})
    assert not accepted.is_error
    assert (tmp_path/'report.md').read_text('utf-8')==good.replace('原稿','修订')
