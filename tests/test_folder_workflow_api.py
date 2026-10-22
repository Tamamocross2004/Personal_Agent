import time
from fastapi.testclient import TestClient
from agent.config import Settings
from desktop_app.manager import Manager
from desktop_app.server import create_app
from folder_assistant.index import FolderIndex
from folder_assistant.jobs import FolderJobs
from knowledge.store import KnowledgeStore
from test_folder_index import Embedding,Vectors
from test_folder_answers import Chat


def test_native_grant_sync_answer_citation_export_revoke_and_read_only_history(tmp_path,monkeypatch):
    docs=tmp_path/'docs';docs.mkdir();(docs/'a.txt').write_bytes(b'alpha original')
    embedding=Embedding();vectors=Vectors();chat=Chat()
    def store(self,fid):
        settings=self.settings.model_copy(update={'knowledge_path':self.folders.root/'indexes'/fid})
        return KnowledgeStore(settings,vectors=vectors,embedding_client=embedding)
    monkeypatch.setattr(FolderIndex,'store',store)
    monkeypatch.setattr('agent.llm.LLMClient',lambda config:chat)
    manager=Manager(tmp_path/'state',start=False,settings=Settings(_env_file=None,deepseek_api_key='fake',siliconflow_api_key='fake',embedding_dimensions=2))
    headers={'Authorization':'Bearer test'}
    with TestClient(create_app(manager,'test',folder_grant_token='native-only')) as client:
        assert client.get('/api/folders').status_code==401
        assert client.post('/api/folders',headers=headers,json={'path':str(docs)}).status_code==403
        fid=client.post('/api/folders',headers={**headers,'X-Folder-Grant':'native-only'},json={'path':str(docs)}).json()['id']
        base='/api/folders/'+fid
        scan=client.post(base+'/scan',headers=headers).json()
        def wait(jid):
            for _ in range(400):
                value=client.get('/api/folder-jobs/'+jid,headers=headers).json()
                if value['status'] not in ('running','queued'):return value
                time.sleep(.025)
            raise AssertionError('operation did not finish')
        built=wait(client.post(base+'/sync',headers=headers,json={'scan_id':scan['id']}).json()['id'])
        assert built['status']=='completed' and built['usage_events'][0]['input_tokens'] is not None
        for controls in ({'output_tokens':127},{'output_tokens':4097},{'batch_size':7},{'expected_fields':0},{'batch_mode':'unknown'}):
            assert client.post(base+'/questions',headers=headers,json={'query':'alpha',**controls}).status_code==422
        assert not chat.calls
        job=wait(client.post(base+'/questions',headers=headers,json={'query':'alpha'}).json()['id'])
        assert job['status']=='completed'
        url='/api/folder-jobs/'+job['id']
        citation=client.get(url+'/citations/C1',headers=headers)
        assert citation.status_code==200 and citation.json()['delivered']['text']=='alpha original'
        assert client.get(url+'/citations/C999',headers=headers).status_code==404
        reading=client.post(base+'/reading',headers=headers,json={'generation':job['inputs']['generation']})
        assert reading.status_code==200 and reading.json()['cloud_calls'] is False
        assert client.post(base+'/reading',headers=headers,json={'generation':job['inputs']['generation'],'file_ids':['foreign']}).status_code==400
        assert client.get(url+'/citations/C1/neighbors',headers=headers).json()['cloud_calls'] is False
        assert client.get(url+'/citations/C999/neighbors',headers=headers).status_code==404
        exported=client.post(url+'/export',headers=headers)
        assert exported.status_code==200 and len(exported.json()['files'])==6
        assert client.get(url+'/export/comparison.xlsx',headers=headers).content[:2]==b'PK'
        assert client.get(url+'/export/unknown.exe',headers=headers).status_code==404
        client.delete(base,headers=headers)
        calls=(len(embedding.calls),len(chat.calls))
        assert client.get(base+'/index',headers=headers).status_code==400
        assert client.post(base+'/questions',headers=headers,json={'query':'alpha'}).status_code==400
        assert client.get(base+'/files/'+scan['entries'][0]['id'],headers=headers,params={'scan_id':scan['id']}).status_code==400
        assert client.get(base+'/jobs',headers=headers).status_code==200
        assert client.get(url+'/citations/C1',headers=headers).status_code==200
        assert client.get(url+'/citations/C1/neighbors',headers=headers).status_code==200
        assert client.post(base+'/reading',headers=headers,json={'generation':job['inputs']['generation']}).status_code==400
        assert client.post(url+'/export',headers=headers).json()['cached']
        assert (len(embedding.calls),len(chat.calls))==calls
        assert (docs/'a.txt').read_bytes()==b'alpha original'
