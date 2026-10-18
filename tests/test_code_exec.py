"""真实子进程验证计算、错误恢复、超时和图表落盘；不调用模型 API。"""

import time

from agent.loop import Agent
from fakes import ScriptedLLM, completion
from tools.code_exec import make_run_python
from tools.registry import ToolRegistry
from tools.report import make_write_report


def test_calculation_cwd_unicode_and_script_preserved(tmp_path):
    task = tmp_path / "任务"
    registry = ToolRegistry([make_run_python(task)])
    result = registry.execute("run_python", {"code": "from pathlib import Path\nprint('计算结果', 100000*3/1000000+10000*6/1000000)\nPath('result.txt').write_text('完成', encoding='utf-8')"})
    assert not result.is_error
    assert "计算结果 0.36" in result.content
    assert "result.txt" in result.content
    assert (task / "result.txt").read_text(encoding="utf-8") == "完成"
    assert len(list((task / ".code_runs").glob("*/code.py"))) == 1


def test_child_does_not_inherit_secrets_or_pythonpath(tmp_path, monkeypatch):
    for key in ("DEEPSEEK_API_KEY", "UNRELATED_SECRET", "PYTHONPATH"):
        monkeypatch.setenv(key, "stage6-secret")
    result = make_run_python(tmp_path)("import os\nprint([os.getenv(k) for k in ['DEEPSEEK_API_KEY','UNRELATED_SECRET','PYTHONPATH']])")
    assert not result.is_error
    assert "[None, None, None]" in result.content
    assert "stage6-secret" not in result.content


def test_failure_is_tool_error_with_traceback(tmp_path):
    registry = ToolRegistry([make_run_python(tmp_path)])
    result = registry.execute("run_python", {"code": "print('before failure')\n1 / 0"})
    assert result.is_error
    assert result.content.startswith("ERROR:")
    assert "退出码: 1" in result.content
    assert "before failure" in result.content and "ZeroDivisionError" in result.content


def test_timeout_terminates_parent_and_child(tmp_path):
    # 若进程树未被终止，后代将创建 leaked.txt；不只检查超时提示。
    code = """import subprocess, sys, time, os
subprocess.Popen([sys.executable, '-c', "import time; from pathlib import Path; time.sleep(1.5); Path('leaked.txt').write_text('leak')"], creationflags=(subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
print('started', flush=True)
time.sleep(20)
"""
    start = time.monotonic()
    result = make_run_python(tmp_path, timeout=0.7)(code)
    assert result.is_error and "超时" in result.content
    assert time.monotonic() - start < 6
    time.sleep(1.5)
    assert not (tmp_path / "leaked.txt").exists()


def test_output_is_bounded_both_streams(tmp_path):
    result = make_run_python(tmp_path, max_output_chars=100)("import sys\nprint('x'*1000000)\nprint('y'*1000000, file=sys.stderr)")
    assert not result.is_error
    assert result.content.count("[输出已截断]") == 2
    assert len(result.content) < 600


def test_blank_code_rejected(tmp_path):
    result = ToolRegistry([make_run_python(tmp_path)]).execute("run_python", {"code": "  "})
    assert result.is_error and not list(tmp_path.iterdir())


def test_agent_recovers_computes_plots_and_links_report(tmp_path):
    code = """import pandas as pd
import matplotlib.pyplot as plt
df = pd.DataFrame({'model':['A','B'], 'input_price':[3,5], 'output_price':[6,10]})
df['cost'] = df.input_price * 100000 / 1000000 + df.output_price * 10000 / 1000000
print(df.to_string(index=False))
df.to_csv('costs.csv', index=False)
plt.bar(df.model, df.cost)
plt.ylabel('CNY / task')
plt.savefig('costs.png')
plt.close()
"""
    report = "# 示例计算（测试数据）\n\n|模型|元/任务|\n|---|---|\n|A|0.36|\n|B|0.60|\n\n![成本](costs.png)"
    llm = ScriptedLLM([
        completion(tool_calls=[("bad", "run_python", {"code": "1/0"})]),
        completion(tool_calls=[("fixed", "run_python", {"code": code})]),
        completion(tool_calls=[("report", "write_report", {"content": report})]),
        completion("计算与报告已完成"),
    ])
    registry = ToolRegistry([make_run_python(tmp_path), make_write_report(tmp_path)])
    result = Agent(llm, registry, max_steps=8).run("根据示例价格计算成本并绘图")
    assert result.stop_reason == "final_answer"
    assert "ZeroDivisionError" in llm.requests[1]["messages"][-1]["content"]
    tool_results = [m for m in llm.requests[2]["messages"] if m["role"] == "tool"]
    assert "costs.png" in tool_results[-1]["content"]
    assert not tool_results[-1]["content"].startswith("ERROR:")
    assert (tmp_path / "costs.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert "0.36" in (tmp_path / "costs.csv").read_text(encoding="utf-8")
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == report
