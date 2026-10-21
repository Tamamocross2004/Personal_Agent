"""仅隔离桌面测试进程的假服务；生产适配器仍负责校验、缓存、证据与计量。"""
import json
from types import SimpleNamespace
import httpx


class OfflineEmbedding:
    def __init__(self):self.embeddings=self
    def create(self,**kwargs):
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=11*len(kwargs['input'])),data=[
            SimpleNamespace(index=i,embedding=[1.]+[0.]*1023) for i,_ in enumerate(kwargs['input'])])


def offline_rerank_client():
    def respond(request):
        count=len(json.loads(request.content)['documents'])
        return httpx.Response(200,json={'id':'offline-rerank','meta':{'tokens':{'input_tokens':23,'output_tokens':0}},
            'results':[{'index':i,'relevance_score':1/(i+1)} for i in range(count)]})
    return httpx.Client(transport=httpx.MockTransport(respond))


def inject_clients():
    from knowledge.embedding import Embedder
    from knowledge.reranking import Reranker
    embedding_init=Embedder.__init__;rerank_init=Reranker.__init__
    def embedding(self,settings,record,client=None):embedding_init(self,settings,record,client or OfflineEmbedding())
    def rerank(self,settings,record,client=None):rerank_init(self,settings,record,client or offline_rerank_client())
    Embedder.__init__=embedding;Reranker.__init__=rerank
