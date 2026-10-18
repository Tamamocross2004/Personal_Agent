"""统一使用 Markdown 语法解析正文表格，代码块和转义竖线由解析器处理。"""

from markdown_it import MarkdownIt


def markdown_tables(text: str) -> list[tuple[tuple[str, ...], list[tuple[str, ...]]]]:
    tables = []
    header, rows, cells = (), [], []
    in_head = False
    for token in MarkdownIt("commonmark").enable("table").parse(text):
        if token.type == "table_open":
            header, rows = (), []
        elif token.type == "thead_open":
            in_head = True
        elif token.type == "thead_close":
            in_head = False
        elif token.type == "tr_open":
            cells = []
        elif token.type == "inline" and token.level > 0:
            # Formatting is irrelevant to field identity; retain literal code and escaped text.
            cells.append("".join(child.content for child in token.children or []
                                 if child.type in ("text", "code_inline")).strip())
        elif token.type == "tr_close":
            if in_head:
                header = tuple(cells)
            elif header:
                rows.append(tuple(cells))
        elif token.type == "table_close":
            tables.append((header, rows))
            header = ()
    return tables
