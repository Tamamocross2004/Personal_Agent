import React, { useEffect, useRef, useState } from "react";

export function FolderQuestions({
  api,
  folderId,
  index,
  job,
  setJob,
  busy,
  act,
}: any) {
  const [question, setQuestion] = useState(""),
    [mode, setMode] = useState("keyword"),
    [budget, setBudget] = useState(10000);
  const [scope, setScope] = useState("all"),
    [files, setFiles] = useState<string[]>([]);
  const [history, setHistory] = useState<any[]>([]),
    [view, setView] = useState<any>(null),
    [citation, setCitation] = useState<any>(null);
  const alive = useRef(true),
    turn = useRef(0);
  useEffect(() => {
    alive.current = true;
    api(`/folders/${folderId}/jobs`)
      .then((rows: any[]) => {
        if (alive.current) setHistory(rows.filter((r) => r.kind === "answer"));
      })
      .catch(() => {});
    return () => {
      alive.current = false;
      turn.current++;
    };
  }, [folderId, job?.status]);
  const current = view || (job?.kind === "answer" ? job : history[0]);
  const labels: any = {
    direct: "原文事实",
    inference: "推断",
    unknown: "未知",
    conflict: "冲突",
    stated: "有原文值",
    blank: "原文空白",
  };
  const cites = (ids: string[]) =>
    ids.map((id) => {
      const e = current.evidence?.find((r: any) => r.citation_id === id);
      return (
        <button
          className="folder-citation"
          key={id}
          onClick={() =>
            act(async () => {
              const request = ++turn.current,
                value = await api(`/folder-jobs/${current.id}/citations/${id}`);
              if (alive.current && turn.current === request) setCitation(value);
            })
          }
        >
          [{id}] {e?.name || "引用"}
        </button>
      );
    });
  return (
    <section className="folder-questions">
      <h3>④ 针对资料提问</h3>
      <p>
        短答调用语言模型；混合检索还使用嵌入及配置的重排服务。仅发送实际召回的有限片段。结果未正式审稿，请核对引用和条件。
      </p>
      <label>
        问题
        <textarea
          aria-label="文件夹资料问题"
          maxLength={1000}
          rows={3}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="例如：比较两份资料的交付条件；依据不足请说明。"
        />
      </label>
      <div className="knowledge-actions">
        <select
          aria-label="问答文件范围"
          value={scope}
          onChange={(e) => setScope(e.target.value)}
        >
          <option value="all">整个已建库目录</option>
          <option value="selected">只问所选文件</option>
        </select>
        <select
          aria-label="问答检索方式"
          value={mode}
          onChange={(e) => setMode(e.target.value)}
        >
          <option value="keyword">关键词检索</option>
          <option value="hybrid">混合检索（云端）</option>
        </select>
        <label>
          Token预算{" "}
          <input
            aria-label="短答Token预算"
            type="number"
            min={256}
            max={50000}
            value={budget}
            onChange={(e) => setBudget(Number(e.target.value))}
          />
        </label>
      </div>
      {scope === "selected" && (
        <fieldset>
          <legend>检索前限定文件范围</legend>
          {index?.active?.files.map((f: any) => (
            <label className="folder-scope-file" key={f.file_id}>
              <input
                type="checkbox"
                checked={files.includes(f.file_id)}
                onChange={(e) =>
                  setFiles((old) =>
                    e.target.checked
                      ? [...old, f.file_id]
                      : old.filter((id) => id !== f.file_id),
                  )
                }
              />
              {f.name}
            </label>
          ))}
        </fieldset>
      )}
      <button
        className="folder-cta"
        disabled={
          busy ||
          !index?.active ||
          !question.trim() ||
          (scope === "selected" && !files.length) ||
          budget < 256 ||
          budget > 50000
        }
        onClick={() =>
          act(async () => {
            const value = await api(`/folders/${folderId}/questions`, {
              method: "POST",
              body: JSON.stringify({
                query: question,
                mode,
                budget,
                file_ids: scope === "all" ? null : files,
                generation: index.active.id,
              }),
            });
            if (alive.current) {
              setView(null);
              setCitation(null);
              turn.current++;
              setJob(value);
            }
          })
        }
      >
        开始资料短答（调用模型）
      </button>
      <h4>已保存的短答（只读查看，不重复调用）</h4>
      <select
        aria-label="历史资料问答"
        value={current?.id || ""}
        onChange={(e) => {
          turn.current++;
          setView(history.find((j) => j.id === e.target.value));
          setCitation(null);
        }}
      >
        {!current && <option value="">暂无问答</option>}
        {current && !history.some((j) => j.id === current.id) && (
          <option value={current.id}>
            {current.inputs.query} · {current.status}
          </option>
        )}
        {history.map((j) => (
          <option key={j.id} value={j.id}>
            {j.inputs.query} · {j.status} ·{" "}
            {new Date(j.created).toLocaleString()}
          </option>
        ))}
      </select>
      {current && (
        <div className="folder-answer">
          <h4>回答 · 未正式审稿</h4>
          <p>
            执行状态 {current.status} · 内容状态{" "}
            {current.result?.content_status || "尚未形成答案"} · 固定索引{" "}
            {current.inputs.generation}
          </p>
          {index?.active?.id !== current.inputs.generation && (
            <p className="muted">
              这是保存的历史版本，查看不会重新请求模型或混入新资料。
            </p>
          )}
          <p>{current.message || current.result?.message}</p>
          {current.result?.answer?.findings.map((f: any, i: number) => (
            <article key={i}>
              <strong>{labels[f.kind]}</strong>
              <p>{f.statement}</p>
              {cites(f.citations)}
            </article>
          ))}
          {current.result?.answer?.unknowns.map((s: string, i: number) => (
            <p key={i}>待核对：{s}</p>
          ))}
          {!!current.result?.answer?.comparison.length && (
            <table>
              <thead>
                <tr>
                  <th>对象/来源</th>
                  <th>字段</th>
                  <th>值/状态</th>
                  <th>依据</th>
                </tr>
              </thead>
              <tbody>
                {current.result.answer.comparison.map((r: any, i: number) => (
                  <tr key={i}>
                    <td>{r.subject}</td>
                    <td>{r.field}</td>
                    <td>
                      {r.status === "unknown"
                        ? "未知"
                        : r.status === "blank"
                          ? "空白"
                          : (r.value ?? "未知")}
                      （{labels[r.status]}）
                    </td>
                    <td>{cites(r.citations)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <details>
            <summary>范围、实际送达证据与用量</summary>
            <p>问题：{current.inputs.query}</p>
            {current.inputs.source_snapshot?.map((f: any) => (
              <p key={f.id}>
                {f.name} · SHA256 {f.file_sha256} · 扫描 {f.scan_id}
              </p>
            ))}
            <p>
              实际送达 {current.evidence?.length || 0}{" "}
              段；候选及全文缓存不等于模型已读。
            </p>
            {current.evidence?.map((e: any) => (
              <article key={e.citation_id}>
                [{e.citation_id}] {e.name} · {JSON.stringify(e.source_location)}
                <pre>{e.text}</pre>
                <small>送达SHA256 {e.delivered_sha256}</small>
              </article>
            ))}
            {current.usage_events?.map((u: any) => (
              <p key={u.id}>
                {u.operation || "embedding"} · {u.model} · {u.status} · 输入{" "}
                {u.input_tokens ?? "未知"} · 输出{" "}
                {u.output_tokens ?? (u.operation === "llm" ? "未知" : "不适用")}{" "}
                tokens
              </p>
            ))}
            {current.model_output && (
              <details>
                <summary>原始模型响应（未自动修补）</summary>
                <pre>{current.model_output}</pre>
              </details>
            )}
          </details>
        </div>
      )}
      {citation && (
        <aside className="folder-citation-preview">
          <h4>引用原文 · 保存的提取版本</h4>
          <button onClick={() => setCitation(null)}>关闭引用</button>
          <p>
            {citation.name} · {JSON.stringify(citation.block.location)} · SHA256{" "}
            {citation.file_sha256}
          </p>
          <p>本次实际送达片段：</p>
          <pre>{citation.delivered.text}</pre>
          <details>
            <summary>
              该位置的完整提取块（补充查看，不代表模型已读全部）
            </summary>
            <pre>{citation.block.text}</pre>
          </details>
          {citation.warnings.map((s: string, i: number) => (
            <p className="muted" key={i}>
              {s}
            </p>
          ))}
        </aside>
      )}
    </section>
  );
}
