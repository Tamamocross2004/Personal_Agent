import time
from fastapi.testclient import TestClient
from agent.config import Settings
from desktop_app.manager import Manager
from desktop_app.server import create_app
from folder_assistant.index import FolderIndex
from knowledge.store import KnowledgeStore
from test_folder_index import Embedding,Vectors
from test_folder_answers import Chat


def test_project_http_lifecycle_validation_and_readonly_exports(tmp_path,monkeypatch):
    docs=tmp_path/'docs';docs.mkdir();(docs/'a.txt').write_text('alpha original','utf-8')
    embedding=Embedding();vectors=Vectors();chat=Chat()
    monkeypatch.setattr(FolderIndex,'store',lambda self,fid:KnowledgeStore(self.settings.model_copy(update={'knowledge_path':self.folders.root/'indexes'/fid}),vectors=vectors,embedding_client=embedding))
    monkeypatch.setattr('agent.llm.LLMClient',lambda config:chat)
    manager=Manager(tmp_path/'state',start=False,settings=Settings(_env_file=None,deepseek_api_key='fake',siliconflow_api_key='fake',embedding_dimensions=2))
    h={'Authorization':'Bearer token'}
    with TestClient(create_app(manager,'token',folder_grant_token='native')) as c:
        fid=c.post('/api/folders',headers={**h,'X-Folder-Grant':'native'},json={'path':str(docs)}).json()['id'];base='/api/folders/'+fid
        scan=c.post(base+'/scan',headers=h).json();sync=c.post(base+'/sync',headers=h,json={'scan_id':scan['id']}).json()
        def wait_job(jid):
            for _ in range(400):
                value=c.get('/api/folder-jobs/'+jid,headers=h).json()
                if value['status'] not in ('queued','running'):return value
                time.sleep(.025)
            raise AssertionError('job timeout')
        assert wait_job(sync['id'])['status']=='completed'
        config={'title':'台账','objective':'整理原文','fields':['摘录']}
        assert c.post(base+'/projects',json=config).status_code==401
        assert c.post(base+'/projects',headers=h,json={**config,'fields':[]}).status_code==422
        p=c.post(base+'/projects',headers=h,json=config).json();url='/api/folder-projects/'+p['id']
        req={'revision':1,'expected_head':None,'action':'update','max_batches':2,'batch_mode':'fixed'}
        preview=c.post(url+'/preview',headers=h,json=req).json();calls=len(chat.calls)
        assert not preview['cloud_calls'] and len(chat.calls)==calls
        assert c.post(url+'/run',headers=h,json={**req,'plan_hash':'0'*64}).status_code==400
        started=c.post(url+'/run',headers=h,json={**req,'plan_hash':preview['plan_hash']}).json()
        if started['pending_job']:wait_job(started['pending_job'])
        p=c.get(url,headers=h).json();assert p['head'] and not p['pending']
        version_url='/api/folder-project-versions/'+p['head']
        v=c.get(version_url,headers=h).json();assert v['status']=='structured'
        follow=c.post(url+'/followups',headers=h,json={'version_id':p['head'],'query':'alpha'}).json()
        assert wait_job(follow['id'])['status']=='completed'
        assert c.get(url,headers=h).json()['head']==p['head']
        assert c.post(url+'/followups',headers=h,json={'version_id':'a'*32,'query':'x'}).status_code==404
        assert c.get(version_url+'?limit=21',headers=h).status_code==400
        exported=c.post(version_url+'/export',headers=h).json();assert len(exported['files'])==6
        assert c.get(version_url+'/export/comparison.xlsx',headers=h).content[:2]==b'PK'
        assert c.get(version_url+'/export/no.exe',headers=h).status_code==404
        c.delete(base,headers=h);calls=(len(chat.calls),len(embedding.calls))
        assert c.get(url,headers=h).status_code==200 and c.get(version_url,headers=h).status_code==200
        assert c.post(version_url+'/export',headers=h).json()['cached']
        assert c.post(url+'/preview',headers=h,json={**req,'expected_head':p['head']}).status_code==400
        assert c.patch(url,headers=h,json={**config,'revision':1}).status_code==400
        assert (len(chat.calls),len(embedding.calls))==calls
