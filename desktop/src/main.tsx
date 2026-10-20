import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Plus,
  Search,
  Settings,
  ArrowUp,
  Square,
  FolderOpen,
  FileText,
  PanelRightClose,
  PanelRightOpen,
  ChevronDown,
  ChevronRight,
  Check,
  LoaderCircle,
  Archive,
  MoreHorizontal,
  ArrowDownToLine,
  BookOpen,
  FlaskConical,
  Calculator,
  Globe,
  X,
  AlertCircle,
  Command,
} from "lucide-react";
import "./style.css";
import { UsagePanel, PricingEditor, money } from "./usage";

declare global {
  interface Window {
    desktop: {
      connection: () => Promise<{ url: string; token: string }>;
      openTask: (path: string) => Promise<void>;
      openExternal: (url: string) => Promise<void>;
    };
  }
}
type Session = {
  id: string;
  title: string;
  archived: number;
  updated_at: string;
};
type Run = {
  id: string;
  session_id: string;
  question: string;
  task_dir: string;
  state: string;
  created_at: string;
  options: any;
  result: any;
};
type Detail = Run & {
  usage: any;
  files: { name: string; size: number; modified: number; type: string }[];
  review: any;
  evidence: any;
  quality: any;
  contract: any;
  plan: any;
  claims: any;
  sources: any;
};
type Connection = { url: string; token: string };
let connection: Connection;
async function api(path: string, options: RequestInit = {}) {
  const response = await fetch(connection.url + "/api" + path, {
    ...options,
    headers: {
      Authorization: "Bearer " + connection.token,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "本地服务连接失败" }));
    throw Error(
      typeof error.detail === "string"
        ? error.detail
        : JSON.stringify(error.detail),
    );
  }
  return response.json();
}
async function fileResponse(run: string, name: string) {
  const r = await fetch(
    connection.url +
      `/api/runs/${run}/files/` +
      name.split("/").map(encodeURIComponent).join("/"),
    { headers: { Authorization: "Bearer " + connection.token } },
  );
  if (!r.ok) {
    const e = await r.json();
    throw Error(e.detail || "无法读取文件");
  }
  return r;
}
const labels: Record<string, string> = {
  queued: "排队中",
  running: "研究中",
  cancelling: "正在停止",
  completed: "研究结束",
  partial: "提前收尾",
  failed: "执行失败",
  cancelled: "已取消",
  interrupted: "已中断",
  imported: "历史记录",
};
const active = (r: Run) =>
  ["queued", "running", "cancelling"].includes(r.state);
const time = (value: string) =>
  new Date(value).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
const number = (value: number) =>
  new Intl.NumberFormat("zh-CN").format(value || 0);
const kindLabel: Record<string, string> = {
  direct: "直接资料",
  calculated: "计算所得",
  observed: "工具 / 文件实读",
  inference: "依赖假设",
  unknown: "未知 / 冲突",
};

