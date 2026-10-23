import React, { useEffect, useRef, useState } from "react";
import { locationLabel } from "./folder-location";
import { FolderCompletion } from "./folder-completion";
import { FolderPlanningPreview } from "./folder-planning-preview";

export function FolderFullReading({
  api,
  saved,
  controls,
  valid,
  busy,
  act,
  onStarted,
  setMaxBatches,
  setReadToEnd,
  setMaxCalls,
  setPlanFields,
}: any) {
  const [preview, setPreview] = useState<any>(null);
  const turn = useRef(0);
  const key = JSON.stringify({ id: saved.id, controls });
  useEffect(() => {
    turn.current++;
    setPreview(null);
    return () => {
      turn.current++;
    };
  }, [key]);
  const current = preview?.controlsKey === key ? preview : null;
  return (
    <aside className="folder-full-reading" aria-label="从局部问答创建全量整理">
      <h4>需要整理所选文件的全部已解析文字？</h4>
      <p>
        这份短答只读取有限命中片段；问题中写“全部”也不会自动改变读取方式。可沿用原问题、所选文件和固定版本，从头新建全量整理任务。原答案保留，新任务另计模型调用。
      </p>
      <label>
        新任务本次最多批数{" "}
        <input
          aria-label="新建全量整理最多批数"
          type="number"
          min={1}
          max={8}
          disabled={controls.read_to_end}
          value={controls.max_batches}
          onChange={(e) => setMaxBatches(Number(e.target.value))}
        />
      </label>
      <FolderCompletion
        enabled={controls.read_to_end || false}
        onChange={setReadToEnd}
        maxCalls={controls.max_calls || 16}
        setMaxCalls={setMaxCalls}
        planFields={controls.plan_fields}
        setPlanFields={setPlanFields}
        prefix="原范围"
      />
      <p>
        使用上方预算与输出设置：预算 {controls.budget} Token，每次输出上限{" "}
        {controls.output_tokens}{" "}
        Token；强度沿用原短答。实际批次受剩余预算和输出空间限制。
      </p>
      <button
        className="folder-cta"
        disabled={busy || !valid}
        onClick={() =>
          act(async () => {
            const request = ++turn.current;
            setPreview(null);
            const value = await api(
              `/folder-jobs/${saved.id}/full-reading-preview`,
              { method: "POST", body: JSON.stringify(controls) },
            );
            if (turn.current === request)
              setPreview({ ...value, controlsKey: key });
          })
        }
      >
        预览原范围的全量整理（本地）
      </button>
      {current && (
        <div>
          <p>原问题：{current.query}</p>
          <FolderPlanningPreview preview={current} />
          <p>
            运行方式：{current.read_to_end ? "持续整理" : "普通分批"} · 本次最多{" "}
            {current.batch_call_limit} 次模型调用 · 总预算 {current.budget}{" "}
            Token（逐批扣减）。
          </p>
          <p>
            固定索引 {current.generation} · {current.files.length}份文件 ·{" "}
            {current.total_segments}个主片段 · 首批预计送达{" "}
            {current.first_batch_end}段。文件：
            {current.files.map((f: any) => f.name).join("、")}
          </p>
          <p>{current.meaning}</p>
          {current.source_usage_unknown && (
            <p role="alert">
              原短答存在未知用量，请先核对账单；新任务不会补算或消除原调用费用。
            </p>
          )}
          {!current.services.length && (
            <p role="alert">
              当前设置无法发送首批：{current.decision.stop_reason}
              。请检查预算、输出空间或资料范围。
            </p>
          )}
          <details>
            <summary>查看首批预计发送的原文（本地）</summary>
            {current.first_batch.map((h: any) => (
              <article key={`${h.segment}-${h.role}`}>
                <p>
                  {h.name} · {locationLabel(h.source_location)} ·{" "}
                  {h.role === "sequential" ? "主片段" : "附带上下文"}
                </p>
                <pre>{h.text || "（空白提取块）"}</pre>
              </article>
            ))}
          </details>
          <button
            className="folder-cta"
            disabled={busy || !valid || !current.services.length}
            onClick={() =>
              act(async () => {
                const value = await api(
                  `/folder-jobs/${saved.id}/full-reading`,
                  {
                    method: "POST",
                    body: JSON.stringify({
                      ...controls,
                      plan_hash: current.plan_hash,
                    }),
                  },
                );
                onStarted(value);
              })
            }
          >
            按原范围新建全量整理（调用模型）
          </button>
        </div>
      )}
    </aside>
  );
}
