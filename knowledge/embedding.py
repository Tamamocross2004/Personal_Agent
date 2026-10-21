"""仅项目Key的硅基流动适配器；请求前后持久记录，不自动重试。"""
import math
import time
from uuid import uuid4

from agent.context import estimate_tokens


class EmbeddingFailure(ValueError):
    def __init__(self,message,events):
        super().__init__(message)
        self.events=events


class Embedder:
    def __init__(self,settings,record,client=None):
        self.settings=settings; self.record=record; self.client=client
        self.identity={'provider':'siliconflow','base_url':settings.embedding_base_url.rstrip('/'),
                       'model':settings.embedding_model,'dimensions':settings.embedding_dimensions}

    def embed(self,texts,purpose,budget):
        if not self.settings.siliconflow_api_key:
            raise ValueError('请在本项目.env配置SILICONFLOW_API_KEY；不会使用全局或示例项目Key')
        estimate=sum(estimate_tokens(t) for t in texts)
        if estimate>budget: raise ValueError('嵌入请求预计超出剩余token预算，未发送')
        event={'id':uuid4().hex,'purpose':purpose,'provider':'siliconflow','model':self.settings.embedding_model,
               'status':'started','input_tokens':None,'estimated_input_tokens':estimate,'request_attempts':1}
        self.record(event)
        start=time.perf_counter()
        try:
            if self.client is None:
                from openai import OpenAI
                self.client=OpenAI(api_key=self.settings.siliconflow_api_key,
                    base_url=self.settings.embedding_base_url,timeout=self.settings.embedding_timeout,max_retries=0)
            response=self.client.embeddings.create(model=self.settings.embedding_model,input=texts,encoding_format='float')
            usage=response.usage
            event.update(status='returned',seconds=round(time.perf_counter()-start,3),
                         input_tokens=usage.prompt_tokens if usage else None)
            if type(event['input_tokens']) is not int or event['input_tokens']<0:
                event['input_tokens']=None
                raise ValueError('嵌入服务未返回有效用量')
            rows=sorted(response.data,key=lambda d:d.index)
            if [r.index for r in rows]!=list(range(len(texts))): raise ValueError('嵌入返回数量或索引不符')
            vectors=[r.embedding for r in rows]
            if any(len(v)!=self.settings.embedding_dimensions or not all(math.isfinite(x) for x in v)
                   or sum(x*x for x in v)==0 for v in vectors): raise ValueError('嵌入向量维度或数值无效')
            self.record(event)
            return vectors,[event]
        except Exception as exc:
            event.update(status='failed',seconds=round(time.perf_counter()-start,3),error_type=type(exc).__name__)
            self.record(event)
            raise EmbeddingFailure('嵌入调用失败或响应无效（'+type(exc).__name__+'）；详情只记录类型，未自动重试',[event]) from None
