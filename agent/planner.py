"""计划生成：在 Agent 开始工作前，先单独调用一次 LLM 把问题拆解成 3-6 个子问题（报告要回答的方面）。

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
import re
from pathlib import Path

MAX_TASKS = 6

# 阶段 5 实测：旧提示词的示例是"搜索 X → 读 Y 报告"这类流程步骤，模型照着拆出了
# "搜索 → 收集文档 → 找评测 → 分析 → 写报告"，和报告结构对不上。
# 现在要求按"报告要回答的方面"拆，示例刻意选了与研究问题无关的领域，避免模型照抄。
PLANNER_PROMPT = """你是一名研究助手。今天是 {today}。
用户会给你一个研究问题。请把它拆解成 3-6 个子问题：每个子问题是最终报告要回答的一个**方面 / 维度**，
将来对应报告里的一节。

要求：
- 按"要回答什么"拆，不要按"怎么做"拆。搜索、阅读、整理、写报告是每个子问题都要做的事，不要单独列为子任务。
- 子问题之间尽量不重叠，合起来能完整回答用户的问题。
- 标题具体到能直接拿来搜索，写清楚要比较或查明的点。
- 问题涉及"最新""当前"或具体产品时，第一个子问题先确认研究对象（例如当前最新版本是什么），
  不要凭记忆假定——你的知识可能已经过时。

示例（问题：“对比 FastAPI、Django 和 Flask，哪个适合做中小型 API 服务”）：
{{
  "tasks": [
    {{"id": "task-1", "title": "三个框架的当前版本与定位（同步/异步、全栈/微框架）"}},
    {{"id": "task-2", "title": "性能：公开基准测试中的吞吐量与延迟"}},
    {{"id": "task-3", "title": "开发效率：数据校验、自动文档、ORM 等内置能力"}},
    {{"id": "task-4", "title": "生态与社区：插件、维护活跃度、学习资料"}},
    {{"id": "task-5", "title": "部署与运维：并发模型、生产环境注意事项"}}
  ]
}}

只输出 JSON，格式同示例：id 依次为 task-1、task-2……，title 一句话。

用户问题：
"""

# 即使提示词里说了，模型仍可能把"写报告"列成子任务；在代码里再过滤一次
_PROCESS_ONLY = re.compile(r"^(撰写|编写|写|输出|生成|整理).{0,10}(报告|总结)")

DEFAULT_TASKS = [
    {"id": "task-1", "title": "确认研究对象与背景"},
    {"id": "task-2", "title": "关键事实与数据"},
    {"id": "task-3", "title": "不同来源的观点、争议与不确定之处"},
]


def generate_plan(question: str, chat, task_dir: Path, today: str = "") -> dict:
    """调用 LLM 生成计划，保存到 task_dir/plan.json。

    chat: 可调用对象 chat(messages, **kwargs) -> ChatCompletion。由调用方传入，
    这样规划调用能计入 Agent 的 token 预算和 trace，而不是绕过它们。
    today: 今天的日期。不告诉模型日期，它会按训练数据猜"最新"的产品（阶段 5 实测拆出了"文心一言"）。
    """
    prompt = PLANNER_PROMPT.format(today=today or "（未知）")
    messages = [{"role": "user", "content": prompt + question}]

    # response_format 指定 JSON 输出
    resp = chat(messages, response_format={"type": "json_object"})
    text = resp.choices[0].message.content or "{}"

    try:
        plan = json.loads(text)
        if not isinstance(plan, dict):
            raise json.JSONDecodeError("not an object", text, 0)
    except json.JSONDecodeError:
        plan = {}
    plan["tasks"] = normalize_tasks(plan.get("tasks"))

    # 保存到文件
    task_dir.mkdir(parents=True, exist_ok=True)
    plan_path = task_dir / "plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    return plan


def normalize_tasks(tasks) -> list[dict]:
    """整理模型给出的子任务：去掉无标题的和纯流程的（写报告），最多 6 个，重新编号，补齐字段。

    全部被过滤掉（或解析失败）时用默认计划兜底。
    """
    out = []
    for t in tasks if isinstance(tasks, list) else []:
        if not isinstance(t, dict):
            continue
        title = str(t.get("title", "")).strip()
        if not title or _PROCESS_ONLY.match(title):
            continue
        out.append(title)
    titles = out[:MAX_TASKS] or [t["title"] for t in DEFAULT_TASKS]
    return [
        {"id": f"task-{i}", "title": title, "status": "pending", "note": ""} for i, title in enumerate(titles, 1)
    ]


def unfinished_tasks(plan: dict | None) -> list[dict]:
    """计划中尚未完成（pending / in_progress）的子任务。"""
    if not plan:
        return []
    return [t for t in plan.get("tasks", []) if t.get("status", "pending") in ("pending", "in_progress")]


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
