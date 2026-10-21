import React, { useEffect, useState } from "react";

const count = (value: number) =>
  new Intl.NumberFormat("zh-CN").format(value || 0);
export const money = (value: string | null | undefined) =>
  value == null
    ? "未计价"
    : "¥" +
      new Intl.NumberFormat("zh-CN", {
        minimumFractionDigits: 4,
        maximumFractionDigits: 8,
      }).format(Number(value));
const names: Record<string, string> = {
  planning: "规划",
  agent: "研究",
  report_review: "审稿",
  other: "其他",
};

export function UsagePanel({
  value,
  onPricing,
}: {
  value: any;
  onPricing: () => void;
}) {
  if (!value) return <p className="muted">用量记录尚未加载。</p>;
  if (!value.available)
    return (
      <div className="panel-intro">
        <strong>用量未知</strong>
        <p>日志缺失，无法确认调用次数与费用。</p>
      </div>
    );
  return (
    <div className="usage-panel">
      <div className="panel-intro">
        <strong>本轮调用与成本</strong>
        <p>统计已经返回或明确失败的模型请求，正在等待的请求尚未计入。</p>
        {!!value.embedding?.calls && (
          <p>
            检索嵌入：{value.embedding.calls} 次，输入{" "}
            {count(value.embedding.input_tokens)} token，
            {value.embedding.unknown_usage_calls}{" "}
            次用量未知。费用未计价，不含在下方对话模型估算中。建库用量见知识库。
          </p>
        )}
      </div>
      <div className="usage-cards">
        <div>
          <span>已记录模型调用</span>
          <strong>
            {count(value.calls)}
            <small> 次</small>
          </strong>
        </div>
        <div>
          <span>人民币估算</span>
          <strong>{money(value.estimated_cost)}</strong>
        </div>
      </div>
      {value.unpriced_calls > 0 && (
        <div className="usage-note">
          已知部分 {money(value.known_cost)} · {value.unpriced_calls} 次未计价
          <br />
          {value.unpriced_reasons.join("；")}
        </div>
      )}
      <div className="usage-table">
        <table>
          <thead>
            <tr>
              <th>用途</th>
              <th>调用</th>
              <th>输入</th>
              <th>输出</th>
              <th>估算</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(value.by_purpose).map(
              ([key, row]: [string, any]) => (
                <tr key={key}>
                  <td>{names[key]}</td>
                  <td>{row.calls}</td>
                  <td>{count(row.input_tokens)}</td>
                  <td>{count(row.output_tokens)}</td>
                  <td>
                    {money(row.estimated_cost)}
                    {row.unpriced_calls > 0 && (
                      <small>已知 {money(row.known_cost)}</small>
                    )}
                  </td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>
      <div className="quality-row">
        <span>返回 / 失败</span>
        <b>
          {value.returned} / {value.failed} 次
        </b>
      </div>
      <div className="quality-row">
        <span>请求尝试（含重试）</span>
        <b>
          {value.http_attempts_complete ? "" : "至少 "}
          {count(value.http_attempts_lower_bound)} 次
        </b>
      </div>
      <div className="quality-row">
        <span>额外重试</span>
        <b>
          {value.http_attempts_complete ? "" : "至少 "}
          {count(value.retry_attempts_lower_bound)} 次
        </b>
      </div>
      <div className="quality-row">
        <span>输入 token（含缓存）</span>
        <b>{count(value.input_tokens)}</b>
      </div>
      <div className="quality-row">
        <span>缓存命中 / 未命中</span>
        <b>
          {count(value.cached_tokens)} / {count(value.uncached_tokens)}
        </b>
      </div>
      <div className="quality-row">
        <span>缓存命中率</span>
        <b>
          {value.cache_hit_ratio == null
            ? "未知 / 无输入"
            : (value.cache_hit_ratio * 100).toFixed(1) + "%"}
        </b>
      </div>
      <div className="quality-row">
        <span>输出 token</span>
        <b>{count(value.output_tokens)}</b>
      </div>
      {(value.unknown_usage_calls > 0 || value.unknown_cache_calls > 0) && (
        <div className="usage-note">
          用量未知 {value.unknown_usage_calls} 次；缓存拆分未知{" "}
          {value.unknown_cache_calls}{" "}
          次。上面仅汇总已知数值，命中率仅使用可拆分的输入。
        </div>
      )}
      <div className="panel-intro usage-pricing">
        <strong>估算费率</strong>
        <p>
          {value.pricing_basis.includes("task_snapshot")
            ? "包含任务提交时保存的费率快照。"
            : ""}
          {value.pricing_basis.includes("current_reference")
            ? "包含按当前配置回溯估算的历史调用；修改费率会改变这部分估算。"
            : ""}
        </p>
        {value.profiles.map((p: any, i: number) => (
          <div className="usage-rate" key={i}>
            <b>
              {p.model} · {p.label}
            </b>
            <p>
              每百万 token：未缓存输入 {p.input_per_million} 元 / 缓存输入{" "}
              {p.cached_per_million} 元 / 输出 {p.output_per_million} 元
            </p>
            <small>参考日期：{p.verified_at || "用户未注明"}</small>
            {/^https?:\/\//.test(p.source || "") ? (
              <button
                className="rate-source"
                onClick={() => window.desktop.openExternal(p.source)}
              >
                查看费率来源
              </button>
            ) : (
              <small> · {p.source || "用户配置"}</small>
            )}
          </div>
        ))}
        <button className="rate-button" onClick={onPricing}>
          配置估算费率
        </button>
      </div>
      <p className="usage-disclaimer">
        估算 =（未缓存输入 × 单价 + 缓存输入 × 单价 + 输出 × 单价）÷
        1,000,000。固定费率不自动判断高峰、空闲或节假日。
      </p>
      <p className="usage-disclaimer">
        {value.scope}等效 token 是运行预算指标，不直接用于费用计算。
      </p>
      {value.warnings.map((text: string, i: number) => (
        <p key={i} className="usage-note">
          {text}
        </p>
      ))}
    </div>
  );
}

export function PricingEditor({
  provider,
  model,
  api,
  expanded,
}: {
  provider: string;
  model: string;
  api: (path: string, options?: RequestInit) => Promise<any>;
  expanded: boolean;
}) {
  const [book, setBook] = useState<any>({ profiles: [], presets: [] }),
    [form, setForm] = useState<any>(null),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    setMessage("");
    setForm(null);
    api("/pricing")
      .then((value) => {
        if (live) {
          setBook(value);
          setForm(
            value.profiles.find(
              (p: any) => p.provider === provider && p.model === model,
            ) || {
              provider,
              model,
              label: "自定义费率",
              input_per_million: "",
              cached_per_million: "",
              output_per_million: "",
              source: "用户配置",
              verified_at: "",
            },
          );
        }
      })
      .catch((e) => {
        if (live) setMessage(String(e));
      });
    return () => {
      live = false;
    };
  }, [provider, model]);
  function change(field: string, value: string) {
    setForm({
      ...form,
      [field]: value,
      label: "自定义费率",
      source: "用户配置",
      verified_at: new Date().toISOString().slice(0, 10),
    });
    setMessage("");
  }
  async function save(remove = false) {
    setBusy(true);
    setMessage("");
    try {
      if (
        !remove &&
        ["input_per_million", "cached_per_million", "output_per_million"].some(
          (key) =>
            form[key] === "" ||
            !Number.isFinite(Number(form[key])) ||
            Number(form[key]) < 0,
        )
      )
        throw Error("请填写三个非负费率；免费项目可以明确填0。");
      const value = await api(
        remove
          ? `/pricing?provider=${provider}&model=${encodeURIComponent(model)}`
          : "/pricing",
        {
          method: remove ? "DELETE" : "PUT",
          ...(remove ? {} : { body: JSON.stringify(form) }),
        },
      );
      setBook({ ...book, ...value });
      setMessage(
        remove
          ? "已关闭此模型费用估算；已有任务快照仍保留。"
          : "费率已保存。新任务使用快照，无快照的历史调用按新费率重估。",
      );
      if (remove)
        setForm({
          ...form,
          input_per_million: "",
          cached_per_million: "",
          output_per_million: "",
        });
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="pricing-editor" open={expanded || undefined}>
      <summary>费用估算费率 · 人民币 / 百万 token</summary>
      <p className="muted small">
        当前模型 {provider} / {model}
        。固定参考费率，不自动切换时段，也不会读取或修改平台账单。
      </p>
      {book.warning && <p className="usage-note">{book.warning}</p>}
      {form && (
        <>
          <div className="price-presets">
            {book.presets
              .filter((p: any) => p.provider === provider && p.model === model)
              .map((p: any, i: number) => (
                <button
                  key={i}
                  onClick={() => {
                    setForm(p);
                    setMessage("参考值已填入，点击保存费率生效。");
                  }}
                >
                  {p.label}
                </button>
              ))}
          </div>
          <div className="rate-inputs">
            {[
              ["input_per_million", "未缓存输入"],
              ["cached_per_million", "缓存输入"],
              ["output_per_million", "输出"],
            ].map(([key, label]) => (
              <label key={key}>
                {label}
                <input
                  aria-label={label + "费率"}
                  type="number"
                  min="0"
                  max="1000000"
                  step="any"
                  value={form[key]}
                  onChange={(e) => change(key, e.target.value)}
                />
              </label>
            ))}
          </div>
          <p className="muted small">
            {form.label} · 参考日期 {form.verified_at || "未填写"}
          </p>
          <div className="price-actions">
            <button
              disabled={busy}
              className="rate-button"
              onClick={() => save()}
            >
              保存费率
            </button>
            <button disabled={busy} onClick={() => save(true)}>
              关闭此模型估算
            </button>
          </div>
        </>
      )}
      {message && (
        <p className="usage-note" role="status">
          {message}
        </p>
      )}
    </details>
  );
}
