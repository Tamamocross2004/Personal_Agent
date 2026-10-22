"""一轮研究一个进程，使用与CLI相同的入口和任务约定。"""
import argparse
import json
import os
import threading
from dataclasses import asdict
from pathlib import Path

from agent.config import Settings,load_llm_config
from agent.llm import LLMClient
from main import research,resume_research
from desktop_app.lifecycle import watch_parent


def main():
    parser=argparse.ArgumentParser();parser.add_argument('task_dir',type=Path);parser.add_argument('--parent',type=int)
    args=parser.parse_args()
    if args.parent:threading.Thread(target=watch_parent,args=(args.parent,),daemon=True).start()
    request=json.loads((args.task_dir/'.desktop-request.json').read_text(encoding='utf-8'))
    options=request['options']
    settings=Settings().model_copy(update={'memory_enabled':options['memory'],
        'llm_strength':options.get('strength','default'),
        'knowledge_enabled':options.get('knowledge',False),
        'knowledge_path':Path(request.get('knowledge_path',Settings().knowledge_path))})
    if options.get('rerank') is not None:
        settings=settings.model_copy(update={'knowledge_rerank_enabled':options['rerank']})
    try:
        if request.get('resume'):
            result=resume_research(args.task_dir,should_stop=(args.task_dir/'.desktop-stop').exists,clear_stop_marker=False)
        else:
            config=load_llm_config(provider=options['provider'],model=options['model'],settings=settings)
            client=LLMClient(config)
            result=research(client,settings,request['question'],options['max_steps'],options['budget'],
                task_dir=args.task_dir,historical_context=request.get('historical_context'),
                should_stop=(args.task_dir/'.desktop-stop').exists)
        value=asdict(result);value.pop('messages',None)
    except KeyboardInterrupt:value={'stop_reason':'interrupted','error':'用户请求停止。'}
    except Exception as exc:value={'stop_reason':'error','error':f'{type(exc).__name__}: {exc}'}
    temporary=args.task_dir/'.desktop-result.tmp'
    temporary.write_text(json.dumps(value,ensure_ascii=False,default=str),encoding='utf-8')
    temporary.replace(args.task_dir/'.desktop-result.json')


if __name__=='__main__':main()
