export function FolderTransmission({ value, label = "发送构成" }: any) {
  if (!value) return null;
  const names: any = {
    system: "系统规则与结构说明",
    question: "本次问题",
    evidence: "本次原文证据",
    reading_scope: "读取范围说明",
    project_context: "项目要求与历史背景",
    field_intents: "字段意图（来自问题，不是证据）",
  };
  const history = value.project_history;
  return (
    <details className="folder-transmission" aria-label={label}>
      <summary>
        {label}：预计输入{value.estimated_input_tokens} Token
        {history ? `，历史正文${history.history_characters}字符` : ""}
      </summary>
      <p>
        本次证据：
        {value.evidence
          .map((e: any) => e.name)
          .filter((n: string, i: number, a: string[]) => a.indexOf(n) === i)
          .join("、") || "无命中原文"}
        ；共{value.evidence.length}段。
      </p>
      {history && (
        <>
          <p>
            原结果背景：{history.selected_groups.length}批；最近对话：
            {history.selected_dialogue_jobs.length}轮（
            {history.recent_dialogue_enabled ? "已显式开启" : "未开启"}）。
          </p>
          <p>
            未发送的旧结果：{history.omitted_groups_before_selection}
            批未纳入候选，{history.excluded_groups_not_delivered}
            批与本次送达文字不完全一致。省略不代表内容不存在。
          </p>
          {history.selected_groups.map((g: any) => (
            <p key={g.job_id + ":" + g.batch}>
              背景任务{g.job_id} · 第{g.batch}批 ·{" "}
              {Array.from(new Set(g.sources.map((s: any) => s.name))).join(
                "、",
              )}
            </p>
          ))}
          <p>{history.meaning}</p>
        </>
      )}
      <table>
        <thead>
          <tr>
            <th>请求部分</th>
            <th>字符</th>
            <th>估算Token</th>
          </tr>
        </thead>
        <tbody>
          {value.sections.map((s: any) => (
            <tr key={s.name}>
              <td>{names[s.name] || s.name}</td>
              <td>{s.characters}</td>
              <td>{s.estimated_tokens}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p>{value.meaning}</p>
    </details>
  );
}
