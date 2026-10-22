import React, { useEffect, useRef, useState } from "react";
import { FolderQuestions } from "./folder-questions";
import { locationLabel } from "./folder-location";

export function FolderIndexPanel({
  api,
  folderId,
  scanId,
}: {
  api: (path: string, options?: RequestInit) => Promise<any>;
  folderId: string;
  scanId?: string;
}) {
  const [index, setIndex] = useState<any>(null),
    [job, setJob] = useState<any>(null);
  const [query, setQuery] = useState(""),
    [mode, setMode] = useState("keyword");
  const [result, setResult] = useState<any>(null),
    [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const guard = useRef(false),
    alive = useRef(true);
  const base = `/folders/${folderId}`;
  const refresh = async () => {
    const value = await api(base + "/index");
    if (alive.current) setIndex(value);
  };
  useEffect(() => {
    alive.current = true;
    refresh().catch((e) => {
      if (alive.current) setError(String(e));
    });
    api(base + "/jobs")
      .then((rows) => {
        if (alive.current) setJob(rows[0] || null);
      })
      .catch((e) => {
        if (alive.current) setError(String(e));
      });
    return () => {
      alive.current = false;
    };
  }, [folderId, scanId]);
  const pending = job && ["queued", "running"].includes(job.status);
  useEffect(() => {
    if (!pending) return;
    let disposed = false;
    const timer = window.setInterval(async () => {
      try {
        const value = await api(`/folder-jobs/${job.id}`);
        if (!disposed) {
          setJob(value);
          if (!["queued", "running"].includes(value.status)) await refresh();
        }
      } catch (e) {
        if (!disposed) setError(String(e));
      }
    }, 1000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [job?.id, pending]);
  const act = async (work: () => Promise<void>) => {
    if (guard.current) return;
    guard.current = true;
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      if (alive.current) setError(String(e));
    } finally {
      guard.current = false;
      if (alive.current) setBusy(false);
    }
  };
  return (
    <section className="folder-index-panel">
      <h3>③ 建库与手动同步</h3>
      <p>
        同步会把可读取资料的文字发送给嵌入服务。每个文件夹独立建库；扫描和预览仍只在本地完成。
      </p>
      <button
        className="folder-cta"
        disabled={busy || pending || !scanId || !index?.diff?.complete}
        onClick={() =>
          act(async () => {
            const value = await api(base + "/sync", {
              method: "POST",
              body: JSON.stringify({ scan_id: scanId }),
            });
            if (alive.current) setJob(value);
          })
        }
      >
        同步资料索引（调用嵌入服务）
      </button>
      {index && (
        <>
          <p>
            当前索引：
            {index.active
              ? `${index.active.id} · ${index.active.files.length}份资料 · ${index.active.chunks}片段`
              : "尚未建库"}
          </p>
          {index.diff && (
            <details open>
              <summary>
                扫描差异 ·{" "}
                {index.diff.complete ? "清单完整" : "清单不完整，不能同步"}
              </summary>
              {[
                ["added", "新增"],
                ["modified", "修改"],
                ["missing", "消失或不可读取"],
                ["unchanged", "未变化"],
              ].map(([key, label]) => (
                <p key={key}>
                  {label} {index.diff[key].length}：
                  {index.diff[key].join("、") || "无"}
                </p>
              ))}
              <p>
                跳过 {index.diff.skipped.length} 项：
                {index.diff.skipped
                  .map((s: any) => `${s.path}（${s.reason || s.status}）`)
                  .join("、") || "无"}
              </p>
            </details>
          )}
          <p>
            嵌入请求累计 {index.usage.calls} 次 · 已知输入{" "}
            {index.usage.input_tokens} tokens · 用量未知{" "}
            {index.usage.unknown_usage_calls} 次；最近构建缓存命中{" "}
            {index.active?.cache_hits ?? 0} 片段。
          </p>
          <p>
            重排请求累计 {index.rerank.usage.calls} 次 · 用量未知{" "}
            {index.rerank.usage.unknown_usage_calls} 次。排序分不代表答案正确。
          </p>
        </>
      )}
      {job && (
        <p role="status">
          操作状态：{job.status} {job.message || ""}{" "}
          {pending && (
            <button
              onClick={() =>
                act(async () => {
                  const v = await api(`/folder-jobs/${job.id}/stop`, {
                    method: "POST",
                  });
                  if (alive.current) setJob(v);
                })
              }
            >
              停止当前操作
            </button>
          )}
        </p>
      )}
      <h3>检索预览</h3>
      <div className="knowledge-actions">
        <input
          aria-label="资料检索问题"
          value={query}
          maxLength={1000}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="输入要查找的文字或问题"
        />
        <select
          aria-label="资料检索方式"
          value={mode}
          onChange={(e) => setMode(e.target.value)}
        >
          <option value="keyword">关键词（本地）</option>
          <option value="hybrid">混合（嵌入及配置的重排服务）</option>
        </select>
        <button
          disabled={busy || pending || !index?.active || !query.trim()}
          onClick={() =>
            act(async () => {
              const value = await api(base + "/search", {
                method: "POST",
                body: JSON.stringify({ query, mode }),
              });
              if (alive.current) setResult(value);
              await refresh();
            })
          }
        >
          检索资料
        </button>
      </div>
      {result && (
        <div className="folder-retrieval">
          <p>
            固定版本 {result.generation} · 命中 {result.hits.length}{" "}
            段（有限检索不证明全目录不存在信息）
          </p>
          {result.hits.map((h: any) => (
            <article key={h.chunk_id}>
              <strong>{h.name}</strong>
              <p>{locationLabel(h.source_location)}</p>
              <pre>{h.text}</pre>
              <small>
                文件 SHA256 {h.file_sha256} · 片段 {h.chunk_id}
              </small>
            </article>
          ))}
        </div>
      )}
      <FolderQuestions
        api={api}
        folderId={folderId}
        index={index}
        job={job}
        setJob={setJob}
        busy={busy || pending}
        act={act}
      />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
