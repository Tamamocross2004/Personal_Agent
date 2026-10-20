"""一轮研究一个进程，使用与CLI相同的入口和任务约定。"""
import argparse
import json
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path

from agent.config import Settings,load_llm_config
from agent.llm import LLMClient
from main import research,resume_research


def watch_parent(pid):
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.windll.kernel32
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x100000,False,pid)
        if handle:
            kernel.WaitForSingleObject(handle,0xFFFFFFFF)
            kernel.CloseHandle(handle)
        import subprocess
        subprocess.run(['taskkill','/PID',str(os.getpid()),'/T','/F'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        while True:
            time.sleep(1)
            if os.getppid()!=pid:os._exit(130)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('task_dir',type=Path);parser.add_argument('--parent',type=int)
    args=parser.parse_args()
    if args.parent:threading.Thread(target=watch_parent,args=(args.parent,),daemon=True).start()
    request=json.loads((args.task_dir/'.desktop-request.json').read_text(encoding='utf-8'))
    options=request['options']
    settings=Settings().model_copy(update={'memory_enabled':options['memory']})
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
