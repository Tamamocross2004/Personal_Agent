"""真实阶段12失败类别的回归及反例，不调用付费模型。"""
import json

import pytest

from agent.evidence import Claim, EvidenceLedger
from agent.eval_checks import session_from_events
from agent.loop import Agent
from agent.quality import inspect_report, urls
from fakes import ScriptedLLM, completion
from tools.registry import ToolRegistry, tool
from tools.session import ResearchSession, normalize_url


URL = 'https://example.org/price'
ERROR = 'ERROR: FetchError: 网络不可用'


@pytest.mark.parametrize('text,known,exempt', [
    (f'尝试读取 {URL} 失败，价格未知。', True, True),
    (f'尝试读取 {URL} 失败，价格未知。', False, False),
    (f'读取 {URL} 失败，但价格为8元。', True, False),
    (f'读取 {URL} 失败，价格未知[1]。\n## 参考资料\n[1] {URL}', True, False),
    (f'读取 {URL} 失败。\n\n该网址 {URL} 说明价格便宜。', True, False),
    (f'## 参考资料\n读取 {URL} 失败。', True, False),
])
def test_failed_url_is_operation_only_with_real_failure_and_no_source_use(tmp_path, text, known, exempt):
    s = ResearchSession()
    if known:
        s.failed_fetches[normalize_url(URL)] = ERROR
    result = inspect_report(text, tmp_path, s)
    assert bool(result.failed_operations) == exempt
    assert any(i.category == 'source' for i in result.issues) != exempt


def test_replay_reconstructs_real_failure_but_not_validation_errors(tmp_path):
    events = [{'kind':'tool_call','id':'f','name':'fetch_url','arguments':json.dumps({'url':URL})},
              {'kind':'tool_result','id':'f','name':'fetch_url','is_error':True,'content':ERROR}]
    s = session_from_events(events)
    assert normalize_url(URL) in s.failed_fetches
    assert not inspect_report(f'读取 {URL} 失败。',tmp_path,s).issues
    events[-1]['content'] = 'ERROR: 参数不是合法JSON'
    assert not session_from_events(events).failed_fetches


def test_url_boundaries_keep_chinese_paths_but_exclude_brackets():
    assert urls('https://example.org/资料（非现实设备）') == ['https://example.org/资料']
    assert urls('https://example.org/x【示例】 https://example.org/y《说明》') == ['https://example.org/x','https://example.org/y']


def test_failed_execution_is_observed_and_never_successful_calculation(tmp_path):
    ledger = EvidenceLedger(tmp_path, '请读取价格')
    source = ledger.execution('fetch_url',json.dumps({'url':URL}),ERROR,'call-f',True)
    c = {'quote':'读取失败','kind':'observed','supports':[{'source_id':source,'excerpt':ERROR}]}
    assert ledger.validate('读取失败',[Claim(**c)])[0]['supports'][0]['source_kind']=='execution'
    for kind in ('calculated','direct'):
        with pytest.raises(ValueError):
            ledger.validate('读取失败',[Claim(**dict(c,kind=kind))])
    c['supports'] = [{'source_id':'user-1','excerpt':'请读取价格'}]
    with pytest.raises(ValueError,match='用户要求不能证明实际执行'):
        ledger.validate('读取失败',[Claim(**c)])
    assert not any(r['kind'] in ('web','calculation') for r in ledger.records.values())


def test_agent_emits_execution_id_for_failed_tool_and_replay_state(tmp_path):
    @tool(name='fetch_url')
    def fetch_url(url: str):
        """读取网页。"""
        from tools.fetch import FetchError
        raise FetchError('网络不可用')
    s = ResearchSession()
    llm = ScriptedLLM([completion('无需规划'), completion(tool_calls=[('f','fetch_url',{'url':URL})]), completion('价格未知')])
    Agent(llm,ToolRegistry([fetch_url]),task_dir=tmp_path,research_session=s).run('读取价格')
    records = s.evidence.records
    assert records['execution-1']['is_error']
    assert not any(r['kind']=='web' for r in records.values())
    assert normalize_url(URL) in s.failed_fetches
    assert any('execution-1' in m.get('content','') for m in llm.requests[-1]['messages'] if m['role']=='tool')


