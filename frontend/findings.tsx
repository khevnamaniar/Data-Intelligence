import { useState } from "react";
import Chart from "./Chart";
import { api, Evidence, Finding } from "./api";
import { Button, ConfidenceBadge, Drawer, ErrorBox, Spinner, Tag, useAsync } from "./ui";

const CATEGORY_LABEL: Record<string, string> = {
  trend: "Trend", change: "Change", comparison: "Comparison", concentration: "Concentration", gap: "Gap", opportunity: "Opportunity",
  marketing: "Marketing", roas: "ROAS", strategy: "Strategy", plan_vs_actual: "Plan vs actual", impact: "Impact", anomaly: "Anomaly",
  relationship: "Relationship", reported: "Reported in source", contradiction: "Contradiction", theme: "Theme",
};

export function FindingCard({ f, projectId, defaultOpen = false }: { f: Finding; projectId: string; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const [source, setSource] = useState<Evidence | null>(null);
  const parts: [string, string, string][] = [
    ["FACT", f.fact, "What the data directly shows"],
    ["INTERPRETATION", f.interpretation, "What it may mean"],
    ["IMPLICATION", f.implication, "Why it may matter"],
    ["HYPOTHESIS", f.hypothesis, "What might explain it — not established"],
    ["VALIDATION", f.validation, "What should be checked"],
  ];
  return (
    <article className="rounded-lg border border-slate-200 bg-white" data-testid="finding">
      <button className="w-full text-left px-4 py-3" onClick={() => setOpen(!open)} aria-expanded={open}>
        <div className="flex flex-wrap items-center gap-2">
          <Tag tone={f.category === "opportunity" ? "green" : f.category === "gap" || f.category === "contradiction" ? "amber" : "blue"}>{CATEGORY_LABEL[f.category] || f.category}</Tag>
          <ConfidenceBadge level={f.confidence} />
          {!!f.is_approximate && <Tag tone="amber">Approximate / visually interpreted</Tag>}
          <span className="ml-auto text-xs text-slate-400">#{f.rank}</span>
        </div>
        <h3 className="mt-1.5 font-medium text-slate-900">{f.title}</h3>
        {!open && <p className="mt-1 line-clamp-2 text-sm text-slate-600">{f.fact}</p>}
      </button>
      {open && (
        <div className="border-t border-slate-100 px-4 pb-4">
          <dl className="mt-3 space-y-2.5">
            {parts.filter(([, v]) => v).map(([k, v, hint]) => (
              <div key={k} className={k === "HYPOTHESIS" ? "rounded bg-amber-50/60 p-2" : ""}>
                <dt className="text-[11px] font-semibold tracking-wide text-slate-500" title={hint}>{k} <span className="font-normal normal-case text-slate-400">— {hint}</span></dt>
                <dd className="text-sm text-slate-800">{v}</dd>
              </div>
            ))}
          </dl>
          {f.chart_json && <Chart spec={f.chart_json} />}
          <div className="mt-3">
            <p className="text-[11px] font-semibold tracking-wide text-slate-500">EVIDENCE → SOURCE</p>
            <ul className="mt-1 space-y-1">
              {f.evidence.map((e) => (
                <li key={e.id} className="flex items-start justify-between gap-2 rounded bg-slate-50 px-2 py-1.5 text-xs">
                  <span className="text-slate-700">
                    {e.source_label}
                    {!!e.is_approximate && <span className="ml-1 text-amber-700">(approximate)</span>}
                  </span>
                  {(e.dataset_id || e.chunk_id || e.file_id) && <Button small kind="secondary" onClick={() => setSource(e)}>View source</Button>}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
      {source && <SourceViewer projectId={projectId} evidence={source} onClose={() => setSource(null)} />}
    </article>
  );
}

export function SourceViewer({ projectId, evidence, onClose }: { projectId: string; evidence: Evidence; onClose: () => void }) {
  return (
    <Drawer open onClose={onClose} title="Source evidence">
      <p className="text-sm text-slate-700"><span className="font-medium">Source:</span> {evidence.source_label}</p>
      {evidence.excerpt && <blockquote className="mt-2 border-l-4 border-slate-300 pl-3 text-sm text-slate-700">{evidence.excerpt}</blockquote>}
      {!!evidence.is_approximate && <p className="mt-2 text-sm text-amber-800">These values were visually interpreted from an image and are approximate.</p>}
      {evidence.dataset_id && <DatasetRows projectId={projectId} datasetId={evidence.dataset_id} locator={evidence.locator_json} />}
      {evidence.chunk_id && <Passage projectId={projectId} chunkId={evidence.chunk_id} />}
      {evidence.file_id && (
        <p className="mt-4"><a className="text-sm text-blue-700 underline" href={`/api/projects/${projectId}/files/${evidence.file_id}/download`}>Download the original file</a></p>
      )}
    </Drawer>
  );
}

function DatasetRows({ projectId, datasetId, locator }: { projectId: string; datasetId: string; locator: any }) {
  const rows: number[] = locator?.row_sample || [];
  const q = rows.length ? `?rows=${rows.join(",")}&limit=50` : "?limit=25";
  const { data, error, loading } = useAsync(() => api.get<any>(`/api/projects/${projectId}/datasets/${datasetId}/preview${q}`), [datasetId]);
  if (loading) return <div className="mt-3"><Spinner label="Loading rows…" /></div>;
  if (error) return <ErrorBox error={error} />;
  const cols: string[] = data.columns;
  const highlight = new Set<string>(locator?.columns || []);
  return (
    <div className="mt-4">
      <p className="text-xs text-slate-500">
        {locator?.row_count ? `${locator.row_count} source row(s) support this finding (rows ${locator.rows}); showing up to 12 of them. ` : ""}
        Row numbers match the original file. Highlighted columns were used in the calculation.
        {locator?.filter && ` Filter: ${Object.entries(locator.filter).map(([k, v]) => `${k} = ${v}`).join(", ")}.`}
      </p>
      <div className="mt-2 max-h-96 overflow-auto rounded border">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-slate-50">
            <tr>{cols.map((c) => <th key={c} className={`px-2 py-1 text-left font-medium ${highlight.has(c) ? "bg-blue-50 text-blue-900" : "text-slate-600"}`}>{c === "__row__" ? "Row" : c}</th>)}</tr>
          </thead>
          <tbody>
            {data.rows.map((r: any, i: number) => (
              <tr key={i} className="border-t border-slate-100">{cols.map((c) => <td key={c} className={`px-2 py-1 ${highlight.has(c) ? "bg-blue-50/40" : ""}`}>{r[c] === null || r[c] === undefined ? "" : String(r[c])}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Passage({ projectId, chunkId }: { projectId: string; chunkId: string }) {
  const { data, error, loading } = useAsync(() => api.get<any>(`/api/projects/${projectId}/chunks/${chunkId}`), [chunkId]);
  if (loading) return <div className="mt-3"><Spinner label="Loading passage…" /></div>;
  if (error) return <ErrorBox error={error} />;
  return (
    <div className="mt-4 space-y-2">
      <p className="text-xs text-slate-500">Passage in context from {data.file.original_name}:</p>
      {data.context.map((c: any) => (
        <p key={c.id} className={`rounded p-2 text-sm ${c.id === chunkId ? "bg-yellow-50 ring-1 ring-yellow-300" : "text-slate-500"}`}>{c.text}</p>
      ))}
    </div>
  );
}
