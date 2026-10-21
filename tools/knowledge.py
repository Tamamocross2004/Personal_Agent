"""把知识库检索接入统一工具派发；只登记实际送达的片段。"""
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field
from agent.evidence import write_json
from knowledge.store import KnowledgeStore
from .registry import tool, ToolResult


def make_search_knowledge(settings,task_dir:Path,session,store_factory=KnowledgeStore):
    @tool(name='search_knowledge')
    def search_knowledge(
        query:Annotated[str,Field(min_length=1,max_length=1000,description='检索本地原始文档的问题，不检索历史研究摘要')],
        mode:Literal['hybrid','keyword','vector']='hybrid',
        k:Annotated[int,Field(ge=1,le=8)]=4,
        rerank:Annotated[bool|None,Field(description='留空沿用本任务重排设置；false关闭。本任务已关闭时不能自行开启。关键词模式不执行云端重排')]=None,
    )->ToolResult:
        """检索已导入知识库。hybrid独立召回关键词和向量后融合；keyword不调用嵌入API。
        可按本轮设置对候选进行cross-encoder重排。返回原文、文件/页码/代际；rank_score和rerank_score
        仅用于排序，不代表答案正确概率。无命中不等于全文无该事实。"""
        events={}
        def collect(event):
            events[event['id']]=event
            journal=task_dir/('rerank_usage.jsonl' if event.get('operation')=='rerank' else 'embedding_usage.jsonl')
            if journal.is_symlink(): raise ValueError('嵌入用量日志不得为链接')
            with journal.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(event,ensure_ascii=False)+'\n')
        try:
            if rerank is True and not settings.knowledge_rerank_enabled:
                raise ValueError('当前任务已关闭重排，不能通过工具自行开启')
            store=store_factory(settings,event_callback=collect)
            snapshot=task_dir/'knowledge_snapshot.json'
            if snapshot.is_symlink(): raise ValueError('知识库任务快照不得为链接')
            if snapshot.exists():
                if snapshot.stat().st_size>10000: raise ValueError('知识库快照无效')
                pinned=json.loads(snapshot.read_text(encoding='utf-8'))
                if pinned['root']!=str(store.root): raise ValueError('知识库路径已变化，不混用任务快照')
                generation=pinned['generation']
            else:
                generation=(store.generation() or {}).get('id')
                if generation:
                    write_json(snapshot,{'root':str(store.root),'generation':generation,
                        'meaning':'本任务检索固定代际，仅证明当时原文，不认证当前库或内容真实。'})
            if not generation:
                return ToolResult('知识库尚无已完成索引；请先在知识库管理中导入资料。')
            result=store.search(query,generation=generation,mode=mode,k=k,
                                budget=getattr(session,'embedding_budget_remaining',0),rerank=rerank)
            content=json.dumps({key:value for key,value in result.items() if key not in ('hits','usage','candidates')},ensure_ascii=False)+'\n'
            metadata=[]
            for hit in result['hits']:
                content+=f"\n【{hit['name']} 第{hit['page']}页 片段{hit['chunk_id']} {hit['role']}】\n<document_content>\n"
                start=len(content); content+=hit['text']; end=len(content)
                content+='\n</document_content>\n'
                metadata.append({**hit,'output_start':start,'output_end':end})
            if not result['hits']: content+='本次检索无命中。仅说明本次检索没有返回片段，不证明知识库不存在相关事实。'
            return ToolResult(content,evidence=metadata,usage=list(events.values()))
        except Exception as exc:
            message=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
            return ToolResult('ERROR: 知识库检索失败：'+message,True,usage=list(events.values()))
    return search_knowledge
