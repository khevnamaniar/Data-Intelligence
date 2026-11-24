import { useEffect } from "react";
import { api, ApiError, ErrorInfo, FileRow, fmtDate, Project, RunSummary } from "../api";
import { Link, navigate } from "../router";
import { Button, cx, Empty, ErrorBox, Panel, Spinner, Tag, useAsync } from "../ui";
import { ProjectForm } from "./Dashboard";
import { SourcesPage } from "./Sources";
import { AnalysePage } from "./Analyse";
import { ResultsPage } from "./Results";
import { AskPage } from "./Ask";
import { ReportsPage } from "./Reports";

export type ProjectDetail = {
  project: Project; files: FileRow[]; runs: RunSummary[]; dashboard: any; date_range: { start: string; end: string } | null;
  urls: any[]; quality: Record<string, number>;
  source_summary?: { counts: Record<string, number>; message: string; can_continue: boolean };
};

export function SourceSummary({ d }: { d: ProjectDetail }) {
  const s = d.source_summary;
  if (!s || !s.message) return null;
  const bad = (s.counts.failed || 0) + (s.counts.partial || 0) > 0;
  return (
    <div role="status" className={`rounded-md border px-3 py-2 text-sm ${bad ? "border-amber-200 bg-amber-50 text-amber-900" : "border-emerald-200 bg-emerald-50 text-emerald-900"}`}>
      {s.message}{bad && s.can_continue && " You can continue with the successfully processed information; see each file for details."}
    </div>
  );
}

const TABS = [["overview", "Overview"], ["sources", "Sources"], ["analyse", "Analyse"], ["results", "Results"], ["ask", "Ask your data"], ["reports", "Reports"], ["settings", "Settings"]];

export function ProjectShell({ id, tab, sub, query }: { id: string; tab: string; sub?: string; query: URLSearchParams }) {
  const st = useAsync(() => api.get<ProjectDetail>(`/api/projects/${id}`), [id, tab]);
  const busy = st.data && (st.data.files.some((f) => f.status === "uploaded" || f.status === "processing") || st.data.runs.some((r) => r.status === "queued" || r.status === "running"));
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(st.reload, 2000);
    return () => clearInterval(t);
  }, [busy]);
  if (st.loading && !st.data) return <Spinner label="Loading project…" />;
  if (st.error && !st.data) return <ErrorBox error={st.error} />;
  const d = st.data!;
  return (
    <div>
      <div className="mb-1 text-sm text-slate-500"><Link to="/" className="hover:underline">Projects</Link> /</div>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{d.project.name}</h1>
          {d.project.business_objective && <p className="max-w-3xl text-sm text-slate-600">{d.project.business_objective}</p>}
        </div>
        {busy && <Spinner label="Processing in the background…" />}
      </div>
      <nav className="mt-4 mb-5 flex gap-1 overflow-x-auto border-b border-slate-200">
        {TABS.map(([k, label]) => (
          <Link key={k} to={`/p/${id}/${k}`} className={cx("whitespace-nowrap border-b-2 px-3 py-2 text-sm", tab === k ? "border-slate-900 font-medium text-slate-900" : "border-transparent text-slate-600 hover:text-slate-900")}>{label}</Link>
        ))}
      </nav>
      {tab === "overview" && <Overview d={d} />}
      {tab === "sources" && <SourcesPage d={d} reload={st.reload} />}
      {tab === "analyse" && <AnalysePage d={d} />}
      {tab === "results" && <ResultsPage d={d} runId={sub || d.runs.find((r) => r.status === "completed" || r.status === "partial")?.id || d.runs[0]?.id} />}
      {tab === "ask" && <AskPage d={d} />}
      {tab === "reports" && <ReportsPage d={d} />}
      {tab === "settings" && <ProjectSettings d={d} reload={st.reload} />}
    </div>
  );
}

