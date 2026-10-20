"""阶段14离线桌面验收夹具；只在测试子进程中替换模型，不修改生产配置。"""
import json
import time
from pathlib import Path

from agent.config import Settings, load_llm_config
from fakes import completion


def settings(root):
    return Settings(_env_file=None, deepseek_api_key='offline-stage14-test-key',
                    memory_enabled=False, memory_db_path=Path(root)/'memory.sqlite3',
                    report_review_enabled=True, report_review_max_calls=1,
                    agent_max_steps=12, agent_token_budget=80000)


CODE = """from pathlib import Path
import matplotlib.pyplot as plt
Path('data.csv').write_text('item,value\\nA,2\\nB,5\\n',encoding='utf-8')
plt.bar(['A','B'],[2,5]);plt.savefig('chart.png');plt.close()
print('offline-fixture: A=2, B=5')
"""
REPORT = '''# 离线界面验收

## 摘要
本报告仅演示界面流程。

## 方法与范围
仅使用虚构输入，未请求真实模型服务。

## 正文
数据见 [data.csv](data.csv)。

工具记录的退出码为零。

![模拟图表](chart.png)

## 结论
业务状态未知。

## 局限性
界面与工程检查不认证业务事实。

## 参考资料
本任务的虚构资料。
'''


class OfflineModel:
    def __init__(self, config, question='', root=None):
        self.config = config
        self.question = question
        self.root = Path(root) if root else None
        self.request_attempts = 0

    def chat(self, messages, tools=None, **kwargs):
        self.request_attempts += 1
        if '服务失败' in self.question:
            import httpx
            import openai
            raise openai.APIConnectionError(request=httpx.Request('POST','https://offline.invalid'))
        if tools is None:
            if '独立报告审稿人' in messages[0]['content']:
                return completion('{"issues":[]}')
            return completion('{}', prompt_tokens=1500 if '预算不足' in self.question else 10)
        if '等待停止' in self.question:
            # 停在模型响应提交之前；收到停止后仍返回真实响应供原运行机制记账。
            if self.root: (self.root/'.desktop-test-waiting').touch()
            deadline=time.monotonic()+40
            while time.monotonic()<deadline and ('强制中断' in self.question or not (self.root/'.desktop-stop').exists()):
                time.sleep(.05)
            return completion('离线等待已结束。')
        results = [m for m in messages if m.get('role')=='tool']
        names = {m.get('name') for m in results}
        # 工具返回的协议消息可能不带name，按其原始tool_call_id匹配。
        calls = {c['id']:c['function']['name'] for m in messages for c in m.get('tool_calls',[])}
        names.update(calls.get(m.get('tool_call_id')) for m in results)
        if not results:
            return completion(tool_calls=[('fixture-code','run_python',{'code':CODE})])
        if 'write_report' not in names:
            return completion(tool_calls=[('fixture-report','write_report',{'content':REPORT,
                'claims':claims()})])
        if '审稿限制' in self.question:
            written=[m for m in results if calls.get(m.get('tool_call_id'))=='write_report']
            if len(written)==1:
                revised=REPORT.replace('仅演示界面流程。','仅演示界面流程与修订。')
                return completion(tool_calls=[('fixture-revision','write_report',{'content':revised,
                    'claims':claims()})])
        return completion('离线演示结束。查看 [报告](report.md)、[数据](data.csv) 和图表。')


def claims():
    return [{'quote':'业务状态未知。','kind':'unknown'},
            {'quote':'工具记录的退出码为零。','kind':'observed',
             'supports':[{'source_id':'execution-1','excerpt':'退出码: 0'}]}]


def seed_recovery(manager, title, *, corrupt=False):
    from unittest.mock import patch
    from main import research
    from test_checkpoint import Crash, CrashStore
    sid=manager.store.create_session(title)['id']
    options={'provider':'deepseek','model':'deepseek-flash','max_steps':12,'budget':80000,'memory':False}
    row=manager.submit(sid,'不联网，仅用于隔离恢复流程的虚构任务。',options)
    root=Path(row['task_dir'])
    model=OfflineModel(load_llm_config(settings=manager.settings),row['question'],root)
    try:
        with patch('main.CheckpointStore',lambda path:CrashStore(path,lambda d:
                   d['phase']=='loop' and d['state']['_steps']==1 and not d.get('pending'))):
            research(model,manager.settings,row['question'],12,80000,task_dir=root)
    except Crash: pass
    manager.store.set_run(row['id'],'interrupted',{'stop_reason':'interrupted',
                                               'error':'离线测试夹具，未调用真实服务。'})
    if corrupt: (root/'.agent-checkpoint.sqlite3').write_bytes(b'corrupt fixture')
    return row