function App() {
  const [ready, setReady] = useState(false),
    [error, setError] = useState(""),
    [config, setConfig] = useState<any>(null);
  const [sessions, setSessions] = useState<Session[]>([]),
    [sid, setSid] = useState(""),
    [runs, setRuns] = useState<Run[]>([]),
    [rid, setRid] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null),
    [query, setQuery] = useState(""),
    [archived, setArchived] = useState(false),
    [draft, setDraft] = useState("");
  const [options, setOptions] = useState<any>({
    provider: "deepseek",
    model: "deepseek-flash",
    max_steps: 30,
    budget: 300000,
    memory: true,
  });
  const [settings, setSettings] = useState(false),
    [inspector, setInspector] = useState(true),
    [tab, setTab] = useState("report"),
    [selectedFile, setSelectedFile] = useState("report.md");
  const [pricingExpanded, setPricingExpanded] = useState(false);
  const [content, setContent] = useState(""),
    [fileError, setFileError] = useState(""),
    [busy, setBusy] = useState(false),
    [events, setEvents] = useState<Record<string, any[]>>({}),
    [source, setSource] = useState<any>(null);
  const [renaming, setRenaming] = useState<Session | null>(null),
    [newTitle, setNewTitle] = useState("");
  const [statistics, setStatistics] = useState<
    Record<string, { tokens: number; tools: number }>
  >({});
  const feed = useRef<HTMLDivElement>(null);
  async function refreshSessions() {
    setSessions(await api("/sessions?archived=" + archived));
  }
  useEffect(() => {
    (async () => {
      try {
        connection = await window.desktop.connection();
        const c = await api("/config");
        setConfig(c);
        setOptions({
          provider: c.provider,
          model: c.model,
          max_steps: c.max_steps,
          budget: c.budget,
          memory: c.memory,
        });
        setReady(true);
      } catch (e) {
        setError(String(e));
      }
    })();
  }, []);
  useEffect(() => {
    if (!ready) return;
    refreshSessions().catch((e) => setError(e.message));
  }, [ready, archived]);
  useEffect(() => {
    if (!sid) {
      setRuns([]);
      setRid("");
      setDetail(null);
      return;
    }
    let live = true;
    const refresh = async () => {
      try {
        const list: Run[] = await api(`/sessions/${sid}/runs`);
        if (live) {
          setRuns(list);
          setRid((old) =>
            list.some((r) => r.id === old) ? old : list.at(-1)?.id || "",
          );
        }
      } catch (e) {
        if (live) setError(String(e));
      }
    };
    refresh();
    const timer = setInterval(refresh, 2000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [sid]);
  useEffect(() => {
    if (!rid) {
      setDetail(null);
      return;
    }
    setDetail(null);
    let live = true;
    const refresh = async () => {
      try {
        const value = await api(`/runs/${rid}`);
        if (live) setDetail(value);
      } catch (e) {
        if (live) setError(String(e));
      }
    };
    refresh();
    const timer = setInterval(refresh, 1800);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [rid]);
  useEffect(() => {
    if (!rid || !ready) return;
    const controller = new AbortController();
    let cursor = 0;
    setEvents((old) => ({ ...old, [rid]: [] }));
    setStatistics((old) => ({ ...old, [rid]: { tokens: 0, tools: 0 } }));
    (async () => {
      while (!controller.signal.aborted) {
        try {
          const r = await fetch(
            connection.url + `/api/runs/${rid}/events?cursor=${cursor}`,
            {
              headers: { Authorization: "Bearer " + connection.token },
              signal: controller.signal,
            },
          );
          if (!r.ok || !r.body) throw Error("事件连接中断");
          const reader = r.body.getReader(),
            decoder = new TextDecoder();
          let buffer = "",
            ended = false;
          while (!ended) {
            const item = await reader.read();
            if (item.done) break;
            buffer += decoder.decode(item.value, { stream: true });
            let at;
            while ((at = buffer.indexOf("\n\n")) >= 0) {
              const block = buffer.slice(0, at);
              buffer = buffer.slice(at + 2);
              const data = block
                .split("\n")
                .find((x) => x.startsWith("data: "));
              if (!data) continue;
              const type = block
                .split("\n")
                .find((x) => x.startsWith("event: "))
                ?.slice(7);
              const id = block.split("\n").find((x) => x.startsWith("id: "));
              if (id) cursor = Number(id.slice(4));
              const value = JSON.parse(data.slice(6));
              if (type === "state") {
                ended = true;
                break;
              }
              setEvents((old) => ({
                ...old,
                [rid]: [
                  ...(old[rid] || []),
                  { ...value, _cursor: cursor },
                ].slice(-400),
              }));
              setStatistics((old) => {
                const previous = old[rid] || { tokens: 0, tools: 0 };
                return {
                  ...old,
                  [rid]: {
                    tokens:
                      previous.tokens +
                      (value.kind === "llm_call"
                        ? value.equivalent_tokens || 0
                        : 0),
                    tools:
                      previous.tools + (value.kind === "tool_call" ? 1 : 0),
                  },
                };
              });
            }
          }
          await reader.cancel();
          return;
        } catch (e) {
          if (controller.signal.aborted) return;
          await new Promise((r) => setTimeout(r, 2000));
        }
      }
    })();
    return () => controller.abort();
  }, [rid, ready]);
  useEffect(() => {
    if (feed.current) feed.current.scrollTop = feed.current.scrollHeight;
  }, [runs.length, events[rid]?.length]);
  useEffect(() => {
    setSource(null);
  }, [rid]);
  useEffect(() => {
    if (!rid) return;
    const controller = new AbortController();
    let live = true;
    const name = tab === "report" ? "report.md" : selectedFile;
    if (tab !== "report" && tab !== "files") return;
    setContent("");
    setFileError("");
    if (/\.(png|jpg|jpeg|webp)$/i.test(name)) return;
    fileResponse(rid, name)
      .then((r) => r.text())
      .then((t) => {
        if (live) setContent(t);
      })
      .catch((e) => {
        if (live) setFileError(e.message);
      });
    return () => {
      live = false;
      controller.abort();
    };
  }, [
    rid,
    tab,
    selectedFile,
    detail?.files.find(
      (f) => f.name === (tab === "report" ? "report.md" : selectedFile),
    )?.modified,
  ]);
  async function create() {
    try {
      const s = await api("/sessions", { method: "POST" });
      setArchived(false);
      setSessions((old) => [s, ...old]);
      setSid(s.id);
      setDraft("");
    } catch (e) {
      setError(String(e));
    }
  }
  async function submit() {
    if (!draft.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      let sessionId = sid;
      if (!sessionId) {
        const s = await api("/sessions", { method: "POST" });
        sessionId = s.id;
        setSid(s.id);
      }
      const row = await api(`/sessions/${sessionId}/runs`, {
        method: "POST",
        body: JSON.stringify({ ...options, question: draft }),
      });
      setRuns((old) => [...old, row]);
      setRid(row.id);
      setDraft("");
      setTab("report");
      await refreshSessions();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function stop(force = false) {
    if (!rid) return;
    try {
      await api(`/runs/${rid}/stop?force=${force}`, { method: "POST" });
      setDetail(await api(`/runs/${rid}`));
    } catch (e) {
      setError(String(e));
    }
  }
  async function saveTitle() {
    if (!renaming || !newTitle.trim()) return;
    try {
      await api(`/sessions/${renaming.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title: newTitle.trim() }),
      });
      setRenaming(null);
      await refreshSessions();
    } catch (e) {
      setError(String(e));
    }
  }
  async function archive(s: Session) {
    try {
      await api(`/sessions/${s.id}`, {
        method: "PATCH",
        body: JSON.stringify({ archived: !s.archived }),
      });
      if (s.id === sid) setSid("");
      await refreshSessions();
    } catch (e) {
      setError(String(e));
    }
  }
  async function importHistory() {
    try {
      const value = await api("/import", { method: "POST" });
      await refreshSessions();
      setError(
        value.imported
          ? `已索引 ${value.imported} 个已有任务（未调用模型）`
          : "已有顶层任务均已索引",
      );
    } catch (e) {
      setError(String(e));
    }
  }
  const current = sessions.find((s) => s.id === sid),
    selected = runs.find((r) => r.id === rid);
  const timeline = events[rid] || [],
    usage = statistics[rid]?.tokens || 0;
  return (
    <div className={"app " + (!inspector ? "hide-inspector" : "")}>
      <aside className="sidebar">
        <div className="brand">
          <div className="mark">
            p<span>·</span>
          </div>
          <div>
            Personal Agent<small>你的本地研究工作台</small>
          </div>
          <span className="local-dot" title="本地应用" />
        </div>
        <button className="new-session" onClick={create}>
          <Plus size={17} />
          新建研究
        </button>
        <label className="search">
          <Search size={14} />
          <input
            placeholder="搜索会话"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <div className="section-label">
          {archived ? "已归档" : "研究会话"}
          <button title="切换归档" onClick={() => setArchived(!archived)}>
            <Archive size={13} />
          </button>
        </div>
        <div className="session-list">
          {sessions
            .filter((s) => s.title.toLowerCase().includes(query.toLowerCase()))
            .map((s) => (
              <div
                key={s.id}
                className={"session " + (s.id === sid ? "selected" : "")}
              >
                <button className="session-title" onClick={() => setSid(s.id)}>
                  <FileText size={15} />
                  <span>
                    {s.title}
                    <small>{time(s.updated_at)}</small>
                  </span>
                </button>
                <details className="session-menu">
                  <summary aria-label="会话操作">
                    <MoreHorizontal size={15} />
                  </summary>
                  <div>
                    <button
                      onClick={(e) => {
                        e.currentTarget
                          .closest("details")
                          ?.removeAttribute("open");
                        setRenaming(s);
                        setNewTitle(s.title);
                      }}
                    >
                      重命名
                    </button>
                    <button
                      onClick={(e) => {
                        e.currentTarget
                          .closest("details")
                          ?.removeAttribute("open");
                        archive(s);
                      }}
                    >
                      {s.archived ? "取消归档" : "归档会话"}
                    </button>
                  </div>
                </details>
              </div>
            ))}
          {sessions.length === 0 && (
            <p className="muted empty-list">
              还没有{archived ? "归档" : "研究"}会话
            </p>
          )}
        </div>
        <button className="import-history" onClick={importHistory}>
          <ArrowDownToLine size={14} />
          导入已有任务
        </button>
        <button className="settings-entry" onClick={() => setSettings(true)}>
          <div className="avatar">P</div>
          <div>
            本地工作区<small>{options.model}</small>
          </div>
          <Settings size={16} />
        </button>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="breadcrumb">
            <span>工作区</span>
            <ChevronRight size={13} />
            <strong>{current?.title || "新研究"}</strong>
          </div>
          <div className="header-actions">
            <span className="local-tag">
              <span />
              本机运行
            </span>
            <button
              title="显示或隐藏成果面板"
              onClick={() => setInspector(!inspector)}
            >
              {inspector ? (
                <PanelRightClose size={18} />
              ) : (
                <PanelRightOpen size={18} />
              )}
            </button>
          </div>
        </header>
        {error && (
          <div className="error-banner">
            <AlertCircle size={15} />
            <span>{error}</span>
            <button onClick={() => setError("")}>
              <X size={14} />
            </button>
          </div>
        )}
        <div className="conversation" ref={feed}>
          {!runs.length ? (
            <div className="welcome">
              <div className="welcome-symbol">
                <FlaskConical size={30} />
              </div>
              <div className="eyebrow">PERSONAL RESEARCH WORKSPACE</div>
              <h1>从一个问题，开始研究。</h1>
              <p>查阅资料，运行计算，留下可以追溯的结论。</p>
              <div className="suggestions">
                {[
                  [
                    BookOpen,
                    "比较与分析",
                    "仅依据给定虚构资料，不联网：甲输入2元/百万token，乙9元/百万token，输出价格与质量未知。全文不超过300字，说明能比较什么、不能判断什么。",
                  ],
                  [
                    Calculator,
                    "费用测算",
                    "不联网，仅用虚构价格：EvalAlpha输入2、输出8元/百万token，EvalBeta输入9、输出27元/百万token。输入20万、输出1万token，用Python计算，生成costs.csv与报告，关联关键结论的依据。",
                  ],
                  [
                    Globe,
                    "网页核对",
                    "只阅读 https://docs.python.org/3/library/sqlite3.html 的相关片段，简述Connection作为上下文管理器是否自动关闭连接。关联实际读到的原文，不把抓取时间当成发布日期。",
                  ],
                ].map(([Icon, title, prompt]: any) => (
                  <button key={title} onClick={() => setDraft(prompt)}>
                    <Icon size={19} />
                    <strong>{title}</strong>
                    <span>形成带依据的研究结果</span>
                    <ArrowUp size={14} />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="messages">
              {runs.map((run, index) => (
                <section
                  key={run.id}
                  className={"run-block " + (run.id === rid ? "focused" : "")}
                >
                  <div className="user-message">
                    <div className="user-label">
                      你 <span>{time(run.created_at)}</span>
                    </div>
                    <p>{run.question}</p>
                  </div>
                  <div className="assistant-label">
                    <div className="assistant-icon">
                      <FlaskConical size={15} />
                    </div>
                    <strong>Personal Agent</strong>
                    <span className={"state " + run.state}>
                      {active(run) ? (
                        <LoaderCircle size={12} className="spin" />
                      ) : ["completed", "imported"].includes(run.state) ? (
                        <Check size={12} />
                      ) : (
                        <AlertCircle size={12} />
                      )}{" "}
                      {labels[run.state] || run.state}
                    </span>
                    <button
                      onClick={() => {
                        setRid(run.id);
                        setInspector(true);
                      }}
                    >
                      {run.id === rid ? "正在查看" : "查看本轮"}
                      <ChevronRight size={13} />
                    </button>
                  </div>
                  {run.id === rid && (
                    <div className="execution">
                      <details open={active(run)}>
                        <summary>
                          <ChevronDown size={13} />
                          <span>执行过程</span>
                          <small>
                            {statistics[rid]?.tools || 0} 次工具调用 ·{" "}
                            {number(run.result.equivalent_tokens ?? usage)} 等效
                            token
                          </small>
                        </summary>
                        <div className="event-list">
                          {timeline
                            .filter((e) =>
                              [
                                "tool_call",
                                "tool_result",
                                "notice",
                                "llm_call",
                                "llm_error",
                                "step",
                              ].includes(e.kind),
                            )
                            .slice(-160)
                            .map((e, i) => (
                              <Event key={e._cursor || i} value={e} />
                            ))}
                          {active(run) && (
                            <div className="working">
                              <LoaderCircle className="spin" size={13} />
                              {run.state === "queued"
                                ? "等待前一项研究结束…"
                                : run.state === "cancelling"
                                  ? "等待当前调用返回…"
                                  : "正在处理研究任务…"}
                            </div>
                          )}
                        </div>
                      </details>
                    </div>
                  )}
                  {run.result.answer ? (
                    <div className="answer markdown">
                      <Markdown
                        remarkPlugins={[remarkGfm]}
                        components={markdownComponents(
                          run.id,
                          (name: string) => {
                            setRid(run.id);
                            setSelectedFile(name);
                            setTab("files");
                            setInspector(true);
                          },
                        )}
                      >
                        {run.result.answer}
                      </Markdown>
                    </div>
                  ) : active(run) ? null : (
                    <p className="muted answer">
                      {run.result.error ||
                        "本轮已结束，查看右侧报告、证据及质量记录。"}
                    </p>
                  )}
                  {run.id === rid && detail && (
                    <div className="result-links">
                      <button
                        onClick={() => {
                          setTab("report");
                          setInspector(true);
                        }}
                      >
                        <FileText size={14} />
                        报告
                      </button>
                      <button
                        onClick={() => {
                          setTab("evidence");
                          setInspector(true);
                        }}
                      >
                        <BookOpen size={14} />
                        {detail.claims?.claims?.length || 0} 条结论关联
                      </button>
                      <button
                        onClick={() => {
                          setTab("usage");
                          setInspector(true);
                        }}
                      >
                        {detail.usage?.available
                          ? `${detail.usage.calls} 次模型 · ${money(detail.usage.estimated_cost)}`
                          : "用量统计"}
                      </button>
                      <button
                        onClick={() => window.desktop.openTask(run.task_dir)}
                      >
                        <FolderOpen size={14} />
                        任务目录
                      </button>
                    </div>
                  )}
                </section>
              ))}
            </div>
          )}
        </div>
        <div className="composer-area">
          <div className="composer">
            <textarea
              aria-label="研究问题"
              placeholder={
                runs.length
                  ? "继续提问，或提出一个新的研究要求…"
                  : "你想研究什么？描述问题、资料范围和期望的输出…"
              }
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  submit();
                }
              }}
            />
            <div className="composer-toolbar">
              <button
                className="model-selector"
                onClick={() => setSettings(true)}
              >
                <span className="model-dot" />
                {options.model}
                <ChevronDown size={12} />
              </button>
              <label className="memory-toggle">
                <input
                  type="checkbox"
                  checked={options.memory}
                  onChange={(e) =>
                    setOptions({ ...options, memory: e.target.checked })
                  }
                />
                历史背景
              </label>
              <span className="spacer" />
              {selected && active(selected) && (
                <button
                  className="stop"
                  title={
                    detail?.state === "cancelling"
                      ? "强制结束当前进程，已保存结果可能不完整"
                      : "等待当前调用返回后停止"
                  }
                  onClick={() => stop(detail?.state === "cancelling")}
                >
                  <Square size={12} />
                  {detail?.state === "cancelling" ? "强制停止" : "停止"}
                </button>
              )}
              <button
                className="send"
                aria-label="发送研究"
                disabled={
                  !ready || busy || !draft.trim() || !!current?.archived
                }
                onClick={submit}
              >
                {busy ? (
                  <LoaderCircle size={18} className="spin" />
                ) : (
                  <ArrowUp size={20} />
                )}
              </button>
            </div>
          </div>
          <div className="composer-note">
            <span>Enter 发送 · Shift + Enter 换行</span>
            <span>每轮独立执行 · 模型调用使用项目 API</span>
          </div>
        </div>
      </main>
      {inspector && (
        <aside className="inspector">
          <div className="inspector-heading">
            <span>
              <FileText size={16} />
              工作成果
            </span>
            <button onClick={() => setInspector(false)}>
              <X size={16} />
            </button>
          </div>
          <nav className="tabs">
            {[
              ["report", "报告"],
              ["files", "文件"],
              ["evidence", "依据"],
              ["quality", "质量"],
              ["usage", "用量"],
            ].map(([id, label]) => (
              <button
                className={tab === id ? "active" : ""}
                key={id}
                onClick={() => setTab(id)}
              >
                {label}
              </button>
            ))}
          </nav>
          {!detail ? (
            <div className="inspector-empty">
              <FileText size={34} />
              <strong>研究成果会出现在这里</strong>
              <p>
                报告、数据和依据，
                <br />
                与每一次研究保存在一起。
              </p>
            </div>
          ) : (
            <>
              <div className="artifact-meta">
                <span className={"state " + detail.state}>
                  {labels[detail.state]}
                </span>
                <span>{time(detail.created_at)}</span>
              </div>
              <div className="inspector-body">
                {tab === "usage" && (
                  <UsagePanel
                    value={detail.usage}
                    onPricing={() => {
                      setPricingExpanded(true);
                      setSettings(true);
                    }}
                  />
                )}
                {tab === "report" &&
                  (content ? (
                    <article className="markdown document">
                      <Markdown
                        remarkPlugins={[remarkGfm]}
                        components={markdownComponents(rid, (name: string) => {
                          setSelectedFile(name);
                          setTab("files");
                        })}
                      >
                        {content}
                      </Markdown>
                    </article>
                  ) : (
                    <Empty
                      text={
                        active(detail)
                          ? "报告尚未生成，执行过程在左侧实时更新。"
                          : "本轮没有可预览的 report.md。"
                      }
                    />
                  ))}
                {tab === "files" && (
                  <>
                    <div className="file-list">
                      {detail.files.map((file) => (
                        <button
                          key={file.name}
                          className={selectedFile === file.name ? "active" : ""}
                          onClick={() => setSelectedFile(file.name)}
                        >
                          <FileText size={14} />
                          <span>{file.name}</span>
                          <small>{(file.size / 1024).toFixed(1)} KB</small>
                        </button>
                      ))}
                    </div>
                    <div className="file-heading">
                      {selectedFile}
                      <button
                        onClick={() => window.desktop.openTask(detail.task_dir)}
                        title="在文件夹中查看"
                      >
                        <FolderOpen size={14} />
                      </button>
                    </div>
                    {/\.(png|jpg|jpeg|webp)$/i.test(selectedFile) ? (
                      <ProtectedImage run={rid} name={selectedFile} />
                    ) : selectedFile.endsWith(".csv") ? (
                      <CSV text={content} />
                    ) : selectedFile.endsWith(".md") ? (
                      <article className="markdown document">
                        <Markdown
                          remarkPlugins={[remarkGfm]}
                          components={markdownComponents(
                            rid,
                            (name: string) => {
                              setSelectedFile(name);
                              setTab("files");
                            },
                          )}
                        >
                          {content}
                        </Markdown>
                      </article>
                    ) : (
                      <pre className="code-preview">
                        {content || fileError || "选择文件预览"}
                      </pre>
                    )}
                  </>
                )}
                {tab === "evidence" && (
                  <>
                    <div className="panel-intro">
                      <strong>关键结论与依据</strong>
                      <p>
                        {detail.claims?.claims?.length || 0} 条选定关联 ·{" "}
                        {detail.evidence.status || "unavailable"}
                        <br />
                        关联可定位不等于结论已经证实。
                      </p>
                    </div>
                    {(detail.claims?.claims || []).map(
                      (claim: any, i: number) => (
                        <div className="claim" key={i}>
                          <span className={"kind " + claim.kind}>
                            {kindLabel[claim.kind] || claim.kind}
                          </span>
                          <blockquote>{claim.quote}</blockquote>
                          {claim.assumptions && (
                            <p>
                              <b>假设</b>
                              {claim.assumptions}
                            </p>
                          )}
                          {claim.limits && (
                            <p>
                              <b>限制</b>
                              {claim.limits}
                            </p>
                          )}
                          <div className="sources">
                            {claim.supports?.map((ref: any, j: number) => (
                              <button
                                key={j}
                                onClick={() =>
                                  setSource({
                                    ...detail.sources.records?.find(
                                      (s: any) => s.id === ref.source_id,
                                    ),
                                    excerpt: ref.excerpt,
                                  })
                                }
                              >
                                <BookOpen size={12} />
                                {ref.source_id}
                                <ChevronRight size={12} />
                              </button>
                            ))}
                          </div>
                        </div>
                      ),
                    )}
                    {!detail.claims?.claims?.length && (
                      <Empty text="尚无关键结论关联；这不代表证据审核通过。" />
                    )}
                  </>
                )}
                {tab === "quality" && (
                  <>
                    <div className="panel-intro">
                      <strong>执行与检查状态</strong>
                      <p>任务结束、审稿完成和事实正确是不同的判断。</p>
                    </div>
                    <div className="quality-row">
                      <span>本轮模型</span>
                      <b>{detail.options.model || "历史配置见执行记录"}</b>
                    </div>
                    <div className="quality-row">
                      <span>本轮上限</span>
                      <b>
                        {detail.options.max_steps
                          ? `${detail.options.max_steps} 步 / ${number(detail.options.budget)} 等效 token`
                          : "历史参数见执行记录"}
                      </b>
                    </div>
                    <div className="quality-row">
                      <span>联网约定</span>
                      <b>
                        {detail.contract.network_allowed === false
                          ? "禁止联网"
                          : detail.contract.network_allowed === true
                            ? "允许联网"
                            : "未识别明确约定"}
                      </b>
                    </div>
                    <div className="quality-row">
                      <span>资料范围</span>
                      <b>
                        {detail.contract.evidence_scope === "provided_only"
                          ? "仅给定资料"
                          : "以原始问题为准"}
                      </b>
                    </div>
                    <div className="quality-row">
                      <span>输出文件约定</span>
                      <b>
                        {detail.contract.output_files?.join("、") ||
                          "以原始问题为准"}
                      </b>
                    </div>
                    <div className="quality-row">
                      <span>结束原因</span>
                      <b>{detail.result.stop_reason || detail.state}</b>
                    </div>
                    <div className="quality-row">
                      <span>语义审稿</span>
                      <b>{detail.review.status || "未记录"}</b>
                    </div>
                    <div className="quality-row">
                      <span>审稿问题</span>
                      <b>{detail.review.issues?.length ?? "—"} 项</b>
                    </div>
                    <div className="quality-row">
                      <span>证据关联</span>
                      <b>{detail.evidence.status}</b>
                    </div>
                    <div className="quality-row">
                      <span>语义支持认证</span>
                      <b>未认证</b>
                    </div>
                    <div className="quality-row">
                      <span>等效 token</span>
                      <b>{number(detail.result.equivalent_tokens || usage)}</b>
                    </div>
                    <div className="quality-row">
                      <span>任务步数</span>
                      <b>{detail.result.steps ?? "执行中"}</b>
                    </div>
                    {detail.result.error && (
                      <div className="issue">{detail.result.error}</div>
                    )}
                    {detail.review.issues?.map((v: any, i: number) => (
                      <div className="issue" key={i}>
                        <b>{v.category}</b>
                        <blockquote>{v.quote}</blockquote>
                        <p>{v.message}</p>
                        <p>{v.suggestion}</p>
                      </div>
                    ))}
                    {detail.quality.issues?.map((v: any, i: number) => (
                      <div className="issue" key={i}>
                        {typeof v === "string" ? v : JSON.stringify(v)}
                      </div>
                    ))}
                    {detail.state === "cancelling" && (
                      <button
                        className="danger-button"
                        onClick={() => stop(true)}
                      >
                        强制停止（结果可能不完整）
                      </button>
                    )}
                    <div className="panel-intro">
                      <strong>研究计划</strong>
                      {detail.plan.tasks?.map((p: any, i: number) => (
                        <p key={i}>
                          {p.status === "done" ? "✓" : "○"} {p.title}
                        </p>
                      ))}
                    </div>
                  </>
                )}
              </div>
              <div className="inspector-footer">
                <button
                  onClick={() => window.desktop.openTask(detail.task_dir)}
                >
                  <FolderOpen size={14} />
                  打开任务目录
                </button>
                <span>本地保存</span>
              </div>
            </>
          )}
        </aside>
      )}
      {renaming && (
        <div className="modal-overlay" onClick={() => setRenaming(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>重命名会话</h2>
              <button onClick={() => setRenaming(null)}>
                <X size={18} />
              </button>
            </div>
            <label>
              名称
              <input
                aria-label="会话名称"
                autoFocus
                maxLength={120}
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.nativeEvent.isComposing)
                    saveTitle();
                }}
              />
            </label>
            <button className="primary" onClick={saveTitle}>
              保存名称
            </button>
          </div>
        </div>
      )}
      {settings && (
        <div className="modal-overlay" onClick={() => setSettings(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>运行设置</h2>
              <button onClick={() => setSettings(false)}>
                <X size={20} />
              </button>
            </div>
            <p className="muted">
              设置用于下一轮任务。API Key 仅从项目 .env 读取。
            </p>
            <label>
              模型厂商
              <select
                value={options.provider}
                onChange={(e) =>
                  setOptions({ ...options, provider: e.target.value })
                }
              >
                {["deepseek", "qwen", "glm", "kimi", "custom"].map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
            </label>
            <label>
              模型名称
              <input
                value={options.model}
                onChange={(e) =>
                  setOptions({ ...options, model: e.target.value })
                }
              />
            </label>
            <div className="settings-grid">
              <label>
                最大步数
                <input
                  type="number"
                  min={3}
                  max={200}
                  value={options.max_steps}
                  onChange={(e) =>
                    setOptions({
                      ...options,
                      max_steps: Number(e.target.value),
                    })
                  }
                />
              </label>
              <label>
                等效 token 预算
                <input
                  type="number"
                  min={1000}
                  max={2000000}
                  value={options.budget}
                  onChange={(e) =>
                    setOptions({ ...options, budget: Number(e.target.value) })
                  }
                />
              </label>
            </div>
            <div className="config-state">
              <span
                className={config?.key_configured ? "dot-good" : "dot-bad"}
              />
              {config?.key_configured
                ? "项目默认模型 Key 已配置"
                : "项目默认模型 Key 未配置"}
              <small>审稿上限 {config?.review_max_calls} 次 · 单任务执行</small>
            </div>
            <PricingEditor
              provider={options.provider}
              model={options.model}
              api={api}
              expanded={pricingExpanded}
            />
            <p className="muted small">
              追问可沿用本会话上一轮报告作为历史数据。停止后重新提交会创建新任务，首版尚无检查点恢复。
            </p>
            <button className="primary" onClick={() => setSettings(false)}>
              完成
            </button>
          </div>
        </div>
      )}
      {source && (
        <div className="modal-overlay" onClick={() => setSource(null)}>
          <div
            className="modal source-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <h2>依据 · {source.id}</h2>
              <button onClick={() => setSource(null)}>
                <X size={20} />
              </button>
            </div>
            <p className="muted">
              {source.kind} ·{" "}
              {source.title || source.description || "本次任务保留的原始数据"}
            </p>
            {source.url && (
              <button
                className="source-url"
                onClick={() => window.desktop.openExternal(source.url)}
              >
                {source.url}
              </button>
            )}
            <div className="source-meta">
              读取：{source.recorded_at || "—"}
              <br />
              区间：{source.start ?? "—"} – {source.end ?? "—"}
              <br />
              页面日期：{source.page_date || "未知"}（元数据类型未核实）
              <br />
              截断：
              {source.truncated || source.delivery_truncated ? "是" : "否"}
            </div>
            <h3>所选摘录</h3>
            <pre className="excerpt">{source.excerpt}</pre>
            <h3>保留原文</h3>
            <pre className="source-text">{source.text}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

function markdownComponents(
  run: string,
  openFile?: (name: string) => void,
): any {
  return {
    a: ({ href, children }: any) => (
      <a
        href={href}
        onClick={(e) => {
          e.preventDefault();
          if (/^https?:\/\//.test(href || ""))
            window.desktop.openExternal(href);
          else if (href && !/^[a-z]+:/i.test(href) && !href.startsWith("#")) {
            try {
              openFile?.(decodeURIComponent(href.replace(/^\.\//, "")));
            } catch {}
          }
        }}
      >
        {children}
      </a>
    ),
    img: ({ src, alt }: any) =>
      /^(https?:|data:|file:)/.test(src || "") ? (
        <span className="muted">[外部图片：{alt}]</span>
      ) : (
        <ProtectedImage
          run={run}
          name={decodeURIComponent(src || "")}
          alt={alt}
        />
      ),
  };
}
function ProtectedImage({
  run,
  name,
  alt = "研究图表",
}: {
  run: string;
  name: string;
  alt?: string;
}) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let current = "",
      live = true;
    fileResponse(run, name)
      .then((r) => r.blob())
      .then((blob) => {
        if (live) {
          current = URL.createObjectURL(blob);
          setUrl(current);
        }
      })
      .catch(() => {});
    return () => {
      live = false;
      if (current) URL.revokeObjectURL(current);
    };
  }, [run, name]);
  return url ? (
    <img className="artifact-image" src={url} alt={alt} />
  ) : (
    <span className="muted">图片尚未生成或不可读取</span>
  );
}
function Empty({ text }: { text: string }) {
  return (
    <div className="empty-artifact">
      <FileText size={26} />
      <p>{text}</p>
    </div>
  );
}
function Event({ value: e }: { value: any }) {
  if (e.kind === "llm_error")
    return (
      <div className="notice-event">
        模型请求失败 · {e.error_type} · {e.request_attempts ?? "未知"}{" "}
        次请求尝试
      </div>
    );
  if (e.kind === "step")
    return <div className="step-divider">步骤 {e.step}</div>;
  if (e.kind === "llm_call")
    return (
      <div className="model-event">
        <span className="tiny-dot" />
        {e.purpose === "report_review"
          ? "报告审稿"
          : e.purpose === "planning"
            ? "研究规划"
            : "研究调用"}
        <span>
          {number(e.equivalent_tokens)} 等效 token · {e.seconds}s
        </span>
      </div>
    );
  if (e.kind === "notice")
    return (
      <details className="notice-event">
        <summary>
          <AlertCircle size={12} />
          {String(e.text).slice(0, 66)}
          {String(e.text).length > 66 ? "…" : ""}
        </summary>
        <pre>{e.text}</pre>
      </details>
    );
  return (
    <details className={"tool-event " + (e.is_error ? "tool-error" : "")}>
      <summary>
        {e.kind === "tool_call" ? (
          <Command size={13} />
        ) : e.is_error ? (
          <AlertCircle size={13} />
        ) : (
          <Check size={13} />
        )}
        <strong>{e.name}</strong>
        <span>
          {e.kind === "tool_call"
            ? "调用参数"
            : e.is_error
              ? "返回错误"
              : "执行结果"}
        </span>
        <ChevronRight size={12} />
      </summary>
      <pre>
        {String(
          typeof e.arguments === "object"
            ? JSON.stringify(e.arguments, null, 2)
            : e.arguments || e.content || "",
        ).slice(0, 18000)}
        {String(e.arguments || e.content || "").length > 18000
          ? "\n[预览截断，完整记录见任务目录 trace.jsonl]"
          : ""}
      </pre>
    </details>
  );
}
function CSV({ text }: { text: string }) {
  const rows: string[][] = [];
  let row: string[] = [],
    cell = "",
    quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else quoted = !quoted;
    } else if (c === "," && !quoted) {
      row.push(cell);
      cell = "";
    } else if (c === "\n" && !quoted) {
      row.push(cell.replace(/\r$/, ""));
      rows.push(row);
      row = [];
      cell = "";
      if (rows.length >= 501) break;
    } else cell += c;
  }
  if (cell || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return (
    <div className="csv-preview">
      <table>
        <thead>
          <tr>
            {rows[0]?.map((c, i) => (
              <th key={i}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(1, 501).map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length >= 501 && (
        <p className="muted">仅预览前500行，完整文件在任务目录。</p>
      )}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
