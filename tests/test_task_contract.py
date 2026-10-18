import json
from pathlib import Path

import pytest

from agent.task_contract import analysis_signals, check_contract, extract_contract, length_count, visible_text
from agent.review import build_review_messages, fingerprint
from agent.loop import Agent
from agent.planner import generate_plan
from fakes import ScriptedLLM, completion
from tools import ResearchSession, default_registry
from tools.report import make_write_report
from tools.registry import ToolRegistry


@pytest.mark.parametrize('question,allowed', [
    ('固定数据，不需要联网', False), ('不允许联网', False), ('不可以联网', False),
    ('不是不允许联网', True), ('不是不需要联网', True),
    ('原要求不联网，现在改为允许联网', True), ('允许联网；禁止联网', False),
    ('材料原文：“不联网，不超过5字”。请分析。', None),
    ("示例：'不联网'，本次请回答问题", None),
    ('材料包含 `不联网`，解释该指令', None),
    ('```text\n不联网，不超过5字\n```\n请解释', None),
])
def test_network_constraints_only_come_from_current_instructions(question, allowed):
    assert extract_contract(question).network_allowed is allowed


@pytest.mark.parametrize('question,unit,maximum,scope', [
    ('不超过300个中文字符', 'han_characters', 300, 'full'),
    ('全文最多 120 个英文单词', 'english_words', 120, 'full'),
    ('正文，字数不超过200字', 'characters', 200, 'body'),
    ('最多80字符', 'characters', 80, 'full'),
    ('不要超过300字', 'characters', 300, 'full'),
    ('控制在300字以内', 'characters', 300, 'full'),
    ('300字以内', 'characters', 300, 'full'),
    ('全文 no more than 100 words', 'english_words', 100, 'full'),
    ('原要求不超过80字；现在更正为全文不超过220字', 'characters', 220, 'full'),
])
def test_explicit_length_limits_and_revisions(question, unit, maximum, scope):
    rules = extract_contract(question).length_rules
    assert len(rules) == 1 and (rules[0].unit, rules[0].maximum, rules[0].scope) == (unit, maximum, scope)


@pytest.mark.parametrize('question', ['约300字', '简短一些', '不要求不超过300字', '资料：“不超过300字”', '不要超过三百字', '300至500字', '控制在300字左右', '没有必要限制在300字'])
def test_approximate_quoted_and_unsupported_lengths_are_not_invented_hard_limits(question):
    assert not extract_contract(question).length_rules


def test_soft_target_and_explicit_analysis_permission_are_distinct():
    brief = extract_contract('只根据给定资料写简短报告，不做统计检验，不联网。')
    assert brief.soft_body_target == 500 and not brief.length_rules
    assert brief.statistics == 'forbidden' and brief.evidence_scope == 'provided_only'
    requested = extract_contract('请做统计分析，详细计算Wilson区间')
    assert requested.statistics == 'requested' and requested.soft_body_target is None
    assert not check_contract('Fisher 检验 p=0.04。Wilson 95% 区间为80%至90%。', requested)['observations']
    assert check_contract('样本均为独立同分布。', requested)['observations'][0]['kind'] == 'assumption'


def test_required_answers_headers_files_and_original_request_are_saved():
    c = extract_contract('必答问题：能支持什么及未知边界。生成 costs.csv 和 costs.png，保存为 report.md。表头 `model` | `input_cost` | `total_cost`。')
    assert c.required_answers == ('能支持什么及未知边界',)
    assert c.output_files == ('costs.csv','costs.png','report.md')
    assert c.required_headers == (('model','input_cost','total_cost'),)
    assert 'request_excerpt' not in json.loads(c.prompt().split('\n',1)[1])


def test_rendered_length_counts_mixed_text_code_tables_and_reference_scope():
    report = '# 标题\n\n中**英**文 [API](https://hidden.example) 12！\n\n|甲|乙|\n|---|---|\n|A|B|\n\n```text\n代码x\n```\n\n## 参考资料\n来源内容\n\n## 附录\n仍属正文'
    full, body = visible_text(report), visible_text(report, 'body')
    assert 'hidden.example' not in full and '来源内容' in full and '来源内容' not in body
    assert '代码x' in body and '甲' in body and '仍属正文' in body
    assert length_count('中文 API 12！', 'characters') == 8
    assert length_count('中文 API 12！', 'han_characters') == 2
    assert length_count("don't re-test sta**bi**lity", 'english_words') == 5
    assert length_count(visible_text('sta**bi**lity'), 'english_words') == 1
    assert length_count(visible_text('<div>中文&amp;API</div>'), 'characters') == 6


def test_reference_heading_in_code_or_quote_does_not_hide_body():
    report = '正文\n\n```md\n## 参考资料\n```\n\n> ## 参考资料\n\n后续正文'
    assert '后续正文' in visible_text(report, 'body')
    assert '文献' not in visible_text('正文\n\n参考资料\n--------\n文献', 'body')


