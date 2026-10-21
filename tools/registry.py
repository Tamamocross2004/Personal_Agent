"""工具注册表。

模型无法直接调用 Python 函数，它只能看到一份 JSON Schema 形式的"工具说明书"，
然后在回复里写出"我要调用 xx，参数是 {...}"。注册表负责两件事：

1. 函数 → 说明书：根据函数签名和类型标注，自动生成 OpenAI tools 格式的 JSON Schema；
2. 调用请求 → 执行：解析模型给的 JSON 参数、用 pydantic 校验、执行函数。

关键设计：execute() 永远不抛异常，而是返回 ToolResult。
出错信息会作为工具结果回传给模型，让模型自己修正参数或换个办法 —— 这是 Agent 健壮性的基础。

用法：
    @tool
    def add(a: Annotated[int, Field(description="加数")], b: int) -> int:
        '''两数相加。'''
        return a + b

    registry = ToolRegistry([add])
    registry.schemas()                      # 发给模型
    registry.execute("add", '{"a":1,"b":2}')  # ToolResult(content='3', is_error=False)
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable, get_type_hints

from pydantic import BaseModel, ConfigDict, ValidationError, create_model
from agent.report_validation import ReportValidationError


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    evidence: list[dict] | None = None  # 工具生成的结构元数据，不从网页正文解析指令。
    usage: list[dict] | None = None  # 适配器生成的嵌入/重排用量，不从不可信正文解析。
    validation: dict | None = None  # 本地校验器生成的有界反馈，不从模型/网页文字推断。


class Tool:
    """一个可被模型调用的工具：名字 + 描述 + 参数模型 + 实际函数。"""

    def __init__(
        self,
        name: str,
        description: str,
        func: Callable[..., Any],
        args_model: type[BaseModel],
    ):
        self.name = name
        self.description = description
        self.func = func
        self.args_model = args_model

    @classmethod
    def from_function(
        cls,
        func: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
    ) -> Tool:
        tool_name = name or func.__name__
        desc = description or inspect.getdoc(func)
        if not desc:
            # 描述是模型判断"什么时候该用这个工具"的唯一依据，必须写
            raise ValueError(f"工具 {tool_name} 缺少描述（请写 docstring）")

        sig = inspect.signature(func)
        hints = get_type_hints(func, include_extras=True)  # include_extras 保留 Annotated 里的 Field
        fields: dict[str, Any] = {}
        for pname, p in sig.parameters.items():
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                raise TypeError(f"工具 {tool_name} 不支持 *args / **kwargs 参数")
            if pname not in hints:
                raise TypeError(f"工具 {tool_name} 的参数 {pname} 缺少类型标注")
            default = ... if p.default is p.empty else p.default  # ... 表示必填
            fields[pname] = (hints[pname], default)

        args_model = create_model(
            f"{tool_name}_args",
            __config__=ConfigDict(extra="forbid"),  # 模型多传了参数也要报错提醒
            **fields,
        )
        return cls(tool_name, desc, func, args_model)

    def schema(self) -> dict[str, Any]:
        """OpenAI tools 格式（各国产模型的兼容接口都使用这个格式）。"""
        params = _strip_titles(self.args_model.model_json_schema())
        params.setdefault("properties", {})
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": params,
            },
        }

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # 让被 @tool 装饰的函数仍可以像普通函数一样直接调用（方便测试）
        return self.func(*args, **kwargs)

    def __repr__(self) -> str:
        return f"Tool({self.name!r})"


def tool(
    func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Any:
    """装饰器：把普通函数变成 Tool。支持 @tool 和 @tool(name=...) 两种写法。"""

    def wrap(f: Callable[..., Any]) -> Tool:
        return Tool.from_function(f, name=name, description=description)

    return wrap(func) if func is not None else wrap


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, t: Tool) -> None:
        if not isinstance(t, Tool):
            raise TypeError(f"{t!r} 不是 Tool，是否忘了加 @tool 装饰器？")
        if t.name in self._tools:
            raise ValueError(f"工具名重复：{t.name}")
        self._tools[t.name] = t

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def execute(self, name: str, arguments: str | dict[str, Any] | None) -> ToolResult:
        """执行一次工具调用。任何错误都转成 ToolResult(is_error=True)，绝不抛出。"""
        t = self._tools.get(name)
        if t is None:
            return ToolResult(
                f"ERROR: 不存在名为 {name!r} 的工具。可用工具：{', '.join(self.names)}", True
            )

        # 1. 解析参数：模型给的是 JSON 字符串；无参工具有的厂商会给 "" 或 None
        if isinstance(arguments, dict):
            args = arguments
        else:
            try:
                args = json.loads(arguments) if arguments and arguments.strip() else {}
            except json.JSONDecodeError as e:
                return ToolResult(f"ERROR: 参数不是合法的 JSON（{e}）。请修正后重新调用。", True)
        if not isinstance(args, dict):
            return ToolResult("ERROR: 参数必须是 JSON 对象，例如 {\"x\": 1}。", True)

        # 2. 校验参数：类型、必填项、多余字段
        try:
            validated = t.args_model.model_validate(args)
        except ValidationError as e:
            feedback = f"ERROR: 参数校验失败：{_format_validation_error(e)}"
            if name == 'write_report':
                feedback += '\n引用层级：claims:[{quote:当前稿原句,kind:结论类型,supports:[{source_id:真实ID,excerpt:实际原文}]}]。不自动纠正ID或补写摘录；可用read_evidence读取support。'
            return ToolResult(feedback, True)

        # 3. 执行
        kwargs = {k: getattr(validated, k) for k in type(validated).model_fields}
        try:
            result = t.func(**kwargs)
        except ReportValidationError as e:
            return ToolResult(f'ERROR: {type(e).__name__}: {e}',True,validation=e.feedback)
        except Exception as e:  # noqa: BLE001 —— 工具内部的任何异常都要交给模型处理
            return ToolResult(f"ERROR: {type(e).__name__}: {e}", True)

        return result if isinstance(result, ToolResult) else ToolResult(_to_text(result))


def _to_text(result: Any) -> str:
    if result is None:
        return "（工具执行成功，无返回值）"
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)


def _format_validation_error(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "(参数整体)"
        parts.append(f"{loc}: {err['msg']}")
    return "；".join(parts)


def _strip_titles(obj: Any) -> Any:
    """去掉 pydantic 自动生成的 title 字段：对模型没用，只浪费 token。

    注意不能误删名为 "title" 的参数，所以 properties 下的键要原样保留。
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "title" and isinstance(v, str):
                continue
            if k in ("properties", "$defs") and isinstance(v, dict):
                out[k] = {pk: _strip_titles(pv) for pk, pv in v.items()}
            else:
                out[k] = _strip_titles(v)
        return out
    if isinstance(obj, list):
        return [_strip_titles(x) for x in obj]
    return obj
