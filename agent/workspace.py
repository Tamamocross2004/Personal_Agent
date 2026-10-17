"""任务工作目录：每个研究问题一个子目录，存放报告（以及后续阶段的笔记、代码、图表、日志）。"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from agent.config import PROJECT_ROOT

WORKSPACE_ROOT = PROJECT_ROOT / "workspace"

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f\s，。？！、；：“”‘’（）《》【】]+')


def slugify(text: str, max_len: int = 30) -> str:
    """把问题变成适合做目录名的短字符串（保留中文）。"""
    slug = _UNSAFE.sub("-", text).strip("-.")
    return slug[:max_len].rstrip("-.") or "task"


def new_task_dir(question: str, root: Path = WORKSPACE_ROOT, now: datetime | None = None) -> Path:
    """返回本次任务的目录路径（只计算路径，不创建；真正写文件时再创建，避免留下空目录）。"""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    base = root / f"{stamp}-{slugify(question)}"
    path, n = base, 2
    while path.exists():
        path = base.with_name(f"{base.name}-{n}")
        n += 1
    return path
