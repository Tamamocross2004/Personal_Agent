"""隔离workspace+真实队列/子进程/API；禁止真实API调用的阶段14测试服务。"""
import json
import os
from pathlib import Path
import socket
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from desktop_app.manager import Manager
from desktop_app.server import create_app
from desktop_app.lifecycle import watch_parent
from desktop_acceptance_fixture import seed_recovery,settings


class IsolatedManager(Manager):
    def worker_command(self,path):
        return [sys.executable,str(Path(__file__).with_name('desktop_acceptance_worker.py')),
                str(path),'--parent',str(os.getpid())]


root=Path(sys.argv[1])
if '--parent' in sys.argv:
    threading.Thread(target=watch_parent,args=(int(sys.argv[sys.argv.index('--parent')+1]),),daemon=True).start()
manager=IsolatedManager(root,start=False,settings=settings(root))
# 仅隔离验收进程注入假嵌入；向量落盘与桌面导入/检索接口仍使用生产实现。
from knowledge.store import KnowledgeStore
class OfflineEmbedding:
    def __init__(self): self.embeddings=self
    def create(self,**kwargs):
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=11*len(kwargs['input'])),data=[
            SimpleNamespace(index=i,embedding=[1.]+[0.]*1023) for i,_ in enumerate(kwargs['input'])])
manager.knowledge_settings=manager.knowledge_settings.model_copy(update={'siliconflow_api_key':'offline-rag-test-key'})
manager.knowledge_store=lambda:KnowledgeStore(manager.knowledge_settings,embedding_client=OfflineEmbedding())
if not manager.store.runs():
    seed_recovery(manager,'可恢复（离线夹具）')
    seed_recovery(manager,'损坏检查点（离线夹具）',corrupt=True)
    sid=manager.store.create_session('长历史（离线夹具）')['id']
    for i in range(35):
        path=root/f'history-{i}';path.mkdir()
        (path/'trace.jsonl').write_text(json.dumps({'kind':'notice','text':'历史事件回放检查'},ensure_ascii=False)+'\n',encoding='utf-8')
        row=manager.store.add_run(sid,f'虚构历史第{i}轮',path,{},state='imported',
            result={'answer':'历史内容。'*100,'stop_reason':'final_answer'})
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
thread=threading.Thread(target=manager._watch,daemon=True);manager.thread=thread;thread.start()
ready={'event':'ready','port':sock.getsockname()[1],'pid':os.getpid(),
       'parent':int(sys.argv[sys.argv.index('--parent')+1]) if '--parent' in sys.argv else None}
(root/'server-ready.json').write_text(json.dumps(ready),encoding='utf-8')
print(json.dumps(ready),flush=True)
try:
    uvicorn.Server(uvicorn.Config(create_app(manager,os.environ['PERSONAL_AGENT_DESKTOP_TOKEN'],Path.cwd()/'desktop/dist'),
                                 log_level='error',access_log=False)).run(sockets=[sock])
finally:manager.close();sock.close()
