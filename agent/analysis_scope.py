"""已知统计方法的有限范围检查；不认证任意代码或自然语言的全部语义。"""

import ast
import re

from markdown_it import MarkdownIt
from agent.markdown_tables import markdown_tables

METHODS = {
    'Wilson': r'(?<![a-z])wilson(?![a-z])',
    'Wald': r'(?<![a-z])wald(?![a-z])',
    'Clopper-Pearson': r'clopper[\s_–—-]*pearson',
    'Agresti-Coull': r'agresti[\s_–—-]*coull',
    'Fisher': r'(?<![a-z])fisher(?![a-z])',
}


def named_methods(text: str) -> tuple[str, ...]:
    return tuple(name for name, pattern in METHODS.items() if re.search(pattern, text, re.I))


def requested_methods(instructions: str) -> tuple[str, ...]:
    """只提取明确计算/使用/比较指令中的已知方法，不把材料提及当作授权。"""
    requested = []
    for clause in re.split(r'[。；;\n]', instructions):
        if re.search(r'更正|改为|现在只', clause):
            requested = []
        for name, pattern in METHODS.items():
            for match in re.finditer(pattern, clause, re.I):
                prefix = clause[:match.start()]
                local = re.split(r'[，,]', prefix)[-1]
                if re.search(r'不要|不做|禁止|无需|不计算|不使用|不采用|不要求|不必', local):
                    continue
                if re.search(r'(?:计算|求出|估计|构造|使用|采用|比较|对比|请用|请做|分析)[^。；;]{0,45}$', prefix):
                    requested.append(name)
    return tuple(dict.fromkeys(requested))


def code_scope_errors(code: str, allowed: tuple[str, ...]) -> list[str]:
    if not allowed:
        return []
    try:
        tree = ast.parse(code)
    except (SyntaxError, RecursionError):
        return []  # 原有执行器反馈语法错误。
    identifiers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            arguments = [*node.args, *(k.value for k in node.keywords if k.arg not in ('sep','end'))]
            if any(not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)) for arg in arguments):
                for arg in arguments:
                    for part in ast.walk(arg):
                        if isinstance(part, ast.Constant) and isinstance(part.value, str):
                            # 有计算值的输出标签也可能暴露额外方法；纯概念文本与明确未计算的说明保留。
                            if not re.search(r'未计算|不计算|未采用|不采用|未使用|不使用|not[ _](?:computed|calculated|used)', part.value, re.I):
                                identifiers.append(part.value)
        if isinstance(node, ast.Name):
            identifiers.append(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            identifiers.append(node.name)
        elif isinstance(node, ast.Attribute):
            identifiers.append(node.attr)
        elif isinstance(node, ast.alias):
            identifiers.extend([node.name, node.asname or ''])
    extra = set(named_methods(' '.join(identifiers))) - set(allowed)
    return [f'代码包含未要求的方法 {name}；本次仅计算 {"、".join(allowed)}，删除额外实现，保留必要公式与边界。'
            for name in sorted(extra)]


def report_scope_errors(report: str, allowed: tuple[str, ...]) -> list[str]:
    """只拒绝已命名的额外区间/检验结果，不因方法名或否定说明本身拒绝。"""
    if not allowed:
        return []
    errors = []
    numeric_pair = r'[\[（(]\s*[-+]?\d+(?:\.\d+)?\s*[%％]?\s*[,，至到]\s*[-+]?\d'
    for token in MarkdownIt('commonmark').enable('table').parse(report):
        if token.type != 'inline':
            continue
        for sentence in re.split(r'[。；;\n]', token.content):
            for name in set(named_methods(sentence)) - set(allowed):
                method = re.search(METHODS[name], sentence, re.I)
                after = sentence[method.end():]
                # 不把同句后面属于另一方法的结果归到此处。
                starts = [m.start() for pattern in METHODS.values() for m in re.finditer(pattern, after, re.I)]
                after = after[:min(starts)] if starts else after
                if re.search(numeric_pair, after) or re.search(r'(?:区间|下限|上限|检验结果|p\s*)\s*(?:为|是|[=＝<>])\s*[-+]?\d', after, re.I):
                    errors.append(f'未要求的 {name} 数值结果：{sentence.strip()[:250]}')
    for header, rows in markdown_tables(report):
        if not any(re.search(r'方法|区间|method|lower|upper|下限|上限', cell, re.I) for cell in header):
            continue
        for row in rows:
            extra = set(named_methods(' '.join(row))) - set(allowed)
            if extra and (re.search(numeric_pair, ' '.join(row)) or sum(bool(re.fullmatch(r'\s*[-+]?\d+(?:\.\d+)?\s*[%％]?\s*', cell)) for cell in row) >= 2):
                errors.append('未要求的方法结果表：' + ' | '.join(row)[:250])
    return list(dict.fromkeys(errors))[:12]
