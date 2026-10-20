"""本机检查点查看与显式恢复。status不调用模型；run继承原任务配置和累计预算。"""
import argparse
import json
from pathlib import Path

from agent.checkpoint import CheckpointError, CheckpointStore


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('status','stop','run'):
        sub.add_parser(name).add_argument('task_dir',type=Path)
    args=parser.parse_args(argv)
    if args.command=='status':
        value=CheckpointStore(args.task_dir).status()
        print(json.dumps(value,ensure_ascii=False,indent=2))
        return 0 if value['available'] else 1
    try:
        if args.command=='stop':
            CheckpointStore(args.task_dir).request_stop()
            print('停止请求已保存；等待当前模型或工具返回后停止，未自动恢复。')
            return 0
        from main import resume_research
        result=resume_research(args.task_dir)
        return 0 if result.stop_reason=='final_answer' else 1
    except (CheckpointError,OSError) as exc:
        print('无法恢复：'+str(exc))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
