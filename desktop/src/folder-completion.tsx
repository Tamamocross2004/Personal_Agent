import React from "react";

export function FolderCompletion({
  enabled,
  onChange,
  maxCalls,
  setMaxCalls,
  planFields,
  setPlanFields,
  prefix = "",
}: any) {
  return (
    <div className="folder-completion">
      <label className="folder-completion-option">
        <input
          type="checkbox"
          aria-label={`${prefix}持续整理至末尾`}
          checked={enabled}
          onChange={(e) => onChange(e.target.checked)}
        />
        持续整理至末尾（受同一个总预算和调用上限限制）
      </label>
      {enabled && (
        <>
          {setPlanFields && (
            <label className="folder-completion-option">
              <input
                type="checkbox"
                aria-label={`${prefix}先规划字段意图`}
                checked={planFields}
                onChange={(e) => setPlanFields(e.target.checked)}
              />
              先按问题区分属性事实与文字摘录（增加一次模型调用）
            </label>
          )}
          <label>
            本次最多模型调用{" "}
            <input
              type="number"
              min={1}
              max={64}
              aria-label={`${prefix}持续整理调用上限`}
              value={maxCalls}
              onChange={(e) => setMaxCalls(Number(e.target.value))}
            />
          </label>
          <p>
            启用后用此调用上限替代普通批数上限。每批扣减同一个Token预算，不自动加预算；遇到截断、格式或引用检查失败、用量未知及停止请求即结束，不自动重试。达到末尾也只表示已解析文字送达，仍须核对抽取结果。
          </p>
          {planFields && (
            <p>
              字段规划只接收问题，不接收资料。规划调用计入上述上限和同一预算；续读复用原规划。规划仍可能理解有误，保存结果可核对；首批预览在规划后重新估算。
            </p>
          )}
        </>
      )}
    </div>
  );
}
