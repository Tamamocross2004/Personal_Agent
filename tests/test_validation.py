"""阶段12评估完整性；不调用真实模型。"""
import copy
import json
from pathlib import Path

import pytest

from agent import validation as v
from tools import default_registry


def test_single_round_development_report_does_not_claim_three_rounds_or_real_network(tmp_path):
    data={'execution':'live','state':'complete','lock':{'code_sha256':'x','suite_sha256':'y'},
          'summary':{'rounds':[{'round':1,'normal':{'passed':1,'selected':1},
                               'fault':{'passed':1,'selected':1}}],
                     'automatic_gate':False,'metrics':{}},'cases':[]}
    v.report(tmp_path,data)
    text=(tmp_path/'report.md').read_text(encoding='utf-8')
    assert '三轮自动检查' not in text and '另有一题真实联网' not in text
    assert '开发集' in text and '实际轮次数见表' in text


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


@pytest.mark.parametrize('original_form', ['missing', 'null', 'empty'])
@pytest.mark.parametrize('decision_form', ['missing', 'null', 'empty'])
def test_legacy_unexecuted_empty_hash_forms_are_compatible_without_changing_source(tmp_path, original_form, decision_form):
    data, decision = data_for_review(tmp_path)
    skipped = {'id': 'follow', 'round': 1, 'group': 'normal', 'status': 'skipped_dependency'}
    if original_form != 'missing':
        skipped['artifact_sha256'] = None if original_form == 'null' else {}
    data['cases'].append(skipped)
    data['summary']['automatic_gate'] = False
    review = {'id': 'follow', 'round': 1, 'verdict': 'uncertain',
              'reviewer': '测试助手', 'reviewer_kind': 'coding_assistant', 'findings': ['未执行，无法核对报告。']}
    if decision_form != 'missing':
        review['artifact_sha256'] = None if decision_form == 'null' else {}
    original, decisions = copy.deepcopy(data), copy.deepcopy([decision, review])
    result = v.review(data, decisions)
    assert result['counts'] == {'pending': 0, 'pass': 1, 'fail': 0, 'uncertain': 1}
    assert result['acceptance'] == 'not_accepted' and not result['human_review_complete']
    assert data == original and decisions == [decision, review]


@pytest.mark.parametrize('invalid', [None, {}, {'report.md': 'bad-hash'}])
def test_executed_artifact_version_is_not_weakened_by_empty_hash_compatibility(tmp_path, invalid):
    data, decision = data_for_review(tmp_path)
    decision['artifact_sha256'] = invalid
    with pytest.raises(ValueError, match='第1轮 a.*产物版本不同'):
        v.review(data, [decision])


def test_executed_empty_directory_is_not_unexecuted_or_missing_manifest(tmp_path):
    data, decision = data_for_review(tmp_path)
    (Path(data['cases'][0]['task_dir']) / 'report.md').unlink()
    data['cases'][0]['artifact_sha256'] = {}
    decision['artifact_sha256'] = {}
    assert v.review(data, [decision])['counts']['pass'] == 1
    data['cases'][0]['artifact_sha256'] = None
    with pytest.raises(ValueError, match='已执行项缺少产物哈希'):
        v.review(data, [decision])


@pytest.mark.parametrize('mutation', ['empty', 'duplicate', 'boolean_round', 'missing_task', 'malformed_hash'])
def test_review_rejects_invalid_source_records(tmp_path, mutation):
    data, decision = data_for_review(tmp_path)
    if mutation == 'empty': data['cases'] = []
    elif mutation == 'duplicate': data['cases'].append(copy.deepcopy(data['cases'][0]))
    elif mutation == 'boolean_round': data['cases'][0]['round'] = True
    elif mutation == 'missing_task': data['cases'][0].pop('task_dir')
    else: data['cases'][0]['artifact_sha256'] = {'report.md': 'not-a-sha256'}
    with pytest.raises(ValueError):
        v.review(data, [decision])


def test_pending_review_reports_progress_and_never_implies_human_completion(tmp_path):
    data, decision = data_for_review(tmp_path)
    decision.update(verdict='pending', reviewer='', reviewer_kind='')
    result = v.review(data, [decision])
    assert result['review_status'] == 'pending' and result['counts']['pending'] == 1
    assert result['acceptance'] == 'not_accepted'
    assert not result['human_review_complete'] and not result['semantic_clean']
    decision.update(reviewer='填写中的核对人', reviewer_kind='human')
    assert not v.review(data, [decision])['human_review_complete']
    decision.update(verdict='pass', reviewer='   ')
    with pytest.raises(ValueError, match='第1轮 a.*身份'):
        v.review(data, [decision])


def template_fixture(tmp_path):
    data, _ = data_for_review(tmp_path)
    data['cases'].append({'id': 'follow', 'round': 1, 'group': 'normal', 'status': 'skipped_budget'})
    data['summary']['automatic_gate'] = False
    suite = {'version': 1, 'policy': {'normal_min_rate': .9}, 'cases': [
        {'id': name, 'split': 'holdout', 'prompt': name, 'rubric': ['核对原文']}
        for name in ('a', 'follow')]}
    data['lock'] = {'suite_sha256': v.digest(suite)}
    source, definitions = tmp_path / 'results.json', tmp_path / 'suite.json'
    v.save(source, data); v.save(definitions, suite)
    return data, suite, source, definitions


