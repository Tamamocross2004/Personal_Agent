"""Milvus Lite 3：短时独占访问本机索引，不与其他进程共享文件句柄。"""
from contextlib import contextmanager
from importlib.metadata import version
import os
from .locking import lease


class _ManifestOS:
    """仅替换Milvus Lite 3.0清单模块的rename，不改变进程全局os。"""
    rename=staticmethod(os.replace)

    def __getattr__(self,name): return getattr(os,name)


def _install_manifest_compat(platform=None):
    # 3.0用os.rename提交已有manifest；Windows拒绝覆盖，os.replace保留原有提交协议。
    # 限定已核对的版本和模块，不改site-packages，也不吞掉真实的存储错误。
    if (platform or os.name)!='nt' or version('milvus-lite') not in ('3.0','3.0.0'): return
    from milvus_lite.storage import manifest
    if not isinstance(manifest.os,_ManifestOS): manifest.os=_ManifestOS()


@contextmanager
def client_for(root,generation):
    try:
        from pymilvus import MilvusClient
        from milvus_lite.server_manager import server_manager_instance
    except ImportError: raise ValueError('向量依赖未安装，请安装本项目rag依赖') from None
    _install_manifest_compat()
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
