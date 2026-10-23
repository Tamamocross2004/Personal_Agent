"""Isolated real desktop API; only paid providers/vector ranking are deterministic fakes."""
import json,os,socket,sys,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from agent.config import Settings
from desktop_app.manager import Manager
from desktop_app.server import create_app
from desktop_app.lifecycle import watch_parent
from folder_assistant.index import FolderIndex
from knowledge.store import KnowledgeStore
from test_folder_index import Embedding,Vectors
from test_folder_answers import Chat
from folder_assistant.jobs import FolderJobs
from agent.llm import LLMClient
_original_jobs_init=FolderJobs.__init__
class FieldPlanChat(Chat):
    def chat(self,messages,**kwargs):
        if messages[0]['content'].startswith('只规划用户问题要求整理的字段'):
            from types import SimpleNamespace as NS
            query=json.loads(messages[1]['content'])['question']
            fields=[{'name':'摘录','mode':'verbatim','instruction_quote':'摘录'}] if '摘录' in query else []
            return NS(model='fake',usage=NS(prompt_tokens=80,completion_tokens=40),
                      choices=[NS(message=NS(content=json.dumps({'fields':fields})),finish_reason='stop')])
        return super().chat(messages,**kwargs)


class BatchLimitChat(FieldPlanChat):
    """Test-only truncation above three main segments, independent of questions."""
    def chat(self,messages,**kwargs):
        result=super().chat(messages,**kwargs)
        scope=json.loads(messages[1]['content']).get('reading_scope')
        if scope and scope['end_segment']-scope['start_segment']>3:
            result.choices[0].finish_reason='length'
        return result

def jobs_init(self,index,**kw):
    fake=BatchLimitChat if '--batching' in sys.argv else FieldPlanChat
    _original_jobs_init(self,index,llm_factory=lambda c:fake())
FolderJobs.__init__=jobs_init

embedding=Embedding();vectors=Vectors()
def store(self,fid):
    if not any(f['id']==fid for f in self.folders.folders()):raise KeyError('目录不存在')
    settings=self.settings.model_copy(update={'knowledge_path':self.folders.root/'indexes'/fid})
    return KnowledgeStore(settings,vectors=vectors,embedding_client=embedding)
FolderIndex.store=store
fixture=Path(sys.argv[1]);parent=int(sys.argv[sys.argv.index('--parent')+1])
threading.Thread(target=watch_parent,args=(parent,),daemon=True).start()
token=os.environ.pop('PERSONAL_AGENT_DESKTOP_TOKEN');grant=os.environ.pop('PERSONAL_AGENT_FOLDER_GRANT_TOKEN')
manager=Manager(fixture/'state',start=False,settings=Settings(_env_file=None,memory_enabled=False,siliconflow_api_key='fake',deepseek_api_key='fake',embedding_dimensions=2))
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
print(json.dumps({'event':'ready','port':sock.getsockname()[1]}),flush=True)
try:uvicorn.Server(uvicorn.Config(create_app(manager,token,Path(__file__).resolve().parents[1]/'desktop'/'dist',folder_grant_token=grant),log_level='warning',access_log=False)).run(sockets=[sock])
finally:manager.close();sock.close()