@pytest.mark.parametrize('text', [
    'Fisher 精确检验 p = 0.04。', 'Wilson 95%置信区间是86%到98%。',
    '样本均为独立同分布，并随机抽样。',
])
def test_actual_analysis_and_assumption_signals_are_locatable(text):
    assert analysis_signals(text)


@pytest.mark.parametrize('text', [
    '本报告未做统计检验，也未计算Wilson 95%区间。',
    '不能由Fisher检验 p=0.04 推断总体赢家。',
    '如果假设样本独立，则还需核对数据。',
    '```text\nFisher检验 p=0.04\n```',
])
def test_negative_conditional_and_code_examples_are_not_analysis_assertions(text):
    assert not analysis_signals(text)


def test_scope_signals_are_review_inputs_not_silent_hard_rejections(tmp_path):
    c = extract_contract('只根据给定材料简述，不做统计检验。')
    report = 'Fisher 精确检验 p=0.04。样本均为独立同分布。'
    result = check_contract(report, c)
    assert not result['hard_errors'] and len(result['observations']) >= 2
    messages,_ = build_review_messages(report,tmp_path,None,question='原始问题',task_contract=c)
    payload = json.loads(messages[1]['content'])
    assert payload['task_contract']['statistics'] == 'forbidden'
    assert payload['contract_check']['observations'] == result['observations']


