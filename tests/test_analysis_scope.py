import json

import pytest

from agent.analysis_scope import code_scope_errors, report_scope_errors
from agent.task_contract import extract_contract, check_contract
from agent.numeric_evidence import csv_facts, scaling_explanation_errors
from agent.planner import generate_plan
from fakes import completion
from tools import default_registry
from tools.report import make_write_report
from tools.registry import ToolRegistry


@pytest.mark.parametrize('question,expected', [
    ('请用Python计算Wilson 95%置信区间，详细解释公式。', ('Wilson',)),
    ('请计算Wilson和Wald置信区间并比较。', ('Wilson', 'Wald')),
    ('请比较Wilson、Clopper-Pearson和Agresti–Coull。', ('Wilson', 'Clopper-Pearson', 'Agresti-Coull')),
    ('不要计算Wald，仅计算Wilson区间。', ('Wilson',)),
    ('原要求计算Wald；现在更正为计算Wilson区间。', ('Wilson',)),
    ('资料提到Wilson和Wald。请转述，不做统计分析。', ()),
    ('材料原话：“请计算Wald区间”。本次请计算Wilson区间。', ('Wilson',)),
    ('说明Wilson与Wald的概念差异，不进行计算。', ()),
    ('请做统计分析，自行选择合适方法。', ()),
])
def test_requested_method_scope_comes_from_explicit_instructions(question, expected):
    assert extract_contract(question).analysis_methods == expected


@pytest.mark.parametrize('report', [
    'Wald 区间为0.7至1.0。',
    '作为对照，Clopper-Pearson区间 [0.6, 0.99]。',
    '| 方法 | 下限 | 上限 |\n|---|---|---|\n| Agresti-Coull | 0.68 | 0.98 |',
    '| 方法 | 区间 |\n|---|---|\n| Wald | [0.7,1.0] |',
])
def test_unrequested_numeric_results_are_rejected(report):
    c = extract_contract('请计算Wilson区间，详细解释。')
    assert report_scope_errors(report, c.analysis_methods)
    assert check_contract(report, c)['hard_errors']


@pytest.mark.parametrize('report', [
    '未计算Wald区间。本次Wilson区间为[0.699,0.972]。',
    'Wald 方法在接近边界时可能不可靠，本文未采用。',
    'Wald与Wilson的概念不同；只提供Wilson区间[0.699,0.972]。',
    '| 方法 | 下限 | 上限 |\n|---|---|---|\n| Wilson | 0.699 | 0.972 |\n| Wald | 未计算 | 未计算 |',
    '```text\nWald区间为[0.1,0.9]\n```',
])
def test_method_mentions_negation_and_examples_are_not_extra_results(report):
    assert not report_scope_errors(report, ('Wilson',))


def test_multiple_requested_methods_remain_allowed():
    assert not report_scope_errors('Wald区间为[0.7,1.0]。Wilson区间为[0.69,0.97]。', ('Wilson', 'Wald'))
    assert not code_scope_errors('wald_lower = 0.7\nwilson_lower = 0.69\nprint(wald_lower)', ('Wilson', 'Wald'))


def test_code_preflight_checks_ast_names_without_executing_or_creating_files(tmp_path):
    registry = default_registry(tmp_path, question='请计算Wilson区间，不联网。')
    bad = registry.execute('run_python', {'code': "from pathlib import Path\nwald_ci=(0.7,1)\nPath('unwanted.txt').write_text('executed')"})
    assert bad.is_error and '尚未执行' in bad.content
    assert not (tmp_path/'unwanted.txt').exists() and not (tmp_path/'.code_runs').exists()
    good = registry.execute('run_python', {'code': "# 不计算Wald\nwilson_lower=0.699\nprint('未计算Wald', wilson_lower)"})
    assert not good.is_error and '0.699' in good.content


def test_code_guard_uses_updated_shared_contract(tmp_path):
    from tools import ResearchSession
    session = ResearchSession()
    registry = default_registry(tmp_path, question='请计算Wilson区间，不联网。', session=session)
    session.task_contract = extract_contract('请比较Wilson和Wald区间，不联网。')
    assert not registry.execute('run_python', {'code':'wald_lower=0.7\nprint(wald_lower)'}).is_error


def test_scope_rejection_preserves_report_and_sidecars(tmp_path):
    c = extract_contract('请计算Wilson置信区间。')
    registry = ToolRegistry([make_write_report(tmp_path, contract=c)])
    assert not registry.execute('write_report', {'content':'# 结果\nWilson区间为[0.699,0.972]。'}).is_error
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    for mode in ('overwrite', 'append'):
        assert registry.execute('write_report', {'content':'Wald区间为[0.7,1.0]。', 'mode':mode}).is_error
        assert before == {p.name:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}


def test_planner_removes_unrequested_computations_but_keeps_assumption_question(tmp_path):
    c = extract_contract('请计算Wilson区间，解释独立性前提。')
    def chat(*args, **kwargs):
        return completion(json.dumps({'tasks':[{'title':'比较Wilson和Wald区间'}, {'title':'说明独立性前提及外推边界'}]}))
    plan = generate_plan(c.request_excerpt, chat, tmp_path, contract=c)
    assert not any('Wald' in t['title'] for t in plan['tasks'])
    assert any('独立性' in t['title'] for t in plan['tasks'])
    assert plan['scope_adjustments']['removed']


def test_original_statistics_reports_trigger_additional_method_results():
    # 在冻结运行结束后保存的失败原文，避免只验证人工编写的句式。
    from pathlib import Path
    fixture = Path(__file__).parent/'fixtures/requested_statistics_stage10.md'
    assert len(report_scope_errors(fixture.read_text(encoding='utf-8'), ('Wilson',))) >= 2


def test_cost_change_evidence_uses_old_weights_and_labels_counterfactual():
    facts = csv_facts('model,input_tokens,output_tokens,input_price,output_price\nA,200000,10000,2,8\nB,200000,10000,9,27\n')
    assert float(facts[0]['current_input_cost_share']) == pytest.approx(5/6)
    baseline = facts[0]['counterfactual_half_input']
    assert float(baseline['baseline_input_cost_share']) == pytest.approx(5/7)
    assert float(baseline['current_div_baseline_total']) == pytest.approx(12/7)
    assert '不代表实际历史' in baseline['condition']
    assert float(facts[1]['counterfactual_half_input']['current_div_baseline_total']) == pytest.approx(23/13)


@pytest.mark.parametrize('text,bad', [
    ('总费用倍数等于本次输入费用占比×2+输出费用占比×1。', True),
    ('总费用倍数等于历史输入费用占比×2+输出费用占比×1。', False),
    ('本次输入费用占比为83.33%。', False),
    ('错误示例：总费用倍数等于本次输入费用占比×2+输出费用占比×1。', False),
])
def test_weighting_formula_distinguishes_baseline_from_current_composition(text, bad):
    assert bool(scaling_explanation_errors(text)) == bad


def test_call_events_distinguish_requested_alias_and_response_model():
    from types import SimpleNamespace
    from agent.loop import Agent
    from fakes import ScriptedLLM
    model = ScriptedLLM([completion('完成')])
    model.config = SimpleNamespace(model='requested-alias')
    events = []
    Agent(model, ToolRegistry([]), on_event=events.append).run('hi')
    call = next(e.data for e in events if e.kind == 'llm_call')
    assert call['requested_model'] == 'requested-alias'
    assert call['response_model'] == 'fake-model'
