"""结论/前提定位和审稿反馈契约；不以合成模型回复证明语义质量。"""
import json

import pytest

from agent.evidence import Claim, EvidenceLedger, audit_mapping, mapping_sha, sha, write_json
from agent.loop import Agent
from agent.review import ReviewValidationError, build_review_messages, parse_review, review_feedback
from fakes import ScriptedLLM, completion
from tools.registry import ToolRegistry
from tools.report import make_write_report
from tools.session import ResearchSession


QUESTION = '虚构规则：收到签名回执即可归档。档案甲已收到签名回执。'
QUOTE = '档案甲可归档'


def claim():
    return {'quote': QUOTE, 'kind': 'inference',
            'reasoning': '将题给规则应用于档案甲，不增加规则的逆命题。',
            'premises': [
                {'text': '收到签名回执即可归档', 'basis': 'evidence',
                 'supports': [{'source_id': 'user-1', 'excerpt': '收到签名回执即可归档'}]},
                {'text': '档案甲已收到签名回执', 'basis': 'evidence',
                 'supports': [{'source_id': 'user-1', 'excerpt': '档案甲已收到签名回执'}]}]}


def report():
    return ('# 归档\n\n## 摘要\n'+QUOTE+'。\n\n## 方法与范围\n仅题设。'
            '\n\n## 正文\n'+QUOTE+'。\n\n## 结论\n'+QUOTE+'。'
            '\n\n## 局限性\n虚构材料。\n\n## 参考资料\n用户材料。')


def setup(tmp_path):
    ledger = EvidenceLedger(tmp_path, QUESTION)
    session = ResearchSession(evidence=ledger)
    registry = ToolRegistry([make_write_report(tmp_path, session)])
    return ledger, session, registry


def check(index=0, **overrides):
    return {'claim_index': index, 'status': 'supported',
            'reason': '题设规则和实例均支持带原有限定的结论。', **overrides}


def verdict(checks, issues=None):
    return json.dumps({'issues': issues or [], 'claim_checks': checks}, ensure_ascii=False)


def test_reasoning_does_not_require_inventing_an_assumption_and_premises_are_readable(tmp_path):
    ledger, session, registry = setup(tmp_path)
    result = registry.execute('write_report', {'content': report(), 'claims': [claim()]})
    assert not result.is_error
    mapping = json.loads((tmp_path/'report.evidence.json').read_text(encoding='utf-8'))
    assert mapping['claims'][0]['assumptions'] == ''
    assert mapping['claims'][0]['premises'][0]['supports'][0]['source_sha256'] == sha(QUESTION)
    restored = ledger.prepare(tmp_path/'report.md', report(), None, complete=True)
    assert mapping_sha(mapping) == mapping_sha(restored)
    assert audit_mapping(tmp_path)['source_kinds'] == ['user']
    messages, partial = build_review_messages(report(), tmp_path, session)
    packet = json.loads(messages[1]['content'])['claim_evidence']
    assert packet['items'][0]['claim_index'] == 0
    assert QUESTION in packet['sources']['user-1']['contexts'][0]
    assert packet['semantic_support'] == 'not_certified' and not partial


@pytest.mark.parametrize('basis,supports', [
    ('evidence', []),
    ('assumption', [{'source_id': 'user-1', 'excerpt': '签名回执'}]),
    ('unknown', [{'source_id': 'user-1', 'excerpt': '签名回执'}]),
    ('evidence', [{'source_id': 'user-1', 'excerpt': '题设未给的原文'}]),
    ('evidence', [{'source_id': 'missing', 'excerpt': '签名回执'}]),
])
def test_invalid_premise_links_fail_before_any_report_overwrite(tmp_path, basis, supports):
    _, _, registry = setup(tmp_path)
    assert not registry.execute('write_report', {'content': report(), 'claims': [claim()]}).is_error
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    changed = claim()
    changed['premises'][0].update(basis=basis, supports=supports)
    assert registry.execute('write_report', {'content': report(), 'claims': [changed]}).is_error
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_assumptions_and_unknown_premises_remain_uncertified_not_hard_rejected(tmp_path):
    ledger, _, _ = setup(tmp_path)
    item = claim()
    item['premises'] += [{'text': '该虚构规则仍然有效', 'basis': 'assumption'},
                         {'text': '其他档案的状态', 'basis': 'unknown'}]
    mapped = ledger.validate(QUOTE, [Claim.model_validate(item)])[0]
    assert mapped['semantic_support'] == 'not_certified'
    assert not mapped['premises'][2]['supports']
    # 结构定位不自动认证含新增假设的正文，交给审稿逐项反馈。


