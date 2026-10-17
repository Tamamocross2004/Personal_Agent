"""阶段 1 的基础工具：都是模型"自己做不好"的事。

- 模型不知道现在几点（训练数据有截止日期）→ get_current_time
- 模型心算容易出错 → calculator
- 日期差要考虑大小月、闰年 → days_between
"""

from __future__ import annotations

import ast
import math
import operator
from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field

from tools.registry import tool

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


@tool
def get_current_time(
    timezone: Annotated[
        str, Field(description="IANA 时区名，例如 Asia/Shanghai、America/New_York、UTC")
    ] = "Asia/Shanghai",
) -> str:
    """获取当前的日期和时间（含星期）。凡是涉及"今天""现在""还有多少天"等问题，必须先调用本工具，不要凭记忆猜测日期。"""
    try:
        tz = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"未知时区 {timezone!r}，请使用 IANA 时区名，如 Asia/Shanghai") from e
    now = datetime.now(tz)
    offset = now.strftime("%z")
    return (
        f"{now:%Y-%m-%d %H:%M:%S} {WEEKDAYS[now.weekday()]}"
        f"（{timezone}，UTC{offset[:3]}:{offset[3:]}）"
    )


@tool
def days_between(
    start_date: Annotated[str, Field(description="起始日期，格式 YYYY-MM-DD")],
    end_date: Annotated[str, Field(description="结束日期，格式 YYYY-MM-DD")],
) -> str:
    """计算两个日期之间相差的天数（end_date - start_date，可能为负数）。"""
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError as e:
        raise ValueError("日期格式应为 YYYY-MM-DD，例如 2027-01-01") from e
    return f"从 {start} 到 {end} 相差 {(end - start).days} 天"


# ---------------------------------------------------------------------------
# 安全计算器
#
# 千万不要用 eval(expression)：模型传来的字符串可以是 __import__('os').system('...')。
# 来自模型的参数要当作"不可信输入"——尤其到了后面阶段，模型会读网页，网页内容可能诱导它。
# 这里先用 ast 把表达式解析成语法树，只放行数字、四则运算和白名单里的数学函数。
# ---------------------------------------------------------------------------

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "exp": math.exp,
    "log": math.log,  # log(x) 自然对数；log(x, base)
    "log10": math.log10,
    "log2": math.log2,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "ceil": math.ceil,
    "floor": math.floor,
}
_CONSTS = {"pi": math.pi, "e": math.e}

_MAX_EXPR_LEN = 500
_MAX_RESULT_BITS = 100_000  # 防止 9**9**9 这类表达式把 CPU 和内存吃光


def _eval_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    if isinstance(node, ast.Constant):
        # bool 是 int 的子类，要单独排除
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ValueError(f"不支持的常量：{node.value!r}")

    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise ValueError(f"未知名称 {node.id!r}。可用常量：{', '.join(_CONSTS)}")

    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))

    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow):
            _check_pow(left, right)
        return _BIN_OPS[type(node.op)](left, right)

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ValueError(f"不支持的函数。可用函数：{', '.join(_FUNCS)}")
        if node.keywords:
            raise ValueError("函数不支持关键字参数")
        return _FUNCS[node.func.id](*(_eval_node(a) for a in node.args))

    raise ValueError(f"不支持的语法：{type(node).__name__}")


def _check_pow(base: int | float, exp: int | float) -> None:
    if isinstance(base, int) and isinstance(exp, int) and exp > 0:
        if max(abs(base), 2).bit_length() * exp > _MAX_RESULT_BITS:
            raise ValueError("结果过大，拒绝计算")
    elif abs(exp) > 10_000:
        raise ValueError("指数过大，拒绝计算")


@tool
def calculator(
    expression: Annotated[
        str,
        Field(description="数学表达式，例如 (75 * 3) / 7、sqrt(2) ** 3、log(100, 10)"),
    ],
) -> str:
    """精确计算数学表达式。支持 + - * / // % ** 和括号，函数 abs round min max sqrt exp log log10 log2 sin cos tan ceil floor，常量 pi e。任何数值计算都应使用本工具，不要心算。"""
    expr = expression.strip()
    if len(expr) > _MAX_EXPR_LEN:
        raise ValueError(f"表达式过长（超过 {_MAX_EXPR_LEN} 字符）")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"表达式语法错误：{e.msg}") from e
    result = _eval_node(tree)
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        result = int(result)
    return f"{expr} = {result}"


BASIC_TOOLS = [get_current_time, days_between, calculator]
