import React, { useEffect, useRef, useState } from "react";

export function KnowledgeLibrary({
  api,
  onClose,
}: {
  api: (path: string, options?: RequestInit) => Promise<any>;
  onClose: () => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<any>(null),
    [error, setError] = useState("");
  const [pending, setPending] = useState(false),
    [file, setFile] = useState<File | null>(null);
  const [operation, setOperation] = useState("append"),
    [query, setQuery] = useState(""),
    [mode, setMode] = useState("keyword");
  const [result, setResult] = useState<any>(null),
    [preview, setPreview] = useState<any>(null);
  const load = async () => setStatus(await api("/knowledge"));
  useEffect(() => {
    let live = true;
    const refresh = () =>
      api("/knowledge")
        .then((s) => {
          if (live) setStatus(s);
        })
        .catch((e) => {
          if (live) setError(String(e));
        });
    refresh();
    const timer = setInterval(refresh, 2000);
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", escape);
    return () => {
      live = false;
      clearInterval(timer);
      window.removeEventListener("keydown", escape);
    };
  }, []);
  const action = async (work: () => Promise<any>) => {
    setError("");
    setPending(true);
    try {
      await work();
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setPending(false);
    }
  };
  const busy = pending || status?.busy;
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal knowledge-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2>原始资料知识库</h2>
          <button onClick={onClose}>关闭</button>
        </div>
        <p className="muted">
          TXT、Markdown、文本
          PDF。云端嵌入会把文档片段和检索问题发送给硅基流动；聊天模型保持当前设置。扫描件暂不支持。
        </p>
        <p>
          嵌入模型：{status?.embedding?.model || "加载中"} · Key{" "}
          {status?.embedding?.key_configured
            ? "已配置"
            : "未配置（请在本项目 .env 配置后重启应用）"}
        </p>
        <p>
          当前版本：{status?.active?.id || "暂无"} ·{" "}
          {status?.active?.files?.length || 0} 份资料 ·{" "}
          {status?.active?.chunks || 0} 个片段
        </p>
        <p>
          最新构建：
          {status?.busy ? "构建中" : status?.latest?.status || "尚未构建"}
          。构建成功后切换版本，已开始检索的任务继续使用原版本。
        </p>
        <p className="muted">
          累计嵌入：{status?.usage?.calls || 0} 次 · 已知输入{" "}
          {status?.usage?.input_tokens || 0} token · 用量未知{" "}
          {status?.usage?.unknown_usage_calls || 0} 次 ·
          费用未计价（含建库与检索，缓存命中无新增请求）。
        </p>
        {(error || status?.job_error) && (
          <p role="alert" className="error">
            {error || status.job_error}
          </p>
        )}
        <div className="knowledge-actions">
          <input
            ref={fileInput}
            aria-label="选择知识文档"
            type="file"
            accept=".txt,.md,.pdf"
            disabled={busy}
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
          <select
            aria-label="导入方式"
            value={operation}
            onChange={(e) => setOperation(e.target.value)}
            disabled={busy}
          >
            <option value="append">追加资料</option>
            <option value="replace">替换整个库</option>
          </select>
          <button
            disabled={busy || !file || !status?.embedding?.key_configured}
            onClick={() =>
              action(async () => {
                if (!file) return;
                if (file.size > 20_000_000) throw Error("文档超过20MB");
                await api(
                  "/knowledge/import?filename=" +
                    encodeURIComponent(file.name) +
                    "&mode=" +
                    operation,
                  {
                    method: "POST",
                    headers: { "Content-Type": "application/octet-stream" },
                    body: await file.arrayBuffer(),
                  },
                );
                setFile(null);
                if (fileInput.current) fileInput.current.value = "";
                setPreview(null);
                setResult(null);
              })
            }
          >
            导入并构建
          </button>
          <button
            disabled={busy || !status?.active}
            onClick={() =>
              action(() => api("/knowledge/rebuild", { method: "POST" }))
            }
          >
            重建索引
          </button>
        </div>
        <div className="knowledge-documents">
          {status?.active?.files?.map((d: any) => (
            <div key={d.id}>
              <span>
                {d.name} · {d.pages} 页
              </span>
              <button
                onClick={() =>
                  action(async () =>
                    setPreview(await api("/knowledge/documents/" + d.id)),
                  )
                }
              >
                预览
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  action(async () => {
                    await api("/knowledge/documents/" + d.id, {
                      method: "DELETE",
                    });
                    setPreview(null);
                    setResult(null);
                  })
                }
              >
                从当前库移除
              </button>
            </div>
          ))}
        </div>
        {preview && (
          <section>
            <h3>
              {preview.name} · 第 {preview.page} 页
            </h3>
            <div className="knowledge-actions">
              <button
                disabled={preview.page <= 1 || pending}
                onClick={() =>
                  action(async () =>
                    setPreview(
                      await api(
                        "/knowledge/documents/" +
                          preview.id +
                          "?page=" +
                          (preview.page - 1),
                      ),
                    ),
                  )
                }
              >
                上一页
              </button>
              <button
                disabled={
                  pending ||
                  preview.page >=
                    (status?.active?.files?.find(
                      (d: any) => d.id === preview.id,
                    )?.pages || 1)
                }
                onClick={() =>
                  action(async () =>
                    setPreview(
                      await api(
                        "/knowledge/documents/" +
                          preview.id +
                          "?page=" +
                          (preview.page + 1),
                      ),
                    ),
                  )
                }
              >
                下一页
              </button>
            </div>
            <pre className="source-text">{preview.text}</pre>
            {preview.partial && <p>预览仅显示前20000字符。</p>}
          </section>
        )}
        <h3>检索预览</h3>
        <p className="muted">
          关键词模式不调用嵌入服务；混合和向量模式可能产生 API
          用量。排序分仅用于排列片段，不表示答案正确概率。
        </p>
        <div className="knowledge-actions">
          <input
            aria-label="知识库检索问题"
            placeholder="输入资料相关问题"
            value={query}
            maxLength={1000}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select
            aria-label="检索方式"
            value={mode}
            onChange={(e) => setMode(e.target.value)}
          >
            <option value="keyword">关键词</option>
            <option value="hybrid">混合（云端嵌入）</option>
            <option value="vector">向量（云端嵌入）</option>
          </select>
          <button
            disabled={pending || !query.trim()}
            onClick={() =>
              action(async () =>
                setResult(
                  await api("/knowledge/search", {
                    method: "POST",
                    body: JSON.stringify({ query, mode, k: 4 }),
                  }),
                ),
              )
            }
          >
            检索
          </button>
        </div>
        {result && (
          <div>
            <p>
              版本：{result.generation || "暂无"} · {result.hits.length}{" "}
              个片段。无命中不证明资料不存在相关事实。
            </p>
            {result.hits.map((h: any) => (
              <article key={h.chunk_id} className="knowledge-hit">
                <strong>
                  {h.name} · 第 {h.page} 页 ·{" "}
                  {h.role === "direct" ? "直接召回" : "相邻上下文"}
                </strong>
                <p className="muted">
                  提取文本区间 {h.start}–{h.end} · RRF{" "}
                  {h.rank_score?.toFixed(5) || "—"}
                </p>
                <pre>{h.text}</pre>
              </article>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
