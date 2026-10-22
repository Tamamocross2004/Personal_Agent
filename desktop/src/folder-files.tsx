import React, { useState } from "react";

export function FileFilter({ rows, children, select, selected = [] }: any) {
  const [query, setQuery] = useState(""),
    [format, setFormat] = useState(""),
    [status, setStatus] = useState("");
  const visible = rows.filter(
    (f: any) =>
      (f.name || f.relative_path).toLowerCase().includes(query.toLowerCase()) &&
      (!format || f.suffix === format) &&
      (!status || f.status === status),
  );
  return (
    <div className="folder-file-filter">
      <div className="knowledge-actions">
        <input
          aria-label="搜索文件名"
          placeholder="搜索文件名或相对路径"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select
          aria-label="按文件格式筛选"
          value={format}
          onChange={(e) => setFormat(e.target.value)}
        >
          <option value="">全部格式</option>
          {Array.from(
            new Set<string>(
              rows.map((f: any) => f.suffix || "").filter(Boolean),
            ),
          )
            .sort()
            .map((s) => (
              <option key={s}>{s}</option>
            ))}
        </select>
        <select
          aria-label="按文件状态筛选"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="">全部状态</option>
          {Array.from(new Set<string>(rows.map((f: any) => f.status)))
            .sort()
            .map((s) => (
              <option key={s} value={s}>
                {(
                  {
                    ready: "可读取",
                    excluded: "已排除",
                    unsupported: "暂不支持",
                    unreadable: "不可读取",
                    over_limit: "超出限制",
                    unchanged: "与扫描一致",
                    modified: "扫描后待同步",
                    missing: "最近扫描未包含",
                  } as any
                )[s] || s}
              </option>
            ))}
        </select>
        {select && (
          <>
            <button
              type="button"
              onClick={() =>
                select(
                  Array.from(
                    new Set([
                      ...selected,
                      ...visible.map((f: any) => f.file_id),
                    ]),
                  ),
                )
              }
            >
              选择筛选结果
            </button>
            <button type="button" onClick={() => select([])}>
              清空选择
            </button>
          </>
        )}
      </div>
      <p>
        显示 {visible.length} / {rows.length} 项
        {select &&
          `；已选 ${selected.length} 项（含筛选后隐藏的文件，筛选不改变提问范围）`}
      </p>
      {select && (
        <p>
          本次所选：
          {rows
            .filter((f: any) => selected.includes(f.file_id))
            .map((f: any) => f.name)
            .join("、") || "尚未选择"}
        </p>
      )}
      {children(visible)}
    </div>
  );
}