def test_history_used_only_in_premise_still_requires_per_claim_limits(tmp_path):
    ledger, _, _ = setup(tmp_path)
    ledger.add('history', '旧记录中的签名回执', source_id='history-1')
    item = claim()
    item['premises'][1]['supports'] = [{'source_id':'history-1', 'excerpt':'旧记录中的签名回执'}]
    with pytest.raises(ValueError, match='limits'):
        ledger.validate(QUOTE, [Claim.model_validate(item)])
    item['limits'] = '仅旧记录，不认证当前状态。'
    assert ledger.validate(QUOTE, [Claim.model_validate(item)])


def test_nested_premise_tampering_and_unlinked_revision_invalidate_mapping(tmp_path):
    ledger, _, registry = setup(tmp_path)
    registry.execute('write_report', {'content': report(), 'claims': [claim()]})
    path = tmp_path/'report.evidence.json'
    mapping = json.loads(path.read_text(encoding='utf-8'))
    mapping['claims'][0]['premises'][0]['supports'][0]['source_sha256'] = '0'*64
    write_json(path, mapping)
    with pytest.raises(ValueError, match='关联失效'):
        audit_mapping(tmp_path)
    assert ledger.prepare(tmp_path/'report.md', report(), None, complete=True)['status'] == 'stale'
    registry.execute('write_report', {'content': report(), 'claims': [claim()]})
    assert not registry.execute('write_report', {'content': report()+'\n新增说明。'}).is_error
    assert audit_mapping(tmp_path)['status'] == 'stale'


def test_partial_packet_keeps_original_indices_and_does_not_leak_omitted_context(tmp_path):
    ledger, _, _ = setup(tmp_path)
    ledger.add('user', '很长资料。'*500, source_id='user-2')
    huge = claim()
    huge['supports'] = [{'source_id': 'user-2', 'excerpt': '很长资料。'*100}]
    huge['premises'] = []
    mapped = ledger.validate(QUOTE, [Claim.model_validate(huge), Claim.model_validate(claim())])
    packet = ledger.packet({'claims': mapped}, budget=1800)
    assert [i['claim_index'] for i in packet['items']] == [1]
    assert packet['omitted_items'] == 1 and 'user-2' not in packet['sources']
    _, support = parse_review(verdict([check(1)]), QUOTE, packet)
    assert support['status'] == 'incomplete' and support['omitted_items'] == 1


def packet(tmp_path):
    ledger, _, _ = setup(tmp_path)
    return ledger.packet({'claims': ledger.validate(QUOTE, [Claim.model_validate(claim())])})


def test_missing_claim_checks_are_not_an_empty_success_or_an_automatic_retry(tmp_path):
    _, support = parse_review('{"issues": []}', QUOTE, packet(tmp_path))
    assert support['status'] == 'incomplete' and support['missing_claim_indices'] == [0]
    feedback = review_feedback({'status': 'partial', 'issues': [], 'support_review': support})
    assert '未核对不等于支持' in feedback


@pytest.mark.parametrize('changes', [
    {'claim_index': 1}, {'claim_index': True}, {'reason': ' '},
    {'target': 'assumptions'}, {'status': 'insufficient'},
])
def test_claim_checks_reject_unknown_indices_empty_targets_and_unactionable_gaps(tmp_path, changes):
    with pytest.raises(ValueError):
        parse_review(verdict([check(**changes)]), QUOTE, packet(tmp_path))


