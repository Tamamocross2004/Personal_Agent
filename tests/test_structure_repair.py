"""真实短报告失败样本：结构修复须落实到文件，且保持长度/预算/审稿边界。"""

import json
from pathlib import Path

import pytest

from agent.loop import Agent
from agent.quality import inspect_report, structure_repair_feedback
from agent.review import fingerprint
from agent.task_contract import check_contract, extract_contract
from fakes import ScriptedLLM, completion
from tools.registry import ToolRegistry
from tools.report import make_write_report

QUESTION = ('只依据给定虚构资料，不联网、不做统计分析：甲产品输入2元/百万token，'
            '乙产品输入9元/百万token，均没有输出价格和质量测试。全文不超过220字。'
            '请说明可以比较什么，以及为什么不能判断总费用或综合性价比。')
BAD = (Path(__file__).parent / 'fixtures/short_report_missing_sections.md').read_text(encoding='utf-8')
FIXED = BAD.replace('## 可比维度', '## 方法与范围').replace(
    '## 局限性', '## 结论\n仅能比较输入单价，不能判断总费用或综合性价比。\n\n## 局限性')


def run_agent(tmp_path, responses, **kwargs):
    llm = ScriptedLLM([completion('{"tasks": []}')] + responses)
    events = []
    agent = Agent(llm, ToolRegistry([make_write_report(tmp_path, question=QUESTION)]),
                  task_dir=tmp_path, on_event=events.append, **kwargs)
    result = agent.run(QUESTION)
    return agent, llm, events, result


def write(content=BAD, **kwargs):
    return completion(tool_calls=[('write', 'write_report', {'content': content, **kwargs})])


def test_real_failure_fits_with_meaningful_sections_and_feedback(tmp_path):
    contract = extract_contract(QUESTION)
    assert check_contract(BAD, contract)['measurements'][0]['actual'] == 175
    assert check_contract(FIXED, contract)['measurements'][0]['actual'] == 201
    check = inspect_report(BAD, tmp_path, None, contract=contract)
    assert len(check.issues) == 2
    feedback = structure_repair_feedback(BAD, contract, check)
    assert '当前175，上限220，剩余45' in feedback and fingerprint(BAD) in feedback
    assert '余量不保证能直接追加' in feedback
    assert not inspect_report(FIXED, tmp_path, None, contract=contract).issues


@pytest.mark.parametrize('question,label', [('全文不超过220个中文字符', '汉字'),
                                          ('正文最多100个英文单词', '英文单词')])
def test_repair_feedback_uses_current_length_unit(tmp_path, question, label):
    contract = extract_contract(question)
    feedback = structure_repair_feedback(BAD, contract, inspect_report(BAD, tmp_path, None))
    assert label in feedback
    assert '可见非空白字符' not in feedback


def test_unlimited_structure_feedback_does_not_invent_a_cap(tmp_path):
    feedback = structure_repair_feedback(BAD, extract_contract('详细说明'), inspect_report(BAD, tmp_path, None))
    assert '上限' not in feedback.split('用户硬上限')[0]
    assert '剩余' not in feedback