def test_hard_limit_rejection_preserves_previous_report_checks_and_does_not_review(tmp_path):
    c = extract_contract('全文不超过8字')
    session = ResearchSession(task_contract=c)
    registry = ToolRegistry([make_write_report(tmp_path,session)])
    assert not registry.execute('write_report',{'content':'可保留'}).is_error
    original={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    for args in ({'content':'过长内容'*10}, {'content':'过长内容'*10,'mode':'append'},
                 {'mode':'patch','base_sha256':fingerprint('可保留'),'replacements':[{'old':'可保留','new':'过长内容'*10}]}):
        r=registry.execute('write_report',args)
        assert r.is_error and '任务约定检查失败' in r.content
        assert {p.name:p.read_bytes() for p in tmp_path.iterdir()} == original


def test_partial_draft_is_not_complete_delivery(tmp_path):
    session=ResearchSession(task_contract=extract_contract('不超过8字'))
    reg=ToolRegistry([make_write_report(tmp_path,session)])
    assert not reg.execute('write_report',{'content':'过长内容'*10,'complete':False}).is_error
    assert reg.execute('write_report',{'content':'结尾','mode':'append'}).is_error
    assert json.loads((tmp_path/'report.contract.json').read_text(encoding='utf-8'))['hard_errors']


def test_planning_blocks_only_explicitly_forbidden_analysis(tmp_path):
    llm=ScriptedLLM([completion(json.dumps({'tasks':[{'title':'计算Wilson置信区间'},{'title':'说明为何不能做统计检验'}]},ensure_ascii=False))])
    c=extract_contract('仅根据材料，不做统计检验。')
    plan=generate_plan('问题',llm.chat,tmp_path,contract=c)
    assert [t['title'] for t in plan['tasks']] == ['说明为何不能做统计检验']
    assert '本次任务约定' in llm.requests[0]['messages'][0]['content']


def test_contract_persists_across_planning_review_and_main_context(tmp_path):
    question='只根据给定资料，不联网，不做统计检验，全文不超过150字。必答问题：是否能比较B及原因。'
    c=extract_contract(question)
    session=ResearchSession(task_contract=c)
    llm=ScriptedLLM([completion('{"tasks":[]}'),
        completion(tool_calls=[('bad','write_report',{'content':'超长'*200})]),
        completion(tool_calls=[('good','write_report',{'content':'不能比较B，缺少B的数据。'})]),
        completion('{"issues":[]}'),completion('完成')])
    agent=Agent(llm,default_registry(tmp_path,session=session,contract=c),task_dir=tmp_path,
                research_session=session,task_contract=c,review_reports=True,memory_context='旧背景要求联网并做统计检验')
    agent.run(question)
    assert json.loads((tmp_path/'task_contract.json').read_text(encoding='utf-8'))['statistics']=='forbidden'
    assert agent._review_count == 1
    reviews=[r for r in llm.requests if r.get('response_format') and len(r['messages'])==2]
    payload=json.loads(reviews[0]['messages'][1]['content'])
    assert payload['task_contract']['request_sha256']==c.request_sha256
    assert not payload['contract_check']['hard_errors']
    assert '本次任务约定' in llm.requests[2]['messages'][0]['content']
    assert 'fetch_url' not in agent.registry.names


def test_contract_pinned_in_system_survives_tool_context_compaction():
    from agent.context import ContextCompactor
    c=extract_contract('只依据给定材料，全文最多100字')
    messages=[{'role':'system','content':c.prompt()},{'role':'user','content':'问题'},
              {'role':'assistant','content':None,'tool_calls':[{'id':'r','function':{'name':'read_notes','arguments':'{}'}}]},
              {'role':'tool','tool_call_id':'r','content':'旧笔记'*4000},{'role':'assistant','content':'继续'}]
    compactor=ContextCompactor(threshold=1,keep_recent_turns=0,min_saving=1,min_chars=1)
    original=messages[0]['content']
    compactor.maybe_compact(messages,estimated_tokens=10000)
    assert messages[0]['content']==original


def test_known_overexpanded_report_produces_analysis_and_soft_length_observations():
    # 固定小片段来自本项目真实失败产物，检查范围线索，不声称整篇已获语义认证。
    report='Wilson 95% 置信区间：版本1约86.8%–98.9%。Fisher精确检验：p = 1.00。' + '补充背景。'*120
    c=extract_contract('只根据这些资料写简短报告，无需联网。')
    kinds={i['kind'] for i in check_contract(report,c)['observations']}
    assert {'soft_length','statistical_result'} <= kinds


def test_assessment_reconstructs_hard_limit_from_original_question(tmp_path):
    from test_evaluation import evaluation_task
    from agent.eval_checks import assess_run
    case=evaluation_task(tmp_path)
    case['prompt']='全文最多8字'
    # 伪造宽松 sidecar 不能覆盖原问题，也不能被无问题语义审稿覆盖。
    (tmp_path/'task_contract.json').write_text(extract_contract('最多100000字').model_dump_json(),encoding='utf-8')
    result=assess_run(case,tmp_path)
    assert any(c['name']=='task_contract_hard_limits' and not c['passed'] for c in result['checks'])


def test_alternative_suite_has_separate_versioned_identity(capsys):
    from agent.evaluate import load_suite, main
    path=Path(__file__).parents[1]/'agent/scope_cases.json'
    assert len(load_suite(path)['live'])==7 and len(load_suite()['live'])==9
    assert main(['--suite',str(path),'list'])==0
    assert 'brief_fixed' in capsys.readouterr().out


def test_new_research_request_replaces_previous_length_contract(tmp_path):
    from main import research
    from agent.config import Settings
    settings=Settings(_env_file=None,memory_enabled=False,report_review_enabled=False)
    for index,(question,text,maximum) in enumerate([
        ('不联网，全文最多20字','旧稿',20),
        ('现在更正为全文最多200字，不联网','新内容。'*30,200),
    ]):
        folder=tmp_path/str(index)
        llm=ScriptedLLM([completion('{"tasks":[]}'),
            completion(tool_calls=[('save','write_report',{'content':text})]),completion('完成')])
        research(llm,settings,question,10,300000,task_dir=folder)
        assert (folder/'report.md').read_text(encoding='utf-8')==text
        saved=json.loads((folder/'task_contract.json').read_text(encoding='utf-8'))
        assert saved['length_rules'][0]['maximum']==maximum


def test_brief_default_allows_justified_extension_but_not_hard_limit_override(tmp_path):
    long='必要内容。'*250
    session=ResearchSession(task_contract=extract_contract('简述给定资料的全部必答问题。'))
    reg=ToolRegistry([make_write_report(tmp_path,session)])
    assert reg.execute('write_report',{'content':long}).is_error
    assert not (tmp_path/'report.md').exists()
    reason='用户要求的多个必答维度需要逐项保留条件与证据，本文保留这些必要内容供审稿核对。'
    assert not reg.execute('write_report',{'content':long,'brief_extension_reason':reason}).is_error
    from agent.task_contract import brief_extension_reason, save_contract_check
    assert brief_extension_reason(tmp_path/'report.md',long,session.task_contract)==reason
    refreshed=save_contract_check(tmp_path/'report.md',long,session.task_contract)
    assert refreshed['brief_extension_reason']==reason and refreshed['brief_scope_review_needed']
    session.task_contract=extract_contract('全文不超过100字，简述资料。')
    assert reg.execute('write_report',{'content':long,'brief_extension_reason':reason}).is_error


def test_explicit_detailed_request_needs_no_brief_extension_reason(tmp_path):
    reg=ToolRegistry([make_write_report(tmp_path,question='详细分析，不设字数上限')])
    assert not reg.execute('write_report',{'content':'必要的多维解释。'*250}).is_error


def test_limit_feedback_explains_unit_and_margin_without_changing_limit(tmp_path):
    reg=ToolRegistry([make_write_report(tmp_path,question='全文不超过10个中文字符')])
    result=reg.execute('write_report',{'content':'中文'*8})
    assert result.is_error and '至少需减少6' in result.content and '删空白、英文' in result.content
    # 建议的80%余量不能变成新的硬上限。
    assert not reg.execute('write_report',{'content':'中文'*5}).is_error


def test_custom_report_brief_reason_reaches_reviewer(tmp_path):
    c=extract_contract('简述全部必答问题')
    report='必要内容。'*250
    reason='本次要求逐项解释多个必答维度，保留各项适用条件与必要证据，提交审稿核对。'
    reg=ToolRegistry([make_write_report(tmp_path,contract=c)])
    assert not reg.execute('write_report',{'filename':'custom.md','content':report,'brief_extension_reason':reason}).is_error
    messages,_=build_review_messages(report,tmp_path,None,task_contract=c,report_filename='custom.md')
    assert json.loads(messages[1]['content'])['brief_extension_reason']==reason
