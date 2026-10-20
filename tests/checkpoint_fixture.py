from pathlib import Path
import time

from agent.checkpoint import CheckpointStore
from agent.loop import Agent
from tools.registry import tool,ToolRegistry
from tools.session import ResearchSession


def actor(root,llm,store=None):
    root=Path(root)
    @tool
    def append_record()->str:
        """追加一次本地测试记录。"""
        path=root/'effects.txt'
        path.write_text((path.read_text(encoding='utf-8') if path.exists() else '')+'x',encoding='utf-8')
        if (root/'.desktop-slow').exists():
            (root/'.desktop-test-ready').touch()
            while True: time.sleep(.1)
        return '已追加'
    return Agent(llm,ToolRegistry([append_record]),task_dir=root,research_session=ResearchSession(),
                 checkpoint=store or CheckpointStore(root),max_steps=5)