def test_premature_final_is_redirected_then_only_fixed_report_is_reviewed(tmp_path):
    agent, llm, events, result = run_agent(tmp_path, [
        write(), completion('补章节会超限，因此完成。'), write(FIXED),
        completion('{"issues": []}'), completion('修复完成。'),
    ], review_reports=True, review_max_calls=1)
    assert (tmp_path / 'report.md').read_text(encoding='utf-8') == FIXED
    history = [json.loads(s) for s in (tmp_path / 'report.reviews.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [r['status'] for r in history] == ['not_reviewed', 'reviewed']
    assert history[-1]['report_sha256'] == fingerprint(FIXED)
    assert agent._review_count == 1 and result.steps == 4
    reviews = [r for r in llm.requests if r.get('response_format') and r['messages'][0]['role'] == 'system']
    assert len(reviews) == 1 and json.loads(reviews[0]['messages'][1]['content'])['report'] == FIXED
    assert len([e for e in events if e.kind == 'notice' and '【结束前结构核对】' in e.data['text']]) == 1
    assert [e.data['text'] for e in events if e.kind == 'final'] == ['修复完成。']


def test_structure_patch_receives_hash_and_preserves_length(tmp_path):
    agent, _, _, _ = run_agent(tmp_path, [
        write(), completion('完成。'),
        completion(tool_calls=[('patch', 'write_report', {'mode': 'patch', 'base_sha256': fingerprint(BAD),
                   'replacements': [{'old': '## 可比维度', 'new': '## 方法与范围'},
                                    {'old': '## 局限性', 'new': '## 结论\n仅能比较输入单价，不能判断总费用或综合性价比。\n\n## 局限性'}]})]),
        completion('{"issues": []}'), completion('完成。'),
    ], review_reports=True)
    assert agent._review_count == 1
    assert (tmp_path / 'report.md').read_text(encoding='utf-8') == FIXED
    assert not [i for i in json.loads((tmp_path / 'report.quality.json').read_text(encoding='utf-8'))['issues']
                if i['category'] == 'structure']
    assert not json.loads((tmp_path / 'report.contract.json').read_text(encoding='utf-8'))['hard_errors']


def test_structure_repair_cannot_bypass_length_or_destroy_previous_report(tmp_path):
    registry = ToolRegistry([make_write_report(tmp_path, question=QUESTION)])
    assert not registry.execute('write_report', {'content': BAD}).is_error
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = registry.execute('write_report', {'mode': 'patch', 'base_sha256': fingerprint(BAD),
        'replacements': [{'old': '## 可比维度', 'new': '## 方法与范围\n' + '重复内容。'*40}]})
    assert result.is_error and '超过明确上限' in result.content
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize('review', [True, False])
def test_refusal_to_repair_is_bounded_and_remains_visible(tmp_path, review):
    agent, llm, _, result = run_agent(tmp_path, [write(), completion('不修复。'), completion('仍不修复。')],
                                     review_reports=review)
    assert result.steps == 3 and len(llm.requests) == 4
    assert agent._review_count == 0
    assert (tmp_path / 'report.md').read_text(encoding='utf-8') == BAD
    assert len([i for i in json.loads((tmp_path / 'report.quality.json').read_text(encoding='utf-8'))['issues']
                if i['category'] == 'structure']) == 2
    if review:
        assert json.loads((tmp_path / 'report.review.json').read_text(encoding='utf-8'))['status'] == 'not_reviewed'


def test_last_step_does_not_extend_main_loop_for_structure(tmp_path):
    agent, llm, _, result = run_agent(tmp_path, [write(), completion('完成但留有结构警告。')],
                                     review_reports=True, max_steps=2)
    assert result.steps == 2 and not agent._structure_repair_requested
    assert len(llm.requests) == 3 and agent._review_count == 0


def test_budget_exhaustion_keeps_existing_forced_wrapup_boundary(tmp_path):
    agent, llm, _, result = run_agent(tmp_path, [
        write(), completion('暂不修复。', prompt_tokens=80000), completion('预算已用尽，仍有结构警告。'),
    ], review_reports=True, token_budget=80000)
    assert result.stop_reason == 'budget' and agent._structure_repair_requested
    assert agent._review_count == 0 and len(llm.requests) == 4
    assert json.loads((tmp_path / 'report.review.json').read_text(encoding='utf-8'))['status'] == 'not_reviewed'


def test_changed_but_still_incomplete_report_does_not_reset_repair_allowance(tmp_path):
    agent, _, events, result = run_agent(tmp_path, [write(), completion('完成。'),
                                                 write(BAD + '\n仅比输入。'), completion('仍缺章节。')])
    assert result.steps == 4 and agent._structure_repair_requested
    assert len([e for e in events if e.kind == 'notice' and '【结束前结构核对】' in e.data['text']]) == 1


def test_partial_and_preexisting_reports_do_not_trigger_completion_repair(tmp_path):
    (tmp_path / 'old.md').write_text(BAD, encoding='utf-8')
    agent, llm, _, _ = run_agent(tmp_path, [write(complete=False), completion('草稿未完成。')], review_reports=True)
    assert not agent._structure_repair_requested and len(llm.requests) == 3
    assert json.loads((tmp_path / 'report.review.json').read_text(encoding='utf-8'))['status'] == 'not_reviewed'


def test_clean_report_is_not_given_extra_repair_or_review_calls(tmp_path):
    agent, llm, _, _ = run_agent(tmp_path, [write(FIXED), completion('{"issues": []}'), completion('完成。')],
                                  review_reports=True)
    assert not agent._structure_repair_requested and len(llm.requests) == 4
    assert agent._review_count == 1


def test_plain_response_is_not_forced_into_a_report(tmp_path):
    agent, llm, _, _ = run_agent(tmp_path, [completion('甲输入单价更低。')])
    assert not agent._structure_repair_requested and len(llm.requests) == 2
    assert not (tmp_path / 'report.md').exists()