function Overview({ d }: { d: ProjectDetail }) {
  const p = d.project, dash = d.dashboard || {};
  const last = d.runs[0];
  const sec = dash.sections || {};
  const list = (title: string, items: any[]) => items && items.length > 0 && (
    <Panel title={title}>
      <ul className="space-y-2">
        {items.map((f: any) => (
          <li key={f.id} className="text-sm"><Link to={`/p/${p.id}/results/${dash.run_id}?f=${f.id}`} className="text-slate-800 hover:underline">{f.title}</Link> <span className="text-xs text-slate-500">({f.confidence})</span></li>
        ))}
      </ul>
    </Panel>
  );
  return (
    <div className="space-y-5">
      <Panel title="Overview">
        <dl className="grid gap-4 text-sm sm:grid-cols-4">
          <div><dt className="text-slate-500">Objective</dt><dd>{p.business_objective || "—"}</dd></div>
          <div><dt className="text-slate-500">Sources</dt><dd>{d.files.length} file(s){d.urls.length ? `, ${d.urls.length} URL(s)` : ""}</dd></div>
          <div><dt className="text-slate-500">Data date range</dt><dd>{d.date_range ? `${d.date_range.start} → ${d.date_range.end}` : "—"}</dd></div>
          <div><dt className="text-slate-500">Last analysis</dt><dd>{last ? `${last.mode} · ${last.status} · ${fmtDate(last.created_at)}` : "Not yet run"}</dd></div>
        </dl>
        {p.learning_objectives.length > 0 && (
          <div className="mt-4 text-sm"><p className="text-slate-500">Learning objectives</p><ul className="list-disc pl-5">{p.learning_objectives.map((q) => <li key={q}>{q}</li>)}</ul></div>
        )}
      </Panel>
      {!dash.run_id ? (
        <Empty title={d.files.length ? "Ready to analyse" : "Add your sources"}>
          {d.files.length ? <Button onClick={() => navigate(`/p/${p.id}/analyse`)}>Configure analysis</Button> : <Button onClick={() => navigate(`/p/${p.id}/sources`)}>Upload files</Button>}
        </Empty>
      ) : (
        <div className="grid gap-5 lg:grid-cols-2">
          {list("Key findings", sec.key_findings)}
          {list("Major trends", sec.major_trends)}
          {list("Opportunities", sec.opportunities)}
          {list("Gaps", sec.gaps)}
          {list("Risks", sec.risks)}
          {dash.research_questions?.length > 0 && (
            <Panel title="Research questions"><ul className="space-y-1 text-sm">{dash.research_questions.map((q: any, i: number) => <li key={i}>{q.question} <Tag tone={q.priority === "high" ? "red" : "slate"}>{q.priority}</Tag></li>)}</ul></Panel>
          )}
          {dash.next_steps?.length > 0 && (
            <Panel title="Recommended next steps"><ul className="list-disc space-y-1 pl-5 text-sm">{dash.next_steps.map((r: any, i: number) => <li key={i}>{r.recommendation}</li>)}</ul></Panel>
          )}
        </div>
      )}
    </div>
  );
}

function ProjectSettings({ d, reload }: { d: ProjectDetail; reload: () => void }) {
  const p = d.project;
  return (
    <div className="max-w-2xl space-y-5">
      <Panel title="Project details">
        <ProjectForm initial={p} submitLabel="Save changes" onSubmit={async (v) => { await api.patch(`/api/projects/${p.id}`, v); reload(); }} />
      </Panel>
      <Panel title="Delete project">
        <p className="mb-3 text-sm text-slate-600">Deletes the project, its analyses and reports, and permanently removes every uploaded file from storage.</p>
        <Button kind="danger" onClick={async () => {
          if (!window.confirm(`Delete "${p.name}" and all of its files? This cannot be undone.`)) return;
          await api.del(`/api/projects/${p.id}`);
          navigate("/");
        }}>Delete project</Button>
      </Panel>
    </div>
  );
}

export function ErrorList({ errors }: { errors: ErrorInfo[] | null | undefined }) {
  if (!errors || !errors.length) return null;
  return <div className="space-y-2">{errors.map((e, i) => <ErrorBox key={i} error={new ApiError(0, e)} />)}</div>;
}
