import pytest

from agent.eval_checks import table_rows
from agent.markdown_tables import markdown_tables
from agent.numeric_evidence import report_cost_errors
from tools.report import missing_table_headers

FIELDS = ("model", "input_cost", "output_cost", "total_cost")


@pytest.mark.parametrize("outer", [False, True])
def test_table_contract_grading_and_cost_guard_share_markdown_grammar(outer):
    lines = ["**model** | `input_cost` | output_cost | total_cost",
             ":--- | ---: | :---: | ---", "EvalAlpha | 0.4 | 0.08 | 0.48"]
    text = "\n".join("| " + line + " |" if outer else line for line in lines)
    assert not missing_table_headers(text, [FIELDS])
    assert table_rows(text) == [dict(zip(FIELDS, ("EvalAlpha", "0.4", "0.08", "0.48")))]
    csv = "model,input_cost,output_cost,total_cost\nEvalAlpha,0.4,0.08,0.48\n"
    assert not report_cost_errors(text, csv)
    assert report_cost_errors(text.replace("0.48", "0.28"), csv)
    assert not table_rows("```markdown\n" + text + "\n```")
    assert missing_table_headers("```markdown\n" + text + "\n```", [FIELDS])


def test_escaped_pipes_multiple_tables_and_prose_are_distinct():
    text = "name | value\n---|---\nA\\|B|`x`\n\nplain | prose\n\nother|amount\n---|---\nC|3"
    assert markdown_tables(text) == [(('name', 'value'), [('A|B', 'x')]),
                                     (('other', 'amount'), [('C', '3')])]
