"""Per-folder, immutable RAG generations. Source access always goes through FolderStore."""
from __future__ import annotations

import json
from pathlib import Path
import re

from knowledge.documents import sha
from knowledge.locking import lease
from knowledge.store import KnowledgeStore


def index_blocks(blocks):
    """Keep actual table rows together; do not infer headers, types, or business meaning."""
    groups=[]
    for block in blocks:
        location=block['location'];kind=location['kind'];key=None
        if kind=='spreadsheet_cell':
            key=('sheet',location['sheet'],int(re.search(r'\d+$',location['cell'])[0]))
        elif kind=='table_cell':key=('table',location['table'],location['row'])
        if key is not None and groups and groups[-1][0]==key:groups[-1][1].append(block)
        else:groups.append((key,[block]))
    result=[]
    for key,rows in groups:
        if key is None:result.append(rows[0]);continue
        location=({'kind':'spreadsheet_row','sheet':key[1],'row':key[2],'cells':[b['location']['cell'] for b in rows]}
                  if key[0]=='sheet' else {'kind':'table_row','table':key[1],'row':key[2],'columns':[b['location']['column'] for b in rows]})
        # Do not duplicate each cell's JSON string, hash and metadata into the embedding text.
        # Compact values stay with their actual columns; full original blocks remain in the snapshot.
        cells=[]
        for row in rows:
            if key[0]=='sheet':
                cell={'cell':row['location']['cell'],'value':row['value'],'type':row['type']}
                for field in ('formula','merged_range','hidden_row','hidden_column'):
                    if row.get(field):cell[field]=row[field]
                if row.get('sheet_state')!='visible':cell['sheet_state']=row['sheet_state']
                if row.get('number_format')!='General':cell['number_format']=row['number_format']
            else:
                cell={'column':row['location']['column'],'text':row['text']}
                if row.get('merged_origin'):cell['merged_origin']=row['merged_origin']
            cells.append(cell)
        text=json.dumps({'cells':cells},ensure_ascii=False)
        result.append({'id':rows[0]['id'],'location':location,'text':text,'text_sha256':sha(text),'source_blocks':rows})
    return result


