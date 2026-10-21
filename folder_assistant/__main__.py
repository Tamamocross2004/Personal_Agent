import argparse,json
from pathlib import Path
from agent.config import PROJECT_ROOT
from .store import FolderStore

def main():
    p=argparse.ArgumentParser(description='本地只读资料目录，不调用模型')
    p.add_argument('--state-dir',type=Path,default=PROJECT_ROOT/'workspace'/'folders')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('list');sub.add_parser('policy')
    sub.add_parser('add').add_argument('path',type=Path)
    for command in ('scan','show','revoke'):sub.add_parser(command).add_argument('folder_id')
    args=p.parse_args();store=FolderStore(args.state_dir,excluded_roots=(PROJECT_ROOT/'workspace',))
    try:
        if args.command=='add':result=store.grant(args.path)
        elif args.command=='list':result=store.folders()
        elif args.command=='policy':result=store.policy()
        else:result=getattr(store,{'show':'manifest'}.get(args.command,args.command))(args.folder_id)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (ValueError,KeyError) as exc:print(str(exc));return 1

if __name__=='__main__':raise SystemExit(main())
