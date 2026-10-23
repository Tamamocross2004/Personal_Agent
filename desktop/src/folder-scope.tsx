import React, { useEffect, useState } from "react";
import { locationLabel } from "./folder-location";

export function FolderScope({
  api,
  saved,
  controls,
  valid,
  canSplit,
  onSelect,
}: any) {
  const [view, setView] = useState<any>(null),
    [error, setError] = useState("");
  const [offset, setOffset] = useState(0),
    [next, setNext] = useState<any[]>([]);
  const [nextError, setNextError] = useState("");
  useEffect(() => {
    setOffset(0);
    setView(null);
  }, [saved.id]);
  useEffect(() => {
    let active = true;
    setError("");
    api(`/folder-jobs/${saved.id}/scope?offset=${offset}&limit=50`)
      .then((value: any) => {
        if (active) setView({ ...value, forId: saved.id });
      })
      .catch((e: any) => {
        if (active) {
          setError(String(e));
          setView(null);
        }
      });
    return () => {
      active = false;
    };
  }, [
    saved.id,
    saved.status,
    saved.batches?.length,
    saved.coverage?.next_offset,
    offset,
  ]);
  const key = JSON.stringify(controls);
  useEffect(() => {
    let active = true;
    setNext([]);
    setNextError("");
    if (
      !valid ||
      !saved.coverage?.remaining_segments ||
      ["running", "queued"].includes(saved.status)
    )
      return;
    const timer = setTimeout(async () => {
      const choices = canSplit ? [false, true] : [false];
      const values = await Promise.allSettled(
        choices.map((retry_split) =>
          api(`/folder-jobs/${saved.id}/next-scope`, {
            method: "POST",
            body: JSON.stringify({ ...controls, retry_split }),
          }),
        ),
      );
      if (!active) return;
      setNext(
        values
          .filter((v) => v.status === "fulfilled")
          .map((v: any) => ({ ...v.value, controlsKey: key })),
      );
      setNextError(
        values
          .filter((v) => v.status === "rejected")
          .map((v: any) => String(v.reason))
          .join("；"),
      );
    }, 250);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [
    saved.id,
    saved.status,
    saved.coverage?.remaining_segments,
    key,
    valid,
    !!canSplit,
  ]);
  const current = view?.forId === saved.id ? view : null;
  return (
    <section className="folder-scope" aria-label="范围与任务链">
      <h4>范围与任务链 · 本地查看</h4>
      {error && <p role="alert">范围记录读取失败：{error}</p>}
      {!current && !error && <p>正在读取保存的范围记录…</p>}
      {current && (
        <>
          <p>
            固定索引 {current.generation} · 范围文件{" "}
            {current.scope_files.length}份
          </p>
          <p>{current.meaning}</p>
          {current.warnings.map((s: string, i: number) => (
            <p key={i} role="alert">
              任务链记录不完整：{s}
            </p>
          ))}
          {current.mode === "limited_retrieval" ? (
            <p>
              局部检索：确认返回的有限证据 {current.confirmed_evidence}
              段；全文阅读率不适用。
            </p>
          ) : (
            <>
              <p className="scope-counts">
                共{current.total_segments}个主片段：有可用结构
                {current.counts.structure_checked}段；已返回但响应未完成
                {current.counts.response_incomplete}段；发送结果未确认
                {current.counts.delivery_unknown}段；无确认发送记录
                {current.counts.not_sent}段。
              </p>
              <p>
                曾失败后有可用结构：{current.recovered_segments}段。
                {current.counts_meaning}
              </p>
              <details>
                <summary>查看连续范围与逐段位置</summary>
                <p>
                  区间为读取计划的0起始片段号，[起点,
                  终点)，不是页码。原始页/行/单元格位置在下表。
                </p>
                <ul>
                  {current.runs.map((r: any, i: number) => (
                    <li key={i}>
                      [{r.start}, {r.end}) {r.name}：{r.label}
                      {r.failed_before && "；保留此前失败尝试"}
                    </li>
                  ))}
                </ul>
                <div className="folder-scope-table">
                  <table>
                    <thead>
                      <tr>
                        <th>片段</th>
                        <th>来源位置</th>
                        <th>记录状态</th>
                        <th>尝试次数</th>
                      </tr>
                    </thead>
                    <tbody>
                      {current.segments.map((s: any) => (
                        <tr key={s.segment}>
                          <td>{s.segment}</td>
                          <td>
                            {s.name} · {locationLabel(s.source_location)} ·
                            块内字符[{s.start}, {s.end})
                          </td>
                          <td>
                            {s.label}
                            {s.failed_attempts > 0 &&
                              `；历史未完成尝试${s.failed_attempts}次`}
                          </td>
                          <td>{s.attempts.length}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <button
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - 50))}
                >
                  上一组范围
                </button>
                <button
                  disabled={current.next_offset >= current.total_segments}
                  onClick={() => setOffset(current.next_offset)}
                >
                  下一组范围
                </button>
              </details>
              <details>
                <summary>
                  查看前序任务与失败尝试（{current.chain.length}个任务）
                </summary>
                {current.chain.map((j: any) => (
                  <article key={j.task_id}>
                    <button
                      disabled={j.task_id === saved.id}
                      onClick={() => onSelect(j.task_id)}
                    >
                      {j.operation === "initial"
                        ? "原任务"
                        : j.operation === "split_retry"
                          ? "拆小重做"
                          : "续读"}{" "}
                      {j.task_id}
                    </button>
                    <p>
                      执行状态 {j.status} · 已知Token {j.known_tokens} ·
                      未知用量事件 {j.unknown_usage_events}
                    </p>
                    {j.batches.map((b: any) => (
                      <p key={b.batch}>
                        第{b.batch}批 [{b.start}, {b.end})：
                        {b.usable_structure
                          ? "有可用结构"
                          : b.confirmed_return
                            ? "已返回但响应未完成"
                            : b.delivery_unknown
                              ? "发送结果未确认"
                              : "未确认发送"}{" "}
                        · 附带上下文{b.context_segments}段 ·{" "}
                        {b.finish_reason || b.status}
                      </p>
                    ))}
                  </article>
                ))}
              </details>
            </>
          )}
        </>
      )}
      {!!next.filter((p) => p.parent_id === saved.id && p.controlsKey === key)
        .length && (
        <aside className="folder-next-scope">
          <h4>下一次操作预计发送范围</h4>
          {next
            .filter((p) => p.parent_id === saved.id && p.controlsKey === key)
            .map((p: any) => (
              <div key={String(p.retry_split)}>
                <p>
                  {p.retry_split ? "拆小重做" : "继续整理"}：本次待处理区间 [
                  {p.operation_start}, {p.operation_end})，最多
                  {p.batch_call_limit ?? p.max_batches}
                  次模型调用；首批预计 [{p.operation_start}, {p.first_batch_end}
                  )。
                </p>
                {p.plan_fields && (
                  <p>
                    使用字段意图规划；已有规划会复用，首次规划计入调用上限和同一预算，随后重新估算资料批次。
                  </p>
                )}
                <p>
                  服务：
                  {p.services.length
                    ? "聊天模型；不调用检索嵌入或重排"
                    : "输出空间或预算不足，预计不发送"}
                  。预算{p.budget} Token，单次输出设置{p.output_tokens}
                  ；首批预计可用输出上限{p.decision.effective_output_tokens}
                  ，沿用原任务强度。模型：{p.model.provider}/{p.model.model}。
                </p>
                <p>{p.meaning}</p>
                <details>
                  <summary>首批预计主片段与附带上下文</summary>
                  {p.first_batch.map((s: any, i: number) => (
                    <p key={i}>
                      {s.role === "sequential" ? "主片段" : "附带上下文"}{" "}
                      {s.segment} · {s.name} ·{" "}
                      {locationLabel(s.source_location)} · 块内字符[{s.start},{" "}
                      {s.end})
                    </p>
                  ))}
                </details>
              </div>
            ))}
        </aside>
      )}
      {nextError && <p>当前无法预览后续操作：{nextError}。未发送模型。</p>}
    </section>
  );
}
