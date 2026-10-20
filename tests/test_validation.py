"""阶段12评估完整性；不调用真实模型。"""
import copy
import json
from pathlib import Path

import pytest

from agent import validation as v
from tools import default_registry


def test_suite_rejects_duplicate_dependency_and_missing_rubric(tmp_path):
    suite = v.load_suite()
    for mutate in ('duplicate', 'dependency', 'rubric'):
        candidate = copy.deepcopy(suite)
        if mutate == 'duplicate':
            candidate['cases'].append(candidate['cases'][0])
        elif mutate == 'dependency':
            candidate['cases'][0]['depends_on'] = 'not-yet-defined'
        else:
            candidate['cases'][0]['rubric'] = []
        p = tmp_path / 'suite.json'
        p.write_text(json.dumps(candidate), encoding='utf-8')
        with pytest.raises(ValueError):
            v.load_suite(p)


def test_freeze_detects_config_selection_and_code_drift(tmp_path, monkeypatch):
    current = {'split': 'holdout', 'code': 'abc', 'config': {'budget': 80}, 'selection': ['a']}
    monkeypatch.setattr(v, 'identity', lambda *args: copy.deepcopy(current))
    path = tmp_path / 'lock.json'
    lock = v.freeze({}, 'holdout', path)
    v.verify_lock(lock, {})
    with pytest.raises(ValueError):
        v.freeze({}, 'holdout', path)
    current['config']['budget'] = 81
    with pytest.raises(ValueError):
        v.verify_lock(lock, {})
    broken = dict(lock, lock_sha256='fake')
    with pytest.raises(ValueError):
        v.verify_lock(broken, {})


def test_fixture_network_is_closed_and_restored(tmp_path):
    import main
    from tools.session import ResearchSession
    original = main.default_registry
    case = {'environment': 'network_down'}
    with v.fixture_tools(case):
        registry = main.default_registry(tmp_path, session=ResearchSession(), question='读取网页')
        result = registry.execute('fetch_url', {'url': 'https://fixture.invalid/missing'})
        assert result.is_error and '网络' in result.content
    assert main.default_registry is original


def test_injected_tool_failure_preserves_other_tools(tmp_path):
    import main
    with v.fixture_tools({'environment': 'tool_down'}):
        registry = main.default_registry(tmp_path, question='不联网')
        assert registry.execute('calculator', {'expression': '2+3'}).is_error
        assert 'write_report' in registry.names
    assert not default_registry(tmp_path).execute('calculator', {'expression': '2+3'}).is_error


def test_numeric_oracle_uses_exact_independent_values():
    assert v.number_tree({'x': 0.125}) == {'x': v.Decimal('0.125')}
    assert v.number_tree({'x': '0.125'}) != {'x': v.Decimal('0.125')}


def data_for_review(tmp_path):
    task = tmp_path / 'task'; task.mkdir()
    (task / 'report.md').write_text('原文', encoding='utf-8')
    case = {'id': 'a', 'round': 1, 'group': 'normal', 'status': 'pass', 'task_dir': str(task),
            'artifact_sha256': v.artifacts(task)}
    data = {'cases': [case], 'execution': 'live', 'summary': {'automatic_gate': True}}
    decision = {**case, 'verdict': 'pass', 'reviewer': '测试核对人', 'reviewer_kind': 'coding_assistant', 'findings': []}
    return data, decision


def test_review_requires_all_original_versions_and_human_identity(tmp_path):
    data, d = data_for_review(tmp_path)
    assert v.review(data, [d])['acceptance'] == 'not_accepted'
    d['reviewer_kind'] = 'human'
    assert v.review(data, [d])['acceptance'] == 'accepted'
    with pytest.raises(ValueError):
        v.review(data, [])
    with pytest.raises(ValueError):
        v.review(data, [d, d])
    (Path(data['cases'][0]['task_dir']) / 'report.md').write_text('改稿', encoding='utf-8')
    with pytest.raises(ValueError):
        v.review(data, [d])


def test_rescore_cannot_become_original_acceptance(tmp_path):
    data, d = data_for_review(tmp_path)
    data['execution'] = 'rescore'; d['reviewer_kind'] = 'human'
    assert v.review(data, [d])['acceptance'] == 'not_accepted'


def test_gate_requires_three_complete_rounds_separate_fault_success():
    suite = {'policy': {'normal_min_rate': .9}}
    data = {'rounds': 3, 'state': 'complete', 'cases': [
        {'round': r, 'group': group, 'status': 'pass'} for r in range(1, 4) for group in ('normal', 'fault')]}
    assert v.aggregate(data, suite)['automatic_gate']
    data['cases'][-1]['status'] = 'skipped_budget'
    assert not v.aggregate(data, suite)['automatic_gate']
    data['cases'][-1]['status'] = 'pass'; data['state'] = 'running'
    assert not v.aggregate(data, suite)['automatic_gate']


def test_round_budget_is_independent_and_overrun_skips_remaining(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import main
    import agent.llm
    monkeypatch.setattr(v, 'verify_lock', lambda *args: None)
    monkeypatch.setattr(v, 'load_llm_config', lambda **kwargs: None)
    monkeypatch.setattr(agent.llm, 'LLMClient', lambda cfg: object())
    observed = []

    def research(llm, settings, prompt, steps, budget, task_dir):
        observed.append((budget, str(settings.memory_db_path)))
        task_dir.mkdir(parents=True)
        return SimpleNamespace(equivalent_tokens=6, steps=1, stop_reason='final_answer')

    monkeypatch.setattr(main, 'research', research)
    monkeypatch.setattr(v, 'assess', lambda case, task: {'status': 'pass', 'group': 'normal',
        'task_dir': str(task), 'metrics': {'equivalent_tokens': 6}, 'artifact_sha256': {}})
    suite = {'policy': {'normal_min_rate': .9, 'max_steps': 15, 'per_case_budget': 8,
                        'total_budget_per_round': 5},
             'cases': [{'id': x, 'split': 'holdout', 'prompt': x, 'rubric': ['核对']} for x in ('a', 'b')]}
    lock = {'split': 'holdout', 'code_sha256': 'a', 'suite_sha256': 'b'}
    data = v.run(suite, lock, tmp_path, 3)
    assert [v[0] for v in observed] == [5, 5, 5]
    assert len({v[1] for v in observed}) == 3
    assert [c['status'] for c in data['cases']] == ['pass', 'skipped_budget'] * 3


def test_fault_budget_does_not_turn_normal_success_into_expected_failure(tmp_path):
    task = tmp_path
    (task / 'report.md').write_text('# 报告\n未核实', encoding='utf-8')
    def write_end(stop):
        (task / 'trace.jsonl').write_text(json.dumps({'kind': 'run_end', 'stop_reason': stop}), encoding='utf-8')
    write_end('final_answer')
    case = {'fault': 'budget', 'prompt': '不联网'}
    assert v.assess(case, task)['status'] == 'fail'
    write_end('budget')
    assert v.assess(case, task)['status'] == 'pass'
    v.save(task / 'report.review.json', {'status': 'reviewed'})
    assert v.assess(case, task)['status'] == 'fail'
