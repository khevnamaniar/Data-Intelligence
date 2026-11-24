import { useState } from "react";
import { api, fmtDate } from "../api";
import { parseHash } from "../router";
import { Button, Empty, ErrorBox, Panel, Spinner, Tag, useAsync } from "../ui";
import type { ProjectDetail } from "./Project";

export function ReportsPage({ d }: { d: ProjectDetail }) {
  const pid = d.project.id;
  const list = useAsync(() => api.get<{ reports: any[] }>(`/api/projects/${pid}/reports`), [pid]);
  const done = d.runs.filter((r) => r.status === "completed" || r.status === "partial");
  const [runId, setRunId] = useState(parseHash().query.get("run") || done[0]?.id || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState<string | null>(null);
  const create = async () => {
    setBusy(true); setError(null);
    try {
      const r = await api.post<{ report: any }>(`/api/projects/${pid}/reports`, { run_id: runId || null });
      list.reload();
      setOpen(r.report.id);
    } catch (e) { setError(e); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-5">
      <Panel title="Generate a report">
        {done.length === 0 ? <p className="text-sm text-slate-600">Run an analysis first.</p> : (
          <div className="flex flex-wrap items-center gap-2">
            <select aria-label="Analysis run" className="rounded border border-slate-300 bg-white px-2 py-1.5 text-sm" value={runId} onChange={(e) => setRunId(e.target.value)}>
              {done.map((r) => <option key={r.id} value={r.id}>{fmtDate(r.created_at)} · {r.mode} analysis</option>)}
            </select>
            <Button onClick={create} disabled={busy}>{busy ? "Generating…" : "Generate report"}</Button>
          </div>
        )}
        <div className="mt-2"><ErrorBox error={error} /></div>
      </Panel>
      {list.loading ? <Spinner /> : !list.data?.reports.length ? <Empty title="No reports yet" /> : (
        <Panel title="Reports">
          <ul className="divide-y divide-slate-100">
            {list.data.reports.map((r) => (
              <li key={r.id} className="py-3" data-testid="report-row">
                <div className="flex flex-wrap items-center gap-2">
                  <button className="font-medium text-slate-900 hover:underline" onClick={() => setOpen(open === r.id ? null : r.id)}>{r.title}</button>
                  <span className="text-xs text-slate-500">{fmtDate(r.created_at)}</span>
                  <div className="ml-auto flex gap-2">
                    {(["pdf", "docx", "xlsx"] as const).map((f) => (
                      <a key={f} className="rounded border border-slate-300 px-2.5 py-1 text-xs font-medium hover:bg-slate-50" href={`/api/projects/${pid}/reports/${r.id}/export/${f}`} onClick={() => setTimeout(list.reload, 3000)}>
                        Export {f.toUpperCase()}
                      </a>
                    ))}
                    <Button small kind="ghost" onClick={async () => { if (window.confirm("Delete this report?")) { await api.del(`/api/projects/${pid}/reports/${r.id}`); list.reload(); } }}>Delete</Button>
                  </div>
                </div>
                {r.exports.length > 0 && <p className="mt-1 text-xs text-slate-500">Exported: {r.exports.map((e: any) => e.format.toUpperCase()).join(", ")}</p>}
                {open === r.id && <ReportPreview pid={pid} id={r.id} />}
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}

function ReportPreview({ pid, id }: { pid: string; id: string }) {
  const { data, loading, error } = useAsync(() => api.get<{ report: any }>(`/api/projects/${pid}/reports/${id}`), [id]);
  if (loading) return <Spinner />;
  if (error) return <ErrorBox error={error} />;
  const c = data!.report.content;
  return (
    <div className="mt-3 rounded border border-slate-200 bg-slate-50 p-4 text-sm">
      <h3 className="text-lg font-semibold">{c.title}</h3>
      <h4 className="mt-3 font-semibold">Executive Summary</h4>
      <ul className="list-disc pl-5">{c.executive_summary.map((l: string, i: number) => <li key={i}>{l}</li>)}</ul>
      {c.sections.map((s: any) => (
        <div key={s.key} className="mt-3">
          <h4 className="font-semibold">{s.title}</h4>
          <ul className="list-disc pl-5">{s.findings.map((f: any) => <li key={f.id}>{f.title} <Tag>{f.confidence}</Tag></li>)}</ul>
        </div>
      ))}
      <p className="mt-3 text-xs text-slate-500">Includes gap table, data limitations, research plan, recommended actions, future questions, data quality report and source references. Export for the full document.</p>
    </div>
  );
}
