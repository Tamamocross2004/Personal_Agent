from typing import Annotated

import pytest
from pydantic import Field

from tools.registry import Tool, ToolRegistry, tool


@tool
def add(
    a: Annotated[int, Field(description="第一个数")],
    b: int = 1,
) -> int:
    """两数相加。"""
    return a + b


@tool(name="search_title")
def with_title_param(title: str) -> dict:
    """参数名恰好叫 title，用来检查 schema 清理时不会被误删。"""
    return {"title": title, "中文": True}


@tool
def no_args() -> None:
    """无参数、无返回值。"""


@tool
def boom(x: int) -> str:
    """总是抛异常。"""
    raise RuntimeError(f"炸了 {x}")


@pytest.fixture
def reg() -> ToolRegistry:
    return ToolRegistry([add, with_title_param, no_args, boom])


# ---------- schema 生成 ----------


def test_schema_shape(reg):
    s = add.schema()
    assert s["type"] == "function"
    fn = s["function"]
    assert fn["name"] == "add"
    assert fn["description"] == "两数相加。"
    p = fn["parameters"]
    assert p["type"] == "object"
    assert p["required"] == ["a"]  # b 有默认值，不是必填
    assert p["properties"]["a"] == {"type": "integer", "description": "第一个数"}
    assert p["properties"]["b"]["default"] == 1
    assert p["additionalProperties"] is False
    assert "title" not in p and "title" not in p["properties"]["a"]


def test_param_named_title_is_kept():
    props = with_title_param.schema()["function"]["parameters"]["properties"]
    assert "title" in props
    assert props["title"] == {"type": "string"}


def test_no_arg_tool_has_empty_properties():
    p = no_args.schema()["function"]["parameters"]
    assert p["properties"] == {}
    assert "required" not in p


def test_decorated_function_still_callable():
    assert add(2, 3) == 5


def test_missing_docstring_rejected():
    with pytest.raises(ValueError, match="缺少描述"):

        @tool
        def nodoc(x: int) -> int:
            return x


def test_missing_annotation_rejected():
    with pytest.raises(TypeError, match="类型标注"):

        @tool
        def noann(x):
            """doc"""


def test_duplicate_and_non_tool_registration():
    r = ToolRegistry([add])
    with pytest.raises(ValueError, match="重复"):
        r.register(add)
    with pytest.raises(TypeError, match="@tool"):
        r.register(lambda: 1)  # type: ignore[arg-type]


# ---------- 执行 ----------


def test_execute_success(reg):
    r = reg.execute("add", '{"a": 2, "b": 40}')
    assert (r.content, r.is_error) == ("42", False)


def test_execute_uses_default_and_coerces(reg):
    # pydantic 默认宽松模式："5" 会被转成 5（模型偶尔会把数字写成字符串）
    assert reg.execute("add", '{"a": "5"}').content == "6"


def test_execute_accepts_dict(reg):
    assert reg.execute("add", {"a": 1}).content == "2"


@pytest.mark.parametrize("raw", ["", "  ", None, "{}"])
def test_execute_empty_arguments_for_no_arg_tool(reg, raw):
    r = reg.execute("no_args", raw)
    assert not r.is_error
    assert "无返回值" in r.content


def test_non_string_result_is_json_with_unicode(reg):
    r = reg.execute("search_title", '{"title": "大模型"}')
    assert r.content == '{"title": "大模型", "中文": true}'


@pytest.mark.parametrize(
    "name,args,expect",
    [
        ("nope", "{}", "不存在名为 'nope' 的工具"),
        ("add", "{bad json", "不是合法的 JSON"),
        ("add", "[1, 2]", "必须是 JSON 对象"),
        ("add", "{}", "a: Field required"),
        ("add", '{"a": "abc"}', "a: Input should be a valid integer"),
        ("add", '{"a": 1, "c": 2}', "c: Extra inputs are not permitted"),
        ("boom", '{"x": 7}', "RuntimeError: 炸了 7"),
    ],
)
def test_errors_become_results_not_exceptions(reg, name, args, expect):
    r = reg.execute(name, args)
    assert r.is_error
    assert r.content.startswith("ERROR")
    assert expect in r.content


def test_unknown_tool_lists_available(reg):
    r = reg.execute("nope", "{}")
    assert "add" in r.content and "boom" in r.content


def test_tool_object_type():
    assert isinstance(add, Tool)
    assert ToolRegistry([add]).names == ["add"]
