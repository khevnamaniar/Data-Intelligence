import { useEffect, useRef, useState } from "react";
import Chart, { fmtVal } from "../Chart";
import { api, Evidence } from "../api";
import { SourceViewer } from "../findings";
import { parseHash } from "../router";
import { Button, ConfidenceBadge, ErrorBox, inputCls, Panel, Spinner, Tag, useAsync } from "../ui";
import type { ProjectDetail } from "./Project";

const EXAMPLES = ["Why did this category decline?", "Which products are growing?", "What are the biggest gaps?", "Is ROAS being driven by Meta?",
  "Compare the previous strategy with the new strategy.", "What should we investigate next?"];

export function AskPage({ d }: { d: ProjectDetail }) {
  const pid = d.project.id;
  const hist = useAsync(() => api.get<{ messages: any[] }>(`/api/projects/${pid}/ask`), [pid]);
  const [q, setQ] = useState(parseHash().query.get("q") || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [source, setSource] = useState<Evidence | null>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [hist.data]);
  const send = async (question: string) => {
    if (!question.trim()) return;
    setBusy(true); setError(null);
    try {
      const r = await api.post<{ message: any }>(`/api/projects/${pid}/ask`, { question });
      hist.setData({ messages: [...(hist.data?.messages || []), r.message] });
      setQ("");
    } catch (e) { setError(e); } finally { setBusy(false); }
  };
  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <Panel>
        <p className="text-sm text-slate-600">Ask follow-up questions about this project's data. Answers are calculated from your files (no external AI by default) and always show their evidence. If the data cannot answer a question, you'll be told what data would be needed.</p>
        <div className="mt-2 flex flex-wrap gap-1.5">{EXAMPLES.map((e) => <button key={e} onClick={() => send(e)} className="rounded-full border border-slate-300 px-2.5 py-0.5 text-xs text-slate-700 hover:bg-slate-50">{e}</button>)}</div>
      </Panel>
      {hist.loading ? <Spinner /> : (hist.data?.messages || []).map((m) => (
        <div key={m.id} className="space-y-2" data-testid="ask-message">
          <div className="ml-auto w-fit max-w-[80%] rounded-lg bg-slate-900 px-3 py-2 text-sm text-white">{m.question}</div>
          <Answer a={m.answer} onSource={setSource} onFollow={send} />
        </div>
      ))}
      <div ref={end} />
      <ErrorBox error={error} />
      <form className="sticky bottom-3 flex gap-2 rounded-lg border border-slate-200 bg-white p-2 shadow-sm" onSubmit={(e) => { e.preventDefault(); send(q); }}>
        <input aria-label="Your question" className={inputCls} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask a question about your data…" />
        <Button type="submit" disabled={busy || q.trim().length < 3}>{busy ? "Thinking…" : "Ask"}</Button>
      </form>
      {source && <SourceViewer projectId={pid} evidence={source} onClose={() => setSource(null)} />}
    </div>
  );
}

function Answer({ a, onSource, onFollow }: { a: any; onSource: (e: Evidence) => void; onFollow: (q: string) => void }) {
  return (
    <div className="max-w-[90%] rounded-lg border border-slate-200 bg-white p-3 text-sm">
      <div className="mb-1 flex items-center gap-2"><ConfidenceBadge level={a.confidence} />{a.insufficient && <Tag tone="amber">Insufficient data</Tag>}</div>
      <p className="text-slate-800">{a.answer}</p>
      {a.data_needed?.length > 0 && a.insufficient && <p className="mt-1 text-xs text-slate-600">Data that would be needed: {a.data_needed.join("; ")}</p>}
      {a.facts?.length > 1 && (
        <div className="mt-2"><p className="text-[11px] font-semibold tracking-wide text-slate-500">FACTS (computed)</p><ul className="list-disc pl-5 text-slate-700">{a.facts.slice(0, 10).map((f: string, i: number) => <li key={i}>{f}</li>)}</ul></div>
      )}
      {a.hypotheses?.length > 0 && (
        <div className="mt-2 rounded bg-amber-50/60 p-2"><p className="text-[11px] font-semibold tracking-wide text-slate-500">HYPOTHESES (not established)</p><ul className="list-disc pl-5 text-slate-700">{a.hypotheses.map((h: string, i: number) => <li key={i}>{h}</li>)}</ul></div>
      )}
      {a.ai_summary && <div className="mt-2 rounded bg-blue-50 p-2 text-xs"><b>AI-written summary ({a.ai_summary.provider}):</b> {a.ai_summary.text}<div className="text-slate-500">{a.ai_summary.note}</div></div>}
      {a.chart && <Chart spec={a.chart} />}
      {a.table && !a.chart && (
        <div className="mt-2 max-h-56 overflow-auto"><table className="w-full text-xs"><thead><tr>{a.table.columns.map((c: string) => <th key={c} className="border-b px-1 text-left">{c}</th>)}</tr></thead>
          <tbody>{a.table.rows.slice(0, 30).map((r: any, i: number) => <tr key={i}>{a.table.columns.map((c: string) => <td key={c} className="border-b border-slate-100 px-1">{typeof r[c] === "number" ? fmtVal(r[c]) : String(r[c] ?? "")}</td>)}</tr>)}</tbody></table></div>
      )}
      {a.evidence?.length > 0 && (
        <div className="mt-2"><p className="text-[11px] font-semibold tracking-wide text-slate-500">EVIDENCE</p>
          <ul className="space-y-1">{a.evidence.slice(0, 6).map((e: any, i: number) => (
            <li key={i} className="flex items-center justify-between gap-2 rounded bg-slate-50 px-2 py-1 text-xs"><span>{e.source_label}</span>
              {(e.dataset_id || e.chunk_id) && <Button small kind="secondary" onClick={() => onSource({ ...e, id: String(i), locator_json: e.locator || e.locator_json, is_approximate: e.is_approximate })}>View source</Button>}</li>))}</ul></div>
      )}
      {a.follow_ups?.length > 0 && <div className="mt-2 flex flex-wrap gap-1">{a.follow_ups.map((f: string) => <button key={f} onClick={() => onFollow(f)} className="rounded-full border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50">{f}</button>)}</div>}
    </div>
  );
}