def test_duplicate_checks_do_not_cover_a_missing_claim(tmp_path):
    with pytest.raises(ReviewValidationError, match='重复'):
        parse_review(verdict([check(), check()]), QUOTE, packet(tmp_path))


def test_checks_cannot_use_a_packet_from_a_different_report(tmp_path):
    with pytest.raises(ReviewValidationError, match='当前报告'):
        parse_review(verdict([check()]), '另一份报告。', packet(tmp_path))


@pytest.mark.parametrize('status', ['contradicted', 'insufficient', 'uncertain'])
def test_mapping_only_findings_use_program_bound_report_quote_and_repair_path(tmp_path, status):
    issues, support = parse_review(verdict([check(status=status, target='premises',
        reason='前提并未足以支持这项范围判断。', missing_evidence='缺少适用对象的证明。',
        suggestion='限定为题设对象；删除超出部分。')]), QUOTE, packet(tmp_path))
    assert support['status'] == 'complete' and support['counts'][status] == 1
    assert issues[0]['quote'] == QUOTE and 'premises' in issues[0]['message']
    feedback = review_feedback({'status': 'reviewed', 'issues': issues, 'report_sha256': sha(QUOTE)})
    assert 'write_report' in feedback and '缺少适用对象的证明' in feedback


def test_bad_check_preserves_valid_report_issue_for_partial_feedback(tmp_path):
    issue = {'category':'unsupported', 'severity':'error', 'quote':QUOTE,
             'message':'此处需要限定。', 'suggestion':'保留题设范围。'}
    with pytest.raises(ReviewValidationError) as exc:
        parse_review(verdict([check(1)], [issue]), QUOTE, packet(tmp_path))
    assert exc.value.valid_issues == [issue]


def test_same_report_revised_premises_are_reaudited_within_original_call_limit(tmp_path):
    _, session, registry = setup(tmp_path)
    first = claim()
    first['premises'].append({'text': '所有档案都已签名', 'basis': 'assumption'})
    bad = check(status='insufficient', target='premises', reason='新增前提没有资料来源且不必要。',
                missing_evidence='所有档案签名状态未给出。', suggestion='删除这项多余前提，按题设规则推导档案甲。')
    llm = ScriptedLLM([completion('{"tasks": []}'),
        completion(tool_calls=[('w1','write_report',{'content':report(),'claims':[first]})]),
        completion(verdict([bad])),
        completion(tool_calls=[('w2','write_report',{'content':report(),'claims':[claim()]})]),
        completion(verdict([check()])), completion('完成')])
    agent = Agent(llm, registry, task_dir=tmp_path, research_session=session, review_reports=True, review_max_calls=2)
    agent.run(QUESTION)
    history = [json.loads(line) for line in (tmp_path/'report.reviews.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(history) == 2 and all(r['status']=='reviewed' for r in history)
    assert history[0]['issues'] and not history[1]['issues']
    assert history[0]['report_sha256'] == history[1]['report_sha256']
    assert history[0]['evidence_mapping_sha256'] != history[1]['evidence_mapping_sha256']
    requests = [r for r in llm.requests if '独立报告审稿人' in r['messages'][0]['content']]
    assert json.loads(requests[1]['messages'][1]['content'])['previous_review']['support_review']['counts']['insufficient'] == 1
    assert agent._review_count == 2 and len(llm.requests) == 6


def test_agent_keeps_missing_checks_partial_without_spending_extra_call(tmp_path):
    _, session, registry = setup(tmp_path)
    llm = ScriptedLLM([completion('{"tasks": []}'),
        completion(tool_calls=[('w1','write_report',{'content':report(),'claims':[claim()]})]),
        completion('{"issues": []}'), completion('保留未完成的逐项审稿状态。')])
    agent = Agent(llm, registry, task_dir=tmp_path, research_session=session, review_reports=True, review_max_calls=2)
    agent.run(QUESTION)
    review = json.loads((tmp_path/'report.review.json').read_text(encoding='utf-8'))
    assert review['status'] == 'partial' and review['support_review']['status'] == 'incomplete'
    assert agent._review_count == 1 and len(llm.requests) == 4
