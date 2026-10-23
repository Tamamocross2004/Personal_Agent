import React, { useEffect, useRef, useState } from "react";
import { locationLabel } from "./folder-location";
import { FileFilter } from "./folder-files";
import { ModelStrength, strengthLabel } from "./model-strength";
import { FolderScope } from "./folder-scope";
import { FolderOutputDiagnostics } from "./folder-output-diagnostics";
import { FolderTransmission } from "./folder-transmission";

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
  useEffect(() => {
    setFiles([]);
  }, [index?.active?.id]);
  const [history, setHistory] = useState<any[]>([]),
    [view, setView] = useState<any>(null),
    [citation, setCitation] = useState<any>(null);
  const [exported, setExported] = useState<any>(null);
  const [strength, setStrength] = useState("default");
  const [outputTokens, setOutputTokens] = useState(1200),
    [batchMode, setBatchMode] = useState("adaptive"),
    [batchSize, setBatchSize] = useState(6),
    [expectedFields, setExpectedFields] = useState(1);
  const [strategy, setStrategy] = useState("retrieval"),
    [maxBatches, setMaxBatches] = useState(3);
  const [reading, setReading] = useState<any>(null),
    [neighbors, setNeighbors] = useState<any>(null),
    [batchNumber, setBatchNumber] = useState(0);
  const [historyError, setHistoryError] = useState("");
  const alive = useRef(true),
    turn = useRef(0);
  const readingTurn = useRef(0);
  useEffect(() => {
    alive.current = true;
    api(`/folders/${folderId}/jobs`)
      .then((rows: any[]) => {
        if (alive.current) setHistory(rows.filter((r) => r.kind === "answer"));
      })
      .catch((e: any) => {
        if (alive.current) setHistoryError(String(e));
      });
    return () => {
      alive.current = false;
      turn.current++;
    };
  }, [folderId, job?.status]);
  const saved = view || (job?.kind === "answer" ? job : history[0]);
  const batch = saved?.batches?.[batchNumber];
  const current = batch
    ? {
        ...saved,
        result: batch.result || { content_status: "尚未形成该批答案" },
        model_output: batch.model_output,
        finish_reason: batch.finish_reason,
        output_diagnostics: batch.output_diagnostics,
        request: batch.request,
        evidence: batch.evidence,
      }
    : saved;
  const retryBatch = saved?.batches?.find(
    (b: any) =>
      b.start_offset ===
        (saved.coverage?.next_offset ?? saved.coverage?.returned_until) &&
      b.status === "returned" &&
      (!b.result?.answer || b.finish_reason !== "stop"),
  );
  const canSplit =
    retryBatch &&
    retryBatch.next_offset - retryBatch.start_offset > 1 &&
    !saved.usage_events?.some(
      (e: any) =>
        e.input_tokens == null ||
        (e.operation === "llm" && e.output_tokens == null),
    );
  const invalidLimits =
    !Number.isInteger(outputTokens) ||
    outputTokens < 128 ||
    outputTokens > 4096 ||
    !Number.isInteger(batchSize) ||
    batchSize < 1 ||
    batchSize > 6 ||
    !Number.isInteger(expectedFields) ||
    expectedFields < 1 ||
    expectedFields > 12;
  const resume = (split = false) =>
    act(async () => {
      const value = await api(`/folders/${folderId}/questions`, {
        method: "POST",
        body: JSON.stringify({
          query: saved.inputs.query,
          mode: saved.inputs.mode,
          budget,
          strategy: "exhaustive",
          max_batches: maxBatches,
          resume_from: saved.id,
          strength: saved.inputs.strength || "default",
          output_tokens: outputTokens,
          batch_mode: batchMode,
          batch_size: batchSize,
          expected_fields: expectedFields,
          retry_split: split,
        }),
      });
      if (alive.current) {
        setView(null);
        setCitation(null);
        setJob(value);
      }
    });
  useEffect(() => {
    setBatchNumber(0);
    setNeighbors(null);
    setCitation(null);
    turn.current++;
  }, [saved?.id]);
  useEffect(() => {
    setReading(null);
    readingTurn.current++;
  }, [index?.active?.id, scope, files]);
  const loadReading = (offset = 0) =>
    act(async () => {
      const request = ++readingTurn.current;
      const value = await api(`/folders/${folderId}/reading`, {
        method: "POST",
        body: JSON.stringify({
          generation: index.active.id,
          file_ids: scope === "all" ? null : files,
          offset,
        }),
      });
      if (alive.current && readingTurn.current === request) setReading(value);
    });
  const labels: any = {
    direct: "原文整理",
    inference: "推断（基于假设，未证实）",
    difference: "差异（不等于矛盾）",
    unknown: "未知",
    conflict: "模型判断：明确矛盾（待核对）",
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
              if (alive.current && turn.current === request) {
                setCitation(value);
                setNeighbors(null);
              }
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
      {historyError && (
        <p role="alert" className="error">
          历史记录读取失败：{historyError}
        </p>
      )}
      <p>
        局部问答只发送有限召回片段；全量整理按固定范围分批送达已解析文字。两种方式均调用语言模型，结果未正式审稿，请核对引用和条件。
      </p>
      <ModelStrength api={api} value={strength} onChange={setStrength} />
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
          aria-label="资料处理方式"
          value={strategy}
          onChange={(e) => setStrategy(e.target.value)}
        >
          <option value="retrieval">局部检索问答</option>
          <option value="exhaustive">
            全量整理（按固定范围分批，可能部分完成）
          </option>
        </select>
        {strategy === "exhaustive" && (
          <label>
            本次最多批数{" "}
            <input
              aria-label="最多整理批数"
              type="number"
              min={1}
              max={8}
              value={maxBatches}
              onChange={(e) => setMaxBatches(Number(e.target.value))}
            />
          </label>
        )}
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
          disabled={strategy === "exhaustive"}
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
      <details
        className="folder-budget-settings"
        open={strategy === "exhaustive"}
      >
        <summary>输出额度与分批设置（下次新任务）</summary>
        <div className="knowledge-actions">
          <label>
            每次输出Token上限{" "}
            <input
              aria-label="每次输出Token上限"
              type="number"
              min={128}
              max={4096}
              value={outputTokens}
              onChange={(e) => setOutputTokens(Number(e.target.value))}
            />
          </label>
          {strategy === "exhaustive" && (
            <>
              <select
                aria-label="分批策略"
                value={batchMode}
                onChange={(e) => setBatchMode(e.target.value)}
              >
                <option value="adaptive">自动匹配输出空间</option>
                <option value="fixed">固定片段上限（仍检查预算）</option>
              </select>
              <label>
                每批片段上限{" "}
                <input
                  aria-label="每批片段上限"
                  type="number"
                  min={1}
                  max={6}
                  value={batchSize}
                  onChange={(e) => setBatchSize(Number(e.target.value))}
                />
              </label>
              <label>
                每条预计字段数{" "}
                <input
                  aria-label="每条预计字段数"
                  type="number"
                  min={1}
                  max={12}
                  value={expectedFields}
                  onChange={(e) => setExpectedFields(Number(e.target.value))}
                />
              </label>
            </>
          )}
        </div>
        <p className="muted">
          默认输出1200，上限还受项目单次上限和剩余预算约束。预计字段数只影响分批估算，不删减答案或改变校验。思考与正文共用输出额度；未知思考用量不当作已知为零，估算不能保证不截断。调整输出额度不会提高总任务预算。
        </p>
      </details>
      {scope === "selected" && (
        <fieldset>
          <legend>检索前限定文件范围</legend>
          <FileFilter
            rows={(index?.active?.files || []).map((f: any) => ({
              ...f,
              suffix: "." + f.name.split(".").pop().toLowerCase(),
              status: index?.diff?.modified.includes(f.name)
                ? "modified"
                : index?.diff?.missing.includes(f.name)
                  ? "missing"
                  : "unchanged",
            }))}
            selected={files}
            select={setFiles}
          >
            {(visible: any[]) =>
              visible.map((f: any) => (
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
              ))
            }
          </FileFilter>
        </fieldset>
      )}
      {strategy === "exhaustive" && (
        <aside>
          <p>
            顺序读取所选索引版本的已解析文字，每批在所设上限内按资料长度、预计字段数和剩余预算分配片段；每段最多700字符，不按关键词筛掉记录，不调用检索嵌入/重排。分批不会自动推断表头、连接跨页表格或合并业务对象。预算、输出空间和批数限制可能导致部分完成。本地原文分页只是浏览，不等于本次实际发送批次。
          </p>
          <button
            disabled={
              busy || !index?.active || (scope === "selected" && !files.length)
            }
            onClick={() => loadReading()}
          >
            查看读取计划与首批原文（本地）
          </button>
          {reading && (
            <div className="folder-reading-preview">
              <p>
                固定版本 {reading.generation} · {reading.total_blocks}个提取块 /{" "}
                {reading.total_segments}段 · {reading.total_characters}字符。
                {reading.meaning}
              </p>
              {reading.hits.map((h: any) => (
                <article key={h.segment}>
                  <p>
                    {h.name} · {locationLabel(h.source_location)} · 块内字符[
                    {h.start}, {h.end}) ·{" "}
                    {h.role === "table_origin_unverified_header"
                      ? "表格首条记录（未认证表头）"
                      : h.role === "explicit_markdown_heading"
                        ? "原文标题"
                        : "顺序片段"}
                  </p>
                  <pre>{h.text || "（空白提取块）"}</pre>
                </article>
              ))}
              <button
                disabled={busy || reading.next_offset >= reading.total_segments}
                onClick={() => loadReading(reading.next_offset)}
              >
                继续查看下一批原文（本地，不发送模型）
              </button>
            </div>
          )}
        </aside>
      )}
      <button
        className="folder-cta"
        disabled={
          busy ||
          !index?.active ||
          !question.trim() ||
          (scope === "selected" && !files.length) ||
          budget < 256 ||
          budget > 50000 ||
          !Number.isFinite(budget) ||
          invalidLimits ||
          (strategy === "exhaustive" &&
            (!Number.isInteger(maxBatches) || maxBatches < 1 || maxBatches > 8))
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
                strategy,
                strength,
                max_batches: maxBatches,
                output_tokens: outputTokens,
                batch_mode: batchMode,
                batch_size: batchSize,
                expected_fields: expectedFields,
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
        {strategy === "exhaustive"
          ? "开始分批整理（调用模型）"
          : "开始资料短答（调用模型）"}
      </button>
      <p className="muted">
        本次范围：
        {scope === "all"
          ? `整个已建库目录（${index?.active?.files.length || 0}份）`
          : `所选${files.length}份`}{" "}
        · 使用索引 {index?.active?.id || "尚未建库"}。筛选文件不会同步新内容。
      </p>
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
          <FolderScope
            api={api}
            saved={saved}
            controls={{
              budget,
              max_batches: maxBatches,
              output_tokens: outputTokens,
              batch_mode: batchMode,
              batch_size: batchSize,
              expected_fields: expectedFields,
            }}
            valid={
              !invalidLimits &&
              Number.isInteger(budget) &&
              budget >= 256 &&
              budget <= 50000 &&
              Number.isInteger(maxBatches) &&
              maxBatches >= 1 &&
              maxBatches <= 8
            }
            canSplit={canSplit}
            onSelect={(id: string) =>
              act(async () => {
                const token = ++turn.current;
                const value = await api(`/folder-jobs/${id}`);
                if (alive.current && turn.current === token) {
                  setView(value);
                  setCitation(null);
                }
              })
            }
          />
          {saved?.coverage && (
            <aside className="folder-coverage">
              <h4>当前任务保存的覆盖记录</h4>
              <p>
                已确认送达输入范围 {saved.coverage.returned_until} /{" "}
                {saved.coverage.total_segments}段；未完成整理剩余
                {saved.coverage.remaining_segments}段。
                {saved.coverage.input_coverage_complete
                  ? "该范围提取文字已全部送达"
                  : "部分完成，尚未送达全部范围"}
                。
              </p>
              {saved.coverage.protocol_version >= 2 && (
                <p>
                  尚未确认送达 {saved.coverage.unsent_segments}{" "}
                  段；已送达但响应无效或截断的批次仍属于未完成范围，手动继续会重新发送该批并另计调用。
                </p>
              )}
              {!saved.coverage.protocol_version && (
                <p>
                  旧版范围记录仅统计送达位置；继续时依据保存的批次定位未完成内容，旧记录保持只读。
                </p>
              )}
              <p>
                结构及原句检查通过且未截断的批次对应
                {saved.coverage.structured_segments}
                段；不代表这些段中的所有事实都已抽取或判断正确。停止原因：
                {(
                  {
                    batch_limit: "达到本次批数上限",
                    token_budget: "剩余Token预算不足，未发送下一批",
                    invalid_or_truncated_output:
                      "响应格式无效或被截断，已停止后续批次",
                    provider_reported_budget_exceeded:
                      "服务返回的实际用量超出预算",
                    end_of_extraction: "到达已解析文字末尾",
                    output_space:
                      "单片段估算输出仍超出额度；未发送，可显式调整输出额度或分批方式",
                    retry_range_finished:
                      "本次拆小重做范围已处理，其他范围需另行继续",
                  } as any
                )[saved.coverage.stop_reason] || saved.status}
                。
              </p>
              {saved.inputs.resume_from && (
                <p>
                  续读任务：前次结果保留在历史任务 {saved.inputs.resume_from}
                  ，本次不自动合并。
                </p>
              )}
              {saved.inputs.retry_of && (
                <p>
                  本次仅拆小重做原任务 {saved.inputs.retry_of.job_id} 第
                  {saved.inputs.retry_of.batch}批范围 [
                  {saved.inputs.retry_of.start}, {saved.inputs.retry_of.end}
                  )；原失败和费用保留，本任务最多处理到{saved.inputs.range_end}
                  ，不自动扩展到其他未完成范围。
                </p>
              )}
              <p>
                本任务输出设置 {saved.inputs.output_tokens ?? 1200}
                ；项目约束后上限{" "}
                {saved.inputs.effective_output_cap ??
                  "旧记录未保存"}；分批策略{" "}
                {saved.inputs.batch_mode || "旧版固定"}
                。下一次发送前还会检查剩余预算。
              </p>
              {saved.batch_decisions?.map((d: any, i: number) => (
                <details key={i}>
                  <summary>
                    第{i + 1}次分批估算：{d.selected_segments}段，预计正文
                    {d.estimated_body_tokens} Token，实际可用输出上限
                    {d.effective_output_tokens}
                  </summary>
                  <p>
                    思考参考用量：
                    {d.reasoning_usage_unknown
                      ? "未知"
                      : d.estimated_reasoning_tokens}
                    ；剩余预算{d.remaining_tokens}。{d.meaning}
                  </p>
                  <pre>{JSON.stringify(d.trials, null, 2)}</pre>
                </details>
              ))}
              <select
                aria-label="查看分批结果"
                value={batchNumber}
                onChange={(e) => {
                  setBatchNumber(Number(e.target.value));
                  turn.current++;
                  setCitation(null);
                  setNeighbors(null);
                }}
              >
                {saved.batches?.map((b: any, i: number) => (
                  <option key={i} value={i}>
                    第{b.number}批 · [{b.start_offset}, {b.next_offset}) ·{" "}
                    {b.result?.content_status || b.status}
                  </option>
                ))}
              </select>
              <button
                disabled={
                  busy ||
                  !saved.coverage.remaining_segments ||
                  budget < 256 ||
                  budget > 50000 ||
                  invalidLimits ||
                  !Number.isFinite(budget) ||
                  !Number.isInteger(maxBatches) ||
                  maxBatches < 1 ||
                  maxBatches > 8
                }
                onClick={() => resume()}
              >
                继续整理剩余范围（新任务，使用上方预算并调用模型）
              </button>
              {retryBatch && (
                <>
                  <button
                    disabled={
                      busy ||
                      !canSplit ||
                      invalidLimits ||
                      !Number.isFinite(budget) ||
                      budget < 256 ||
                      budget > 50000 ||
                      !Number.isInteger(maxBatches) ||
                      maxBatches < 1 ||
                      maxBatches > 8
                    }
                    onClick={() => resume(true)}
                  >
                    拆小重做未完成批次（新任务，重新计费）
                  </button>
                  <p>
                    仅重发原未完成批次，片段上限至少减半；输出额度和总预算取上方设置，不后台重试。
                    {!canSplit &&
                      "单片段无法再按片段拆分，或原用量仍未知，请先核对。"}
                  </p>
                </>
              )}
              <p>{saved.result?.message || saved.message}</p>
            </aside>
          )}
          <button
            className="folder-cta"
            disabled={busy || ["queued", "running"].includes(current.status)}
            onClick={() =>
              act(async () => {
                const value = await api(`/folder-jobs/${current.id}/export`, {
                  method: "POST",
                });
                if (alive.current)
                  setExported({ ...value, task_id: current.id });
              })
            }
          >
            ⑤ 导出摘要 / 对照表 / 来源清单（本地）
          </button>
          {exported?.task_id === current.id && (
            <p role="status">
              已导出至 {exported.directory}（{exported.files.join("、")}）
              <button
                onClick={() => window.desktop.openTask(exported.directory)}
              >
                打开导出文件夹
              </button>
            </p>
          )}

          <p>
            执行状态 {current.status} · 内容状态{" "}
            {current.result?.content_status || "尚未形成答案"} · 固定索引{" "}
            {current.inputs.generation}
          </p>
          <p>
            本任务模型强度：
            {current.inputs.strength
              ? strengthLabel(current.inputs.strength)
              : "旧记录未保存"}
            {current.inputs.model?.reasoning_effort &&
              `（${current.inputs.model.reasoning_effort}）`}
            ；续读继承原任务强度，上方选择只用于新问题。
          </p>
          {index?.active?.id !== current.inputs.generation && (
            <p className="muted">
              这是保存的历史版本，查看不会重新请求模型或混入新资料。
            </p>
          )}
          <p>{current.message || current.result?.message}</p>
          <FolderOutputDiagnostics value={current.output_diagnostics} />
          <FolderTransmission
            value={current.request?.transmission}
            label="实际保存的发送构成"
          />
          {current.result?.contract_checks && (
            <aside className="folder-contract-notice">
              <p>
                仅检查结构和依据原句是否匹配；不认证事实、对象关系或矛盾判断，仍未正式审稿。
              </p>
              {current.result.contract_checks.warnings?.map(
                (w: any, i: number) => (
                  <p key={i}>需核对：{w.message}</p>
                ),
              )}
              {current.result.contract_checks.issues?.map(
                (w: any, i: number) => (
                  <p key={i}>
                    未形成合格整理：{w.message}（{w.path}）
                  </p>
                ),
              )}
            </aside>
          )}
          {current.result?.answer?.findings.map((f: any, i: number) => (
            <article key={i}>
              <strong>{labels[f.kind]}</strong>
              <p>{f.statement}</p>
              {f.assumptions?.map((s: string, j: number) => (
                <p key={"a" + j}>假设（未证实）：{s}</p>
              ))}
              {f.limitations?.map((s: string, j: number) => (
                <p key={"l" + j}>判断限制：{s}</p>
              ))}
              {f.relation && (
                <div>
                  <p>
                    比较字段：{f.relation.field}；共同范围：
                    {f.relation.common_scope || "尚未明确"}
                  </p>
                  <p>
                    可比条件
                    {f.relation.comparability === "established"
                      ? "由模型声明已明确，须核对"
                      : "尚未明确"}
                    ：{f.relation.reason}
                  </p>
                </div>
              )}
              {!!f.support?.length && (
                <details>
                  <summary>本项依据原句</summary>
                  {f.support.map((q: any, j: number) => (
                    <blockquote key={j}>
                      [{q.citation_id}] {q.quote}
                    </blockquote>
                  ))}
                </details>
              )}
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
                  <th>
                    {current.result.answer.schema_version >= 2
                      ? "对象（来源另列）"
                      : "对象/来源（旧版）"}
                  </th>
                  {current.result.answer.schema_version >= 2 && <th>来源</th>}
                  <th>字段</th>
                  <th>值/状态</th>
                  <th>依据</th>
                </tr>
              </thead>
              <tbody>
                {current.result.answer.comparison.map((r: any, i: number) => (
                  <tr key={i}>
                    <td>
                      {r.subject ?? "对象未明确"}
                      {r.subject_type && (
                        <small>
                          （
                          {r.subject_type === "entity"
                            ? "原文对象"
                            : r.subject_type === "source"
                              ? "来源级"
                              : "需核对"}
                          ）
                        </small>
                      )}
                    </td>
                    {current.result.answer.schema_version >= 2 && (
                      <td>
                        {Array.from(
                          new Set<string>(
                            r.citations.map(
                              (id: string) =>
                                current.evidence?.find(
                                  (e: any) => e.citation_id === id,
                                )?.name || id,
                            ),
                          ),
                        ).join("、")}
                      </td>
                    )}
                    <td>{r.field}</td>
                    <td>
                      {r.status === "unknown"
                        ? "未知"
                        : r.status === "blank"
                          ? "空白"
                          : (r.value ?? "未知")}
                      （{labels[r.status]}）
                    </td>
                    <td>
                      {cites(r.citations)}
                      {r.subject_quote && (
                        <details>
                          <summary>对象原文</summary>
                          <blockquote>{r.subject_quote.quote}</blockquote>
                        </details>
                      )}
                      {r.value_quote && (
                        <details>
                          <summary>值原文</summary>
                          <blockquote>{r.value_quote.quote}</blockquote>
                          {current.result?.contract_checks?.quotes
                            ?.find(
                              (q: any) =>
                                q.path === `comparison.${i}.value_quote`,
                            )
                            ?.value_matches?.map((span: any, j: number) => (
                              <p key={j}>
                                本次送达片段中的取值字符区间：[{span.start},{" "}
                                {span.end})
                              </p>
                            ))}
                        </details>
                      )}
                    </td>
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
              {batch && batch.status !== "returned"
                ? "本批准备的请求片段（未确认送达）"
                : "本次实际送达片段"}{" "}
              {current.evidence?.length || 0}{" "}
              段；候选及全文缓存不等于模型已读。全量整理的累计送达范围以上方覆盖记录为准。
            </p>
            {current.evidence?.map((e: any) => (
              <article key={e.citation_id}>
                [{e.citation_id}] {e.name} · {locationLabel(e.source_location)}
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
            {citation.name} · {locationLabel(citation.block.location)} · SHA256{" "}
            {citation.file_sha256}
          </p>
          <p>本次实际送达片段：</p>
          <pre>{citation.delivered.text}</pre>
          <button
            onClick={() =>
              act(async () => {
                const request = ++turn.current;
                const value = await api(
                  `/folder-jobs/${current.id}/citations/${citation.delivered.citation_id}/neighbors`,
                );
                if (alive.current && turn.current === request)
                  setNeighbors(value);
              })
            }
          >
            查看前后相邻原文（本地，不发送模型）
          </button>
          {neighbors && (
            <div>
              <p>{neighbors.meaning}</p>
              {neighbors.blocks.map((b: any) => (
                <article key={b.id}>
                  <p>
                    {locationLabel(b.location)}
                    {b.partial ? "（显示前4000字符）" : ""}
                  </p>
                  <pre>{b.text || "（空白提取块）"}</pre>
                </article>
              ))}
            </div>
          )}
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
