"""硅基流动文本cross-encoder：按原始索引绑定分数，不自动重试/降级。"""
import math
import time
from uuid import uuid4

import httpx
from agent.context import estimate_tokens
from .documents import sha


class RerankFailure(ValueError):
    pass


def validate_rows(rows,count):
    if not isinstance(rows,list) or len(rows)!=count:
        raise ValueError('重排返回数量不符')
    if any(not isinstance(row,dict) or type(row.get('index')) is not int for row in rows):
        raise ValueError('重排返回索引无效')
    if sorted(row['index'] for row in rows)!=list(range(count)):
        raise ValueError('重排返回索引重复或越界')
    for row in rows:
        score=row.get('relevance_score')
        if type(score) not in (float,int) or not math.isfinite(score):
            raise ValueError('重排分数无效')
    return [{'index':row['index'],'relevance_score':row['relevance_score']} for row in rows]


class Reranker:
    def __init__(self,settings,record,client=None):
        self.settings=settings;self.record=record;self.client=client
        self.identity={'provider':'siliconflow','base_url':settings.rerank_base_url.rstrip('/'),
                       'model':settings.rerank_model}

    def rank(self,query,texts,purpose,budget):
        if not self.settings.siliconflow_api_key:
            raise ValueError('请在本项目.env配置SILICONFLOW_API_KEY')
        if not texts or len(texts)>80: raise ValueError('重排候选数须为1至80')
        estimate=sum(estimate_tokens(query)+estimate_tokens(text) for text in texts)
        if estimate>budget: raise ValueError('重排请求预计超出剩余token预算，未发送；可显式关闭重排')
        event={'id':uuid4().hex,'operation':'rerank','purpose':purpose,**self.identity,
               'status':'started','input_tokens':None,'output_tokens':None,
               'estimated_input_tokens':estimate,'documents':len(texts),'request_attempts':1,
               'query_sha256':sha(query),'document_sha256':[sha(text) for text in texts]}
        self.record(event);start=time.perf_counter()
        try:
            payload={'model':self.settings.rerank_model,'query':query,'documents':texts,
                     'top_n':len(texts),'return_documents':False}
            def send(client):
                return client.post(self.identity['base_url']+'/rerank',json=payload,
                    headers={'Authorization':'Bearer '+self.settings.siliconflow_api_key,
                             'X-Trace-Id':event['id']})
            if self.client is None:
                with httpx.Client(timeout=self.settings.rerank_timeout) as client: response=send(client)
            else: response=send(self.client)
            event['http_status']=response.status_code
            response.raise_for_status()
            body=response.json()
            tokens=body.get('meta',{}).get('tokens',{})
            for name in ('input_tokens','output_tokens'):
                value=tokens.get(name)
                event[name]=value if type(value) is int and value>=0 else None
            if event['input_tokens'] is None or event['output_tokens'] is None:
                raise ValueError('重排响应缺少有效用量')
            rows=validate_rows(body.get('results'),len(texts))
            event.update(status='returned',seconds=round(time.perf_counter()-start,3),results=rows,
                         response_id=body.get('id') if isinstance(body.get('id'),str) else None)
            self.record(event)
            return rows,[event]
        except Exception as exc:
            event.update(status='failed',seconds=round(time.perf_counter()-start,3),error_type=type(exc).__name__)
            self.record(event)
            raise RerankFailure('重排调用失败或响应无效（'+type(exc).__name__+'）；未重试或自动降级') from None
