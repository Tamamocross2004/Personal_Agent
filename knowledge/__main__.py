"""原始资料知识库命令行；向量操作显式调用项目嵌入服务。"""
import argparse
import json
from pathlib import Path

from agent.config import Settings
from .documents import MAX_BYTES
from .store import KnowledgeStore


def main():
    parser=argparse.ArgumentParser(description='管理本地RAG知识库')
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('status')
    add=commands.add_parser('import'); add.add_argument('files',nargs='+',type=Path)
    add.add_argument('--replace',action='store_true')
    commands.add_parser('rebuild')
    delete=commands.add_parser('remove'); delete.add_argument('document_id')
    search=commands.add_parser('search'); search.add_argument('query')
    search.add_argument('--mode',choices=['keyword','vector','hybrid'],default='hybrid')
    search.add_argument('-k',type=int,default=4)
    args=parser.parse_args(); store=KnowledgeStore(Settings())
    try:
        if args.command=='status': result=store.status()
        elif args.command=='import':
            uploads=[]
            for path in args.files:
                if path.stat().st_size>MAX_BYTES: raise ValueError('文档超过20MB')
                uploads.append((path.name,path.read_bytes()))
            result=store.build(uploads,mode='replace' if args.replace else 'append')
        elif args.command=='rebuild': result=store.build(mode='rebuild')
        elif args.command=='remove': result=store.build(remove=[args.document_id])
        else: result=store.search(args.query,mode=args.mode,k=args.k)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except ValueError as exc: parser.exit(1,str(exc)+'\n')


if __name__=='__main__': main()
