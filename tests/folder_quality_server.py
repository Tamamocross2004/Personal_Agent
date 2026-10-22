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
class QualityChat(Chat):
    def chat(self,messages,**kwargs):
        response=super().chat(messages,**kwargs)
        data=json.loads(response.choices[0].message.content)
        text=json.loads(messages[1]['content'])['evidence'][0]['text']
        data['findings'].extend([
            {'statement':'示例推断须核对','kind':'inference','citations':['C1'],
             'support':[{'citation_id':'C1','quote':text}],
             'assumptions':['展示用假设，未被证实'],'limitations':['测试替身不认证语义']},
            {'statement':'示例差异的可比性尚未建立','kind':'difference','citations':['C1'],
             'support':[{'citation_id':'C1','quote':text[:2]},{'citation_id':'C1','quote':text[-2:]}],
             'relation':{'field':'展示字段','common_scope':None,'comparability':'not_established','reason':'缺少共同条件，仅验证展示'}}])
        response.choices[0].message.content=json.dumps(data,ensure_ascii=False)
        return response
quality_chat=QualityChat()

_original_jobs_init=FolderJobs.__init__
def jobs_init(self,index,**kw): _original_jobs_init(self,index,llm_factory=lambda c:quality_chat)
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
