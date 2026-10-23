import React from "react";

export function FolderPlanningPreview({ preview }: any) {
  const plan = preview.planning;
  if (!plan || plan.status === "not_requested") return null;
  return (
    <div aria-label="字段规划发送预览">
      {plan.status === "pending" && (
        <p>
          首次只发送用户问题来规划字段，不含资料；预计输入
          {plan.estimated_input_tokens} Token，规划输出上限
          {plan.max_output_tokens}{" "}
          Token。此调用计入同一预算和调用上限；实际用量返回后再估算资料批次。
        </p>
      )}
      {plan.status === "inherited" && (
        <p>复用原问题的字段规划，不增加规划调用。</p>
      )}
      {plan.status === "budget_insufficient" && (
        <p role="alert">预算不足以规划字段，不会调用模型或发送资料。</p>
      )}
      {plan.data_call_slots === 0 && (
        <p role="alert">
          调用上限仅够字段规划，本次不会发送资料。若要整理原文，请增加调用上限后重新预览。
        </p>
      )}
      {preview.first_batch_provisional && (
        <p>
          下方资料范围是规划前的临时估算，未扣除未知的规划实际用量；字段数量和剩余预算可能使其缩小或无法发送。
        </p>
      )}
    </div>
  );
}
