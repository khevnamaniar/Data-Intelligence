import { useState } from "react";
import { api, fmtDate, Project } from "../api";
import { Link, navigate } from "../router";
import { Button, Empty, ErrorBox, Field, inputCls, Panel, Spinner, useAsync } from "../ui";

export function DashboardPage() {
  const { data, error, loading } = useAsync(() => api.get<{ projects: Project[] }>("/api/projects"), []);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const demo = async () => {
    setBusy(true);
    try {
      const r = await api.post<{ project: Project }>("/api/projects/demo");
      navigate(`/p/${r.project.id}/sources`);
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
          <p className="text-sm text-slate-600">Each project holds related sources, questions, analyses and reports.</p>
        </div>
        <div className="flex gap-2">
          <Button kind="secondary" onClick={demo} disabled={busy}>{busy ? "Creating demo…" : "Create demo project"}</Button>
          <Button onClick={() => navigate("/projects/new")}>New project</Button>
        </div>
      </div>
      <ErrorBox error={err || error} />
      {loading ? <Spinner label="Loading projects…" /> : data && data.projects.length === 0 ? (
        <Empty title="No projects yet">Create a project, or try the fictional <b>Consumer Health Market Analysis</b> demo to see every feature working.</Empty>
      ) : (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-slate-600">
              <tr><th className="px-4 py-2 font-medium">Project</th><th className="px-4 py-2 font-medium">Industry</th><th className="px-4 py-2 font-medium">Sources</th><th className="px-4 py-2 font-medium">Last analysis</th></tr>
            </thead>
            <tbody>
              {data?.projects.map((p) => (
                <tr key={p.id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-4 py-3"><Link to={`/p/${p.id}`} className="font-medium text-slate-900 hover:underline">{p.name}</Link>
                    {p.is_demo && <span className="ml-2 text-xs text-slate-500">demo</span>}
                    {p.business_objective && <p className="text-xs text-slate-500 line-clamp-1">{p.business_objective}</p>}</td>
                  <td className="px-4 py-3 text-slate-600">{p.industry || "—"}</td>
                  <td className="px-4 py-3 text-slate-600">{p.file_count}</td>
                  <td className="px-4 py-3 text-slate-600">{p.last_analysis ? fmtDate(p.last_analysis) : "Not yet analysed"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function ProjectForm({ initial, onSubmit, submitLabel }: { initial?: Partial<Project>; onSubmit: (v: Partial<Project>) => Promise<void>; submitLabel: string }) {
  const [v, setV] = useState<Partial<Project>>({ name: "", description: "", industry: "", geography: "", time_period: "", business_objective: "", focus_entity: "", ...initial });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof Project) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setV({ ...v, [k]: e.target.value });
  return (
    <form className="space-y-4" onSubmit={async (e) => { e.preventDefault(); setBusy(true); try { await onSubmit(v); } catch (err) { setError(err); } finally { setBusy(false); } }}>
      <Field label="Project name"><input className={inputCls} required value={v.name} onChange={set("name")} placeholder="e.g. OptiChoice Market Analysis" /></Field>
      <Field label="Description"><textarea className={inputCls} rows={2} value={v.description} onChange={set("description")} /></Field>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Industry"><input className={inputCls} value={v.industry} onChange={set("industry")} /></Field>
        <Field label="Geography"><input className={inputCls} value={v.geography} onChange={set("geography")} /></Field>
        <Field label="Time period"><input className={inputCls} value={v.time_period} onChange={set("time_period")} placeholder="e.g. 2023–2025" /></Field>
      </div>
      <Field label="Business objective"><textarea className={inputCls} rows={2} value={v.business_objective} onChange={set("business_objective")} placeholder="What decision will this analysis support?" /></Field>
      <Field label="Your company / brand (optional)" hint="Used to separate you from competitors in portfolio, pricing and competitive gap analysis.">
        <input className={inputCls} value={v.focus_entity} onChange={set("focus_entity")} />
      </Field>
      <ErrorBox error={error} />
      <Button type="submit" disabled={busy}>{busy ? "Saving…" : submitLabel}</Button>
    </form>
  );
}

export function NewProjectPage() {
  return (
    <div className="max-w-2xl">
      <h1 className="mb-4 text-2xl font-semibold tracking-tight">New project</h1>
      <Panel>
        <ProjectForm submitLabel="Create project" onSubmit={async (v) => {
          const r = await api.post<{ project: Project }>("/api/projects", v);
          navigate(`/p/${r.project.id}/sources`);
        }} />
      </Panel>
    </div>
  );
}