def test_template_binds_source_hash_and_copies_manifest_without_mutation(tmp_path):
    data, suite, _, _ = template_fixture(tmp_path)
    original = copy.deepcopy(data)
    worksheet = v.review_template(data, suite['cases'])
    assert worksheet[1]['artifact_sha256'] is None
    assert all(d['source_results_sha256'] == v.digest(data) for d in worksheet)
    assert v.review(data, worksheet)['counts']['pending'] == 2
    worksheet[0]['artifact_sha256']['report.md'] = 'f' * 64
    assert data == original
    with pytest.raises(ValueError, match='产物版本不同'):
        v.review(data, worksheet)
    worksheet = v.review_template(data, suite['cases'])
    data['summary']['automatic_gate'] = True
    with pytest.raises(ValueError, match='绑定的原始结果不同'):
        v.review(data, worksheet)


def test_cli_exports_old_results_and_accepts_pending_input_without_loading_model(tmp_path, monkeypatch):
    data, _, source, definitions = template_fixture(tmp_path)
    before = source.read_bytes()
    monkeypatch.setattr(v, 'Settings', lambda *a, **kw: pytest.fail('离线复核不应加载模型配置'))
    out = tmp_path / 'export'
    assert v.main(['--suite', str(definitions), 'template', str(source), '--out', str(out)]) == 0
    worksheet = v.read(out / 'review-template.json')
    assert v.read(out / 'review-status.json')['counts']['pending'] == 2
    # review不依赖当前题集或模型配置。
    checked = tmp_path / 'reviewed'
    assert v.main(['--suite', str(tmp_path / 'missing-suite.json'), 'review', str(source),
                   str(out / 'review-template.json'), '--out', str(checked)]) == 1
    result = v.read(checked / 'review.json')
    assert result['acceptance'] == 'not_accepted' and not result['human_review_complete']
    assert result['counts']['pending'] == 2 and source.read_bytes() == before
    assert all(d['reviewer'] == d['reviewer_kind'] == '' for d in worksheet)


def test_invalid_cli_input_does_not_create_output_or_overwrite_existing_files(tmp_path, capsys):
    _, _, source, definitions = template_fixture(tmp_path)
    wrong_suite = v.read(definitions)
    wrong_suite['cases'][0]['rubric'] = ['另一版本']
    v.save(definitions, wrong_suite)
    out = tmp_path / 'new-export'
    with pytest.raises(SystemExit) as exc:
        v.main(['--suite', str(definitions), 'template', str(source), '--out', str(out)])
    assert exc.value.code == 2 and not out.exists()
    assert '冻结版本不符' in capsys.readouterr().err
    out.mkdir(); (out / 'keep.txt').write_text('用户文件', encoding='utf-8')
    decisions = tmp_path / 'decisions.json'
    v.save(decisions, v.review_template(v.read(source), wrong_suite['cases']))
    with pytest.raises(SystemExit) as exc:
        v.main(['review', str(source), str(decisions), '--out', str(out)])
    assert exc.value.code == 2 and (out / 'keep.txt').read_text(encoding='utf-8') == '用户文件'
    assert sorted(p.name for p in out.iterdir()) == ['keep.txt']


def test_interrupted_execution_keeps_partial_task_versions_in_review_template(tmp_path, monkeypatch):
    import main
    import agent.llm
    monkeypatch.setattr(v, 'verify_lock', lambda *args: None)
    monkeypatch.setattr(v, 'load_llm_config', lambda **kwargs: None)
    monkeypatch.setattr(agent.llm, 'LLMClient', lambda cfg: object())
    def interrupted(llm, settings, prompt, steps, budget, task_dir):
        task_dir.mkdir(parents=True)
        (task_dir / 'report.md').write_text('未完成的真实输出', encoding='utf-8')
        raise KeyboardInterrupt
    monkeypatch.setattr(main, 'research', interrupted)
    suite = {'policy': {'normal_min_rate': .9, 'max_steps': 15, 'per_case_budget': 8,
                        'total_budget_per_round': 10},
             'cases': [{'id': x, 'split': 'holdout', 'prompt': x, 'rubric': ['核对']} for x in ('a', 'b')]}
    data = v.run(suite, {'split': 'holdout', 'code_sha256': 'x', 'suite_sha256': 'y'}, tmp_path, 1)
    assert data['state'] == 'interrupted' and data['cases'][0]['status'] == 'interrupted'
    assert data['cases'][0]['artifact_sha256'] == v.artifacts(Path(data['cases'][0]['task_dir']))
    template = v.read(tmp_path / 'review-template.json')
    assert template[0]['artifact_sha256'] and template[1]['artifact_sha256'] is None
    assert v.review(data, template)['counts']['pending'] == 2
