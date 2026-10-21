from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import secrets
import socket
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from agent.config import PROJECT_ROOT,ProviderName
from desktop_app.manager import Manager,FINAL
from desktop_app.usage import PriceProfile, flash_profile


class SessionPatch(BaseModel):
    title:str|None=Field(default=None,min_length=1,max_length=120)
    archived:bool|None=None


class RunInput(BaseModel):
    question:str=Field(min_length=1,max_length=20000)
    provider:ProviderName='deepseek'
    model:str=Field(default='deepseek-flash',min_length=1,max_length=160)
    max_steps:int=Field(default=30,ge=3,le=200)
    budget:int=Field(default=300000,ge=1000,le=2000000)
    memory:bool=True
    knowledge:bool=False
    rerank:bool|None=None


class KnowledgeQuery(BaseModel):
    query:str=Field(min_length=1,max_length=1000)
    mode:Literal['keyword','vector','hybrid']='keyword'
    k:int=Field(default=4,ge=1,le=8)
    rerank:bool|None=None


class FolderGrant(BaseModel):
    path:str=Field(min_length=1,max_length=2048)


def create_app(manager:Manager,token:str,ui_dir:Path|None=None,*,folder_grant_token:str|None=None):
    from folder_assistant.store import FolderStore
    folders=FolderStore(manager.root/'folders',excluded_roots=(manager.root,))
    @asynccontextmanager
    async def lifespan(app):
        yield
        manager.close()
    app=FastAPI(title='Personal Agent Desktop',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    private_values=[str(getattr(manager.settings,k)) for k in type(manager.settings).model_fields
                    if k.endswith('_api_key') and getattr(manager.settings,k)]

    def redact(value):
        if isinstance(value,str):
            for key in private_values:value=value.replace(key,'[已隐藏密钥]')
            return value
        if isinstance(value,list):return [redact(v) for v in value]
        if isinstance(value,dict):return {k:redact(v) for k,v in value.items()}
        return value

    @app.middleware('http')
    async def access(request:Request,call_next):
        if request.headers.get('host','').split(':',1)[0] not in {'127.0.0.1','localhost','testserver'}:
            return JSONResponse({'detail':'仅允许本机访问'},status_code=403)
        if request.url.path.startswith('/api/'):
            if not secrets.compare_digest(request.headers.get('authorization',''),'Bearer '+token):
                return JSONResponse({'detail':'本地会话凭证无效'},status_code=401)
            origin=request.headers.get('origin')
            if origin and origin!=str(request.base_url).rstrip('/'):
                return JSONResponse({'detail':'不允许跨来源调用'},status_code=403)
        response=await call_next(request)
        if request.url.path.startswith('/api/'):response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; connect-src 'self'; script-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(KeyError)
    async def missing(request,exc):return JSONResponse({'detail':str(exc)},status_code=404)
    @app.exception_handler(ValueError)
    async def invalid(request,exc):return JSONResponse({'detail':redact(str(exc))},status_code=400)

    @app.get('/api/config')
    def config():return manager.config()
    @app.get('/api/folders')
    def folder_list():return {'folders':folders.folders(),'policy':folders.policy()}
    @app.post('/api/folders')
    def folder_grant(body:FolderGrant,request:Request):
        if not folder_grant_token or not secrets.compare_digest(request.headers.get('x-folder-grant',''),folder_grant_token):
            raise HTTPException(403,'请通过桌面原生目录选择器添加目录，或使用本地CLI')
        return folders.grant(body.path)
    @app.delete('/api/folders/{fid}')
    def folder_revoke(fid:str):return folders.revoke(fid)
    @app.post('/api/folders/{fid}/scan')
    def folder_scan(fid:str):return folders.scan(fid)
    @app.get('/api/folders/{fid}/scan')
    def folder_manifest(fid:str):return folders.manifest(fid)
    @app.get('/api/knowledge')
    def knowledge():return redact(manager.knowledge_status())
    @app.post('/api/knowledge/import')
    async def knowledge_import(request:Request,filename:str,mode:Literal['append','replace']='append'):
        from knowledge.documents import MAX_BYTES,parse
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>MAX_BYTES: raise ValueError('文档超过20MB')
        await run_in_threadpool(parse,bytes(data),filename)
        return manager.knowledge_build([(filename,bytes(data))],mode=mode)
    @app.post('/api/knowledge/rebuild')
    def knowledge_rebuild():return manager.knowledge_build(mode='rebuild')
    @app.delete('/api/knowledge/documents/{doc_id}')
    def knowledge_delete(doc_id:str):return manager.knowledge_build(remove=[doc_id])
    @app.get('/api/knowledge/documents/{doc_id}')
    def knowledge_preview(doc_id:str,page:int=1):return redact(manager.knowledge_store().preview(doc_id,page))
    @app.post('/api/knowledge/search')
    def knowledge_search(body:KnowledgeQuery):return redact(manager.knowledge_store().search(**body.model_dump()))
    @app.get('/api/pricing')
    def pricing():return redact({**manager.pricing.read(),'presets':[flash_profile(),flash_profile(False)]})
    @app.put('/api/pricing')
    def update_pricing(body:PriceProfile):return redact(manager.pricing.save(body.provider,body.model,body))
    @app.delete('/api/pricing')
    def delete_pricing(provider:ProviderName,model:str):return redact(manager.pricing.save(provider,model))
    @app.get('/api/sessions')
    def sessions(archived:bool=False):return redact(manager.store.sessions(archived))
    @app.post('/api/sessions')
    def new_session():return manager.store.create_session()
    @app.patch('/api/sessions/{sid}')
    def update_session(sid:str,body:SessionPatch):return redact(manager.store.update_session(sid,**body.model_dump()))
    @app.get('/api/sessions/{sid}/runs')
    def runs(sid:str):
        manager.store.session(sid)
        return redact(manager.store.runs(sid))
    @app.post('/api/sessions/{sid}/runs')
    def new_run(sid:str,body:RunInput):
        values=body.model_dump();question=values.pop('question').strip()
        if not question:raise ValueError('请输入研究问题')
        return redact(manager.submit(sid,question,values))
    @app.get('/api/runs/{rid}')
    def run(rid:str):return redact(manager.detail(rid))
    @app.post('/api/runs/{rid}/stop')
    def stop(rid:str,force:bool=False):return redact(manager.stop(rid,force))
    @app.post('/api/runs/{rid}/resume')
    def resume(rid:str):return redact(manager.resume(rid))
    @app.post('/api/import')
    def import_history():return {'imported':manager.import_history()}
    @app.get('/api/runs/{rid}/files/{name:path}')
    def file(rid:str,name:str):
        path=manager.artifact(rid,name)
        if path.suffix.lower() in {'.md','.json','.jsonl','.csv','.txt','.py','.svg'}:
            if path.stat().st_size>2_000_000:raise ValueError('文本超过2MB，请打开任务目录查看')
            from fastapi.responses import Response
            return Response(redact(path.read_text(encoding='utf-8',errors='replace')),media_type='text/plain; charset=utf-8')
        return FileResponse(path,media_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
    @app.get('/api/runs/{rid}/events')
    async def events(rid:str,cursor:int=0):
        path=manager.safe_task(manager.store.run(rid))/'trace.jsonl'
        if cursor<0:raise ValueError('游标无效')
        async def stream():
            position=0;number=0;heartbeat=0
            while True:
                terminal=manager.store.run(rid)['state'] in FINAL
                if path.is_file() and not path.is_symlink():
                    with path.open('rb') as f:
                        f.seek(position)
                        for _ in range(100):
                            line=f.readline(1_000_000)
                            if not line:break
                            if not line.endswith(b'\n'):
                                # 运行时等待完整行；结束后的残行或超长事件必须能排空。
                                if len(line)>=1_000_000:
                                    while line and not line.endswith(b'\n'):line=f.readline(1_000_000)
                                    position=f.tell();number+=1
                                    continue
                                if terminal:position=f.tell()
                                break
                            position=f.tell();number+=1
                            if number<=cursor:continue
                            try:value=json.loads(line)
                            except (ValueError,UnicodeDecodeError):continue
                            yield f'id: {number}\nevent: trace\ndata: {json.dumps(redact(value),ensure_ascii=False)}\n\n'
                row=manager.store.run(rid)
                if row['state'] in FINAL:
                    # 排空本轮最后的日志后再发结束状态。
                    if path.is_file() and not path.is_symlink() and path.stat().st_size>position:continue
                    yield f'event: state\ndata: {json.dumps(redact(row),ensure_ascii=False)}\n\n'
                    return
                heartbeat+=1
                if heartbeat%20==0:yield ': heartbeat\n\n'
                await asyncio.sleep(.5)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-store'})

    if ui_dir and (ui_dir/'index.html').is_file():
        app.mount('/',StaticFiles(directory=ui_dir,html=True),name='desktop')
    return app


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=0);parser.add_argument('--ui-dir',type=Path)
    parser.add_argument('--parent',type=int)
    args=parser.parse_args()
    if args.parent:
        from desktop_app.lifecycle import watch_parent
        threading.Thread(target=watch_parent,args=(args.parent,),daemon=True).start()
    token=os.environ.pop('PERSONAL_AGENT_DESKTOP_TOKEN',None) or secrets.token_urlsafe(32)
    folder_grant_token=os.environ.pop('PERSONAL_AGENT_FOLDER_GRANT_TOKEN',None)
    manager=Manager(PROJECT_ROOT/'workspace')
    sock=socket.socket();sock.bind(('127.0.0.1',args.port));sock.listen(128)
    print(json.dumps({'event':'ready','port':sock.getsockname()[1]}),flush=True)
    app=create_app(manager,token,args.ui_dir,folder_grant_token=folder_grant_token)
    try:uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False)).run(sockets=[sock])
    finally:manager.close();sock.close()


if __name__=='__main__':main()
