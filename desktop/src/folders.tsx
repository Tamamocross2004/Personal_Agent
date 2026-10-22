import React, { useEffect, useRef, useState } from "react";
import { FolderIndexPanel } from "./folder-index";

export function FolderLibrary({
  api,
  onClose,
}: {
  api: (path: string, options?: RequestInit) => Promise<any>;
  onClose: () => void;
}) {
  const [folders, setFolders] = useState<any[]>([]),
    [selected, setSelected] = useState("");
  const [policy, setPolicy] = useState<any>(null),
    [scan, setScan] = useState<any>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [preview, setPreview] = useState<any>(null),
    [parseStates, setParseStates] = useState<Record<string, string>>({});
  const busyGuard = useRef(false),
    previewTurn = useRef(0);
  const alive = useRef(true),
    selection = useRef(0);
  useEffect(() => {
    alive.current = true;
    api("/folders")
      .then((v) => {
        if (alive.current) {
          setFolders(v.folders);
          setPolicy(v.policy);
        }
      })
      .catch((e) => {
        if (alive.current) setError(String(e));
      });
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", escape);
    return () => {
      alive.current = false;
      selection.current++;
      previewTurn.current++;
      window.removeEventListener("keydown", escape);
    };
  }, []);
  const action = async (work: () => Promise<any>) => {
    if (busyGuard.current) return;
    busyGuard.current = true;
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      if (alive.current) setError(String(e));
    } finally {
      busyGuard.current = false;
      if (alive.current) setBusy(false);
    }
  };
  const choose = async (id: string) => {
    const turn = ++selection.current;
    setSelected(id);
    setScan(null);
    setPreview(null);
    setParseStates({});
    previewTurn.current++;
    setError("");
    try {
      const result = await api("/folders/" + id + "/scan");
      if (alive.current && turn === selection.current) setScan(result);
    } catch (e) {
      if (alive.current && turn === selection.current) setError(String(e));
    }
  };
  const openPreview = async (file: any, offset = 0) => {
    const turn = ++previewTurn.current;
    setPreview(null);
    const result = await api(
      `/folders/${selected}/files/${file.id}?scan_id=${scan.id}&offset=${offset}`,
    );
    if (alive.current && turn === previewTurn.current) {
      setPreview({
        ...result,
        relative_path: file.relative_path,
        file_id: file.id,
      });
      setParseStates((s) => ({
        ...s,
        [file.id]:
          result.status === "ready"
            ? "解析完成"
            : result.message || result.status,
      }));
    }
  };
  return (
    <div className="modal-overlay" onClick={onClose}>
      <section
        className="modal folder-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2>文件夹资料助手</h2>
          <button onClick={onClose}>关闭</button>
        </div>
        <p className="muted">
          只读浏览用户选定的本地目录，源文件不会被修改。扫描和内容预览均在本地；点击同步或混合检索才调用相应云端服务。
        </p>
        <p className="folder-steps">
          使用顺序：添加文件夹 → 扫描清单 → 点击文件右侧“打开内容预览”
        </p>
        {policy && (
          <details>
            <summary>扫描范围与排除规则</summary>
            <p>
              最多{policy.max_entries}项、总计{policy.max_total_bytes / 1000000}
              MB、单文件{policy.max_file_bytes / 1000000}MB、深度
              {policy.max_depth}。支持：{policy.extensions.join("、")}。
            </p>
            <p>
              排除：
              {[...policy.excluded_names, ...policy.excluded_patterns].join(
                "、",
              )}
              ，以及链接、重解析点、网络目录、云端占位、应用索引与任务产物。
            </p>
          </details>
        )}
        <div className="knowledge-actions folder-primary-actions">
          <button
            disabled={busy}
            onClick={() =>
              action(async () => {
                const row = await window.desktop.pickFolder();
                if (row && alive.current) {
                  setFolders((await api("/folders")).folders);
                  setSelected(row.id);
                  setScan(null);
                  setPreview(null);
                  setParseStates({});
                  previewTurn.current++;
                  setError("");
                }
              })
            }
          >
            ① 添加资料文件夹
          </button>
          <select
            aria-label="资料目录"
            value={selected}
            disabled={busy}
            onChange={(e) => choose(e.target.value)}
          >
            <option value="" disabled>
              选择已授权目录
            </option>
            {folders
              .filter((f) => f.active)
              .map((f) => (
                <option key={f.id} value={f.id}>
                  {f.path}
                </option>
              ))}
          </select>
          <button
            disabled={busy || !selected}
            onClick={() =>
              action(async () => {
                const id = selected;
                const turn = ++selection.current;
                setPreview(null);
                setParseStates({});
                previewTurn.current++;
                const result = await api("/folders/" + id + "/scan", {
                  method: "POST",
                });
                if (alive.current && turn === selection.current)
                  setScan(result);
              })
            }
          >
            {busy ? "处理中…" : "② 扫描文件清单"}
          </button>
          <button
            disabled={busy || !selected}
            onClick={() =>
              action(async () => {
                await api("/folders/" + selected, { method: "DELETE" });
                if (alive.current) {
                  selection.current++;
                  setSelected("");
                  setScan(null);
                  setPreview(null);
                  setParseStates({});
                  previewTurn.current++;
                  setFolders((await api("/folders")).folders);
                }
              })
            }
          >
            取消目录授权
          </button>
        </div>
        {error && (
          <p role="status" className="error">
            {error}
          </p>
        )}
        {selected && (
          <FolderIndexPanel
            key={selected + (scan?.id || "")}
            api={api}
            folderId={selected}
            scanId={scan?.id}
          />
        )}
        {scan && (
          <>
            <p>
              本次记录{scan.entries.length}项 ·{" "}
              {scan.total_bytes.toLocaleString()}字节 ·{" "}
              {scan.complete ? "扫描完成" : "扫描未完成"}
              （不代表所有文件均可解析）
            </p>
            {scan.limits.map((x: string, i: number) => (
              <p key={i} className="error">
                {x}
              </p>
            ))}
            <div className="folder-file-list">
              <table>
                <thead>
                  <tr>
                    <th>相对路径</th>
                    <th>类型</th>
                    <th>大小</th>
                    <th>修改时间</th>
                    <th>状态 / 原因</th>
                    <th>本地预览</th>
                  </tr>
                </thead>
                <tbody>
                  {scan.entries.map((f: any) => (
                    <tr key={f.id}>
                      <td
                        style={{
                          paddingLeft:
                            Math.min(
                              f.relative_path.split("/").length - 1,
                              12,
                            ) *
                              12 +
                            8,
                        }}
                      >
                        {f.relative_path}
                      </td>
                      <td>
                        {f.kind === "directory" ? "目录" : f.suffix || "—"}
                      </td>
                      <td>{f.size ?? "—"}</td>
                      <td>
                        {f.modified_ns
                          ? new Date(f.modified_ns / 1000000).toLocaleString()
                          : "—"}
                      </td>
                      <td>
                        {
                          (
                            {
                              ready: "可读取",
                              excluded: "已排除",
                              unsupported: "暂不支持",
                              unreadable: "不可读取",
                              over_limit: "超出限制",
                            } as any
                          )[f.status]
                        }{" "}
                        {f.reason}
                        {parseStates[f.id] && <div>{parseStates[f.id]}</div>}
                      </td>
                      <td>
                        {f.kind === "file" && f.status === "ready" && (
                          <button
                            disabled={busy}
                            aria-label={"预览 " + f.relative_path}
                            onClick={() => action(() => openPreview(f))}
                          >
                            打开内容预览
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        {preview && (
          <section className="folder-preview">
            <h3>
              {preview.relative_path} ·{" "}
              {preview.status === "ready" ? "本地解析预览" : "解析未完成"}
            </h3>
            {preview.status !== "ready" ? (
              <p role="alert">{preview.message || preview.status}</p>
            ) : (
              <>
                <p className="muted">
                  文件 SHA256：{preview.file_sha256} ·{" "}
                  {preview.cache_hit ? "复用本地解析缓存" : "本次本地解析"} ·{" "}
                  {preview.total_blocks}块
                </p>
                {preview.warnings.map((x: string, i: number) => (
                  <p className="muted" key={i}>
                    {x}
                  </p>
                ))}
                <div className="knowledge-actions">
                  <button
                    disabled={busy || preview.offset === 0}
                    onClick={() =>
                      action(() =>
                        openPreview(
                          {
                            id: preview.file_id,
                            relative_path: preview.relative_path,
                          },
                          Math.max(0, preview.offset - preview.limit),
                        ),
                      )
                    }
                  >
                    上一组
                  </button>
                  <span>
                    第{preview.offset + 1}—
                    {Math.min(
                      preview.offset + preview.limit,
                      preview.total_blocks,
                    )}
                    块
                  </span>
                  <button
                    disabled={
                      busy ||
                      preview.offset + preview.limit >= preview.total_blocks
                    }
                    onClick={() =>
                      action(() =>
                        openPreview(
                          {
                            id: preview.file_id,
                            relative_path: preview.relative_path,
                          },
                          preview.offset + preview.limit,
                        ),
                      )
                    }
                  >
                    下一组
                  </button>
                </div>
                <div className="folder-preview-blocks">
                  {preview.blocks.map((b: any) => (
                    <article key={b.id}>
                      <strong>
                        块{b.id} · {locationLabel(b.location)}
                      </strong>
                      {b.location.kind === "spreadsheet_cell" ? (
                        <>
                          <p>
                            值：
                            {b.value === null
                              ? "空白 / 未知"
                              : String(b.value)}{" "}
                            · 类型：{b.type} · 格式：{b.number_format}
                          </p>
                          {b.formula !== null && (
                            <p>
                              公式：{b.formula} · 缓存：
                              {b.type === "formula_cached_missing"
                                ? "未提供（未知）"
                                : String(b.value)}
                              ，未重新计算
                            </p>
                          )}
                          {b.merged_range && <p>合并范围：{b.merged_range}</p>}
                          {(b.sheet_state !== "visible" ||
                            b.hidden_row ||
                            b.hidden_column) && <p>包含隐藏工作表 / 行列</p>}
                        </>
                      ) : (
                        <pre>{b.text || "（空白内容）"}</pre>
                      )}
                      {b.merged_origin && (
                        <p>
                          合并单元格原点：行{b.merged_origin.row} 列
                          {b.merged_origin.column}
                        </p>
                      )}
                      <details>
                        <summary>定位与文本哈希</summary>
                        <pre>
                          {JSON.stringify(
                            {
                              location: b.location,
                              text_sha256: b.text_sha256,
                            },
                            null,
                            2,
                          )}
                        </pre>
                      </details>
                    </article>
                  ))}
                </div>
              </>
            )}
          </section>
        )}
        <p className="muted">
          取消授权会禁止后续读取，但保留已有扫描清单；重新授权不会自动扫描。
        </p>
      </section>
    </div>
  );
}

function locationLabel(l: any): string {
  if (l.kind === "paragraph") return `正文段落${l.paragraph}`;
  if (l.kind === "table_cell") return `表格${l.table} 行${l.row} 列${l.column}`;
  if (l.kind === "spreadsheet_cell") return `${l.sheet}!${l.cell}`;
  if (l.kind === "csv_record")
    return `记录${l.record} · 物理行${l.physical_line_start}—${l.physical_line_end}`;
  return `${l.page ? "第" + l.page + "页 · " : ""}第${l.line}行 · 字符${l.start}—${l.end}`;
}
