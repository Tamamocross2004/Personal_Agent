"""Milvus Lite 3：短时独占访问本机索引，不与其他进程共享文件句柄。"""
from contextlib import contextmanager
from .locking import lease


@contextmanager
def client_for(root,generation):
    try:
        from pymilvus import MilvusClient
        from milvus_lite.server_manager import server_manager_instance
    except ImportError: raise ValueError('向量依赖未安装，请安装本项目rag依赖') from None
    path=root/'vectors'/(generation+'.db')
    path.parent.mkdir(parents=True,exist_ok=True)
    with lease(root/'.vectors.lock',wait=10):
        client=None
        try:
            client=MilvusClient(str(path))
            yield client
        finally:
            try:
                if client is not None: client.close()
            finally:
                server_manager_instance.release_server(str(path))


class MilvusVectors:
    def build(self,root,generation,vectors,dimension):
        with client_for(root,generation) as client:
            client.create_collection(collection_name='chunks',dimension=dimension,metric_type='COSINE')
            for start in range(0,len(vectors),64):
                client.upsert(collection_name='chunks',data=[{'id':i,'vector':v}
                    for i,v in enumerate(vectors[start:start+64],start)])
            client.load_collection('chunks')
            if vectors:
                assert client.search(collection_name='chunks',data=[vectors[0]],limit=1)

    def search(self,root,generation,vector,limit):
        with client_for(root,generation) as client:
            client.load_collection('chunks')
            rows=client.search(collection_name='chunks',data=[vector],limit=limit)[0]
            return [int(row['id']) for row in rows]
