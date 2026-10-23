import React from "react";

export function FolderFieldPlan({ plan }: any) {
  if (!plan) return null;
  return (
    <details className="folder-scope" aria-label="已保存的字段意图规划">
      <summary>
        字段意图规划 ·{" "}
        {(
          {
            ready: "已形成规划，未认证正确性",
            inherited: "复用前序规划",
            prepared: "准备中",
            invalid: "结构或字段依据无效",
            unfinished: "响应未完成",
            usage_unknown: "用量未知",
            failed: "请求失败",
            stopped: "已停止后续处理",
            budget_insufficient: "预算不足，未调用",
            budget_exceeded: "返回用量已超过预算，停止后续处理",
            setup_failed: "调用准备失败，未发送请求",
          } as any
        )[plan.status] || plan.status}
      </summary>
      <p>{plan.meaning}</p>
      {plan.source_job_id && <p>规划来源任务：{plan.source_job_id}</p>}
      {(plan.fields || []).map((f: any) => (
        <article key={f.name}>
          <p>
            {f.name}：{f.mode === "verbatim" ? "原样摘录文字" : "属性事实"}
          </p>
          <p>用户问题依据：{f.instruction_quote}</p>
        </article>
      ))}
      {plan.fields?.length === 0 && (
        <p>问题没有形成明确字段列表；后续保留原有问答约定，不编造字段。</p>
      )}
      {plan.model_output && (
        <details>
          <summary>查看规划原始响应</summary>
          <pre>{plan.model_output}</pre>
        </details>
      )}
    </details>
  );
}
