import pytest

from agent.numeric_evidence import arithmetic_errors, csv_facts, price_ratio_errors
from tools.registry import ToolRegistry
from tools.report import make_write_report


@pytest.mark.parametrize('text,wrong', [
    ('49/51≈96.1%，48/51≈94.1%，合计98/102≈95.1%。', True),
    ('合计97/102≈95.1%。', False), ('合计49+48=98。', True),
    ('变量x98/102≈95.1%。', False), ('版本v2+3=8。', False),
    ('> 原错误：合计98/102≈95.1%。', False),
])
def test_chinese_adjacent_equations_keep_ascii_identifier_boundary(text, wrong):
    assert bool(arithmetic_errors(text)) is wrong


@pytest.mark.parametrize('text,wrong', [
    ('两型号的输入、输出单价比例均为1:3。', True),
    ('Alpha的输入/输出单价之比为1:4。', False),
    ('Alpha的输入/输出单价比例为1:3。', True),
    ('Beta的输入、输出单价比例为1:3。', False),
    ('不能说两型号的输入、输出单价比例均为1:3。', False),
    ('> 原错误：两型号的输入、输出单价比例均为1:3。', False),
])
def test_intra_model_price_ratio_is_not_cross_model_ratio(text, wrong):
    assert bool(price_ratio_errors(text, 'model,input_price,output_price\nAlpha,2,8\nBeta,9,27\n')) is wrong


def test_actual_failed_reports_are_rejected_and_old_file_preserved(tmp_path):
    from pathlib import Path
    (tmp_path/'costs.csv').write_text('model,input_price,output_price\nEvalAlpha,2,8\nEvalBeta,9,27\n',encoding='utf-8')
    reg=ToolRegistry([make_write_report(tmp_path)])
    assert not reg.execute('write_report',{'content':'保留正确稿。'}).is_error
    for name in ['numeric_bad_total.md', 'numeric_bad_internal_ratio.md']:
        text=(Path(__file__).parent/'fixtures'/name).read_text(encoding='utf-8')
        assert reg.execute('write_report',{'content':text}).is_error
        assert (tmp_path/'report.md').read_text(encoding='utf-8')=='保留正确稿。'


@pytest.mark.parametrize("text", ["27 ÷ 8 = 4.5", "2+3=8", "1/0=0", "9×2＝20",
                                  "**27 ÷ 8 = 4.5**", "27 ÷ 8 = **4.5 倍**"])
def test_incorrect_explicit_arithmetic_is_rejected(text):
    assert arithmetic_errors(text)


@pytest.mark.parametrize('text,bad', [
    (r'0.9 \times 0.1/20 = 0.0045', False),
    (r'0.9 \cdot 0.1/20 = 0.0045', False),
    (r'9 \div 2 = 4.5', False),
    (r'0.9 \times 0.1/20 = 0.0050', True),
])
def test_latex_operators_do_not_create_false_suffix_equations(text, bad):
    assert bool(arithmetic_errors(text)) == bad


@pytest.mark.parametrize("text", ["27 ÷ 8 = 3.375", "1.17/0.28≈4.18", "49/51≈96.1%", "0.4+0.08=0.48",
                                  "1.17/0.28≈**4.18 倍**",
                                  "200,000 ÷ 1,000,000 × 2 = 0.4", "200 000 ÷ 1 000 000 × 2 = 0.40",
                                  "(200000 / 1000000) * 2 = 0.4", "2 + 3 * 4 = 14", "2 × (3 + 4) = 14",
                                  "```python\nx=2+3==8\n```", "> 原错误：27/8=4.5"])
def test_rounding_correct_equations_and_nonassertions(text):
    assert not arithmetic_errors(text)


def test_full_chained_expression_is_checked_not_suffix():
    assert arithmetic_errors("200000 ÷ 1000000 × 2 = 0.2")
    assert not arithmetic_errors("200000 token ÷ 1000000 × 2 = 0.4")  # 未支持的单位表达式不能截后缀误判。


@pytest.mark.parametrize('text', [
    '0.9 + 3.8416/40 = 0.9 + 0.096040 = 0.996040',
    '2 * 3 = 1 + 5', '2 × (3 + 4) = 7 * 2', '(2 + 3) = (4 + 1)',
    '2 * 3 = 2 * sqrt(9)',
])
def test_complete_rhs_and_correct_equality_chains_are_not_prefix_errors(text):
    assert not arithmetic_errors(text)


@pytest.mark.parametrize('text', [
    '0.9 + 3.8416/40 = 0.9 + 0.096040 = 0.9',
    '0.8 + 3.8416/40 = 0.9 + 0.096040 = 0.996040',
    '2 * 3 = 1 + 8', '2 * 3 = 27 / 4',
])
def test_full_rhs_mismatch_and_incorrect_chain_members_still_fail(text):
    assert arithmetic_errors(text)


def test_wrong_equation_cannot_overwrite_saved_report(tmp_path):
    registry = ToolRegistry([make_write_report(tmp_path)])
    assert not registry.execute("write_report", {"content": "27/8=3.375"}).is_error
    assert registry.execute("write_report", {"content": "27/8=4.5"}).is_error
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "27/8=3.375"


def test_csv_facts_compute_both_price_ratios_and_cost_components():
    facts = csv_facts("model,input_price,output_price,input_tokens,output_tokens\nA,2,8,200000,10000\nB,9,27,200000,10000\n")
    assert facts[0]["output_price_div_input_price"] == "4"
    assert facts[1]["output_price_div_input_price"] == "3"
    assert facts[0]["expected_total_cost"] == "0.48"
    assert facts[1]["expected_total_cost"] == "2.07"
    assert facts[1]["input_price_div_reference"] == "4.5"
    assert facts[1]["output_price_div_reference"] == "3.375"
    assert csv_facts("model,input_price,output_price\nA,NaN,8\n") == []


def test_current_report_table_cannot_mix_in_historical_amounts(tmp_path):
    (tmp_path / "costs.csv").write_text("model,input_cost,output_cost,total_cost\nA,0.4,0.08,0.48\n", encoding="utf-8")
    good = "| model | input_cost | output_cost | total_cost |\n|---|---|---|---|\n| A | 0.4 | 0.08 | 0.48 |"
    registry = ToolRegistry([make_write_report(tmp_path)])
    assert not registry.execute("write_report", {"content": good}).is_error
    result = registry.execute("write_report", {"content": good + "\n\n## 历史\n" + good.replace('0.4 |', '0.2 |').replace('0.48 |', '0.28 |')})
    assert result.is_error and "历史数据" in result.content
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == good


@pytest.mark.parametrize("text,wrong", [
    ("Beta 的输入、输出单价均为 Alpha 对应单价的 4.5 倍。", True),
    ("Beta 的输入、输出单价均为 Alpha 的 4.5 倍。", True),
    ("Beta 的输入单价为 Alpha 的 4.5 倍，输出单价为 3.375 倍。", False),
    ("Beta 的输出单价为 Alpha 的 3.375 倍。", False),
    ("历史摘要曾称 Beta 的输入、输出单价均为 Alpha 的 4.5 倍，这一说法有误。", False),
])
def test_named_price_ratio_claims_against_csv(text, wrong):
    content = "model,input_price,output_price\nAlpha,2,8\nBeta,9,27\n"
    assert bool(price_ratio_errors(text, content)) is wrong
