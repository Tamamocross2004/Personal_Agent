"""有界数值证据：只核对显式算式和 CSV 算术，不判断任意自然语言事实。"""

import csv
import ast
import io
import re
from decimal import Decimal, InvalidOperation, localcontext
from agent.markdown_tables import markdown_tables


def number(value: str) -> Decimal:
    result = Decimal(re.sub(r"[,\s\u202f\u00a0]", "", value))
    if not result.is_finite():
        raise ValueError("非有限数值")
    return result


def csv_facts(content: str) -> list[dict]:
    """只处理完整行，输出可复算的费用与比例，最多 50 行。"""
    reader = csv.DictReader(io.StringIO(content))
    facts = []
    for index, row in enumerate(reader):
        if index >= 50:
            break
        if not all(row.get(key) for key in ("model", "input_price", "output_price")):
            continue
        try:
            pi, po = number(row["input_price"]), number(row["output_price"])
            fact = {"model": row["model"], "input_price": str(pi), "output_price": str(po)}
            with localcontext() as ctx:
                ctx.prec = 16
                if pi:
                    fact["output_price_div_input_price"] = str(po / pi)
                if po:
                    fact["input_price_div_output_price"] = str(pi / po)
                if row.get("input_tokens") and row.get("output_tokens"):
                    ci = number(row["input_tokens"]) * pi / 1000000
                    co = number(row["output_tokens"]) * po / 1000000
                    fact.update(expected_input_cost=str(ci), expected_output_cost=str(co), expected_total_cost=str(ci + co))
                if facts:
                    base = facts[0]
                    fact["relative_to_model"] = base["model"]
                    if number(base["input_price"]):
                        fact["input_price_div_reference"] = str(pi / number(base["input_price"]))
                    if number(base["output_price"]):
                        fact["output_price_div_reference"] = str(po / number(base["output_price"]))
            facts.append(fact)
        except (InvalidOperation, ValueError, ArithmeticError):
            continue
    return facts


def price_ratio_errors(report: str, csv_content: str) -> list[str]:
    """核对已知型号明确的“X 的输入/输出单价为 Y 的 n 倍”陈述，非通用语义检查。"""
    prices = {}
    for row in csv_facts(csv_content):
        pair = (number(row["input_price"]), number(row["output_price"]))
        if row["model"] in prices and prices[row["model"]] != pair:
            return []
        prices[row["model"]] = pair
    if len(prices) < 2:
        return []
    names = '|'.join(re.escape(name) for name in sorted(prices, key=len, reverse=True))
    pattern = re.compile(rf"({names})\s*的?\s*(输入\s*[、及和与/]\s*输出|输入|输出)\s*单价\s*(?:均|都)?(?:为|是)\s*({names})\s*(?:对应单价)?\s*的?\s*(?:约\s*)?({_NUM})\s*倍")
    report = re.sub(r"(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1[^\n]*$", "", report)
    errors = []
    for line in report.splitlines():
        if line.lstrip().startswith('>') or re.search(r"历史摘要|曾称|旧说法|原先错误|错误示例|原错误", line):
            continue
        for match in pattern.finditer(line.replace('**', '').replace('`', '')):
            numerator, field, denominator, stated = match.groups()
            fields = [0, 1] if '输入' in field and '输出' in field else [0 if field == '输入' else 1]
            for index in fields:
                try:
                    actual = prices[numerator][index] / prices[denominator][index]
                    claimed = number(stated)
                    tolerance = Decimal(5).scaleb(claimed.as_tuple().exponent - 1)
                    if abs(actual - claimed) > tolerance:
                        errors.append(f"「{match[0]}」与 CSV 不一致：{numerator}/{denominator} 的{'输入' if index == 0 else '输出'}单价倍数为 {actual}。")
                except ArithmeticError:
                    errors.append(f"「{match[0]}」的单价倍数无法计算。")
    return errors[:20]