class FolderIndex:
    def __init__(self, folders, settings, *, factory=KnowledgeStore):
        self.folders=folders
        self.settings=settings
        self.factory=factory

    def store(self, fid):
        # IDs come exclusively from the catalog; never interpolate an arbitrary path.
        if not any(f['id']==fid for f in self.folders.folders()): raise KeyError('目录不存在')
        settings=self.settings.model_copy(update={'knowledge_path':self.folders.root/'indexes'/fid})
        return self.factory(settings)

    def status(self,fid):
        self.folders.authorized(fid)
        store=self.store(fid)
        result=store.status()
        try: result['diff']=self.diff(fid)
        except KeyError: result['diff']=None
        return result

    def diff(self,fid,scan_id=None):
        manifest=self.folders.manifest(fid,scan_id)
        current=self.store(fid).generation()
        previous={d['file_id']:d for d in (current or {}).get('files',[])}
        ready={r['id']:r for r in manifest['entries'] if r['kind']=='file' and r['status']=='ready'}
        changes={'added':[],'modified':[],'missing':[],'unchanged':[]}
        for key,row in ready.items():
            category='added' if key not in previous else ('unchanged' if previous[key]['source_identity']==row['identity'] else 'modified')
            changes[category].append(row['relative_path'])
        changes['missing']=[d['name'] for key,d in previous.items() if key not in ready]
        return {'scan_id':manifest['id'],'complete':manifest['complete'],**changes,
                'skipped':[{'path':r['relative_path'],'status':r['status'],'reason':r.get('reason')}
                           for r in manifest['entries'] if r['kind']=='file' and r['status']!='ready']}

    def sync(self,fid,scan_id,*,check=lambda:None,event_callback=None):
        store=self.store(fid)
        store.event_callback=event_callback
        with lease(store.root/'.sync.lock'):
            manifest=self.folders.manifest(fid,scan_id)
            if not manifest['complete']: raise ValueError('扫描不完整，请解决限制或读取错误后重新扫描；未发送嵌入请求')
            if any(r['status'] in ('unreadable','over_limit') for r in manifest['entries']):
                raise ValueError('清单有不可读取或超限项，未同步；请解决后重新扫描')
            rows=[r for r in manifest['entries'] if r['kind']=='file' and r['status']=='ready']
            if len(rows)>50: raise ValueError('索引最多50份可读取资料；请使用较小的资料目录')
            prepared=[];parse_hits=0
            for row in rows:
                check()
                preview=self.folders.preview(fid,scan_id,row['id'],limit=100)
                if preview['status']!='ready': raise ValueError(row['relative_path']+'：'+preview.get('message',preview['status'])+'；本次未切换索引')
                parse_hits+=int(preview['cache_hit'])
                # Preview seeded the bounded parser cache. Read its entire immutable extraction once.
                cache=self.folders.root/'parsed'/(preview['file_sha256']+'-'+str(preview['parser_version'])+row['suffix']+'.json')
                parsed=json.loads(cache.read_text('utf-8'))
                if parsed['file_sha256']!=preview['file_sha256']: raise ValueError('提取版本不一致')
                pages=[{'page':i+1,'text':b['text'],'location':b['location'],'block':b}
                       for i,b in enumerate(index_blocks(parsed['blocks']))]
                prepared.append({'id':sha(fid+'\0'+row['relative_path']+'\0'+preview['file_sha256']),
                    'name':row['relative_path'],'file_id':row['id'],'file_sha256':preview['file_sha256'],
                    'pages':pages,'bytes':row['size'],'source_identity':row['identity'],
                    'folder_id':fid,'scan_id':scan_id,'parser_version':preview['parser_version'],
                    'parse_cache_hit':preview['cache_hit'],'warnings':preview['warnings']})
            def validate():
                check()
                self.folders.authorized(fid)
                for doc in prepared:
                    _,data=self.folders.read(fid,scan_id,doc['file_id'])
                    if sha(data)!=doc['file_sha256']: raise ValueError('构建期间源文件变化，请重新扫描同步')
            validate()
            result=store.build(mode='replace',prepared=prepared,before_activate=validate)
            return {**result,'scan_id':scan_id,'parse_cache_hits':parse_hits,'diff':self.diff(fid,scan_id)}

    def search(self,fid,query,*,generation=None,file_ids=None,event_callback=None,**kwargs):
        self.folders.authorized(fid)
        store=self.store(fid)
        store.event_callback=event_callback
        current=store.generation(generation)
        if not current: raise ValueError('请先同步资料索引')
        documents=current['files']
        if file_ids is not None and not set(file_ids).issubset({d['file_id'] for d in documents}):
            raise ValueError('文件不在固定索引版本中')
        scope=None if file_ids is None else [d['id'] for d in documents if d['file_id'] in file_ids]
        result=store.search(query,generation=current['id'],doc_ids=scope,**kwargs)
        # The internal page ordinal identifies a parsed block, never a claimed Office page number.
        result['hits']=[{k:v for k,v in hit.items() if k not in ('page','page_sha256')}
                        for hit in result['hits']]
        return result

    def citation(self,fid,generation,doc_id,block_id):
        # Historical extraction is readable even if the original is moved/revoked; no source I/O.
        current=self.store(fid).generation(generation)
        if not current or current['status']!='ready': raise ValueError('索引版本未就绪')
        doc=next((d for d in current['files'] if d['id']==doc_id),None)
        if not doc: raise KeyError('文档不属于该索引版本')
        page=next((p for p in doc['pages'] if p['block']['id']==block_id),None)
        if not page: raise KeyError('引用位置不存在')
        return {'generation':generation,'name':doc['name'],'file_sha256':doc['file_sha256'],
                'block':page['block'],'historical_snapshot':True,'warnings':doc['warnings']}
