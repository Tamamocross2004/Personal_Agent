import argparse,json
from pathlib import Path
from agent.config import PROJECT_ROOT
from .store import FolderStore

def main():
    p=argparse.ArgumentParser(description='文件夹资料助手：扫描/预览在本地，sync和hybrid显式调用云端')
    p.add_argument('--state-dir',type=Path,default=PROJECT_ROOT/'workspace'/'folders')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('list');sub.add_parser('policy')
    sub.add_parser('add').add_argument('path',type=Path)
    for command in ('scan','show','revoke'):sub.add_parser(command).add_argument('folder_id')
    preview=sub.add_parser('preview');preview.add_argument('folder_id');preview.add_argument('scan_id');preview.add_argument('file_id')
    preview.add_argument('--offset',type=int,default=0);preview.add_argument('--limit',type=int,default=50)
    for command in ('index','diff'):sub.add_parser(command).add_argument('folder_id')
    sync=sub.add_parser('sync');sync.add_argument('folder_id');sync.add_argument('scan_id')
    search=sub.add_parser('search');search.add_argument('folder_id');search.add_argument('query');search.add_argument('--mode',choices=['keyword','hybrid'],default='keyword')
    args=p.parse_args();store=FolderStore(args.state_dir,excluded_roots=(PROJECT_ROOT/'workspace',))
    try:
        if args.command=='add':result=store.grant(args.path)
        elif args.command=='list':result=store.folders()
        elif args.command=='policy':result=store.policy()
        elif args.command=='preview':result=store.preview(args.folder_id,args.scan_id,args.file_id,offset=args.offset,limit=args.limit)
        elif args.command in ('index','diff','sync','search'):
            from agent.config import Settings
            from .index import FolderIndex
            index=FolderIndex(store,Settings())
            if args.command=='index':result=index.status(args.folder_id)
            elif args.command=='sync':result=index.sync(args.folder_id,args.scan_id)
            elif args.command=='search':result=index.search(args.folder_id,args.query,mode=args.mode)
            else:result=index.diff(args.folder_id)
        else:result=getattr(store,{'show':'manifest'}.get(args.command,args.command))(args.folder_id)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (ValueError,KeyError) as exc:print(str(exc));return 1

if __name__=='__main__':raise SystemExit(main())
