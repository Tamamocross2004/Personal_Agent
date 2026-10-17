import re

import pytest

from tools import default_registry
from tools.basic import calculator, days_between, get_current_time


# ---------- calculator ----------


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("1 + 2 * 3", "7"),
        ("(1 + 2) * 3", "9"),
        ("7 / 2", "3.5"),
        ("8 / 2", "4"),  # 整数结果的浮点数显示为整数
        ("7 // 2", "3"),
        ("7 % 3", "1"),
        ("2 ** 10", "1024"),
        ("-3 + +5", "2"),
        ("sqrt(16)", "4"),
        ("log(100, 10)", "2"),
        ("max(3, 9, 4)", "9"),
        ("round(3.14159, 2)", "3.14"),
        ("floor(pi)", "3"),
        ("76 * 3", "228"),
    ],
)
def test_calculator_ok(expr, expected):
    assert calculator(expr) == f"{expr} = {expected}"


@pytest.mark.parametrize(
    "expr,msg",
    [
        ("__import__('os').system('echo hacked')", "不支持的函数"),
        ("open('x')", "不支持的函数"),
        ("(1).__class__", "不支持的语法"),
        ("x + 1", "未知名称"),
        ("'a' * 3", "不支持的常量"),
        ("True + 1", "不支持的常量"),
        ("[1, 2]", "不支持的语法"),
        ("lambda: 1", "不支持的语法"),
        ("9 ** 9 ** 9", "过大"),
        ("2 ** 1e6", "过大"),
        ("1 +", "语法错误"),
        ("1" * 600, "过长"),
        ("round(1.5, ndigits=1)", "关键字参数"),
    ],
)
def test_calculator_rejects(expr, msg):
    with pytest.raises(ValueError, match=msg):
        calculator(expr)


def test_calculator_division_by_zero_surfaces_through_registry():
    r = default_registry().execute("calculator", '{"expression": "1/0"}')
    assert r.is_error and "ZeroDivisionError" in r.content


# ---------- days_between ----------


def test_days_between():
    assert days_between("2026-10-17", "2027-01-01") == "从 2026-10-17 到 2027-01-01 相差 76 天"
    assert days_between("2024-03-01", "2024-02-28").endswith("-2 天")  # 闰年 + 负数


def test_days_between_bad_format():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        days_between("2027/01/01", "2027-01-02")


# ---------- get_current_time ----------


def test_get_current_time_format():
    out = get_current_time("Asia/Shanghai")
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} 星期[一二三四五六日]（Asia/Shanghai，UTC\+08:00）", out
    )


def test_get_current_time_utc_and_default():
    assert "UTC+00:00" in get_current_time("UTC")
    assert "Asia/Shanghai" in get_current_time()


def test_get_current_time_unknown_zone():
    with pytest.raises(ValueError, match="未知时区"):
        get_current_time("Mars/Olympus")


def test_default_registry_contents():
    assert default_registry().names == ["get_current_time", "days_between", "calculator"]
