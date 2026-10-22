export function FolderOutputDiagnostics({ value }: { value: any }) {
  if (!value) return null;
  const labels: Record<string, string> = {
    output_limit: "输出达到上限",
    unfinished_response: "响应未正常结束",
    invalid_structure: "JSON或字段格式错误",
    contract_violation: "引用或字段条件未通过",
    structure_checked: "结构检查完成",
    not_checked: "尚未检查",
  };
  return (
    <aside className="folder-contract-notice" aria-label="输出诊断">
      <strong>{labels[value.code] || value.code}</strong>
      <p>{value.message}</p>
      <p>{value.suggestion}</p>
      {value.usage_status === "unknown" && <p>本次用量未知，保留请求记录。</p>}
    </aside>
  );
}
