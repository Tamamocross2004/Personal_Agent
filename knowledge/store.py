"""SQLite元数据/全文索引与不可变Milvus代际；只有成功构建才切换active。"""
from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from agent.config import PROJECT_ROOT
from agent.memory import search_terms
from .documents import parse, split, sha
from .embedding import Embedder
from .locking import lease
from .vectors import MilvusVectors
from .reranking import Reranker,validate_rows


def now(): return datetime.now(timezone.utc).isoformat()


class KnowledgeStore:
    def __init__(self,settings,*,vectors=None,embedding_client=None,rerank_client=None,event_callback=None):
        self.settings=settings
        self.event_callback=event_callback
        self.root=Path(settings.knowledge_path)
        if not self.root.is_absolute(): self.root=PROJECT_ROOT/self.root
        self.root=self.root.resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'knowledge.sqlite3'
        if self.path.is_symlink(): raise ValueError('知识库数据库不得为链接')
        self.vectors=vectors or MilvusVectors()
        self.embedder=Embedder(settings,self.record_event,embedding_client)
        self.reranker=Reranker(settings,self.record_event,rerank_client)
        self.signature=sha(json.dumps(self.embedder.identity,sort_keys=True))
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS generations(id TEXT PRIMARY KEY,status TEXT,created TEXT,data TEXT);
                CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,data TEXT);
                CREATE TABLE IF NOT EXISTS chunks(generation TEXT,idx INTEGER,data TEXT,PRIMARY KEY(generation,idx));
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(generation UNINDEXED,idx UNINDEXED,terms);
                CREATE TABLE IF NOT EXISTS vectors(signature TEXT,text_hash TEXT,data TEXT,PRIMARY KEY(signature,text_hash));
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,data TEXT);
                CREATE TABLE IF NOT EXISTS rerank_cache(key TEXT PRIMARY KEY,data TEXT);
            ''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def record_event(self,event):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO events VALUES (?,?)',(event['id'],json.dumps({**event,'recorded_at':now()},ensure_ascii=False)))
        if self.event_callback: self.event_callback(dict(event))

    def events(self):
        with self.connect() as db: return [json.loads(r[0]) for r in db.execute('SELECT data FROM events ORDER BY rowid')]

    def generation(self,generation=None):
        with self.connect() as db:
            if generation is None:
                active=db.execute("SELECT value FROM meta WHERE key='active'").fetchone()
                if not active: return None
                generation=active[0]
            row=db.execute('SELECT * FROM generations WHERE id=?',(generation,)).fetchone()
        if not row: raise ValueError('知识库版本不存在')
        return {'id':row['id'],'status':row['status'],'created':row['created'],**json.loads(row['data'])}

    def status(self):
        current=self.generation()
        with self.connect() as db:
            row=db.execute('SELECT * FROM generations ORDER BY rowid DESC LIMIT 1').fetchone()
        latest=({'id':row['id'],'status':row['status'],'created':row['created'],**json.loads(row['data'])} if row else None)
        if latest and latest['status']=='building':
            try:
                with lease(self.root/'.build.lock'): latest={**latest,'status':'interrupted'}
            except ValueError: pass
        def public(g):
            if not g: return None
            return {k:v for k,v in g.items() if k!='files'} | {'files':[
                {k:v for k,v in d.items() if k!='pages'} | {'pages':len(d['pages'])} for d in g.get('files',[])]}
        all_events=self.events()
        events=[e for e in all_events if e.get('operation')!='rerank']
        rerank_events=[e for e in all_events if e.get('operation')=='rerank']
        return {'active':public(current),'latest':public(latest),
                'embedding':{**self.embedder.identity,'key_configured':bool(self.settings.siliconflow_api_key)},
                'rerank':{**self.reranker.identity,'enabled':self.settings.knowledge_rerank_enabled,
                    'candidates':self.settings.knowledge_rerank_candidates,
                    'usage':{'calls':len(rerank_events),'input_tokens':sum(e.get('input_tokens') or 0 for e in rerank_events),
                        'output_tokens':sum(e.get('output_tokens') or 0 for e in rerank_events),
                        'unknown_usage_calls':sum(e.get('input_tokens') is None or e.get('output_tokens') is None for e in rerank_events)}},
                'usage':{'calls':len(events),'returned':sum(e['status']=='returned' for e in events),
                    'unknown_usage_calls':sum(e.get('input_tokens') is None for e in events),
                    'input_tokens':sum(e.get('input_tokens') or 0 for e in events)},
                'limits':'仅原始资料检索；排序分不是正确概率。旧代际保留供任务证据快照使用。'}

    def _save_generation(self,generation,status,data,activate=False):
        with self.connect() as db:
            db.execute('UPDATE generations SET status=?,data=? WHERE id=?',
                       (status,json.dumps(data,ensure_ascii=False),generation))
            if activate: db.execute("INSERT OR REPLACE INTO meta VALUES ('active',?)",(generation,))

    def _cached_vectors(self,texts,purpose,budget):
        rows={}; events=[]; hits=0
        with self.connect() as db:
            for text in texts:
                h=sha(text)
                row=db.execute('SELECT data FROM vectors WHERE signature=? AND text_hash=?',(self.signature,h)).fetchone()
                if row: rows[h]=json.loads(row[0]); hits+=1
        missing=list(dict.fromkeys(t for t in texts if sha(t) not in rows))
        for offset in range(0,len(missing),16):
            batch=missing[offset:offset+16]
            vectors,usage=self.embedder.embed(batch,purpose,budget)
            events+=usage
            budget-=sum(e['input_tokens'] for e in usage)
            with self.connect() as db:
                for text,vector in zip(batch,vectors):
                    rows[sha(text)]=vector
                    db.execute('INSERT OR REPLACE INTO vectors VALUES (?,?,?)',
                               (self.signature,sha(text),json.dumps(vector)))
        return [rows[sha(t)] for t in texts],events,hits

    def build(self,uploads=(),*,mode='append',remove=()):
        if mode not in ('append','replace','rebuild'): raise ValueError('索引操作无效')
        with lease(self.root/'.build.lock'):
            old=self.generation()
            files=[] if mode=='replace' else list((old or {}).get('files',[]))
            if remove:
                if not set(remove).issubset({d['id'] for d in files}): raise ValueError('待删除文档不存在')
                files=[d for d in files if d['id'] not in remove]
            for name,data in uploads:
                pages=parse(data,name)
                checksum=sha(data); doc_id=sha(name+'\0'+checksum)
                same=next((d for d in files if d['name']==name),None)
                if same:
                    if same['id']==doc_id: continue
                    raise ValueError('同名文档内容不同；请先删除原文档或明确替换知识库')
                document={'id':doc_id,'name':name,'file_sha256':checksum,'pages':pages,'bytes':len(data)}
                blobs=self.root/'documents'; blobs.mkdir(exist_ok=True)
                blob=blobs/checksum
                if blob.is_symlink(): raise ValueError('文档存储不得为链接')
                if blob.exists():
                    if sha(blob.read_bytes())!=checksum: raise ValueError('原始文档存储损坏')
                else:
                    with blob.open('xb') as stream: stream.write(data)
                with self.connect() as db:
                    db.execute('INSERT OR REPLACE INTO documents VALUES (?,?)',(doc_id,json.dumps(document,ensure_ascii=False)))
                files.append(document)
            if len(files)>50 or sum(len(p['text']) for d in files for p in d['pages'])>2_000_000:
                raise ValueError('首版知识库最多50份文档、200万提取字符')
            chunks=[]
            for document in files:
                for chunk in split(document['pages'],self.settings.knowledge_chunk_size,self.settings.knowledge_chunk_overlap):
                    chunks.append({**chunk,'doc_id':document['id'],'name':document['name'],
                                   'file_sha256':document['file_sha256'],'page_sha256':sha(document['pages'][chunk['page']-1]['text'])})
            if len(chunks)>5000: raise ValueError('首版知识库最多5000片段')
            generation=uuid4().hex
            data={'files':files,'chunks':len(chunks),'embedding':self.embedder.identity,'signature':self.signature,
                  'chunk_size':self.settings.knowledge_chunk_size,'overlap':self.settings.knowledge_chunk_overlap}
            with self.connect() as db:
                db.execute('INSERT INTO generations VALUES (?,?,?,?)',(generation,'building',now(),json.dumps(data,ensure_ascii=False)))
            try:
                vectors,events,hits=self._cached_vectors([c['text'] for c in chunks],'build:'+generation,
                                                       self.settings.knowledge_build_token_budget)
                if chunks: self.vectors.build(self.root,generation,vectors,self.settings.embedding_dimensions)
                with self.connect() as db:
                    for index,chunk in enumerate(chunks):
                        db.execute('INSERT INTO chunks VALUES (?,?,?)',(generation,index,json.dumps(chunk,ensure_ascii=False)))
                        db.execute('INSERT INTO chunks_fts VALUES (?,?,?)',(generation,index,' '.join(search_terms(chunk['text'],4000))))
                data.update(cache_hits=hits,embedding_calls=len(events),input_tokens=sum(e['input_tokens'] for e in events))
                self._save_generation(generation,'ready',data,activate=True)
                return self.status()
            except Exception as exc:
                data['error_type']=type(exc).__name__
                data['error']='构建失败，当前可用版本未切换；已发生的嵌入请求保留在用量记录。'
                self._save_generation(generation,'failed',data)
                raise

    def preview(self,doc_id,page=1):
        current=self.generation()
        document=next((d for d in (current or {}).get('files',[]) if d['id']==doc_id),None)
        if not document: raise ValueError('文档不在当前知识库')
        if not 1<=page<=len(document['pages']): raise ValueError('页码超出范围')
        return {'id':doc_id,'name':document['name'],'page':page,'text':document['pages'][page-1]['text'][:20000],
                'partial':len(document['pages'][page-1]['text'])>20000,'file_sha256':document['file_sha256']}

    def _rerank(self,query,texts,purpose,budget):
        key=sha(json.dumps({'identity':self.reranker.identity,'query':sha(query),
                            'texts':[sha(text) for text in texts]},sort_keys=True))
        with self.connect() as db:
            row=db.execute('SELECT data FROM rerank_cache WHERE key=?',(key,)).fetchone()
        if row: return validate_rows(json.loads(row[0]),len(texts)),[],True
        rows,events=self.reranker.rank(query,texts,purpose,budget)
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO rerank_cache VALUES (?,?)',(key,json.dumps(rows,allow_nan=False)))
        return rows,events,False

    def _chunks(self,generation,indices):
        indices=list(dict.fromkeys(indices))
        if not indices: return {}
        with self.connect() as db:
            return {r['idx']:json.loads(r['data']) for r in db.execute(
                'SELECT idx,data FROM chunks WHERE generation=? AND idx IN ('+','.join('?' for _ in indices)+')',
                [generation,*indices])}

    def search(self,query,*,generation=None,mode='hybrid',k=4,budget=None,rerank=None):
        if mode not in ('keyword','vector','hybrid') or not query.strip() or len(query)>1000 or not 1<=k<=8:
            raise ValueError('检索参数无效；问题最多1000字符，k为1至8')
        if rerank is True and mode=='keyword': raise ValueError('关键词模式不执行云端重排；请选择hybrid或vector')
        use_rerank=mode!='keyword' and (self.settings.knowledge_rerank_enabled if rerank is None else rerank)
        budget=self.settings.knowledge_search_token_budget if budget is None else budget
        current=self.generation(generation)
        if not current or not current['chunks']: return {'generation':current['id'] if current else None,'hits':[],'usage':[],'mode':mode}
        if current['status']!='ready': raise ValueError('知识库版本未完成')
        if mode!='keyword' and current['signature']!=self.signature:
            raise ValueError('嵌入模型配置已变更，请重建索引或显式选择关键词检索')
        with self.connect() as db:
            terms=search_terms(query)
            lexical=[]
            if terms and mode!='vector':
                expression=' OR '.join('"'+t.replace('"','""')+'"' for t in terms)
                lexical=[int(r[0]) for r in db.execute('SELECT idx FROM chunks_fts WHERE chunks_fts MATCH ? AND generation=? ORDER BY bm25(chunks_fts),idx LIMIT ?',
                                                     (expression,current['id'],self.settings.knowledge_lexical_candidates))]
        dense=[]; events=[]
        if mode!='keyword':
            vectors,events,_=self._cached_vectors([query],'query:'+current['id'],budget)
            budget-=sum(e['input_tokens'] for e in events)
            dense=self.vectors.search(self.root,current['id'],vectors[0],min(self.settings.knowledge_vector_candidates,current['chunks']))
        scores={}
        for ranks in (lexical,dense):
            for rank,index in enumerate(ranks,1): scores[index]=scores.get(index,0)+1/(60+rank)
        pool=sorted(scores,key=lambda i:(-scores[i],i))[:self.settings.knowledge_rerank_candidates]
        chunks=self._chunks(current['id'],pool)
        if len(chunks)!=len(pool): raise ValueError('检索索引与片段元数据不一致')
        ranking=list(pool);rerank_scores={}
        reranking={'enabled':use_rerank,'status':'disabled' if not use_rerank else 'empty',
                   'model':self.settings.rerank_model if use_rerank else None,'candidate_count':len(pool),'cache_hit':False}
        if use_rerank and pool:
            rows,usage,cache_hit=self._rerank(query,[chunks[i]['text'] for i in pool],'query:'+current['id'],budget)
            events+=usage
            rerank_scores={pool[row['index']]:row['relevance_score'] for row in rows}
            ranking=sorted(pool,key=lambda i:(-rerank_scores[i],pool.index(i)))
            reranking.update(status='applied',cache_hit=cache_hit)
        direct=ranking[:k]
        neighbors=[n for i in direct for n in (i-1,i+1) if n not in chunks]
        chunks.update(self._chunks(current['id'],neighbors))
        selected=[(i,'direct') for i in direct]
        for index in direct:
            for neighbor in (index-1,index+1):
                if neighbor in chunks and chunks[neighbor]['doc_id']==chunks[index]['doc_id'] and neighbor not in [i for i,_ in selected]:
                    selected.append((neighbor,'adjacent'))
        hits=[]; remaining=5500
        for index,role in selected[:12]:
            if remaining<=0: break
            chunk=chunks[index]; text=chunk['text'][:remaining]
            hits.append({**chunk,'text':text,'end':chunk['start']+len(text),'generation':current['id'],
                         'chunk_id':index,'role':role,'rank_score':scores.get(index),
                         'rerank_score':rerank_scores.get(index),
                         'partial':len(text)<len(chunk['text'])})
            remaining-=len(text)
        return {'generation':current['id'],'hits':hits,'usage':events,'mode':mode,
                'reranking':reranking,'candidates':[{'chunk_id':i,'rrf_score':scores[i],
                    'rerank_score':rerank_scores.get(i)} for i in pool],
                'lexical_candidates':len(lexical),'vector_candidates':len(dense),
                'meaning':'RRF与重排分仅用于排序，不是答案正确概率；无命中或有限片段不证明原资料不存在信息，检索不认证答案。'}
