import React, { useEffect, useState } from "react";

export const strengthLabel = (value?: string) =>
  (({ default: "默认", low: "低", medium: "中", high: "高" }) as any)[
    value || "default"
  ] || value;

export function ModelStrength({ api, value, onChange, provider, model }: any) {
  const [cap, setCap] = useState<any>(null),
    [error, setError] = useState("");
  useEffect(() => {
    let disposed = false;
    setCap(null);
    setError("");
    const params = new URLSearchParams();
    if (provider) params.set("provider", provider);
    if (model) params.set("model", model);
    api(`/model-strength?${params}`)
      .then((data: any) => {
        if (!disposed) {
          setCap(data);
          if (!data.supported && value !== "default") onChange("default");
        }
      })
      .catch((e: any) => {
        if (!disposed) setError(String(e));
      });
    return () => {
      disposed = true;
    };
  }, [provider, model]);
  return (
    <div className="model-strength-control">
      <label>
        模型强度（思考）
        <select
          aria-label="模型强度"
          value={value || "default"}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="default">默认（沿用原行为）</option>
          {["low", "medium", "high"].map((level) => (
            <option key={level} value={level} disabled={!cap?.supported}>
              {strengthLabel(level)}
              {cap?.supported
                ? `（${cap.effort_mapping[level]}）`
                : "（尚未支持）"}
            </option>
          ))}
        </select>
      </label>
      <p className="muted">
        {error ||
          (cap
            ? `${cap.provider} / ${cap.model}：${cap.message}`
            : "正在核对模型支持范围…")}
      </p>
    </div>
  );
}
