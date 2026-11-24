import { useState } from "react";
import { api } from "../api";
import { navigate } from "../router";
import { Button, cx, ErrorBox, inputCls, Panel, Spinner, useAsync } from "../ui";
import type { ProjectDetail } from "./Project";
import { SourceSummary } from "./Project";

const MODES = [
  ["quick", "Quick analysis", "Major trends, significant changes, obvious gaps and initial opportunities."],
  ["deep", "Deep analysis", "Adds comparisons, product, category and channel analysis, anomalies, implications, risks and recommendations."],
  ["research", "Research analysis", "Everything in Deep, plus known vs unknown, research gaps, assumptions, hypotheses to validate and suggested methods."],
] as const;

export function AnalysePage({ d }: { d: ProjectDetail }) {
  const pid = d.project.id;
  const caps = useAsync(() => api.get<any>(`/api/projects/${pid}/capabilities`), [pid, d.files.length]);
  const [questions, setQuestions] = useState<string[]>(d.project.learning_objectives.length ? d.project.learning_objectives : [""]);
  const [auto, setAuto] = useState(true);
  const [mode, setMode] = useState<"quick" | "deep" | "research">("deep");
  const usable = d.files.filter((f) => f.status === "processed" || f.status === "partial");
  const [selected, setSelected] = useState<Set<string> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const pending = d.files.filter((f) => f.status === "uploaded" || f.status === "processing").length;
  const sel = selected ?? new Set(usable.map((f) => f.id));

  const run = async () => {
    setBusy(true); setError(null);
    try {
      const qs = questions.map((q) => q.trim()).filter(Boolean);
      const r = await api.post<{ run: { id: string } }>(`/api/projects/${pid}/analyses`, {
        mode, questions: qs, auto_discover: auto || qs.length === 0,
        file_ids: sel.size === usable.length ? null : Array.from(sel),
      });
      navigate(`/p/${pid}/results/${r.run.id}`);
    } catch (e) { setError(e); setBusy(false); }
  };
  const addQ = (q: string) => setQuestions((qs) => (qs.includes(q) ? qs : [...qs.filter(Boolean), q]));

  return (
    <div className="grid gap-5 lg:grid-cols-3">
      <div className="space-y-5 lg:col-span-2">
        <Panel title="What do you want to learn from this data?">
          <div className="space-y-2">
            {questions.map((q, i) => (
              <div key={i} className="flex gap-2">
                <input aria-label={`Question ${i + 1}`} className={inputCls} value={q} placeholder="e.g. Is ROAS being driven by a particular channel?"
                  onChange={(e) => setQuestions(questions.map((x, j) => (j === i ? e.target.value : x)))} />
                {questions.length > 1 && <Button kind="ghost" small onClick={() => setQuestions(questions.filter((_, j) => j !== i))}>Remove</Button>}
              </div>
            ))}
          </div>
          <div className="mt-2"><Button kind="secondary" small onClick={() => setQuestions([...questions, ""])}>+ Add another question</Button></div>
          <label className="mt-4 flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
            <span><b>I don't know — find interesting insights.</b> <span className="text-slate-600">The system also scans everything for unusual growth or decline, anomalies, concentration, gaps, strategy mismatches and contradictions, and generates its own questions.</span></span>
          </label>
        </Panel>
        <Panel title="Analysis mode">
          <div className="grid gap-3 sm:grid-cols-3">
            {MODES.map(([k, label, desc]) => (
              <button key={k} onClick={() => setMode(k)} className={cx("rounded-lg border p-3 text-left", mode === k ? "border-slate-900 ring-1 ring-slate-900" : "border-slate-200 hover:border-slate-400")}>
                <p className="font-medium">{label}</p><p className="mt-1 text-xs text-slate-600">{desc}</p>
              </button>
            ))}
          </div>
        </Panel>
        <Panel title={`Data to analyse (${sel.size} of ${usable.length} files)`}>
          <div className="mb-2"><SourceSummary d={d} /></div>
          {pending > 0 && <div className="mb-2"><Spinner label={`${pending} file(s) still processing — they will be included once ready.`} /></div>}
          {usable.length === 0 ? <p className="text-sm text-slate-600">No processed files yet. Upload files on the Sources tab.</p> : (
            <ul className="grid gap-1 text-sm sm:grid-cols-2">
              {usable.map((f) => (
                <li key={f.id}><label className="flex items-center gap-2">
                  <input type="checkbox" checked={sel.has(f.id)} onChange={(e) => { const s = new Set(sel); if (e.target.checked) s.add(f.id); else s.delete(f.id); setSelected(s); }} />
                  {f.original_name} <span className="text-xs text-slate-500">({f.source_role.replace(/_/g, " ")})</span></label></li>
              ))}
            </ul>
          )}
        </Panel>
        <ErrorBox error={error} />
        <Button onClick={run} disabled={busy || usable.length === 0 || sel.size === 0}>{busy ? "Starting…" : `Run ${mode} analysis`}</Button>
      </div>
      <div>
        <Panel title="What can I do with this data?">
          {caps.loading ? <Spinner /> : caps.error ? <ErrorBox error={caps.error} /> : (
            <div className="space-y-3 text-sm">
              {caps.data.capabilities.length === 0 && <p className="text-slate-600">Upload and process files to see which analyses are possible.</p>}
              {caps.data.capabilities.map((c: any) => (
                <div key={c.key} className="border-b border-slate-100 pb-2">
                  <p className="font-medium">{c.title}</p>
                  <p className="text-xs text-slate-600">{c.description}</p>
                  <p className="mt-0.5 text-[11px] text-slate-500">Based on: {c.based_on.join(", ")}</p>
                  {c.unlock_more && <p className="text-[11px] text-amber-700">{c.unlock_more}</p>}
                  {c.example_questions.map((q: string) => (
                    <button key={q} onClick={() => addQ(q)} className="mt-1 block text-left text-xs text-blue-700 hover:underline">+ “{q}”</button>
                  ))}
                </div>
              ))}
              {caps.data.missing_data?.length > 0 && (
                <div><p className="text-xs font-medium text-slate-500">To unlock more analyses</p>
                  <ul className="list-disc pl-4 text-xs text-slate-600">{caps.data.missing_data.map((m: string) => <li key={m}>{m}</li>)}</ul></div>
              )}
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
