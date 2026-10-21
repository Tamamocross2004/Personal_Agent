import json
from pathlib import Path
import os
import pytest
from folder_assistant.store import FolderStore

def setup(tmp_path):
    source=tmp_path/'资料 空间';source.mkdir()
    (source/'甲').mkdir();(source/'乙').mkdir()
    (source/'甲'/'同名.txt').write_text('第一份',encoding='utf-8')
    (source/'乙'/'同名.txt').write_text('第二份',encoding='utf-8')
    (source/'.env').write_text('PRIVATE=hidden')
    (source/'legacy.doc').write_bytes(b'old')
    store=FolderStore(tmp_path/'state')
    grant=store.grant(source)
    return source,store,grant['id']

def test_scan_identity_exclusion_and_readonly(tmp_path):
    source,store,key=setup(tmp_path)
    before={p:p.read_bytes() for p in source.rglob('*') if p.is_file()}
    result=store.scan(key);rows=result['entries']
    files=[r for r in rows if r['status']=='ready' and r['kind']=='file']
    assert len(files)==2 and len({r['id'] for r in files})==2
    assert next(r for r in rows if r['relative_path']=='.env')['status']=='excluded'
    assert next(r for r in rows if r['relative_path']=='legacy.doc')['status']=='unsupported'
    assert store.read(key,result['id'],files[0]['id'])[1] in ('第一份'.encode(),'第二份'.encode())
    with pytest.raises(ValueError):store.read(key,result['id'],'../.env')
    assert before=={p:p.read_bytes() for p in before}
    store.revoke(key)
    for operation in (lambda:store.scan(key),lambda:store.manifest(key),lambda:store.read(key,result['id'],files[0]['id'])):
        with pytest.raises(ValueError,match='授权'):operation()

def test_changed_file_and_replaced_root_are_rejected(tmp_path):
    source,store,key=setup(tmp_path);scan=store.scan(key)
    row=next(r for r in scan['entries'] if r['relative_path']=='甲/同名.txt')
    (source/row['relative_path']).write_text('changed')
    with pytest.raises(ValueError,match='变化'):store.read(key,scan['id'],row['id'])
    source.rename(tmp_path/'moved');source.mkdir()
    with pytest.raises(ValueError,match='替换'):store.scan(key)

def test_link_escape_is_not_read_and_hardlinks_excluded(tmp_path):
    source,store,key=setup(tmp_path);outside=tmp_path/'outside.txt';outside.write_text('outside')
    os.link(outside,source/'alias.txt')
    try:(source/'link.txt').symlink_to(outside)
    except OSError:pass
    rows=store.scan(key)['entries']
    assert all(r['status']=='excluded' for r in rows if r['relative_path'] in ('alias.txt','link.txt'))

def test_capacity_is_explicit_and_old_manifests_persist(tmp_path,monkeypatch):
    import folder_assistant.store as module
    source,store,key=setup(tmp_path);first=store.scan(key)
    monkeypatch.setattr(module,'MAX_ENTRIES',2)
    limited=store.scan(key)
    assert not limited['complete'] and limited['limits'] and len(limited['entries'])==2
    assert store.manifest(key,first['id'])==first

def test_desktop_grant_requires_native_capability_and_revoke(tmp_path):
    from fastapi.testclient import TestClient
    from agent.config import Settings
    from desktop_app.manager import Manager
    from desktop_app.server import create_app
    source=tmp_path/'docs';source.mkdir();(source/'a.txt').write_text('text')
    manager=Manager(tmp_path/'workspace',start=False,settings=Settings(_env_file=None))
    with TestClient(create_app(manager,'auth',folder_grant_token='picker-only')) as client:
        assert client.get('/api/folders').status_code==401
        client.headers['Authorization']='Bearer auth'
        assert client.post('/api/folders',json={'path':str(source)}).status_code==403
        row=client.post('/api/folders',json={'path':str(source)},headers={'X-Folder-Grant':'picker-only'}).json()
        assert row['id'] and client.post('/api/folders/'+row['id']+'/scan').json()['complete']
        assert client.delete('/api/folders/'+row['id']).status_code==200
        assert client.get('/api/folders/'+row['id']+'/scan').status_code==400
