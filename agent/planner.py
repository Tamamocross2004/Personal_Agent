"""计划生成：在 Agent 开始工作前，先单独调用一次 LLM 把问题拆解成 3-6 个子任务。

生成的 plan 是 JSON 格式：
{
  "tasks": [
    {"id": "task-1", "title": "...", "status": "pending", "note": ""},
    {"id": "task-2", "title": "...", "status": "pending", "note": ""}
  ]
}

status: pending | in_progress | done | skipped
note: Agent 执行过程中的发现或决定（update_plan 工具写入）

plan.json 保存在任务目录，Agent 每轮都能看到当前状态。
"""

from __future__ import annotations

import json
from pathlib import Path

PLANNER_PROMPT = """你是一名研究助手。用户会给你一个问题，你需要把它拆解成 3-6 个独立的子任务。
每个子任务应该是可执行的、有明确输出的步骤（搜索、读网页、整理分析、写报告）。

输出 JSON：
{
  "tasks": [
    {"id": "task-1", "title": "搜索关键词 X 了解背景", "status": "pending", "note": ""},
    {"id": "task-2", "title": "读 Y 机构的报告", "status": "pending", "note": ""},
    ...
  ]
}

- id: task-1, task-2, ...（依次编号）
- title: 简短描述（一句话）
- status: 全部填 "pending"
- note: 留空

用户问题：
"""


def generate_plan(question: str, chat, task_dir: Path) -> dict:
    """调用 LLM 生成计划，保存到 task_dir/plan.json。

    chat: 可调用对象 chat(messages, **kwargs) -> ChatCompletion。由调用方传入，
    这样规划调用能计入 Agent 的 token 预算和 trace，而不是绕过它们。
    """
    messages = [{"role": "user", "content": PLANNER_PROMPT + question}]

    # response_format 指定 JSON 输出
    resp = chat(messages, response_format={"type": "json_object"})
    text = resp.choices[0].message.content or "{}"

    try:
        plan = json.loads(text)
        if not isinstance(plan, dict):
            raise json.JSONDecodeError("not an object", text, 0)
    except json.JSONDecodeError:
        # 兜底：解析失败就生成一个默认计划
        plan = {
            "tasks": [
                {"id": "task-1", "title": "搜索相关信息", "status": "pending", "note": ""},
                {"id": "task-2", "title": "整理分析", "status": "pending", "note": ""},
                {"id": "task-3", "title": "写报告", "status": "pending", "note": ""},
            ]
        }

    # 补充缺失字段
    if "tasks" not in plan:
        plan["tasks"] = []
    for task in plan["tasks"]:
        task.setdefault("status", "pending")
        task.setdefault("note", "")

    # 保存到文件
    task_dir.mkdir(parents=True, exist_ok=True)
    plan_path = task_dir / "plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    return plan


def load_plan(task_dir: Path) -> dict | None:
    """从任务目录加载 plan.json（如果存在）。"""
    plan_path = task_dir / "plan.json"
    if not plan_path.exists():
        return None
    try:
        return json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def format_plan_for_prompt(plan: dict) -> str:
    """把 plan 格式化成可以插入 system prompt 的文本。"""
    if not plan or "tasks" not in plan:
        return ""

    lines = ["当前计划："]
    for task in plan["tasks"]:
        status_icon = {
            "pending": "⏳",
            "in_progress": "🔄",
            "done": "✅",
            "skipped": "⏭️",
        }.get(task.get("status", "pending"), "?")

        title = task.get("title", "")
        note = task.get("note", "")
        line = f"{status_icon} {task.get('id', '')}: {title}"
        if note:
            line += f"（{note}）"
        lines.append(line)

    return "\n".join(lines)
