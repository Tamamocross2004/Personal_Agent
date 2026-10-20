"""仅供隔离桌面smoke：无调度线程、无真实模型请求。"""
import json
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import uvicorn
from agent.checkpoint import CheckpointStore
from agent.config import Settings,load_llm_config
from desktop_app.manager import Manager
from desktop_app.server import create_app
from fakes import ScriptedLLM,completion
from main import research
from test_checkpoint import Crash,CrashStore


root=Path(sys.argv[1])
settings=Settings(memory_enabled=False,report_review_enabled=False,deepseek_api_key='isolated-smoke-not-a-real-key')
manager=Manager(root,start=False,settings=settings)
sid=manager.store.create_session('阶段13恢复检查（模拟任务）')['id']
options={'provider':'deepseek','model':'deepseek-flash','max_steps':15,'budget':80000,'memory':False}
row=manager.submit(sid,'仅用于界面检查的模拟中断任务',options)
model=ScriptedLLM([completion('{}'),completion('模拟完成，无真实服务调用')])
model.config=load_llm_config(provider='deepseek',model='deepseek-flash',settings=settings)
try:
    with patch('main.CheckpointStore',lambda path:CrashStore(path,lambda d:d['phase']=='response')):
        research(model,settings,row['question'],15,80000,task_dir=Path(row['task_dir']))
except Crash: pass
manager.store.set_run(row['id'],'interrupted',{'stop_reason':'interrupted','steps':1,'equivalent_tokens':40,
                                           'error':'隔离测试任务，未发出真实模型请求。'})
assert CheckpointStore(Path(row['task_dir'])).status()['resumable']
sock=socket.socket(); sock.bind(('127.0.0.1',0)); sock.listen(128)
print(json.dumps({'ready':True,'url':f'http://127.0.0.1:{sock.getsockname()[1]}','rid':row['id']}),flush=True)
try:
    uvicorn.Server(uvicorn.Config(create_app(manager,os.environ['STAGE13_SMOKE_TOKEN'],Path.cwd()/'desktop/dist'),
                                 log_level='error',access_log=False)).run(sockets=[sock])
finally: manager.close();sock.close()
