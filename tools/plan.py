"""计划管理工具：Agent 可以更新 plan.json 里任务项的状态和备注。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from tools.registry import Tool, tool


def make_update_plan(task_dir: Path, on_update: callable = None) -> Tool:
    """构造 update_plan 工具。

    Args:
        task_dir: 任务目录（plan.json 所在位置）
        on_update: 可选回调，plan 更新后调用（用于通知 Agent loop reload）
    """

    @tool(name="update_plan")
    def update_plan(
        task_id: Annotated[str, Field(description="任务 ID，例如 task-1")],
        status: Annotated[
            Literal["pending", "in_progress", "done", "skipped"] | None,
            Field(description="新状态（可选）"),
        ] = None,
        note: Annotated[str | None, Field(description="备注或发现（可选）")] = None,
    ) -> str:
        """更新计划中某个任务的状态或备注。开始一个子任务时设为 in_progress，完成时设为 done。"""
        plan_path = task_dir / "plan.json"
        if not plan_path.exists():
            raise FileNotFoundError("plan.json 不存在")

        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        tasks = plan.get("tasks", [])

        # 找到对应任务
        found = False
        for task in tasks:
            if task.get("id") == task_id:
                if status is not None:
                    task["status"] = status
                if note is not None:
                    task["note"] = note
                found = True
                break

        if not found:
            raise ValueError(f"计划中找不到任务 {task_id}")

        # 保存回文件
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

        # 触发回调（通知 Agent loop）
        if on_update is not None:
            on_update()

        # 把最新的完整计划放进返回值：它只会追加在历史末尾，不改动开头，不影响前缀缓存
        from agent.planner import format_plan_for_prompt

        return f"已更新 {task_id}。\n{format_plan_for_prompt(plan)}"

    return update_plan
