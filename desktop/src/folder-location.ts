export function locationLabel(l: any): string {
  if (l.kind === "spreadsheet_row")
    return `${l.sheet} 第${l.row}行（${l.cells.join("、")}）`;
  if (l.kind === "table_row")
    return `表格${l.table} 第${l.row}行（列${l.columns.join("、")}）`;
  if (l.kind === "paragraph") return `正文段落${l.paragraph}`;
  if (l.kind === "table_cell") return `表格${l.table} 行${l.row} 列${l.column}`;
  if (l.kind === "spreadsheet_cell") return `${l.sheet}!${l.cell}`;
  if (l.kind === "csv_record")
    return `记录${l.record} · 物理行${l.physical_line_start}—${l.physical_line_end}`;
  return `${l.page ? "第" + l.page + "页 · " : ""}第${l.line}行 · 字符${l.start}—${l.end}`;
}
