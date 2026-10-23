import React, { useEffect, useRef, useState } from "react";
import { ModelStrength } from "./model-strength";
import { locationLabel } from "./folder-location";
import { FolderTransmission } from "./folder-transmission";

function ProjectGroup({ group, cite }: any) {
  const labels: any = {
    direct: "原文整理",
    inference: "推断",
    difference: "差异",
    conflict: "模型判断的矛盾",
    unknown: "未知",
  };
  const refs = (ids: string[]) =>
    ids.map((id) => (
      <button
        key={id}
        className="folder-citation"
        onClick={() => cite(group.job_id, id)}
      >
        [{id}]{" "}
        {group.evidence.find((e: any) => e.citation_id === id)?.name || id}
      </button>
    ));
  return (
    <article className="project-group">
      <p>
        来源任务 {group.job_id} · 第{group.batch || "短答"}批 · 固定索引{" "}
        {group.generation} · 未正式审稿
      </p>
      {group.answer?.findings.map((f: any, i: number) => (
        <div key={i}>
          <strong>{labels[f.kind] || f.kind}</strong>
          <p>{f.statement}</p>
          {f.assumptions?.map((s: string, j: number) => (
            <p key={"a" + j}>假设：{s}</p>
          ))}
          {f.limitations?.map((s: string, j: number) => (
            <p key={"l" + j}>限制：{s}</p>
          ))}
          {f.relation && (
            <p>
              比较字段：{f.relation.field}；共同范围：
              {f.relation.common_scope || "未明确"}；{f.relation.reason}
            </p>
          )}
          {refs(f.citations)}
        </div>
      ))}
      {!!group.answer?.comparison.length && (
        <div className="folder-scope-table">
          <table>
            <thead>
              <tr>
                <th>对象</th>
                <th>字段</th>
                <th>原值 / 状态</th>
                <th>来源</th>
              </tr>
            </thead>
            <tbody>
              {group.answer.comparison.map((r: any, i: number) => (
                <tr key={i}>
                  <td>{r.subject || "对象未明确"}</td>
                  <td>{r.field}</td>
                  <td>
                    {r.status === "blank"
                      ? "原文空白"
                      : r.status === "unknown"
                        ? "未知"
                        : r.value}
                  </td>
                  <td>{refs(r.citations)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {group.answer?.unknowns.map((s: string, i: number) => (
        <p key={i}>待核对：{s}</p>
      ))}
    </article>
  );
}

export function FolderProjects({
  api,
  folderId,
  index,
  job,
  setJob,
  busy,
  act,
}: any) {
  const [rows, setRows] = useState<any[]>([]),
    [pid, setPid] = useState(() => {
      try {
        return localStorage.getItem(`folder-project:${folderId}`) || "";
      } catch {
        return "";
      }
    });
  const [title, setTitle] = useState(""),
    [objective, setObjective] = useState(""),
    [fields, setFields] = useState("摘录");
  const [vid, setVid] = useState(""),
    [version, setVersion] = useState<any>(null),
    [offset, setOffset] = useState(0);
  const [action, setAction] = useState("update"),
    [selected, setSelected] = useState<string[]>([]),
    [instruction, setInstruction] = useState("");
  const [budget, setBudget] = useState(10000),
    [output, setOutput] = useState(1200),
    [batches, setBatches] = useState(3),
    [size, setSize] = useState(6),
    [mode, setMode] = useState("adaptive"),
    [strength, setStrength] = useState("default");
  const [preview, setPreview] = useState<any>(null),
    [error, setError] = useState(""),
    [question, setQuestion] = useState("");
  const [response, setResponse] = useState<any>(null),
    [citation, setCitation] = useState<any>(null),
    [exported, setExported] = useState<any>(null);
  const [includeResults, setIncludeResults] = useState(true),
    [includeDialogue, setIncludeDialogue] = useState(false);
  const [followPreview, setFollowPreview] = useState<any>(null);
  const alive = useRef(true),
    requestTurn = useRef(0);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      requestTurn.current++;
    };
  }, []);
  const project = rows.find((p) => p.id === pid);
  useEffect(() => {
    try {
      if (pid) localStorage.setItem(`folder-project:${folderId}`, pid);
      else localStorage.removeItem(`folder-project:${folderId}`);
    } catch {}
  }, [folderId, pid]);
  const refresh = async () => {
    const r = await api(`/folders/${folderId}/projects`);
    if (alive.current) setRows(r);
    return r;
  };
  useEffect(() => {
    let current = true;
    api(`/folders/${folderId}/projects`)
      .then((r: any[]) => {
        if (current) setRows(r);
      })
      .catch((e: any) => {
        if (current) setError(String(e));
      });
    return () => {
      current = false;
    };
  }, [folderId, index?.active?.id, job?.status]);
  useEffect(() => {
    if (!project?.pending) return;
    const timer = setInterval(
      () =>
        refresh().catch((e: any) => {
          if (alive.current) setError(String(e));
        }),
      1000,
    );
    return () => clearInterval(timer);
  }, [pid, project?.pending]);
  useEffect(() => {
    setTitle(project?.config.title || "");
    setObjective(project?.config.objective || "");
    setFields(project?.config.fields.join("\n") || "摘录");
  }, [pid, project?.revision]);
  useEffect(() => {
    setVid(project?.head || "");
    setOffset(0);
    setSelected([]);
    setResponse(null);
    setCitation(null);
  }, [pid, project?.head]);
  useEffect(() => {
    let current = true;
    setVersion(null);
    if (vid)
      api(`/folder-project-versions/${vid}?offset=${offset}&limit=10`)
        .then((v: any) => {
          if (current) setVersion(v);
        })
        .catch((e: any) => {
          if (current) setError(String(e));
        });
    return () => {
      current = false;
    };
  }, [vid, offset]);
  const valid =
    Number.isInteger(budget) &&
    budget >= 256 &&
    budget <= 50000 &&
    Number.isInteger(output) &&
    output >= 128 &&
    output <= 4096 &&
    Number.isInteger(batches) &&
    batches >= 1 &&
    batches <= 8 &&
    Number.isInteger(size) &&
    size >= 1 &&
    size <= 6;
  const payload = {
    action,
    expected_head: project?.head || null,
    revision: project?.revision,
    instruction,
    file_ids: selected,
    budget,
    output_tokens: output,
    max_batches: batches,
    batch_size: size,
    batch_mode: mode,
    strength,
  };
  const key = JSON.stringify(payload);
  const followKey = JSON.stringify({
    version_id: vid,
    query: question,
    budget,
    output_tokens: output,
    strength,
    include_results: includeResults,
    include_dialogue: includeDialogue,
  });
  useEffect(() => {
    let current = true;
    setFollowPreview(null);
    if (!pid || !vid || !question.trim() || !valid) return;
    const timer = setTimeout(
      () =>
        api(`/folder-projects/${pid}/followups/preview`, {
          method: "POST",
          body: followKey,
        })
          .then((v: any) => {
            if (current) setFollowPreview({ ...v, key: followKey });
          })
          .catch((e: any) => {
            if (current) setFollowPreview({ error: String(e), key: followKey });
          }),
      300,
    );
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [pid, followKey, valid, project?.messages.length]);
  useEffect(() => {
    let current = true;
    setPreview(null);
    if (!project || !valid || project.pending || !index?.active) return;
    const timer = setTimeout(
      () =>
        api(`/folder-projects/${pid}/preview`, { method: "POST", body: key })
          .then((v: any) => {
            if (current) setPreview({ ...v, key });
          })
          .catch((e: any) => {
            if (current) setPreview({ error: String(e), key });
          }),
      300,
    );
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [pid, key, valid, project?.pending, index?.active?.id]);
  const cite = async (jid: string, cid: string) => {
    const turn = ++requestTurn.current;
    const c = await api(`/folder-jobs/${jid}/citations/${cid}`);
    if (alive.current && turn === requestTurn.current) setCitation(c);
  };
  const values = () =>
    fields
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
  return (
    <details className="folder-projects" open={!!pid}>
      <summary>持续资料项目（追问 / 修订 / 增量更新）</summary>
      <section aria-label="持续资料项目">
        <p>
          固定整理目标与字段，围绕同一结果持续工作。只读查看、配置和导出不调用模型；处理资料与追问会调用云端。增量更新以手动同步后的索引为准。
        </p>
        <label>
          资料项目{" "}
          <select
            aria-label="资料项目"
            value={pid}
            onChange={(e) => {
              requestTurn.current++;
              setPid(e.target.value);
              setError("");
            }}
          >
            <option value="">新建资料项目</option>
            {rows.map((p) => (
              <option key={p.id} value={p.id}>
                {p.config.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          项目名称{" "}
          <input
            aria-label="项目名称"
            value={title}
            maxLength={100}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        <label>
          固定整理目标{" "}
          <textarea
            aria-label="固定整理目标"
            value={objective}
            maxLength={1000}
            onChange={(e) => setObjective(e.target.value)}
          />
        </label>
        <label>
          结果字段（每行一个，最多12个）
          <textarea
            aria-label="项目结果字段"
            value={fields}
            onChange={(e) => setFields(e.target.value)}
          />
        </label>
        <button
          disabled={
            busy || !!project?.pending || !title.trim() || !objective.trim()
          }
          onClick={() =>
            act(async () => {
              const body = {
                title,
                objective,
                fields: values(),
                ...(project ? { revision: project.revision } : {}),
              };
              const p = await api(
                project
                  ? `/folder-projects/${pid}`
                  : `/folders/${folderId}/projects`,
                {
                  method: project ? "PATCH" : "POST",
                  body: JSON.stringify(body),
                },
              );
              await refresh();
              if (alive.current) setPid(p.id);
            })
          }
        >
          {project
            ? "保存项目设置（本地，下次处理生效）"
            : "创建资料项目（本地）"}
        </button>
        {project && (
          <>
            <p>
              整个已建库目录 · 设置版本{project.revision}
              ；更改目标或字段后，下次更新会重新处理全部文件。保存设置不调用模型，名称变化不扩大处理范围。
            </p>
            <div className="knowledge-actions">
              <label>
                项目操作{" "}
                <select
                  aria-label="项目操作"
                  value={action}
                  onChange={(e) => setAction(e.target.value)}
                >
                  <option value="update">首次整理 / 增量更新</option>
                  <option value="revise">修订指定文件</option>
                  <option value="continue">继续上次未完成范围</option>
                </select>
              </label>
              <label>
                项目Token预算{" "}
                <input
                  aria-label="项目Token预算"
                  type="number"
                  min={256}
                  max={50000}
                  value={budget}
                  onChange={(e) => setBudget(Number(e.target.value))}
                />
              </label>
              <label>
                项目单次输出上限{" "}
                <input
                  aria-label="项目单次输出上限"
                  type="number"
                  min={128}
                  max={4096}
                  value={output}
                  onChange={(e) => setOutput(Number(e.target.value))}
                />
              </label>
              <label>
                项目最多批数{" "}
                <input
                  aria-label="项目最多批数"
                  type="number"
                  min={1}
                  max={8}
                  value={batches}
                  onChange={(e) => setBatches(Number(e.target.value))}
                />
              </label>
              <label>
                项目每批片段上限{" "}
                <input
                  aria-label="项目每批片段上限"
                  type="number"
                  min={1}
                  max={6}
                  value={size}
                  onChange={(e) => setSize(Number(e.target.value))}
                />
              </label>
              <select
                aria-label="项目分批策略"
                value={mode}
                onChange={(e) => setMode(e.target.value)}
              >
                <option value="adaptive">自动匹配输出空间</option>
                <option value="fixed">固定片段上限</option>
              </select>
            </div>
            <ModelStrength api={api} value={strength} onChange={setStrength} />
            {action === "revise" && (
              <fieldset>
                <legend>需要修订的文件（其他结果保留）</legend>
                {version?.files.map((f: any) => (
                  <label key={f.file_id}>
                    <input
                      type="checkbox"
                      checked={selected.includes(f.file_id)}
                      onChange={(e) =>
                        setSelected((s) =>
                          e.target.checked
                            ? [...s, f.file_id]
                            : s.filter((x) => x !== f.file_id),
                        )
                      }
                    />
                    {f.name}
                  </label>
                ))}
              </fieldset>
            )}
            {action === "revise" && (
              <label>
                修订要求{" "}
                <textarea
                  aria-label="项目修订要求"
                  value={instruction}
                  maxLength={1000}
                  onChange={(e) => setInstruction(e.target.value)}
                />
              </label>
            )}
            {preview?.key === key &&
              (preview.error ? (
                <p>当前操作：{preview.error}</p>
              ) : (
                <aside className="project-preview">
                  <p>
                    预计重新处理：
                    {preview.target_names.join("、") || "无文件，保存本地版本"}
                    ；保留{preview.carried_groups}个原批次。
                  </p>
                  <p>
                    新增{preview.changes.added.length}，变化
                    {preview.changes.changed.length}，删除
                    {preview.changes.removed.length}。服务：
                    {preview.has_work
                      ? "聊天模型（不调用检索嵌入/重排）"
                      : "本地，无模型调用"}
                    。固定索引 {preview.generation}。
                  </p>
                  <p>{preview.meaning}</p>
                  <FolderTransmission
                    value={preview.first_request_transmission}
                    label="首批发送预览"
                  />
                </aside>
              ))}
            <button
              disabled={
                busy ||
                !!project.pending ||
                !valid ||
                !preview?.plan_hash ||
                preview.key !== key ||
                (vid && vid !== project.head)
              }
              onClick={() =>
                act(async () => {
                  const p = await api(`/folder-projects/${pid}/run`, {
                    method: "POST",
                    body: JSON.stringify({
                      ...payload,
                      plan_hash: preview.plan_hash,
                    }),
                  });
                  await refresh();
                  if (p.pending_job)
                    setJob(await api(`/folder-jobs/${p.pending_job}`));
                })
              }
            >
              执行项目操作（按预览范围，可能调用模型）
            </button>
            {!!project.pending && (
              <p>
                正在处理项目；进程中断不会自动重试。
                {project.pending_job && (
                  <button
                    onClick={() =>
                      act(async () => {
                        setJob(
                          await api(
                            `/folder-jobs/${project.pending_job}/stop`,
                            { method: "POST" },
                          ),
                        );
                      })
                    }
                  >
                    停止项目本次操作
                  </button>
                )}
              </p>
            )}
            <h4>结果版本（只读，旧版不覆盖）</h4>
            <select
              aria-label="项目结果版本"
              value={vid}
              onChange={(e) => {
                setVid(e.target.value);
                setOffset(0);
                setCitation(null);
                setResponse(null);
              }}
            >
              <option value="">尚未形成版本</option>
              {project.versions.map((v: any, i: number) => (
                <option key={v.id} value={v.id}>
                  {i === 0 ? "最新" : "历史"} · {v.action} · {v.status} ·{" "}
                  {v.created}
                </option>
              ))}
            </select>
            {version && (
              <div className="project-version">
                <p>
                  版本 {version.id} ·{" "}
                  {version.status === "structured"
                    ? "已解析范围有可用结构，内容未认证"
                    : "部分完成"}{" "}
                  · 可用结构覆盖{version.coverage.usable_segments}/
                  {version.coverage.total_segments}段 · 未正式审稿。
                </p>
                <p>
                  本版目标：{version.config.objective}；字段：
                  {version.config.fields.join("、")}。{version.meaning}
                </p>
                {index?.active?.id !== version.generation && (
                  <p>
                    当前索引与本结果不同。更新前查看上方变化范围，旧版引用仍保留原来源。
                  </p>
                )}
                {vid !== project.head && (
                  <p>
                    正在查看历史版本；处理操作请先选择最新版本，追问可针对当前历史版。
                  </p>
                )}
                <details>
                  <summary>各文件覆盖与来源版本</summary>
                  {version.files.map((f: any) => (
                    <p key={f.file_id}>
                      {f.name} · SHA256 {f.file_sha256} ·{" "}
                      {
                        version.coverage.files.find(
                          (r: any) => r.file_id === f.file_id,
                        )?.usable_segments
                      }
                      /
                      {
                        version.coverage.files.find(
                          (r: any) => r.file_id === f.file_id,
                        )?.total_segments
                      }
                      段
                    </p>
                  ))}
                </details>
                <button
                  onClick={() =>
                    act(async () => {
                      const e = await api(
                        `/folder-project-versions/${vid}/export`,
                        { method: "POST" },
                      );
                      if (alive.current) setExported({ ...e, version_id: vid });
                    })
                  }
                >
                  导出本版汇总结果（本地）
                </button>
                {exported?.version_id === vid && (
                  <p role="status">
                    项目结果已导出：{exported.directory}
                    <button
                      onClick={() =>
                        window.desktop.openTask(exported.directory)
                      }
                    >
                      打开项目导出文件夹
                    </button>
                  </p>
                )}
                {version.groups.map((g: any) => (
                  <ProjectGroup
                    key={g.job_id + ":" + g.batch}
                    group={g}
                    cite={(j: string, c: string) => act(() => cite(j, c))}
                  />
                ))}
                <button
                  disabled={!offset}
                  onClick={() => setOffset(Math.max(0, offset - 10))}
                >
                  上一组项目结果
                </button>
                <button
                  disabled={version.next_offset >= version.group_count}
                  onClick={() => setOffset(version.next_offset)}
                >
                  下一组项目结果
                </button>
                <label>
                  围绕本版继续追问{" "}
                  <textarea
                    aria-label="项目追问"
                    value={question}
                    maxLength={1000}
                    onChange={(e) => setQuestion(e.target.value)}
                  />
                </label>
                <p>
                  追问会重新检索本版固定来源，历史结果仅作为背景，不当作证据。不会修改本版结果。
                </p>
                <label className="project-context-option">
                  <input
                    type="checkbox"
                    checked={includeResults}
                    onChange={(e) => setIncludeResults(e.target.checked)}
                  />
                  带上与本次原文完全匹配的旧结果
                </label>
                <label className="project-context-option">
                  <input
                    type="checkbox"
                    checked={includeDialogue}
                    onChange={(e) => setIncludeDialogue(e.target.checked)}
                  />
                  带上最近两轮对话（会重发历史文字）
                </label>
                {followPreview?.key === followKey &&
                  (followPreview.error ? (
                    <p>{followPreview.error}</p>
                  ) : (
                    <>
                      <p>
                        {followPreview.meaning}
                        {!followPreview.will_call_model &&
                          " 当前无命中证据，不发送聊天请求。"}
                      </p>
                      <FolderTransmission
                        value={followPreview.transmission}
                        label="追问发送预览"
                      />
                    </>
                  ))}
                <button
                  disabled={
                    busy ||
                    !valid ||
                    !question.trim() ||
                    followPreview?.key !== followKey ||
                    !followPreview?.plan_hash
                  }
                  onClick={() =>
                    act(async () => {
                      const j = await api(`/folder-projects/${pid}/followups`, {
                        method: "POST",
                        body: JSON.stringify({
                          ...JSON.parse(followKey),
                          plan_hash: followPreview.plan_hash,
                        }),
                      });
                      setJob(j);
                      setQuestion("");
                      await refresh();
                    })
                  }
                >
                  追问当前版本（调用模型，不改结果）
                </button>
                {project.messages
                  .filter((m: any) => m.version_id === vid)
                  .map((m: any) => (
                    <button
                      key={m.id}
                      onClick={() =>
                        act(async () => {
                          const token = ++requestTurn.current;
                          const r = await api(`/folder-jobs/${m.job_id}`);
                          if (alive.current && token === requestTurn.current)
                            setResponse({ ...r, version_id: vid });
                        })
                      }
                    >
                      {m.query} · {m.status}（查看已保存回答）
                    </button>
                  ))}
                {response?.version_id === vid && (
                  <aside>
                    <p>
                      保存的追问：
                      {response.result?.content_status || response.status} ·
                      未正式审稿
                    </p>
                    <FolderTransmission
                      value={response.request?.transmission}
                      label="追问实际发送构成"
                    />
                    {response.result?.answer ? (
                      <ProjectGroup
                        group={{
                          job_id: response.id,
                          generation: response.inputs.generation,
                          answer: response.result.answer,
                          evidence: response.evidence,
                        }}
                        cite={(j: string, c: string) => act(() => cite(j, c))}
                      />
                    ) : (
                      <p>
                        {response.result?.message ||
                          response.message ||
                          "尚未形成可用结构"}
                      </p>
                    )}
                  </aside>
                )}
              </div>
            )}
          </>
        )}
        {citation && (
          <aside className="project-citation">
            <h4>项目引用 · 保存的原文</h4>
            <button onClick={() => setCitation(null)}>关闭项目引用</button>
            <p>
              {citation.name} · {locationLabel(citation.block.location)} ·
              SHA256 {citation.file_sha256}
            </p>
            <pre>{citation.delivered.text}</pre>
          </aside>
        )}
        {error && <p role="alert">{error}</p>}
      </section>
    </details>
  );
}