def test_direct_composite_numeric_result_rejects_before_mapping(tmp_path):
    ledger = EvidenceLedger(tmp_path,'输入单价13元，用量120000token。')
    c = {'quote':'| 输入 | 13 | 120000 | 1.56 |','kind':'direct',
         'supports':[{'source_id':'user-1','excerpt':'输入单价13元，用量120000token'}]}
    with pytest.raises(ValueError,match='摘录未提供的数值'):
        ledger.validate(c['quote'],[Claim(**c)])
    c.update(quote='输入单价13元[2]')
    assert ledger.validate(c['quote'],[Claim(**c)])
    c.update(quote='输入单价13元[user-1]')
    assert ledger.validate(c['quote'],[Claim(**c)])
    c.update(quote='输入单价13元[unknown-99]')
    with pytest.raises(ValueError,match='摘录未提供的数值'):
        ledger.validate(c['quote'],[Claim(**c)])


def test_numeric_presence_allows_decimal_notation_and_grouping(tmp_path):
    ledger = EvidenceLedger(tmp_path,'输入1,000,000，比例.25，系数1e-6。')
    c = Claim(quote='输入1000000、比例0.25、系数0.000001',kind='direct',
              supports=[{'source_id':'user-1','excerpt':'输入1,000,000，比例.25，系数1e-6'}])
    assert ledger.validate(c.quote,[c])


def test_history_feedback_locates_every_missing_limits_even_unknown(tmp_path):
    ledger = EvidenceLedger(tmp_path,'沿用旧假设')
    ledger.add('history','记录日期2026-09-01，输入单价13元。',source_id='history-1')
    refs = [{'source_id':'history-1','excerpt':'输入单价13元'}]
    claims = [Claim(quote='沿用13元',kind='inference',supports=refs,assumptions='沿用虚构历史假设'),
              Claim(quote='当前未知',kind='unknown',supports=refs)]
    with pytest.raises(ValueError) as e:
        ledger.validate('沿用13元；当前未知',[*claims])
    assert 'claims[0].limits' in str(e.value) and 'claims[1].limits' in str(e.value)
    for c in claims:
        c.limits = '2026-09-01记录，仅作虚构假设，当前价格未知'
    assert len(ledger.validate('沿用13元；当前未知',claims))==2


def test_delivered_coverage_merges_overlap_and_keeps_page_versions_separate(tmp_path):
    ledger = EvidenceLedger(tmp_path,'研究')
    def add(start,end,version='v1'):
        return ledger.add('web','字'*(end-start),url=URL,start=start,end=end,page_characters=100,
                          page_text_sha256=version)
    first = add(0,40)
    add(20,80)
    add(0,100,'other')
    assert ledger.web_coverage(ledger.records[first])['delivered_characters']==80
    assert not ledger.web_coverage(ledger.records[first])['full_page_delivered']
    add(80,100)
    assert ledger.web_coverage(ledger.records[first])['full_page_delivered']


def test_reviewer_sees_referenced_json_types_but_not_internal_or_unrelated_files(tmp_path):
    from agent.review import build_review_messages
    (tmp_path/'fee.json').write_text('{"total_cost":"4.425"}',encoding='utf-8')
    (tmp_path/'unrelated.json').write_text('{"private":"not requested"}',encoding='utf-8')
    (tmp_path/'messages.json').write_text('{"internal":"not research output"}',encoding='utf-8')
    messages,_ = build_review_messages('fee.json 含数值字段。另见messages.json。',tmp_path,ResearchSession(),
                                       question='total_cost必须是JSON数字')
    payload = json.loads(messages[1]['content'])
    assert len(payload['json_samples'])==1
    assert payload['json_samples'][0]['field_types']=={'total_cost':'str'}
    assert '4.425' in payload['json_samples'][0]['content']
    assert 'not requested' not in messages[1]['content']
    assert 'not research output' not in messages[1]['content']