_NUM = r"(?:\d{1,3}(?:[, \u202f\u00a0]\d{3})+|\d+)(?:\.\d+)?"
_ATOM = rf"\(*\s*[+-]?{_NUM}\s*\)*"
_EQUATION = re.compile(rf"(?<![\w.,/÷×*+−-])({_ATOM}(?:\s*[÷/×*+−-]\s*{_ATOM})+)\s*(?:=|≈|≃|＝)\s*(?:约\s*)?({_NUM})(\s*%)?")


def _calculate(expression: str) -> Decimal:
    expression = expression.strip().translate(str.maketrans({'÷': '/', '×': '*', '−': '-'}))
    expression = re.sub(_NUM, lambda m: re.sub(r"[,\s\u202f\u00a0]", "", m[0]), expression)
    if len(expression) > 500:
        raise ValueError("算式过长")
    tree = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ValueError("算式过于复杂")

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return number(ast.get_source_segment(expression, node))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return visit(node.operand) if isinstance(node.op, ast.UAdd) else -visit(node.operand)
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
        raise ValueError("不支持的算式")

    return visit(tree.body)


def arithmetic_errors(report: str) -> list[str]:
    """检查显式四则算式；允许按结果末位四舍五入。代码块及引述不作报告断言。"""
    text = re.sub(r"(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1[^\n]*$", "", report)
    errors = []
    for line in text.splitlines():
        if line.lstrip().startswith(">"):
            continue
        line = re.sub(r"`[^`]*`", "", line).replace('**', '')
        for match in _EQUATION.finditer(line):
            expression, claimed, percent = match.groups()
            if re.search(r"[÷/×*+−-]\s*$", line[:match.start()]):
                continue  # 未识别完整左式时不可仅核对后缀。
            try:
                stated = number(claimed)
                with localcontext() as ctx:
                    ctx.prec = 28
                    actual = _calculate(expression)
                    tolerance = Decimal(5).scaleb(stated.as_tuple().exponent - 1)
                    if percent:
                        stated, tolerance = stated / 100, tolerance / 100
                    if abs(actual - stated) > tolerance:
                        errors.append(f"算式「{match[0].strip()}」不成立；计算值为 {actual}。")
            except (InvalidOperation, ValueError, ArithmeticError, SyntaxError):
                errors.append(f"算式「{match[0].strip()}」无法完成有效计算。")
    return errors[:20]


def report_cost_errors(report: str, csv_content: str) -> list[str]:
    """规定的主费用表须与当前 CSV 一致；历史对比应使用 history_* 等明确列名。"""
    fields = ("model", "input_cost", "output_cost", "total_cost")
    reader = csv.DictReader(io.StringIO(csv_content))
    if not set(fields).issubset(reader.fieldnames or []):
        return []
    expected = {}
    try:
        for index, row in enumerate(reader):
            if index >= 50 or row["model"] in expected:
                return []  # 多场景/重复模型 CSV，不能假定型号为唯一键。
            expected[row["model"]] = tuple(number(row[key]) for key in fields[1:])
    except (InvalidOperation, ValueError, KeyError, TypeError):
        return []
    errors = []
    for header, rows in markdown_tables(report):
        if header != fields:
            continue
        for cells in rows:
            if cells[0] not in expected:
                continue
            try:
                amounts = [re.fullmatch(rf"(?:¥|￥)?\s*({_NUM})\s*(?:元|CNY)?", cell, re.I) for cell in cells[1:]]
                if not all(amounts):
                    raise ValueError("无法解析金额")
                values = tuple(number(amount[1]) for amount in amounts)
                if values != expected[cells[0]]:
                    errors.append(f"主费用表 {cells[0]} 的分项/总额与当前 costs.csv 不一致；历史数据请用明确不同的列名，不要混入本次主表。")
            except (InvalidOperation, ValueError):
                errors.append(f"主费用表 {cells[0]} 的金额不是可核对数字。")
    return errors[:20]
